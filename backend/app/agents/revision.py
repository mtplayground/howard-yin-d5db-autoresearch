from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Artifact, ArtifactKind, Paper, PaperStatus
from app.services.model_adapter import ModelAdapter, ModelAdapterError, ModelMessage, ModelRequest, build_model_adapter
from app.services.model_settings import load_effective_model_settings
from app.services.storage import ObjectNotFoundError, ObjectStorageClient, StoredObjectRef, StorageError, get_storage_client

MAX_LATEX_BYTES = 600_000
MAX_REVISION_ITERATIONS = 5
SAFE_KEY_PATTERN = re.compile(r"[^A-Za-z0-9._/-]+")


class PaperRevisionAgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class RevisionIteration:
    index: int
    critique: str
    changes: list[str]
    quality_score: float
    stop: bool
    latex_source: str
    usage: dict[str, Any] = field(default_factory=dict)

    def as_metadata(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "critique": self.critique,
            "changes": self.changes,
            "quality_score": self.quality_score,
            "stop": self.stop,
            "usage": self.usage,
        }


@dataclass(frozen=True)
class PaperRevisionResult:
    latex_source: str
    iterations: list[RevisionIteration]
    provider: str
    model: str
    stopped_reason: str


class PaperRevisionAgent:
    def __init__(self, adapter: ModelAdapter) -> None:
        self._adapter = adapter

    async def revise(
        self,
        paper: Paper,
        latex_source: str,
        *,
        max_iterations: int = 3,
        min_quality_score: float = 0.88,
    ) -> PaperRevisionResult:
        if max_iterations < 1 or max_iterations > MAX_REVISION_ITERATIONS:
            raise PaperRevisionAgentError(f"max_iterations must be between 1 and {MAX_REVISION_ITERATIONS}")
        if min_quality_score < 0 or min_quality_score > 1:
            raise PaperRevisionAgentError("min_quality_score must be between 0 and 1")
        current_latex = _latex_source(latex_source)
        iterations: list[RevisionIteration] = []
        provider = ""
        model = ""
        stopped_reason = "max_iterations"

        for index in range(1, max_iterations + 1):
            try:
                response = await self._adapter.complete(
                    ModelRequest(
                        messages=[
                            ModelMessage(
                                role="system",
                                content=(
                                    "You are a critical research-paper reviewer and careful LaTeX editor. "
                                    "Review the draft, identify concrete weaknesses, and return strict JSON only "
                                    "with critique, changes, quality_score, stop, and latex_source. Preserve valid "
                                    "citations, bibliography, figure inputs, labels, and arXiv-style structure. "
                                    "Set stop true only when the paper is clear, internally consistent, evidence-bound, "
                                    "and needs no further substantial revision."
                                ),
                            ),
                            ModelMessage(
                                role="user",
                                content=_revision_prompt(paper, current_latex, index=index, min_quality_score=min_quality_score),
                            ),
                        ],
                        temperature=0.2,
                        max_tokens=5200,
                    )
                )
            except ModelAdapterError as exc:
                raise PaperRevisionAgentError(str(exc)) from exc

            payload = _parse_json_object(response.content)
            iteration = RevisionIteration(
                index=index,
                critique=_required_text(payload.get("critique"), "critique"),
                changes=_text_list(payload.get("changes"), "changes"),
                quality_score=_quality_score(payload.get("quality_score")),
                stop=bool(payload.get("stop")),
                latex_source=_latex_source(payload.get("latex_source")),
                usage=response.usage,
            )
            iterations.append(iteration)
            current_latex = iteration.latex_source
            provider = response.provider
            model = response.model
            if iteration.stop:
                stopped_reason = "model_stop"
                break
            if iteration.quality_score >= min_quality_score:
                stopped_reason = "quality_threshold"
                break

        return PaperRevisionResult(
            latex_source=current_latex,
            iterations=iterations,
            provider=provider,
            model=model,
            stopped_reason=stopped_reason,
        )


async def revise_and_persist_paper(
    db: Session,
    paper_id: uuid.UUID,
    agent: PaperRevisionAgent,
    *,
    storage: ObjectStorageClient | None = None,
    max_iterations: int = 3,
    min_quality_score: float = 0.88,
) -> Paper:
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise PaperRevisionAgentError(f"Paper {paper_id} was not found")
    if not paper.latex_storage_key:
        raise PaperRevisionAgentError("paper has no LaTeX source storage key")
    storage_client = storage or get_storage_client()
    current_latex = _download_latex(storage_client, paper.latex_storage_key)

    paper.status = PaperStatus.REVIEWING.value
    _merge_revision_notes(paper, {"status": "running", "started_at": datetime.now(UTC).isoformat()})
    db.commit()
    db.refresh(paper)

    try:
        result = await agent.revise(
            paper,
            current_latex,
            max_iterations=max_iterations,
            min_quality_score=min_quality_score,
        )
        latex_bytes = result.latex_source.encode("utf-8")
        ref = _upload_final_latex(storage_client, paper, latex_bytes)
    except PaperRevisionAgentError as exc:
        paper.status = PaperStatus.FAILED.value
        _merge_revision_notes(
            paper,
            {
                "status": "failed",
                "error": str(exc),
                "completed_at": datetime.now(UTC).isoformat(),
            },
        )
        db.commit()
        raise
    artifact = _revision_latex_artifact(paper, ref)
    existing = db.scalar(select(Artifact).where(Artifact.storage_key == artifact.storage_key))
    if existing is None:
        db.add(artifact)
    else:
        existing.kind = artifact.kind
        existing.filename = artifact.filename
        existing.content_type = artifact.content_type
        existing.byte_size = artifact.byte_size
        existing.checksum_sha256 = artifact.checksum_sha256
        existing.extra = artifact.extra

    final_score = result.iterations[-1].quality_score if result.iterations else None
    paper.status = PaperStatus.DRAFT.value
    paper.latex_storage_key = ref.key
    paper.pdf_storage_key = None
    paper.compiled_at = None
    _merge_revision_notes(
        paper,
        {
            "status": "succeeded",
            "provider": result.provider,
            "model": result.model,
            "stopped_reason": result.stopped_reason,
            "max_iterations": max_iterations,
            "min_quality_score": min_quality_score,
            "final_quality_score": final_score,
            "iterations": [iteration.as_metadata() for iteration in result.iterations],
            "completed_at": datetime.now(UTC).isoformat(),
            "latex_storage_key": ref.key,
        },
    )
    db.commit()
    db.refresh(paper)
    return paper


async def revise_paper_with_configured_model(
    db: Session,
    settings: Settings,
    paper_id: uuid.UUID,
    *,
    storage: ObjectStorageClient | None = None,
    max_iterations: int = 3,
    min_quality_score: float = 0.88,
) -> Paper:
    effective_settings = load_effective_model_settings(db, settings)
    adapter = build_model_adapter(effective_settings, timeout_seconds=settings.model_request_timeout_seconds)
    return await revise_and_persist_paper(
        db,
        paper_id,
        PaperRevisionAgent(adapter),
        storage=storage,
        max_iterations=max_iterations,
        min_quality_score=min_quality_score,
    )


def _revision_prompt(paper: Paper, latex_source: str, *, index: int, min_quality_score: float) -> str:
    return "\n".join(
        [
            f"Paper ID: {paper.id}",
            f"Revision round: {index}",
            f"Stop when quality_score >= {min_quality_score:.2f} and no material issues remain.",
            f"Title: {paper.title}",
            f"Abstract: {paper.abstract or 'None'}",
            f"Bibliography metadata: {json.dumps(paper.bibliography or {}, ensure_ascii=False, sort_keys=True, default=str)}",
            f"Prior review notes: {json.dumps(paper.review_notes or {}, ensure_ascii=False, sort_keys=True, default=str)}",
            "",
            "Current LaTeX draft:",
            latex_source,
            "",
            "Return strict JSON: {\"critique\":\"...\",\"changes\":[\"...\"],\"quality_score\":0.0,"
            "\"stop\":false,\"latex_source\":\"\\\\documentclass{article}...\"}",
        ]
    )


def _download_latex(storage: ObjectStorageClient, key: str) -> str:
    try:
        data = storage.download_bytes(key)
    except ObjectNotFoundError as exc:
        raise PaperRevisionAgentError("paper LaTeX source was not found in object storage") from exc
    except StorageError as exc:
        raise PaperRevisionAgentError(str(exc)) from exc
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PaperRevisionAgentError("paper LaTeX source was not UTF-8 text") from exc


def _upload_final_latex(storage: ObjectStorageClient, paper: Paper, data: bytes) -> StoredObjectRef:
    checksum = hashlib.sha256(data).hexdigest()
    key = f"papers/runs/{paper.run_id or 'unbound'}/{paper.id}/main.final.tex"
    try:
        return storage.upload_bytes(
            _safe_storage_key(key),
            data,
            content_type="application/x-tex; charset=utf-8",
            checksum_sha256=checksum,
            metadata={
                "paper-id": str(paper.id),
                "artifact-kind": ArtifactKind.LATEX.value,
                "revision": "final",
            },
        )
    except StorageError as exc:
        raise PaperRevisionAgentError(str(exc)) from exc


def _revision_latex_artifact(paper: Paper, ref: StoredObjectRef) -> Artifact:
    return Artifact(
        run_id=paper.run_id,
        idea_id=paper.idea_id,
        experiment_id=paper.experiment_id,
        paper_id=paper.id,
        kind=ArtifactKind.LATEX.value,
        storage_key=ref.key,
        filename="main.final.tex",
        content_type=ref.content_type,
        byte_size=ref.byte_size,
        checksum_sha256=ref.checksum_sha256,
        extra={"source": "paper_revision_agent", "storage_uri": ref.uri},
    )


def _merge_revision_notes(paper: Paper, revision_payload: dict[str, Any]) -> None:
    review_notes = dict(paper.review_notes or {})
    review_notes["revision"] = revision_payload
    paper.review_notes = review_notes


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PaperRevisionAgentError("paper revision model response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise PaperRevisionAgentError("paper revision model response was not a JSON object")
    return payload


def _latex_source(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PaperRevisionAgentError("paper revision latex_source must be non-empty text")
    source = value.strip()
    if len(source.encode("utf-8")) > MAX_LATEX_BYTES:
        raise PaperRevisionAgentError(f"paper revision latex_source exceeds {MAX_LATEX_BYTES} bytes")
    required_fragments = ["\\documentclass", "\\begin{document}", "\\begin{abstract}", "\\end{document}"]
    missing = [fragment for fragment in required_fragments if fragment not in source]
    if missing:
        raise PaperRevisionAgentError(f"paper revision latex_source is missing required LaTeX fragments: {', '.join(missing)}")
    return source


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise PaperRevisionAgentError(f"paper revision response field {field_name} must be text")
    text = " ".join(value.split())
    if not text:
        raise PaperRevisionAgentError(f"paper revision response field {field_name} must not be empty")
    return text


def _text_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PaperRevisionAgentError(f"paper revision response field {field_name} must be an array")
    texts: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise PaperRevisionAgentError(f"paper revision response field {field_name} must contain strings")
        text = " ".join(item.split())
        if text:
            texts.append(text)
    return texts


def _quality_score(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise PaperRevisionAgentError("paper revision response field quality_score must be numeric")
    score = float(value)
    if score < 0 or score > 1:
        raise PaperRevisionAgentError("paper revision response field quality_score must be between 0 and 1")
    return score


def _safe_storage_key(key: str) -> str:
    clean = SAFE_KEY_PATTERN.sub("-", key.strip().strip("/"))
    clean = "/".join(part for part in Path(clean).parts if part not in {"", "."})
    if not clean or clean == ".." or "/../" in f"/{clean}/":
        raise PaperRevisionAgentError(f"unsafe revision storage key: {key}")
    return clean

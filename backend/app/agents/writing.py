from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Artifact, ArtifactKind, Experiment, ExperimentStatus, Idea, KnowledgeItem, Paper, PaperStatus, Run
from app.services.model_adapter import ModelAdapter, ModelAdapterError, ModelMessage, ModelRequest, build_model_adapter
from app.services.model_settings import load_effective_model_settings
from app.services.storage import ObjectStorageClient, StoredObjectRef, StorageError, get_storage_client

MAX_LATEX_BYTES = 500_000
SAFE_KEY_PATTERN = re.compile(r"[^A-Za-z0-9._/-]+")


class PaperWritingAgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class PaperWritingResult:
    title: str
    abstract: str
    latex_source: str
    bibliography_entries: list[dict[str, Any]] = field(default_factory=list)
    section_outline: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


class PaperWritingAgent:
    def __init__(self, adapter: ModelAdapter) -> None:
        self._adapter = adapter

    async def write(
        self,
        run: Run,
        idea: Idea,
        experiment: Experiment,
        *,
        knowledge_items: Sequence[KnowledgeItem] = (),
        artifacts: Sequence[Artifact] = (),
    ) -> PaperWritingResult:
        _ensure_ready_for_writing(run, idea, experiment)
        try:
            response = await self._adapter.complete(
                ModelRequest(
                    messages=[
                        ModelMessage(
                            role="system",
                            content=(
                                "You write complete arXiv-style LaTeX papers from confirmed research ideas, "
                                "related work, and experiment results. Return strict JSON only with keys title, "
                                "abstract, latex_source, bibliography_entries, section_outline, and limitations. "
                                "latex_source must be a full compilable article with documentclass, abstract, "
                                "Introduction, Related Work, Method, Experiments, Results, Limitations, Conclusion, "
                                "and bibliography sections."
                            ),
                        ),
                        ModelMessage(
                            role="user",
                            content=_writing_prompt(run, idea, experiment, knowledge_items=knowledge_items, artifacts=artifacts),
                        ),
                    ],
                    temperature=0.25,
                    max_tokens=4200,
                )
            )
        except ModelAdapterError as exc:
            raise PaperWritingAgentError(str(exc)) from exc

        payload = _parse_json_object(response.content)
        latex_source = _latex_source(payload.get("latex_source"))
        return PaperWritingResult(
            title=_required_text(payload.get("title"), "title", max_length=300),
            abstract=_required_text(payload.get("abstract"), "abstract"),
            latex_source=latex_source,
            bibliography_entries=_dict_list(payload.get("bibliography_entries"), "bibliography_entries"),
            section_outline=_text_list(payload.get("section_outline"), "section_outline"),
            limitations=_text_list(payload.get("limitations"), "limitations"),
            model=response.model,
            provider=response.provider,
            usage=response.usage,
        )


async def write_and_persist_paper(
    db: Session,
    run_id: uuid.UUID,
    agent: PaperWritingAgent,
    *,
    storage: ObjectStorageClient | None = None,
) -> Paper:
    run = db.get(Run, run_id)
    if run is None:
        raise PaperWritingAgentError(f"Run {run_id} was not found")
    idea = _load_run_idea(run)
    experiment = _latest_successful_experiment(db, run)
    knowledge_items = _load_related_knowledge_items(db, idea)
    artifacts = _load_experiment_artifacts(db, experiment)
    result = await agent.write(run, idea, experiment, knowledge_items=knowledge_items, artifacts=artifacts)

    storage_client = storage or get_storage_client()
    generated_at = datetime.now(UTC)
    paper = Paper(
        run_id=run.id,
        idea_id=idea.id,
        experiment_id=experiment.id,
        title=result.title,
        abstract=result.abstract,
        status=PaperStatus.DRAFT.value,
        bibliography={
            "entries": result.bibliography_entries,
            "source_knowledge_item_ids": [str(item.id) for item in knowledge_items],
            "section_outline": result.section_outline,
        },
        review_notes={
            "limitations": result.limitations,
            "writing": {
                "provider": result.provider,
                "model": result.model,
                "usage": result.usage,
                "generated_at": generated_at.isoformat(),
            },
        },
    )
    db.add(paper)
    db.flush()

    latex_bytes = result.latex_source.encode("utf-8")
    ref = _upload_latex(storage_client, paper, latex_bytes)
    paper.latex_storage_key = ref.key
    artifact = _latex_artifact(paper, experiment, ref)
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
    db.commit()
    db.refresh(paper)
    return paper


async def write_paper_with_configured_model(
    db: Session,
    settings: Settings,
    run_id: uuid.UUID,
    *,
    storage: ObjectStorageClient | None = None,
) -> Paper:
    effective_settings = load_effective_model_settings(db, settings)
    adapter = build_model_adapter(effective_settings, timeout_seconds=settings.model_request_timeout_seconds)
    return await write_and_persist_paper(db, run_id, PaperWritingAgent(adapter), storage=storage)


def _ensure_ready_for_writing(run: Run, idea: Idea, experiment: Experiment) -> None:
    if run.id != experiment.run_id:
        raise PaperWritingAgentError("experiment does not belong to the requested run")
    if idea.id != run.idea_id:
        raise PaperWritingAgentError("idea does not belong to the requested run")
    if experiment.status != ExperimentStatus.SUCCEEDED.value:
        raise PaperWritingAgentError("paper writing requires a succeeded experiment")


def _load_run_idea(run: Run) -> Idea:
    if run.idea is None:
        raise PaperWritingAgentError("run is not associated with an idea")
    return run.idea


def _latest_successful_experiment(db: Session, run: Run) -> Experiment:
    experiment = db.scalar(
        select(Experiment)
        .where(Experiment.run_id == run.id, Experiment.status == ExperimentStatus.SUCCEEDED.value)
        .order_by(Experiment.completed_at.desc().nullslast(), Experiment.updated_at.desc())
        .limit(1)
    )
    if experiment is None:
        raise PaperWritingAgentError("run has no succeeded experiment to write about")
    return experiment


def _load_related_knowledge_items(db: Session, idea: Idea) -> list[KnowledgeItem]:
    raw_ids = (idea.source_context or {}).get("knowledge_item_ids")
    if not isinstance(raw_ids, list):
        return []
    knowledge_ids: list[uuid.UUID] = []
    for raw_id in raw_ids:
        if not isinstance(raw_id, str):
            continue
        try:
            knowledge_ids.append(uuid.UUID(raw_id))
        except ValueError:
            continue
    if not knowledge_ids:
        return []
    items_by_id = {item.id: item for item in db.scalars(select(KnowledgeItem).where(KnowledgeItem.id.in_(knowledge_ids)))}
    return [items_by_id[item_id] for item_id in knowledge_ids if item_id in items_by_id]


def _load_experiment_artifacts(db: Session, experiment: Experiment) -> list[Artifact]:
    return list(
        db.scalars(
            select(Artifact)
            .where(Artifact.experiment_id == experiment.id)
            .order_by(Artifact.kind.asc(), Artifact.created_at.asc())
        )
    )


def _writing_prompt(
    run: Run,
    idea: Idea,
    experiment: Experiment,
    *,
    knowledge_items: Sequence[KnowledgeItem],
    artifacts: Sequence[Artifact],
) -> str:
    return "\n\n".join(
        [
            "\n".join(
                [
                    "Confirmed idea",
                    f"Run ID: {run.id}",
                    f"Idea ID: {idea.id}",
                    f"Title: {idea.title}",
                    f"Problem statement: {idea.problem_statement or 'None'}",
                    f"Hypothesis: {idea.hypothesis or 'None'}",
                    f"Motivation: {idea.rationale or 'None'}",
                    f"Feasibility: {(idea.extra or {}).get('feasibility') or 'None'}",
                    f"Reusable points: {json.dumps((idea.extra or {}).get('reusable_points') or [], ensure_ascii=False)}",
                ]
            ),
            "\n".join(
                [
                    "Experiment",
                    f"Experiment ID: {experiment.id}",
                    f"Title: {experiment.title}",
                    f"Hypothesis: {experiment.hypothesis or 'None'}",
                    f"Result summary: {experiment.result_summary or 'None'}",
                    f"Metrics: {json.dumps(experiment.metrics or {}, ensure_ascii=False, sort_keys=True, default=str)}",
                    f"Dependencies: {json.dumps(experiment.dependencies or [], ensure_ascii=False)}",
                    f"Run command: {json.dumps(experiment.run_command or [], ensure_ascii=False)}",
                ]
            ),
            _related_work_prompt(knowledge_items),
            _artifact_prompt(artifacts),
            (
                "Write a complete arXiv-style LaTeX paper. The JSON response must have this exact shape: "
                '{"title":"...","abstract":"...","latex_source":"\\\\documentclass{article}...'
                '\\\\end{document}","bibliography_entries":[{"key":"...","title":"...","url":"..."}],'
                '"section_outline":["Introduction", "..."],"limitations":["..."]}. '
                "Use concise, evidence-bound claims and cite related work with LaTeX \\cite{...} keys that match "
                "bibliography_entries."
            ),
        ]
    )


def _related_work_prompt(knowledge_items: Sequence[KnowledgeItem]) -> str:
    if not knowledge_items:
        return "Related work\nNo structured related work rows were linked to the idea; use the idea context only."
    entries = []
    for index, item in enumerate(knowledge_items, start=1):
        entries.append(
            {
                "index": index,
                "id": str(item.id),
                "title": item.title,
                "authors": item.authors,
                "summary": item.summary or item.abstract,
                "methods": item.methods,
                "contributions": item.contributions,
                "reusable_points": item.reusable_points,
                "url": item.url,
                "code_repository_url": item.code_repository_url,
            }
        )
    return "Related work\n" + json.dumps(entries, ensure_ascii=False, sort_keys=True, default=str)


def _artifact_prompt(artifacts: Sequence[Artifact]) -> str:
    if not artifacts:
        return "Experiment artifacts\nNo persisted artifacts are available."
    return "Experiment artifacts\n" + json.dumps(
        [
            {
                "kind": artifact.kind,
                "filename": artifact.filename,
                "storage_key": artifact.storage_key,
                "content_type": artifact.content_type,
                "byte_size": artifact.byte_size,
            }
            for artifact in artifacts
        ],
        ensure_ascii=False,
        sort_keys=True,
    )


def _upload_latex(storage: ObjectStorageClient, paper: Paper, data: bytes) -> StoredObjectRef:
    checksum = hashlib.sha256(data).hexdigest()
    key = f"papers/runs/{paper.run_id or 'unbound'}/{paper.id}/main.tex"
    try:
        return storage.upload_bytes(
            _safe_storage_key(key),
            data,
            content_type="application/x-tex; charset=utf-8",
            checksum_sha256=checksum,
            metadata={
                "paper-id": str(paper.id),
                "artifact-kind": ArtifactKind.LATEX.value,
            },
        )
    except StorageError as exc:
        raise PaperWritingAgentError(str(exc)) from exc


def _latex_artifact(paper: Paper, experiment: Experiment, ref: StoredObjectRef) -> Artifact:
    return Artifact(
        run_id=paper.run_id,
        idea_id=paper.idea_id,
        experiment_id=experiment.id,
        paper_id=paper.id,
        kind=ArtifactKind.LATEX.value,
        storage_key=ref.key,
        filename="main.tex",
        content_type=ref.content_type,
        byte_size=ref.byte_size,
        checksum_sha256=ref.checksum_sha256,
        extra={"source": "paper_writing_agent", "storage_uri": ref.uri},
    )


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
        raise PaperWritingAgentError("paper writing model response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise PaperWritingAgentError("paper writing model response was not a JSON object")
    return payload


def _required_text(value: Any, field_name: str, *, max_length: int | None = None) -> str:
    if not isinstance(value, str):
        raise PaperWritingAgentError(f"paper writing response field {field_name} must be text")
    text = " ".join(value.split())
    if not text:
        raise PaperWritingAgentError(f"paper writing response field {field_name} must not be empty")
    if max_length is not None and len(text) > max_length:
        return text[: max_length - 3].rstrip() + "..."
    return text


def _latex_source(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PaperWritingAgentError("paper writing response field latex_source must be non-empty text")
    source = value.strip()
    byte_size = len(source.encode("utf-8"))
    if byte_size > MAX_LATEX_BYTES:
        raise PaperWritingAgentError(f"paper writing latex_source exceeds {MAX_LATEX_BYTES} bytes")
    required_fragments = ["\\documentclass", "\\begin{document}", "\\begin{abstract}", "\\end{document}"]
    missing = [fragment for fragment in required_fragments if fragment not in source]
    if missing:
        raise PaperWritingAgentError(f"paper writing latex_source is missing required LaTeX fragments: {', '.join(missing)}")
    return source


def _text_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PaperWritingAgentError(f"paper writing response field {field_name} must be an array")
    texts: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise PaperWritingAgentError(f"paper writing response field {field_name} must contain strings")
        text = " ".join(item.split())
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            texts.append(text)
    return texts


def _dict_list(value: Any, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PaperWritingAgentError(f"paper writing response field {field_name} must be an array")
    entries: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise PaperWritingAgentError(f"paper writing response field {field_name} must contain objects")
        entries.append(dict(item))
    return entries


def _safe_storage_key(key: str) -> str:
    clean = SAFE_KEY_PATTERN.sub("-", key.strip().strip("/"))
    clean = "/".join(part for part in Path(clean).parts if part not in {"", "."})
    if not clean or clean == ".." or "/../" in f"/{clean}/":
        raise PaperWritingAgentError(f"unsafe paper storage key: {key}")
    return clean

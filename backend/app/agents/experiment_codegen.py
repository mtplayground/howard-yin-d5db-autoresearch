from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Experiment, ExperimentStatus, Idea, IdeaStatus
from app.services.model_adapter import ModelAdapter, ModelAdapterError, ModelMessage, ModelRequest, build_model_adapter
from app.services.model_settings import load_effective_model_settings

MAX_CODE_FILES = 16
MAX_CODE_BYTES = 120_000


class ExperimentCodegenAgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExperimentCodegenResult:
    title: str
    hypothesis: str
    files: dict[str, str]
    dependencies: list[str] = field(default_factory=list)
    run_command: list[str] = field(default_factory=list)
    validation_notes: list[str] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


class ExperimentCodegenAgent:
    def __init__(self, adapter: ModelAdapter) -> None:
        self._adapter = adapter

    async def generate(self, idea: Idea) -> ExperimentCodegenResult:
        _ensure_confirmed_idea(idea)
        try:
            response = await self._adapter.complete(
                ModelRequest(
                    messages=[
                        ModelMessage(
                            role="system",
                            content=(
                                "You design runnable automated research experiments from one confirmed idea. "
                                "Return strict JSON only with keys title, hypothesis, files, dependencies, "
                                "run_command, and validation_notes. files must map relative file paths to complete "
                                "file contents. Prefer a small Python experiment with a deterministic smoke path."
                            ),
                        ),
                        ModelMessage(role="user", content=_codegen_prompt(idea)),
                    ],
                    temperature=0.2,
                    max_tokens=2400,
                )
            )
        except ModelAdapterError as exc:
            raise ExperimentCodegenAgentError(str(exc)) from exc

        payload = _parse_json_object(response.content)
        files = _files_map(payload.get("files"))
        return ExperimentCodegenResult(
            title=_required_text(payload.get("title"), "title", max_length=240),
            hypothesis=_required_text(payload.get("hypothesis"), "hypothesis"),
            files=files,
            dependencies=_text_list(payload.get("dependencies"), "dependencies"),
            run_command=_command_list(payload.get("run_command")),
            validation_notes=_text_list(payload.get("validation_notes"), "validation_notes"),
            model=response.model,
            provider=response.provider,
            usage=response.usage,
        )


async def generate_and_persist_experiment_code(
    db: Session,
    idea_id: uuid.UUID,
    agent: ExperimentCodegenAgent,
    *,
    run_id: uuid.UUID | None = None,
) -> Experiment:
    idea = db.get(Idea, idea_id)
    if idea is None:
        raise ExperimentCodegenAgentError(f"Idea {idea_id} was not found")
    result = await agent.generate(idea)
    generated_at = datetime.now(UTC)
    experiment = Experiment(
        run_id=run_id,
        idea_id=idea.id,
        title=result.title,
        hypothesis=result.hypothesis,
        status=ExperimentStatus.PLANNED.value,
        code_files=result.files,
        dependencies=result.dependencies,
        run_command=result.run_command,
        codegen_model=result.model,
        code_generated_at=generated_at,
        metrics={
            "codegen": {
                "provider": result.provider,
                "model": result.model,
                "usage": result.usage,
                "generated_at": generated_at.isoformat(),
                "validation_notes": result.validation_notes,
                "file_count": len(result.files),
            }
        },
    )
    db.add(experiment)
    db.commit()
    db.refresh(experiment)
    return experiment


async def generate_experiment_code_with_configured_model(
    db: Session,
    settings: Settings,
    idea_id: uuid.UUID,
    *,
    run_id: uuid.UUID | None = None,
) -> Experiment:
    effective_settings = load_effective_model_settings(db, settings)
    adapter = build_model_adapter(effective_settings, timeout_seconds=settings.model_request_timeout_seconds)
    return await generate_and_persist_experiment_code(
        db,
        idea_id,
        ExperimentCodegenAgent(adapter),
        run_id=run_id,
    )


def _ensure_confirmed_idea(idea: Idea) -> None:
    if idea.status != IdeaStatus.APPROVED.value:
        raise ExperimentCodegenAgentError("experiment code can only be generated for an approved idea")


def _codegen_prompt(idea: Idea) -> str:
    return "\n".join(
        [
            "Confirmed idea:",
            f"ID: {idea.id}",
            f"Title: {idea.title}",
            f"Problem statement: {idea.problem_statement or 'None'}",
            f"Hypothesis: {idea.hypothesis or 'None'}",
            f"Motivation: {idea.rationale or 'None'}",
            f"Source context: {json.dumps(idea.source_context or {}, ensure_ascii=False, sort_keys=True)}",
            f"Extra: {json.dumps(idea.extra or {}, ensure_ascii=False, sort_keys=True, default=str)}",
            "",
            "Design a runnable experiment package. Return this exact JSON shape:",
            (
                '{"title":"...","hypothesis":"...","files":{"experiment.py":"..."},'
                '"dependencies":["numpy==..."],"run_command":["python","experiment.py"],'
                '"validation_notes":["..."]}'
            ),
            "The run_command must reference files you include and must be suitable for the CPU sandbox.",
        ]
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
        raise ExperimentCodegenAgentError("experiment codegen model response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ExperimentCodegenAgentError("experiment codegen model response was not a JSON object")
    return payload


def _required_text(value: Any, field_name: str, *, max_length: int | None = None) -> str:
    if not isinstance(value, str):
        raise ExperimentCodegenAgentError(f"experiment codegen response field {field_name} must be text")
    text = " ".join(value.split())
    if not text:
        raise ExperimentCodegenAgentError(f"experiment codegen response field {field_name} must not be empty")
    if max_length is not None and len(text) > max_length:
        return text[: max_length - 3].rstrip() + "..."
    return text


def _files_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise ExperimentCodegenAgentError("experiment codegen response field files must be a non-empty object")
    if len(value) > MAX_CODE_FILES:
        raise ExperimentCodegenAgentError(f"experiment codegen response can include at most {MAX_CODE_FILES} files")

    files: dict[str, str] = {}
    total_bytes = 0
    for raw_path, raw_content in value.items():
        if not isinstance(raw_path, str) or not isinstance(raw_content, str):
            raise ExperimentCodegenAgentError("experiment codegen files must map text paths to text contents")
        path = _safe_relative_path(raw_path)
        total_bytes += len(raw_content.encode("utf-8"))
        files[path.as_posix()] = raw_content
    if total_bytes > MAX_CODE_BYTES:
        raise ExperimentCodegenAgentError(f"experiment codegen files exceed {MAX_CODE_BYTES} bytes")
    return files


def _safe_relative_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ExperimentCodegenAgentError(f"unsafe experiment file path: {raw_path}")
    if any(part in {"", "."} for part in path.parts):
        raise ExperimentCodegenAgentError(f"unsafe experiment file path: {raw_path}")
    return path


def _text_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ExperimentCodegenAgentError(f"experiment codegen response field {field_name} must be an array")
    texts: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ExperimentCodegenAgentError(f"experiment codegen response field {field_name} must contain strings")
        text = " ".join(item.split())
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            texts.append(text)
    return texts


def _command_list(value: Any) -> list[str]:
    command = _text_list(value, "run_command")
    if not command:
        raise ExperimentCodegenAgentError("experiment codegen response field run_command must not be empty")
    return command

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class PipelineContext:
    run_id: uuid.UUID
    idea_id: uuid.UUID | None
    parameters: dict[str, Any]


@dataclass(frozen=True)
class StageResult:
    message: str
    payload: dict[str, Any] = field(default_factory=dict)


class PipelineStage(Protocol):
    name: str

    async def run(self, context: PipelineContext) -> StageResult:
        ...


@dataclass(frozen=True)
class MarkerStage:
    name: str
    label: str

    async def run(self, context: PipelineContext) -> StageResult:
        return StageResult(
            message=f"{self.label} stage completed",
            payload={
                "stage": self.name,
                "status": "completed",
                "parameters": context.parameters,
            },
        )


DEFAULT_PIPELINE_STAGES: tuple[PipelineStage, ...] = (
    MarkerStage(name="discovery", label="Discovery"),
    MarkerStage(name="idea_confirmation", label="Idea confirmation"),
    MarkerStage(name="experiment", label="Experiment"),
    MarkerStage(name="writing", label="Writing"),
    MarkerStage(name="revision", label="Revision"),
)

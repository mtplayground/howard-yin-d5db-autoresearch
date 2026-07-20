from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Run, RunEvent, RunStatus
from app.orchestrator.stages import DEFAULT_PIPELINE_STAGES, PipelineContext, PipelineStage, StageResult
from app.services.progress_events import ProgressEvent, ProgressEventType, progress_event_bus


class PipelineExecutionError(RuntimeError):
    pass


class PipelineOrchestrator:
    def __init__(self, db: Session, stages: Sequence[PipelineStage] = DEFAULT_PIPELINE_STAGES) -> None:
        if not stages:
            raise ValueError("PipelineOrchestrator requires at least one stage")
        self._db = db
        self._stages = tuple(stages)
        self._stage_names = [stage.name for stage in self._stages]

    async def create_run(
        self,
        *,
        trigger_source: str,
        idea_id: uuid.UUID | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> Run:
        run = Run(
            idea_id=idea_id,
            status=RunStatus.QUEUED.value,
            trigger_source=trigger_source,
            current_stage=self._stages[0].name,
            parameters=parameters or {},
        )
        self._db.add(run)
        self._db.flush()
        await self._record_event(
            run,
            event_type="run_created",
            stage=run.current_stage,
            message="Pipeline run created",
            payload={"trigger_source": trigger_source},
        )
        return run

    async def run_to_completion(self, run_id: uuid.UUID) -> Run:
        run = self._db.get(Run, run_id)
        if run is None:
            raise PipelineExecutionError(f"Run {run_id} was not found")
        if run.status == RunStatus.CANCELED.value:
            raise PipelineExecutionError(f"Run {run_id} is canceled")
        if run.status == RunStatus.SUCCEEDED.value:
            return run

        try:
            start_index = self._resume_index(run)
            run.status = RunStatus.RUNNING.value
            run.started_at = run.started_at or datetime.now(UTC)
            run.completed_at = None
            run.error_message = None
            self._db.flush()
            await self._record_event(
                run,
                event_type="run_started",
                stage=run.current_stage,
                message="Pipeline run started",
                payload={"resume_from_index": start_index},
            )

            context = PipelineContext(
                run_id=run.id,
                idea_id=run.idea_id,
                parameters=dict(run.parameters or {}),
            )
            for stage in self._stages[start_index:]:
                run.current_stage = stage.name
                self._db.flush()
                await self._record_event(run, event_type="stage_started", stage=stage.name, message=f"Stage {stage.name} started")
                result = await stage.run(context)
                await self._complete_stage(run, stage.name, result)

            run.status = RunStatus.SUCCEEDED.value
            run.current_stage = None
            run.completed_at = datetime.now(UTC)
            self._db.flush()
            await self._record_event(run, event_type="run_completed", stage=None, message="Pipeline run completed")
            return run
        except Exception as exc:
            self._db.rollback()
            failed_run = self._db.get(Run, run_id)
            if failed_run is None:
                raise PipelineExecutionError(f"Run {run_id} disappeared while handling failure") from exc
            failed_run.status = RunStatus.FAILED.value
            failed_run.error_message = str(exc)
            failed_run.completed_at = datetime.now(UTC)
            self._db.flush()
            await self._record_event(
                failed_run,
                event_type="run_failed",
                stage=failed_run.current_stage,
                message="Pipeline run failed",
                payload={"error": str(exc)},
                sse_event_type="log",
            )
            raise PipelineExecutionError(str(exc)) from exc

    def _resume_index(self, run: Run) -> int:
        if not run.current_stage:
            return 0
        try:
            return self._stage_names.index(run.current_stage)
        except ValueError as exc:
            raise PipelineExecutionError(f"Run {run.id} references unknown stage {run.current_stage}") from exc

    async def _complete_stage(self, run: Run, stage_name: str, result: StageResult) -> None:
        next_index = self._stage_names.index(stage_name) + 1
        run.current_stage = self._stage_names[next_index] if next_index < len(self._stage_names) else None
        self._db.flush()
        await self._record_event(
            run,
            event_type="stage_completed",
            stage=stage_name,
            message=result.message,
            payload=result.payload,
        )

    async def _record_event(
        self,
        run: Run,
        *,
        event_type: str,
        stage: str | None,
        message: str,
        payload: dict[str, Any] | None = None,
        sse_event_type: ProgressEventType = "progress",
    ) -> RunEvent:
        event = RunEvent(
            run_id=run.id,
            event_type=event_type,
            stage=stage,
            message=message,
            payload=payload or {},
        )
        self._db.add(event)
        self._db.flush()
        self._db.commit()
        self._db.refresh(run)
        self._db.refresh(event)
        await progress_event_bus.publish(
            ProgressEvent(
                event_type=sse_event_type,
                run_id=str(run.id),
                stage=stage,
                message=message,
                payload={"event_type": event_type, **(payload or {})},
            )
        )
        return event

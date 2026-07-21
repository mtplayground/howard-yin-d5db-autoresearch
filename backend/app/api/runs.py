from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Artifact, Experiment, Run, RunEvent
from app.db.session import get_db_session
from app.models.runs import MonitorExperimentResponse, RunCreateRequest, RunEventResponse, RunMonitorResponse, RunResponse
from app.orchestrator import PipelineExecutionError, PipelineOrchestrator

router = APIRouter(prefix="/api/runs", tags=["runs"])
DatabaseDependency = Annotated[Session, Depends(get_db_session)]


@router.post("", response_model=RunResponse, status_code=201)
async def create_run(payload: RunCreateRequest, db: DatabaseDependency) -> Run:
    orchestrator = PipelineOrchestrator(db)
    run = await orchestrator.create_run(
        trigger_source=payload.trigger_source,
        idea_id=payload.idea_id,
        parameters=payload.parameters,
    )
    if payload.execute:
        try:
            run = await orchestrator.run_to_completion(run.id)
        except PipelineExecutionError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    return run


@router.post("/{run_id}/resume", response_model=RunResponse)
async def resume_run(run_id: uuid.UUID, db: DatabaseDependency) -> Run:
    orchestrator = PipelineOrchestrator(db)
    try:
        return await orchestrator.run_to_completion(run_id)
    except PipelineExecutionError as exc:
        status_code = 404 if "was not found" in str(exc) else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get("/{run_id}", response_model=RunResponse)
async def read_run(run_id: uuid.UUID, db: DatabaseDependency) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/{run_id}/events", response_model=list[RunEventResponse])
async def read_run_events(
    run_id: uuid.UUID,
    db: DatabaseDependency,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[RunEvent]:
    if db.get(Run, run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    statement = select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.created_at.asc()).limit(limit)
    return list(db.scalars(statement))


@router.get("/{run_id}/monitor", response_model=RunMonitorResponse)
async def read_run_monitor(run_id: uuid.UUID, db: DatabaseDependency) -> RunMonitorResponse:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    events = list(
        db.scalars(
            select(RunEvent)
            .where(RunEvent.run_id == run_id)
            .order_by(RunEvent.created_at.asc())
            .limit(200)
        )
    )
    experiments = list(
        db.scalars(
            select(Experiment)
            .where(Experiment.run_id == run_id)
            .order_by(Experiment.created_at.asc())
        )
    )
    artifacts_by_experiment: dict[uuid.UUID, list[Artifact]] = {experiment.id: [] for experiment in experiments}
    if experiments:
        artifact_rows = list(
            db.scalars(
                select(Artifact)
                .where(Artifact.experiment_id.in_([experiment.id for experiment in experiments]))
                .order_by(Artifact.created_at.asc())
            )
        )
        for artifact in artifact_rows:
            if artifact.experiment_id in artifacts_by_experiment:
                artifacts_by_experiment[artifact.experiment_id].append(artifact)

    experiment_payloads = [
        MonitorExperimentResponse.model_validate(
            {
                "id": experiment.id,
                "idea_id": experiment.idea_id,
                "title": experiment.title,
                "hypothesis": experiment.hypothesis,
                "status": experiment.status,
                "metrics": experiment.metrics,
                "result_summary": experiment.result_summary,
                "error_message": experiment.error_message,
                "started_at": experiment.started_at,
                "completed_at": experiment.completed_at,
                "artifacts": artifacts_by_experiment.get(experiment.id, []),
            }
        )
        for experiment in experiments
    ]
    return RunMonitorResponse(
        run=RunResponse.model_validate(run),
        events=[RunEventResponse.model_validate(event) for event in events],
        experiments=experiment_payloads,
    )

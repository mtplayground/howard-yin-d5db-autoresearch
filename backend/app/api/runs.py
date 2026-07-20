from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Run, RunEvent
from app.db.session import get_db_session
from app.models.runs import RunCreateRequest, RunEventResponse, RunResponse
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

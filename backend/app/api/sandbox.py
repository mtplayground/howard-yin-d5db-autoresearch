from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import SandboxJob
from app.db.session import get_db_session
from app.models.sandbox import SandboxJobResponse, SandboxSubmitRequest
from app.services.sandbox import SandboxError, SandboxOrchestrator

router = APIRouter(prefix="/api/sandbox", tags=["sandbox"])
DatabaseDependency = Annotated[Session, Depends(get_db_session)]


@router.post("/jobs", response_model=SandboxJobResponse, status_code=201)
async def submit_sandbox_job(payload: SandboxSubmitRequest, db: DatabaseDependency) -> SandboxJob:
    orchestrator = SandboxOrchestrator(db)
    try:
        return await orchestrator.submit(payload)
    except SandboxError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/execute", response_model=SandboxJobResponse)
async def execute_sandbox_job(job_id: uuid.UUID, db: DatabaseDependency) -> SandboxJob:
    orchestrator = SandboxOrchestrator(db)
    try:
        return await orchestrator.execute(job_id)
    except SandboxError as exc:
        status_code = 404 if "was not found" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get("/jobs/{job_id}", response_model=SandboxJobResponse)
async def read_sandbox_job(job_id: uuid.UUID, db: DatabaseDependency) -> SandboxJob:
    job = db.get(SandboxJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Sandbox job not found")
    return job

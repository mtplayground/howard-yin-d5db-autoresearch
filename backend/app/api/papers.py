from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.revision import PaperRevisionAgentError, revise_paper_with_configured_model
from app.agents.writing import PaperWritingAgentError, write_paper_with_configured_model
from app.core.config import Settings, get_settings
from app.db.models import Artifact, Paper
from app.db.session import get_db_session
from app.models.papers import PaperArtifactResponse, PaperGenerationResponse, PaperResponse, PaperRevisionRequest
from app.services.paper_compile import PaperCompileError, compile_paper_to_pdf

router = APIRouter(prefix="/api/papers", tags=["papers"])
DatabaseDependency = Annotated[Session, Depends(get_db_session)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


@router.post("/runs/{run_id}", response_model=PaperGenerationResponse, status_code=201)
async def generate_paper_for_run(
    run_id: uuid.UUID,
    db: DatabaseDependency,
    settings: SettingsDependency,
) -> PaperGenerationResponse:
    try:
        paper = await write_paper_with_configured_model(db, settings, run_id)
    except PaperWritingAgentError as exc:
        status_code = 404 if "was not found" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    artifacts = list(
        db.scalars(
            select(Artifact)
            .where(Artifact.paper_id == paper.id)
            .order_by(Artifact.created_at.asc())
        )
    )
    return PaperGenerationResponse(
        paper=PaperResponse.model_validate(paper),
        artifacts=[PaperArtifactResponse.model_validate(artifact) for artifact in artifacts],
    )


@router.get("/{paper_id}", response_model=PaperResponse)
async def read_paper(paper_id: uuid.UUID, db: DatabaseDependency) -> Paper:
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    return paper


@router.post("/{paper_id}/compile", response_model=PaperGenerationResponse)
async def compile_paper(paper_id: uuid.UUID, db: DatabaseDependency) -> PaperGenerationResponse:
    try:
        paper = await compile_paper_to_pdf(db, paper_id)
    except PaperCompileError as exc:
        status_code = 404 if "was not found" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    artifacts = list(
        db.scalars(
            select(Artifact)
            .where(Artifact.paper_id == paper.id)
            .order_by(Artifact.created_at.asc())
        )
    )
    return PaperGenerationResponse(
        paper=PaperResponse.model_validate(paper),
        artifacts=[PaperArtifactResponse.model_validate(artifact) for artifact in artifacts],
    )


@router.post("/{paper_id}/revise", response_model=PaperGenerationResponse)
async def revise_paper(
    paper_id: uuid.UUID,
    payload: PaperRevisionRequest,
    db: DatabaseDependency,
    settings: SettingsDependency,
) -> PaperGenerationResponse:
    try:
        paper = await revise_paper_with_configured_model(
            db,
            settings,
            paper_id,
            max_iterations=payload.max_iterations,
            min_quality_score=payload.min_quality_score,
        )
    except PaperRevisionAgentError as exc:
        status_code = 404 if "was not found" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    artifacts = list(
        db.scalars(
            select(Artifact)
            .where(Artifact.paper_id == paper.id)
            .order_by(Artifact.created_at.asc())
        )
    )
    return PaperGenerationResponse(
        paper=PaperResponse.model_validate(paper),
        artifacts=[PaperArtifactResponse.model_validate(artifact) for artifact in artifacts],
    )

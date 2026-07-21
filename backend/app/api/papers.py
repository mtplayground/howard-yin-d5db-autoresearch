from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.revision import PaperRevisionAgentError, revise_paper_with_configured_model
from app.agents.writing import PaperWritingAgentError, write_paper_with_configured_model
from app.core.config import Settings, get_settings
from app.db.models import Artifact, ArtifactKind, Paper
from app.db.session import get_db_session
from app.models.papers import (
    PaperArtifactResponse,
    PaperArtifactsResponse,
    PaperGenerationResponse,
    PaperListResponse,
    PaperResponse,
    PaperRevisionRequest,
)
from app.services.paper_compile import PaperCompileError, compile_paper_to_pdf
from app.services.storage import ObjectNotFoundError, StorageError, get_storage_client

router = APIRouter(prefix="/api/papers", tags=["papers"])
DatabaseDependency = Annotated[Session, Depends(get_db_session)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]
DispositionQuery = Annotated[str, Query(pattern="^(attachment|inline)$")]


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


@router.get("", response_model=PaperListResponse)
async def list_papers(
    db: DatabaseDependency,
    run_id: uuid.UUID | None = None,
    idea_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaperListResponse:
    filters = []
    if run_id is not None:
        filters.append(Paper.run_id == run_id)
    if idea_id is not None:
        filters.append(Paper.idea_id == idea_id)
    if status:
        filters.append(Paper.status == status)

    total_query = select(func.count()).select_from(Paper)
    items_query = select(Paper).order_by(Paper.updated_at.desc(), Paper.created_at.desc()).limit(limit).offset(offset)
    if filters:
        total_query = total_query.where(*filters)
        items_query = items_query.where(*filters)

    total = db.scalar(total_query) or 0
    items = list(db.scalars(items_query))
    return PaperListResponse(
        items=[PaperResponse.model_validate(paper) for paper in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{paper_id}", response_model=PaperResponse)
async def read_paper(paper_id: uuid.UUID, db: DatabaseDependency) -> Paper:
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    return paper


@router.get("/{paper_id}/artifacts", response_model=PaperArtifactsResponse)
async def read_paper_artifacts(paper_id: uuid.UUID, db: DatabaseDependency) -> PaperArtifactsResponse:
    paper = _get_paper_or_404(db, paper_id)
    artifacts = _paper_artifacts(db, paper.id)
    return PaperArtifactsResponse(
        paper=PaperResponse.model_validate(paper),
        artifacts=[PaperArtifactResponse.model_validate(artifact) for artifact in artifacts],
    )


@router.get("/{paper_id}/download/pdf")
async def download_current_pdf(
    paper_id: uuid.UUID,
    db: DatabaseDependency,
    disposition: DispositionQuery = "attachment",
) -> Response:
    paper = _get_paper_or_404(db, paper_id)
    artifact = _current_pdf_artifact(db, paper)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Paper PDF not found")
    return _download_artifact_response(paper, artifact, disposition=disposition)


@router.get("/{paper_id}/artifacts/{artifact_id}/download")
async def download_paper_artifact(
    paper_id: uuid.UUID,
    artifact_id: uuid.UUID,
    db: DatabaseDependency,
    disposition: DispositionQuery = "attachment",
) -> Response:
    paper = _get_paper_or_404(db, paper_id)
    artifact = db.get(Artifact, artifact_id)
    if artifact is None or artifact.paper_id != paper.id:
        raise HTTPException(status_code=404, detail="Paper artifact not found")
    return _download_artifact_response(paper, artifact, disposition=disposition)


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


def _get_paper_or_404(db: Session, paper_id: uuid.UUID) -> Paper:
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    return paper


def _paper_artifacts(db: Session, paper_id: uuid.UUID) -> list[Artifact]:
    return list(
        db.scalars(
            select(Artifact)
            .where(Artifact.paper_id == paper_id)
            .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        )
    )


def _current_pdf_artifact(db: Session, paper: Paper) -> Artifact | None:
    if paper.pdf_storage_key:
        artifact = db.scalar(
            select(Artifact)
            .where(
                Artifact.paper_id == paper.id,
                Artifact.kind == ArtifactKind.PDF.value,
                Artifact.storage_key == paper.pdf_storage_key,
            )
            .order_by(Artifact.created_at.desc())
        )
        if artifact is not None:
            return artifact

    return db.scalar(
        select(Artifact)
        .where(Artifact.paper_id == paper.id, Artifact.kind == ArtifactKind.PDF.value)
        .order_by(Artifact.created_at.desc())
    )


def _download_artifact_response(paper: Paper, artifact: Artifact, *, disposition: str) -> Response:
    try:
        data = get_storage_client().download_bytes(artifact.storage_key)
    except ObjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Stored paper artifact not found") from exc
    except StorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    content_type = artifact.content_type or "application/octet-stream"
    filename = _download_filename(paper, artifact)
    headers = {
        "Content-Disposition": f'{disposition}; filename="{filename}"',
        "Cache-Control": "private, max-age=60",
        "Last-Modified": _http_date(artifact.created_at),
    }
    return Response(content=data, media_type=content_type, headers=headers)


def _download_filename(paper: Paper, artifact: Artifact) -> str:
    raw_name = artifact.filename or f"{paper.title}.{artifact.kind}"
    clean = "".join(character if character.isalnum() or character in {"-", "_", "."} else "-" for character in raw_name)
    clean = "-".join(part for part in clean.split("-") if part)
    return clean[:120] or "paper-artifact"


def _http_date(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")


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

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Text, asc, cast, desc, func, or_, select
from sqlalchemy.orm import Session

from app.agents.ideas import IdeaGenerationAgentError, refine_idea_with_configured_model
from app.core.config import Settings, get_settings
from app.db.models import Idea, IdeaStatus
from app.db.session import get_db_session
from app.models.ideas import IdeaConfirmResponse, IdeaListResponse, IdeaRefineRequest, IdeaRefineResponse, IdeaResponse, IdeaSort
from app.models.runs import RunResponse
from app.orchestrator import PipelineExecutionError, PipelineOrchestrator

router = APIRouter(prefix="/api/ideas", tags=["ideas"])
DatabaseDependency = Annotated[Session, Depends(get_db_session)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


@router.get("", response_model=IdeaListResponse)
async def list_ideas(
    db: DatabaseDependency,
    topic: str | None = Query(default=None, min_length=1, max_length=160),
    status: str | None = Query(default=None, min_length=1, max_length=32),
    min_score: float | None = Query(default=None, ge=0.0, le=1.0),
    sort: IdeaSort = Query(default="created_desc"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> IdeaListResponse:
    conditions = []
    if status and status != "all":
        conditions.append(Idea.status == status)
    if min_score is not None:
        conditions.append(Idea.score >= min_score)
    if topic:
        pattern = f"%{topic.strip()}%"
        conditions.append(
            or_(
                Idea.title.ilike(pattern),
                Idea.problem_statement.ilike(pattern),
                Idea.hypothesis.ilike(pattern),
                Idea.rationale.ilike(pattern),
                cast(Idea.source_context, Text).ilike(pattern),
                cast(Idea.extra, Text).ilike(pattern),
            )
        )

    total_statement = select(func.count()).select_from(Idea).where(*conditions)
    total = db.scalar(total_statement) or 0

    statement = (
        select(Idea)
        .where(*conditions)
        .order_by(*_sort_order(sort))
        .limit(limit)
        .offset(offset)
    )
    ideas = list(db.scalars(statement))
    return IdeaListResponse(items=ideas, total=total, limit=limit, offset=offset, sort=sort)


@router.get("/{idea_id}", response_model=IdeaResponse)
async def read_idea(idea_id: uuid.UUID, db: DatabaseDependency) -> Idea:
    idea = db.get(Idea, idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="Idea not found")
    return idea


@router.post("/{idea_id}/refine", response_model=IdeaRefineResponse)
async def refine_idea(
    idea_id: uuid.UUID,
    payload: IdeaRefineRequest,
    db: DatabaseDependency,
    settings: SettingsDependency,
) -> IdeaRefineResponse:
    try:
        idea, assistant_message = await refine_idea_with_configured_model(db, settings, idea_id, payload.message)
    except IdeaGenerationAgentError as exc:
        status_code = 404 if "was not found" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return IdeaRefineResponse(idea=IdeaResponse.model_validate(idea), assistant_message=assistant_message)


@router.post("/{idea_id}/confirm", response_model=IdeaConfirmResponse, status_code=201)
async def confirm_idea(idea_id: uuid.UUID, db: DatabaseDependency) -> IdeaConfirmResponse:
    idea = db.get(Idea, idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="Idea not found")

    confirmed_at = datetime.now(UTC)
    idea.status = IdeaStatus.APPROVED.value
    extra = dict(idea.extra or {})
    extra["confirmed_at"] = confirmed_at.isoformat()
    idea.extra = extra

    orchestrator = PipelineOrchestrator(db)
    try:
        run = await orchestrator.create_run(
            trigger_source="idea_confirmation",
            idea_id=idea.id,
            parameters={
                "confirmed_at": confirmed_at.isoformat(),
                "idea_title": idea.title,
                "entry_stage": "experiment",
            },
            start_stage="experiment",
        )
        run = await orchestrator.run_to_completion(run.id)
    except PipelineExecutionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    db.refresh(idea)
    return IdeaConfirmResponse(idea=IdeaResponse.model_validate(idea), run=RunResponse.model_validate(run))


def _sort_order(sort: IdeaSort) -> tuple[object, ...]:
    if sort == "created_asc":
        return (asc(Idea.created_at), asc(Idea.id))
    if sort == "score_desc":
        return (desc(Idea.score).nullslast(), desc(Idea.created_at), asc(Idea.id))
    if sort == "score_asc":
        return (asc(Idea.score).nullslast(), desc(Idea.created_at), asc(Idea.id))
    if sort == "title_asc":
        return (asc(func.lower(Idea.title)), desc(Idea.created_at), asc(Idea.id))
    return (desc(Idea.created_at), asc(Idea.id))

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Text, asc, cast, desc, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Idea
from app.db.session import get_db_session
from app.models.ideas import IdeaListResponse, IdeaSort

router = APIRouter(prefix="/api/ideas", tags=["ideas"])
DatabaseDependency = Annotated[Session, Depends(get_db_session)]


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

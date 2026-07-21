from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.runs import RunResponse

IdeaSort = Literal["created_desc", "created_asc", "score_desc", "score_asc", "title_asc"]


class IdeaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    problem_statement: str | None
    hypothesis: str | None
    status: str
    score: float | None
    rationale: str | None
    source_context: dict[str, Any]
    extra: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class IdeaListResponse(BaseModel):
    items: list[IdeaResponse]
    total: int
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    sort: IdeaSort


class IdeaRefineRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class IdeaRefineResponse(BaseModel):
    idea: IdeaResponse
    assistant_message: str


class IdeaConfirmResponse(BaseModel):
    idea: IdeaResponse
    run: RunResponse

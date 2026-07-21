from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PaperArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    storage_key: str
    filename: str | None
    content_type: str | None
    byte_size: int | None
    checksum_sha256: str | None
    extra: dict[str, Any]
    created_at: datetime


class PaperResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID | None
    idea_id: uuid.UUID | None
    experiment_id: uuid.UUID | None
    title: str
    abstract: str | None
    status: str
    latex_storage_key: str | None
    pdf_storage_key: str | None
    bibliography: dict[str, Any]
    review_notes: dict[str, Any]
    compiled_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PaperGenerationResponse(BaseModel):
    paper: PaperResponse
    artifacts: list[PaperArtifactResponse]


class PaperRevisionRequest(BaseModel):
    max_iterations: int = Field(default=3, ge=1, le=5)
    min_quality_score: float = Field(default=0.88, ge=0.0, le=1.0)

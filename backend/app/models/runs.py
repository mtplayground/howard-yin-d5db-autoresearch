from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunCreateRequest(BaseModel):
    trigger_source: str = Field(default="manual", min_length=1, max_length=64)
    idea_id: uuid.UUID | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    execute: bool = False


class RunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    idea_id: uuid.UUID | None
    status: str
    trigger_source: str
    current_stage: str | None
    parameters: dict[str, Any]
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RunEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    event_type: str
    stage: str | None
    message: str
    payload: dict[str, Any]
    created_at: datetime


class MonitorArtifactResponse(BaseModel):
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


class MonitorExperimentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    idea_id: uuid.UUID | None
    title: str
    hypothesis: str | None
    status: str
    metrics: dict[str, Any]
    result_summary: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    artifacts: list[MonitorArtifactResponse]


class RunMonitorResponse(BaseModel):
    run: RunResponse
    events: list[RunEventResponse]
    experiments: list[MonitorExperimentResponse]

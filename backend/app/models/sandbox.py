from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SandboxSubmitRequest(BaseModel):
    command: list[str] = Field(min_length=1, max_length=32)
    stdin: str | None = Field(default=None, max_length=20000)
    files: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    cpu_time_seconds: int = Field(default=10, ge=1, le=300)
    run_id: uuid.UUID | None = None
    experiment_id: uuid.UUID | None = None
    environment: dict[str, str] = Field(default_factory=dict)
    execute_immediately: bool = True

    @field_validator("command")
    @classmethod
    def command_parts_must_be_text(cls, value: list[str]) -> list[str]:
        normalized = [" ".join(part.split()) for part in value]
        if any(not part for part in normalized):
            raise ValueError("command entries must not be empty")
        return normalized

    @field_validator("environment")
    @classmethod
    def environment_values_must_be_text(cls, value: dict[str, str]) -> dict[str, str]:
        return {str(key): str(env_value) for key, env_value in value.items()}


class SandboxJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID | None
    experiment_id: uuid.UUID | None
    status: str
    command: list[str]
    stdin: str | None
    stdout: str | None
    stderr: str | None
    exit_code: int | None
    timeout_seconds: int
    cpu_time_seconds: int
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    extra: dict[str, Any]
    created_at: datetime
    updated_at: datetime

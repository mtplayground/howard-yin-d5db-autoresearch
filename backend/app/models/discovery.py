from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.services.source_connectors import SourceName


class DiscoveryRunRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str | None = Field(default=None, min_length=1, max_length=500)
    limit: int | None = Field(default=None, ge=1, le=100)
    sources: list[SourceName] | None = None

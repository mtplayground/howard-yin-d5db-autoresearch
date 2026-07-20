from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import Run
from app.db.session import SessionLocal, get_db_session
from app.models.discovery import DiscoveryRunRequest
from app.models.runs import RunResponse
from app.services.discovery import DiscoveryError, DiscoveryRunner
from app.services.source_connectors import build_source_search_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/discovery", tags=["discovery"])
DatabaseDependency = Annotated[Session, Depends(get_db_session)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


@router.post("/runs", response_model=RunResponse, status_code=202)
async def trigger_discovery(
    payload: DiscoveryRunRequest,
    db: DatabaseDependency,
    settings: SettingsDependency,
) -> Run:
    query = payload.query or settings.discovery_default_query
    limit = payload.limit or settings.discovery_default_limit
    runner = DiscoveryRunner(db, build_source_search_client(settings))
    try:
        run = await runner.create_run(
            query=query,
            limit=limit,
            trigger_source="manual_discovery",
            sources=payload.sources,
        )
    except DiscoveryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    asyncio.create_task(_execute_discovery_in_background(run.id, settings))
    return run


async def _execute_discovery_in_background(run_id: uuid.UUID, settings: Settings) -> None:
    try:
        with SessionLocal() as db:
            runner = DiscoveryRunner(db, build_source_search_client(settings))
            await runner.execute_run(run_id)
    except Exception:
        logger.exception("manual discovery run failed")

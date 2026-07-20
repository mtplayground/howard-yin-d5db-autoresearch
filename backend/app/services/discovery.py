from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Run, RunEvent, RunStatus
from app.db.session import SessionLocal
from app.services.knowledge import ingest_source_results
from app.services.progress_events import ProgressEvent, ProgressEventBus, ProgressEventType, progress_event_bus
from app.services.source_connectors import SourceName, SourceQuery, SourceSearchClient, build_source_search_client

logger = logging.getLogger(__name__)


class DiscoveryError(RuntimeError):
    pass


class DiscoveryRunner:
    def __init__(
        self,
        db: Session,
        search_client: SourceSearchClient,
        *,
        event_bus: ProgressEventBus = progress_event_bus,
    ) -> None:
        self._db = db
        self._search_client = search_client
        self._event_bus = event_bus

    async def create_run(
        self,
        *,
        query: str,
        limit: int,
        trigger_source: str,
        sources: Iterable[SourceName] | None = None,
    ) -> Run:
        normalized_query = _normalize_query(query)
        normalized_limit = _normalize_limit(limit)
        source_list = list(sources) if sources else None
        run = Run(
            status=RunStatus.QUEUED.value,
            trigger_source=trigger_source,
            current_stage="discovery",
            parameters={
                "query": normalized_query,
                "limit": normalized_limit,
                "sources": source_list,
            },
        )
        self._db.add(run)
        self._db.flush()
        await self._record_event(
            run,
            event_type="discovery_queued",
            message="Discovery run queued",
            payload={"query": normalized_query, "limit": normalized_limit, "sources": source_list},
        )
        return run

    async def run_once(
        self,
        *,
        query: str,
        limit: int,
        trigger_source: str,
        sources: Iterable[SourceName] | None = None,
    ) -> Run:
        run = await self.create_run(query=query, limit=limit, trigger_source=trigger_source, sources=sources)
        return await self.execute_run(run.id)

    async def execute_run(self, run_id: uuid.UUID) -> Run:
        run = self._db.get(Run, run_id)
        if run is None:
            raise DiscoveryError(f"Discovery run {run_id} was not found")
        parameters = dict(run.parameters or {})
        query = _normalize_query(str(parameters.get("query") or ""))
        limit = _normalize_limit(int(parameters.get("limit") or 10))
        sources = parameters.get("sources")
        selected_sources = sources if isinstance(sources, list) else None

        try:
            run.status = RunStatus.RUNNING.value
            run.current_stage = "discovery"
            run.started_at = run.started_at or datetime.now(UTC)
            run.completed_at = None
            run.error_message = None
            self._db.flush()
            await self._record_event(
                run,
                event_type="discovery_started",
                message="Discovery search started",
                payload={"query": query, "limit": limit, "sources": selected_sources},
            )

            batch = await self._search_client.search_all(SourceQuery(query=query, limit=limit), sources=selected_sources)
            for error in batch.errors:
                await self._record_event(
                    run,
                    event_type="discovery_source_error",
                    message=f"{error.source} discovery search failed",
                    payload={"source": error.source, "error": error.message},
                    sse_event_type="log",
                )

            ingest_result = ingest_source_results(self._db, batch.results)
            run = self._db.get(Run, run_id)
            if run is None:
                raise DiscoveryError(f"Discovery run {run_id} disappeared after ingest")
            run.status = RunStatus.SUCCEEDED.value
            run.current_stage = None
            run.completed_at = datetime.now(UTC)
            run.parameters = {
                **dict(run.parameters or {}),
                "result_count": len(batch.results),
                "created_count": ingest_result.created_count,
                "updated_count": ingest_result.updated_count,
                "errors": [error.__dict__ for error in batch.errors],
            }
            self._db.flush()
            await self._record_event(
                run,
                event_type="discovery_completed",
                stage=None,
                message="Discovery search completed",
                payload={
                    "result_count": len(batch.results),
                    "created_count": ingest_result.created_count,
                    "updated_count": ingest_result.updated_count,
                    "error_count": len(batch.errors),
                },
            )
            return run
        except Exception as exc:
            self._db.rollback()
            failed_run = self._db.get(Run, run_id)
            if failed_run is None:
                raise DiscoveryError(f"Discovery run {run_id} disappeared while handling failure") from exc
            failed_run.status = RunStatus.FAILED.value
            failed_run.current_stage = "discovery"
            failed_run.error_message = str(exc)
            failed_run.completed_at = datetime.now(UTC)
            self._db.flush()
            await self._record_event(
                failed_run,
                event_type="discovery_failed",
                message="Discovery search failed",
                payload={"error": str(exc)},
                sse_event_type="log",
            )
            raise DiscoveryError(str(exc)) from exc

    async def _record_event(
        self,
        run: Run,
        *,
        event_type: str,
        message: str,
        stage: str | None = "discovery",
        payload: dict[str, Any] | None = None,
        sse_event_type: ProgressEventType = "progress",
    ) -> RunEvent:
        event = RunEvent(
            run_id=run.id,
            event_type=event_type,
            stage=stage,
            message=message,
            payload=payload or {},
        )
        self._db.add(event)
        self._db.flush()
        self._db.commit()
        self._db.refresh(run)
        self._db.refresh(event)
        await self._event_bus.publish(
            ProgressEvent(
                event_type=sse_event_type,
                run_id=str(run.id),
                stage=stage,
                message=message,
                payload={"event_type": event_type, **(payload or {})},
            )
        )
        return event


async def run_discovery_once(
    *,
    settings: Settings,
    trigger_source: str,
    query: str | None = None,
    limit: int | None = None,
    sources: Iterable[SourceName] | None = None,
) -> Run:
    with SessionLocal() as db:
        runner = DiscoveryRunner(db, build_source_search_client(settings))
        return await runner.run_once(
            query=query or settings.discovery_default_query,
            limit=limit or settings.discovery_default_limit,
            trigger_source=trigger_source,
            sources=sources,
        )


def configure_discovery_scheduler(app: FastAPI, settings: Settings) -> None:
    if settings.discovery_interval_seconds <= 0:
        return

    stop_event = asyncio.Event()

    async def scheduler_loop() -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=settings.discovery_interval_seconds)
                continue
            except TimeoutError:
                pass
            try:
                await run_discovery_once(settings=settings, trigger_source="scheduled_discovery")
            except Exception:
                logger.exception("scheduled discovery run failed")

    async def start_scheduler() -> None:
        app.state.discovery_scheduler_stop_event = stop_event
        app.state.discovery_scheduler_task = asyncio.create_task(scheduler_loop())

    async def stop_scheduler() -> None:
        stop_event.set()
        task = getattr(app.state, "discovery_scheduler_task", None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app.add_event_handler("startup", start_scheduler)
    app.add_event_handler("shutdown", stop_scheduler)


def _normalize_query(query: str) -> str:
    normalized = " ".join(query.split())
    if not normalized:
        raise DiscoveryError("Discovery query must not be empty")
    return normalized


def _normalize_limit(limit: int) -> int:
    return max(1, min(limit, 100))

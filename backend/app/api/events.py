from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.services.progress_events import (
    ProgressEvent,
    ProgressSubscriber,
    event_to_sse,
    heartbeat_to_sse,
    progress_event_bus,
)

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("/stream")
async def stream_events(request: Request, run_id: str | None = None) -> StreamingResponse:
    subscriber = progress_event_bus.subscribe(run_id=run_id)
    return StreamingResponse(
        _event_stream(request, subscriber),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _event_stream(request: Request, subscriber: ProgressSubscriber) -> AsyncIterator[str]:
    connected = ProgressEvent(
        event_type="connected",
        message="SSE channel connected",
        run_id=subscriber.run_id,
    )
    yield event_to_sse(connected)

    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(subscriber.queue.get(), timeout=15)
            except TimeoutError:
                yield heartbeat_to_sse()
                continue
            yield event_to_sse(event)
    finally:
        progress_event_bus.unsubscribe(subscriber)

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

ProgressEventType = Literal["connected", "progress", "log", "artifact", "heartbeat"]


@dataclass(frozen=True)
class ProgressEvent:
    event_type: ProgressEventType
    message: str
    run_id: str | None = None
    stage: str | None = None
    artifact_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_payload(self) -> dict[str, Any]:
        return {
            "id": self.event_id,
            "type": self.event_type,
            "message": self.message,
            "run_id": self.run_id,
            "stage": self.stage,
            "artifact_id": self.artifact_id,
            "payload": self.payload,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(eq=False)
class ProgressSubscriber:
    queue: asyncio.Queue[ProgressEvent]
    run_id: str | None = None


class ProgressEventBus:
    def __init__(self, *, max_queue_size: int = 100) -> None:
        self._max_queue_size = max_queue_size
        self._subscribers: set[ProgressSubscriber] = set()

    def subscribe(self, *, run_id: str | None = None) -> ProgressSubscriber:
        subscriber = ProgressSubscriber(queue=asyncio.Queue(maxsize=self._max_queue_size), run_id=run_id)
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: ProgressSubscriber) -> None:
        self._subscribers.discard(subscriber)

    async def publish(self, event: ProgressEvent) -> None:
        for subscriber in list(self._subscribers):
            if subscriber.run_id and subscriber.run_id != event.run_id:
                continue
            self._enqueue(subscriber.queue, event)

    @staticmethod
    def _enqueue(queue: asyncio.Queue[ProgressEvent], event: ProgressEvent) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(event)


def event_to_sse(event: ProgressEvent) -> str:
    data = json.dumps(event.as_payload(), ensure_ascii=False, separators=(",", ":"))
    lines = [f"id: {event.event_id}", f"event: {event.event_type}"]
    lines.extend(f"data: {line}" for line in data.splitlines())
    return "\n".join(lines) + "\n\n"


def heartbeat_to_sse() -> str:
    return ": keep-alive\n\n"


progress_event_bus = ProgressEventBus()

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_delay_seconds: float = 0.25
    backoff_multiplier: float = 2.0
    max_delay_seconds: float = 2.0

    def normalized(self) -> RetryPolicy:
        return RetryPolicy(
            max_attempts=max(1, self.max_attempts),
            initial_delay_seconds=max(0.0, self.initial_delay_seconds),
            backoff_multiplier=max(1.0, self.backoff_multiplier),
            max_delay_seconds=max(0.0, self.max_delay_seconds),
        )


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy | None = None,
    is_retryable: Callable[[Exception], bool],
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> T:
    resolved_policy = (policy or RetryPolicy()).normalized()
    delay = min(resolved_policy.initial_delay_seconds, resolved_policy.max_delay_seconds)

    for attempt in range(1, resolved_policy.max_attempts + 1):
        try:
            return await operation()
        except Exception as exc:
            if attempt >= resolved_policy.max_attempts or not is_retryable(exc):
                raise
            if delay > 0:
                await sleep(delay)
            delay = min(delay * resolved_policy.backoff_multiplier, resolved_policy.max_delay_seconds)

    raise RuntimeError("retry loop exited without returning or raising")

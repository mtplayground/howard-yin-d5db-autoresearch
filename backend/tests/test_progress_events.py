import asyncio
import json
import unittest

from app.services.progress_events import ProgressEvent, ProgressEventBus, event_to_sse, heartbeat_to_sse


class ProgressEventsTest(unittest.TestCase):
    def test_event_to_sse_serializes_named_event(self) -> None:
        event = ProgressEvent(event_type="progress", message="stage changed", run_id="run-1", stage="discover")

        encoded = event_to_sse(event)

        self.assertIn("event: progress\n", encoded)
        self.assertIn("data: ", encoded)
        payload = json.loads(next(line.removeprefix("data: ") for line in encoded.splitlines() if line.startswith("data: ")))
        self.assertEqual(payload["run_id"], "run-1")
        self.assertEqual(payload["stage"], "discover")

    def test_heartbeat_is_sse_comment(self) -> None:
        self.assertEqual(heartbeat_to_sse(), ": keep-alive\n\n")

    def test_bus_filters_by_run_id(self) -> None:
        async def run() -> None:
            bus = ProgressEventBus()
            all_runs = bus.subscribe()
            run_one = bus.subscribe(run_id="run-1")
            run_two = bus.subscribe(run_id="run-2")

            await bus.publish(ProgressEvent(event_type="log", message="hello", run_id="run-1"))

            self.assertEqual((await asyncio.wait_for(all_runs.queue.get(), timeout=0.1)).message, "hello")
            self.assertEqual((await asyncio.wait_for(run_one.queue.get(), timeout=0.1)).message, "hello")
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(run_two.queue.get(), timeout=0.01)

            bus.unsubscribe(all_runs)
            bus.unsubscribe(run_one)
            bus.unsubscribe(run_two)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

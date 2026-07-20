import asyncio
import uuid
import unittest

from app.db.models import KnowledgeItem, Run, RunEvent, RunStatus
from app.db.session import SessionLocal
from app.services.discovery import DiscoveryError, DiscoveryRunner
from app.services.progress_events import ProgressEventBus
from app.services.source_connectors import SourceQuery, SourceResult, SourceSearchBatch


class FakeSearchClient:
    def __init__(self, batch: SourceSearchBatch | None = None, error: Exception | None = None) -> None:
        self.batch = batch or SourceSearchBatch(results=[], errors=[])
        self.error = error
        self.queries: list[SourceQuery] = []

    async def search_all(self, query: SourceQuery, *, sources: object = None) -> SourceSearchBatch:
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.batch


class DiscoveryRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = SessionLocal()
        self.run_ids: list[uuid.UUID] = []
        self.knowledge_keys: list[str] = []

    def tearDown(self) -> None:
        self.db.rollback()
        for run_id in self.run_ids:
            self.db.query(RunEvent).filter(RunEvent.run_id == run_id).delete(synchronize_session=False)
            self.db.query(Run).filter(Run.id == run_id).delete(synchronize_session=False)
        for key in self.knowledge_keys:
            self.db.query(KnowledgeItem).filter(KnowledgeItem.canonical_key == key).delete(synchronize_session=False)
        self.db.commit()
        self.db.close()

    def test_run_once_ingests_results_and_publishes_progress(self) -> None:
        async def scenario() -> None:
            event_bus = ProgressEventBus()
            subscriber = event_bus.subscribe()
            search_client = FakeSearchClient(
                SourceSearchBatch(
                    results=[
                        SourceResult(
                            source="arxiv",
                            external_id="2401.00001",
                            title="Discovery paper",
                            url="https://arxiv.org/abs/2401.00001",
                            abstract="A discovery result.",
                        )
                    ],
                    errors=[],
                )
            )
            runner = DiscoveryRunner(self.db, search_client, event_bus=event_bus)  # type: ignore[arg-type]

            run = await runner.run_once(query=" discovery topic ", limit=5, trigger_source="test_discovery")
            self.run_ids.append(run.id)
            item = self.db.query(KnowledgeItem).filter(KnowledgeItem.title == "Discovery paper").one()
            self.knowledge_keys.append(item.canonical_key)

            self.assertEqual(run.status, RunStatus.SUCCEEDED.value)
            self.assertIsNone(run.current_stage)
            self.assertEqual(run.parameters["result_count"], 1)
            self.assertEqual(run.parameters["created_count"], 1)
            events = self.db.query(RunEvent).filter(RunEvent.run_id == run.id).order_by(RunEvent.created_at.asc()).all()
            self.assertEqual(events[0].event_type, "discovery_queued")
            self.assertEqual(events[-1].event_type, "discovery_completed")
            published = []
            while not subscriber.queue.empty():
                published.append(subscriber.queue.get_nowait().message)
            self.assertIn("Discovery search started", published)
            self.assertIn("Discovery search completed", published)
            event_bus.unsubscribe(subscriber)

        asyncio.run(scenario())

    def test_search_failure_marks_run_failed(self) -> None:
        async def scenario() -> None:
            runner = DiscoveryRunner(self.db, FakeSearchClient(error=RuntimeError("provider down")))  # type: ignore[arg-type]

            run = await runner.create_run(query="topic", limit=10, trigger_source="test_discovery")
            self.run_ids.append(run.id)
            with self.assertRaises(DiscoveryError):
                await runner.execute_run(run.id)
            self.db.refresh(run)

            self.assertEqual(run.status, RunStatus.FAILED.value)
            self.assertIn("provider down", run.error_message or "")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()

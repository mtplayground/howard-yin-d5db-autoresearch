import asyncio
import uuid
import unittest
from dataclasses import dataclass

from app.db.models import Run, RunEvent, RunStatus
from app.db.session import SessionLocal
from app.orchestrator import PipelineExecutionError, PipelineOrchestrator
from app.orchestrator.stages import PipelineContext, StageResult


@dataclass(frozen=True)
class RecordingStage:
    name: str
    calls: list[str]

    async def run(self, context: PipelineContext) -> StageResult:
        self.calls.append(self.name)
        return StageResult(message=f"{self.name} done", payload={"called": self.name})


class PipelineOrchestratorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = SessionLocal()
        self.run_ids: list[uuid.UUID] = []

    def tearDown(self) -> None:
        for run_id in self.run_ids:
            self.db.query(RunEvent).filter(RunEvent.run_id == run_id).delete(synchronize_session=False)
            self.db.query(Run).filter(Run.id == run_id).delete(synchronize_session=False)
        self.db.commit()
        self.db.close()

    def test_create_and_complete_run_persists_stage_events(self) -> None:
        async def scenario() -> None:
            calls: list[str] = []
            orchestrator = PipelineOrchestrator(
                self.db,
                stages=(RecordingStage("discovery", calls), RecordingStage("experiment", calls)),
            )
            run = await orchestrator.create_run(trigger_source="test", parameters={"topic": "retrieval"})
            self.run_ids.append(run.id)

            completed = await orchestrator.run_to_completion(run.id)

            self.assertEqual(completed.status, RunStatus.SUCCEEDED.value)
            self.assertIsNone(completed.current_stage)
            self.assertEqual(calls, ["discovery", "experiment"])
            events = self.db.query(RunEvent).filter(RunEvent.run_id == run.id).order_by(RunEvent.created_at.asc()).all()
            self.assertEqual(events[0].event_type, "run_created")
            self.assertIn("stage_completed", [event.event_type for event in events])
            self.assertEqual(events[-1].event_type, "run_completed")

        asyncio.run(scenario())

    def test_resume_starts_from_persisted_current_stage(self) -> None:
        async def scenario() -> None:
            calls: list[str] = []
            run = Run(
                status=RunStatus.QUEUED.value,
                trigger_source="test",
                current_stage="experiment",
                parameters={},
            )
            self.db.add(run)
            self.db.commit()
            self.db.refresh(run)
            self.run_ids.append(run.id)
            orchestrator = PipelineOrchestrator(
                self.db,
                stages=(
                    RecordingStage("discovery", calls),
                    RecordingStage("experiment", calls),
                    RecordingStage("writing", calls),
                ),
            )

            completed = await orchestrator.run_to_completion(run.id)

            self.assertEqual(completed.status, RunStatus.SUCCEEDED.value)
            self.assertEqual(calls, ["experiment", "writing"])

        asyncio.run(scenario())

    def test_unknown_resume_stage_marks_run_failed(self) -> None:
        async def scenario() -> None:
            run = Run(
                status=RunStatus.QUEUED.value,
                trigger_source="test",
                current_stage="missing",
                parameters={},
            )
            self.db.add(run)
            self.db.commit()
            self.db.refresh(run)
            self.run_ids.append(run.id)
            orchestrator = PipelineOrchestrator(self.db, stages=(RecordingStage("discovery", []),))

            with self.assertRaises(PipelineExecutionError):
                await orchestrator.run_to_completion(run.id)
            self.db.refresh(run)
            events = self.db.query(RunEvent).filter(RunEvent.run_id == run.id).order_by(RunEvent.created_at.asc()).all()
            self.assertEqual(run.status, RunStatus.FAILED.value)
            self.assertEqual(events[-1].event_type, "run_failed")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()

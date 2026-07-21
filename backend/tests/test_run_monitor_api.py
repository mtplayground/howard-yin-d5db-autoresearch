import os
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.config import get_settings
from app.db.models import Artifact, ArtifactKind, Experiment, ExperimentStatus, Idea, IdeaStatus, Run, RunEvent, RunStatus
from app.db.session import SessionLocal
from app.main import create_app


class RunMonitorApiTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ACCESS_PASSPHRASE"] = "correct-passphrase"
        get_settings.cache_clear()
        self.db = SessionLocal()
        self.idea_ids: list[object] = []
        self.run_ids: list[object] = []
        self.experiment_ids: list[object] = []
        self.artifact_ids: list[object] = []
        self.client = TestClient(create_app())
        self.client.post("/api/auth/login", json={"passphrase": "correct-passphrase"})

    def tearDown(self) -> None:
        self.db.rollback()
        if self.artifact_ids:
            self.db.execute(delete(Artifact).where(Artifact.id.in_(self.artifact_ids)))
        if self.experiment_ids:
            self.db.execute(delete(Experiment).where(Experiment.id.in_(self.experiment_ids)))
        if self.run_ids:
            self.db.execute(delete(RunEvent).where(RunEvent.run_id.in_(self.run_ids)))
            self.db.execute(delete(Run).where(Run.id.in_(self.run_ids)))
        if self.idea_ids:
            self.db.execute(delete(Idea).where(Idea.id.in_(self.idea_ids)))
        self.db.commit()
        self.db.close()
        get_settings.cache_clear()

    def test_read_run_monitor_returns_experiments_events_and_artifacts(self) -> None:
        idea = Idea(
            title="Monitor idea",
            problem_statement="Problem.",
            hypothesis="Hypothesis.",
            status=IdeaStatus.APPROVED.value,
            source_context={},
            extra={},
        )
        self.db.add(idea)
        self.db.flush()
        run = Run(
            idea_id=idea.id,
            status=RunStatus.RUNNING.value,
            trigger_source="test",
            current_stage="experiment",
            parameters={},
        )
        self.db.add(run)
        self.db.flush()
        event = RunEvent(
            run_id=run.id,
            event_type="stage_started",
            stage="experiment",
            message="Experiment started",
            payload={"status": "running"},
        )
        experiment = Experiment(
            run_id=run.id,
            idea_id=idea.id,
            title="Monitor experiment",
            hypothesis="Hypothesis.",
            status=ExperimentStatus.SUCCEEDED.value,
            metrics={"last_run": {"numeric_results": {"accuracy": 0.91}, "logs": {"stdout": "ok", "stderr": ""}}},
            result_summary="Experiment completed with metrics: accuracy=0.91",
        )
        self.db.add_all([event, experiment])
        self.db.flush()
        artifact = Artifact(
            run_id=run.id,
            idea_id=idea.id,
            experiment_id=experiment.id,
            kind=ArtifactKind.FIGURE.value,
            storage_key="workspace/artifacts/chart.png",
            filename="chart.png",
            content_type="image/png",
            byte_size=7,
            checksum_sha256="abc",
            extra={},
        )
        self.db.add(artifact)
        self.db.commit()
        self.idea_ids.append(idea.id)
        self.run_ids.append(run.id)
        self.experiment_ids.append(experiment.id)
        self.artifact_ids.append(artifact.id)

        response = self.client.get(f"/api/runs/{run.id}/monitor")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["run"]["id"], str(run.id))
        self.assertEqual(body["run"]["current_stage"], "experiment")
        self.assertEqual(body["events"][0]["event_type"], "stage_started")
        self.assertEqual(body["experiments"][0]["title"], "Monitor experiment")
        self.assertEqual(body["experiments"][0]["metrics"]["last_run"]["numeric_results"]["accuracy"], 0.91)
        self.assertEqual(body["experiments"][0]["artifacts"][0]["filename"], "chart.png")


if __name__ == "__main__":
    unittest.main()

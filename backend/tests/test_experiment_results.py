import base64
import unittest
import uuid
from io import BytesIO

from botocore.exceptions import ClientError
from sqlalchemy import delete

from app.db.models import Artifact, ArtifactKind, Experiment, ExperimentStatus, Idea, IdeaStatus, Run, RunStatus
from app.db.session import SessionLocal
from app.services.experiment_results import ExperimentResultPersistenceError, persist_experiment_results
from app.services.storage import ObjectStorageClient, StorageConfig


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.puts: list[dict[str, object]] = []

    def put_object(self, **request: object) -> None:
        self.puts.append(request)
        self.objects[(str(request["Bucket"]), str(request["Key"]))] = bytes(request["Body"])  # type: ignore[arg-type]

    def get_object(self, **request: object) -> dict[str, BytesIO]:
        key = (str(request["Bucket"]), str(request["Key"]))
        if key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": BytesIO(self.objects[key])}

    def head_object(self, **request: object) -> None:
        key = (str(request["Bucket"]), str(request["Key"]))
        if key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")


class ExperimentResultPersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = SessionLocal()
        self.idea_ids: list[object] = []
        self.run_ids: list[object] = []
        self.experiment_ids: list[object] = []
        self.artifact_ids: list[object] = []
        self.fake_s3 = FakeS3Client()
        self.storage = ObjectStorageClient(
            StorageConfig(
                bucket="bucket",
                prefix="workspace/artifacts",
                region="auto",
                endpoint_url="https://objects.example",
                access_key_id="access",
                secret_access_key="secret",
            ),
            s3_client=self.fake_s3,  # type: ignore[arg-type]
        )

    def tearDown(self) -> None:
        self.db.rollback()
        if self.artifact_ids:
            self.db.execute(delete(Artifact).where(Artifact.id.in_(self.artifact_ids)))
        if self.experiment_ids:
            self.db.execute(delete(Experiment).where(Experiment.id.in_(self.experiment_ids)))
        if self.run_ids:
            self.db.execute(delete(Run).where(Run.id.in_(self.run_ids)))
        if self.idea_ids:
            self.db.execute(delete(Idea).where(Idea.id.in_(self.idea_ids)))
        self.db.commit()
        self.db.close()

    def test_persists_results_logs_and_charts_to_storage_and_artifacts(self) -> None:
        experiment = self._create_completed_experiment()

        artifacts = persist_experiment_results(self.db, experiment.id, storage=self.storage)
        self.artifact_ids.extend(artifact.id for artifact in artifacts)

        self.assertEqual(len(artifacts), 3)
        self.assertEqual({artifact.kind for artifact in artifacts}, {ArtifactKind.RESULT.value, ArtifactKind.LOG.value, ArtifactKind.FIGURE.value})
        self.assertTrue(all(artifact.run_id == experiment.run_id for artifact in artifacts))
        self.assertTrue(all(artifact.experiment_id == experiment.id for artifact in artifacts))
        self.assertTrue(all(artifact.storage_key.startswith("workspace/artifacts/experiments/runs/") for artifact in artifacts))
        self.assertTrue(all(request["ContentLength"] == len(request["Body"]) for request in self.fake_s3.puts))

        chart = next(artifact for artifact in artifacts if artifact.kind == ArtifactKind.FIGURE.value)
        self.assertEqual(chart.filename, "chart.png")
        self.assertTrue(chart.storage_key.endswith("/files/figures/chart.png"))
        self.assertEqual(self.fake_s3.objects[("bucket", chart.storage_key)], b"PNGDATA")

        self.db.refresh(experiment)
        last_run = experiment.metrics["last_run"]
        self.assertEqual(len(last_run["persisted_artifacts"]), 3)
        self.assertNotIn("base64", last_run["captured_files"][0])
        self.assertNotIn("base64", last_run["charts"][0])

    def test_repeated_persistence_updates_existing_artifact_rows(self) -> None:
        experiment = self._create_completed_experiment()

        first = persist_experiment_results(self.db, experiment.id, storage=self.storage)
        second = persist_experiment_results(self.db, experiment.id, storage=self.storage)
        self.artifact_ids.extend(artifact.id for artifact in second)

        self.assertEqual({artifact.id for artifact in first}, {artifact.id for artifact in second})
        self.assertEqual(
            self.db.query(Artifact).filter(Artifact.experiment_id == experiment.id).count(),
            3,
        )

    def test_missing_last_run_metrics_is_rejected(self) -> None:
        experiment = self._create_completed_experiment(metrics={})

        with self.assertRaises(ExperimentResultPersistenceError):
            persist_experiment_results(self.db, experiment.id, storage=self.storage)

    def _create_completed_experiment(self, *, metrics: dict[str, object] | None = None) -> Experiment:
        idea = Idea(
            title="Persistent result idea",
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
            status=RunStatus.SUCCEEDED.value,
            trigger_source="test",
            parameters={},
        )
        self.db.add(run)
        self.db.flush()
        sandbox_job_id = str(uuid.uuid4())
        default_metrics = {
            "last_run": {
                "sandbox_job_id": sandbox_job_id,
                "status": "succeeded",
                "numeric_results": {"accuracy": 0.91},
                "logs": {"stdout": "ok", "stderr": ""},
                "captured_files": [
                    {
                        "path": "figures/chart.png",
                        "byte_size": 7,
                        "content_type": "image/png",
                        "base64": base64.b64encode(b"PNGDATA").decode("ascii"),
                    }
                ],
                "charts": [
                    {
                        "path": "figures/chart.png",
                        "byte_size": 7,
                        "content_type": "image/png",
                        "base64": base64.b64encode(b"PNGDATA").decode("ascii"),
                    }
                ],
            }
        }
        experiment = Experiment(
            run_id=run.id,
            idea_id=idea.id,
            title="Persistent result experiment",
            hypothesis="Hypothesis.",
            status=ExperimentStatus.SUCCEEDED.value,
            code_files={"experiment.py": "print('ok')"},
            dependencies=[],
            run_command=["python", "experiment.py"],
            metrics=default_metrics if metrics is None else metrics,
        )
        self.db.add(experiment)
        self.db.commit()
        self.db.refresh(idea)
        self.db.refresh(run)
        self.db.refresh(experiment)
        self.idea_ids.append(idea.id)
        self.run_ids.append(run.id)
        self.experiment_ids.append(experiment.id)
        return experiment


if __name__ == "__main__":
    unittest.main()

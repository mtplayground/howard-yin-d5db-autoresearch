import unittest
from io import BytesIO

from botocore.exceptions import ClientError
from sqlalchemy import delete

from app.db.models import Artifact, Experiment, ExperimentStatus, Idea, IdeaStatus, Paper, Run, RunStatus
from app.db.session import SessionLocal
from app.services.paper_figures import embed_figures_in_latex, persist_paper_figures
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


class PaperFiguresTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = SessionLocal()
        self.idea_ids: list[object] = []
        self.run_ids: list[object] = []
        self.experiment_ids: list[object] = []
        self.paper_ids: list[object] = []
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
        if self.paper_ids:
            self.db.execute(delete(Paper).where(Paper.id.in_(self.paper_ids)))
        if self.experiment_ids:
            self.db.execute(delete(Experiment).where(Experiment.id.in_(self.experiment_ids)))
        if self.run_ids:
            self.db.execute(delete(Run).where(Run.id.in_(self.run_ids)))
        if self.idea_ids:
            self.db.execute(delete(Idea).where(Idea.id.in_(self.idea_ids)))
        self.db.commit()
        self.db.close()

    def test_persists_metric_figure_and_embeds_it_in_results_section(self) -> None:
        experiment, paper = self._create_experiment_and_paper()

        artifacts = persist_paper_figures(self.db, experiment, paper, self.storage)
        self.artifact_ids.extend(artifact.id for artifact in artifacts)
        latex = embed_figures_in_latex(
            "\\documentclass{article}\n\\begin{document}\n\\section{Results}\nBaseline text.\n\\end{document}",
            artifacts,
        )

        self.assertEqual(len(artifacts), 1)
        artifact = artifacts[0]
        self.assertEqual(artifact.filename, "metric-summary.tex")
        self.assertTrue(artifact.storage_key.startswith("workspace/artifacts/papers/runs/"))
        self.assertEqual(self.fake_s3.puts[0]["ContentLength"], len(self.fake_s3.puts[0]["Body"]))  # type: ignore[arg-type]
        self.assertIn(b"accuracy", self.fake_s3.objects[("bucket", artifact.storage_key)])
        self.assertIn("\\input{figures/metric-summary.tex}", latex)
        self.assertLess(latex.index("\\input{figures/metric-summary.tex}"), latex.index("Baseline text."))

    def _create_experiment_and_paper(self) -> tuple[Experiment, Paper]:
        idea = Idea(
            title="Figure idea",
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
        experiment = Experiment(
            run_id=run.id,
            idea_id=idea.id,
            title="Figure experiment",
            hypothesis="Hypothesis.",
            status=ExperimentStatus.SUCCEEDED.value,
            metrics={
                "last_run": {
                    "numeric_results": {
                        "accuracy": 0.93,
                        "loss": 0.18,
                        "nested": {"f1_score": 0.87},
                    }
                }
            },
        )
        self.db.add(experiment)
        self.db.flush()
        paper = Paper(
            run_id=run.id,
            idea_id=idea.id,
            experiment_id=experiment.id,
            title="Figure paper",
            abstract="Abstract.",
            status="draft",
            bibliography={},
            review_notes={},
        )
        self.db.add(paper)
        self.db.commit()
        self.db.refresh(experiment)
        self.db.refresh(paper)
        self.idea_ids.append(idea.id)
        self.run_ids.append(run.id)
        self.experiment_ids.append(experiment.id)
        self.paper_ids.append(paper.id)
        return experiment, paper


if __name__ == "__main__":
    unittest.main()

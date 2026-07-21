import os
import unittest
from io import BytesIO
from unittest.mock import patch

from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.config import get_settings
from app.db.models import Artifact, ArtifactKind, Experiment, Idea, IdeaStatus, Paper, Run, RunStatus
from app.db.session import SessionLocal
from app.main import create_app
from app.services.storage import ObjectStorageClient, StorageConfig


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, **request: object) -> None:
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


class PapersApiTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ACCESS_PASSPHRASE"] = "correct-passphrase"
        get_settings.cache_clear()
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
        self.client = TestClient(create_app())
        self.client.post("/api/auth/login", json={"passphrase": "correct-passphrase"})

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
        get_settings.cache_clear()

    def test_generate_paper_for_run_returns_paper_and_latex_artifact(self) -> None:
        async def fake_write(db, settings, run_id):  # type: ignore[no-untyped-def]
            run = db.get(Run, run_id)
            paper = Paper(
                run_id=run.id,
                idea_id=run.idea_id,
                experiment_id=self.experiment_ids[0],
                title="Generated API Paper",
                abstract="API abstract.",
                status="draft",
                latex_storage_key="workspace/artifacts/papers/main.tex",
                bibliography={"entries": [{"key": "api2026"}]},
                review_notes={"writing": {"provider": "test"}},
            )
            db.add(paper)
            db.flush()
            artifact = Artifact(
                run_id=run.id,
                idea_id=run.idea_id,
                experiment_id=self.experiment_ids[0],
                paper_id=paper.id,
                kind=ArtifactKind.LATEX.value,
                storage_key="workspace/artifacts/papers/main.tex",
                filename="main.tex",
                content_type="application/x-tex; charset=utf-8",
                byte_size=120,
                checksum_sha256="abc",
                extra={},
            )
            db.add(artifact)
            db.commit()
            db.refresh(paper)
            self.paper_ids.append(paper.id)
            self.artifact_ids.append(artifact.id)
            return paper

        run = self._create_run()

        with patch("app.api.papers.write_paper_with_configured_model", fake_write):
            response = self.client.post(f"/api/papers/runs/{run.id}")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["paper"]["title"], "Generated API Paper")
        self.assertEqual(body["paper"]["run_id"], str(run.id))
        self.assertEqual(body["paper"]["latex_storage_key"], "workspace/artifacts/papers/main.tex")
        self.assertEqual(body["artifacts"][0]["kind"], ArtifactKind.LATEX.value)
        self.assertEqual(body["artifacts"][0]["filename"], "main.tex")

    def test_read_paper_requires_existing_paper(self) -> None:
        response = self.client.get("/api/papers/00000000-0000-0000-0000-000000000000")

        self.assertEqual(response.status_code, 404)

    def test_list_papers_filters_by_run(self) -> None:
        run = self._create_run()
        paper = Paper(
            run_id=run.id,
            idea_id=run.idea_id,
            experiment_id=self.experiment_ids[0],
            title="Listed paper",
            abstract="Abstract.",
            status="compiled",
            latex_storage_key="workspace/artifacts/papers/main.tex",
            pdf_storage_key="workspace/artifacts/papers/main.pdf",
            bibliography={},
            review_notes={},
        )
        self.db.add(paper)
        self.db.commit()
        self.db.refresh(paper)
        self.paper_ids.append(paper.id)

        response = self.client.get(f"/api/papers?run_id={run.id}")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["id"], str(paper.id))
        self.assertEqual(body["items"][0]["status"], "compiled")

    def test_compile_paper_returns_pdf_artifact(self) -> None:
        async def fake_compile(db, paper_id):  # type: ignore[no-untyped-def]
            paper = db.get(Paper, paper_id)
            paper.status = "compiled"
            paper.pdf_storage_key = "workspace/artifacts/papers/main.pdf"
            artifact = Artifact(
                run_id=paper.run_id,
                idea_id=paper.idea_id,
                experiment_id=paper.experiment_id,
                paper_id=paper.id,
                kind=ArtifactKind.PDF.value,
                storage_key="workspace/artifacts/papers/main.pdf",
                filename="main.pdf",
                content_type="application/pdf",
                byte_size=32,
                checksum_sha256="pdf",
                extra={},
            )
            db.add(artifact)
            db.commit()
            db.refresh(paper)
            self.artifact_ids.append(artifact.id)
            return paper

        run = self._create_run()
        paper = Paper(
            run_id=run.id,
            idea_id=run.idea_id,
            experiment_id=self.experiment_ids[0],
            title="Compile API paper",
            abstract="Abstract.",
            status="draft",
            latex_storage_key="workspace/artifacts/papers/main.tex",
            bibliography={},
            review_notes={},
        )
        self.db.add(paper)
        self.db.commit()
        self.db.refresh(paper)
        self.paper_ids.append(paper.id)

        with patch("app.api.papers.compile_paper_to_pdf", fake_compile):
            response = self.client.post(f"/api/papers/{paper.id}/compile")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["paper"]["status"], "compiled")
        self.assertEqual(body["paper"]["pdf_storage_key"], "workspace/artifacts/papers/main.pdf")
        self.assertEqual(body["artifacts"][0]["kind"], ArtifactKind.PDF.value)

    def test_revise_paper_returns_final_latex_artifact(self) -> None:
        async def fake_revise(db, settings, paper_id, *, max_iterations, min_quality_score):  # type: ignore[no-untyped-def]
            paper = db.get(Paper, paper_id)
            paper.status = "draft"
            paper.latex_storage_key = "workspace/artifacts/papers/main.final.tex"
            paper.pdf_storage_key = None
            paper.review_notes = {
                "revision": {
                    "status": "succeeded",
                    "max_iterations": max_iterations,
                    "min_quality_score": min_quality_score,
                }
            }
            artifact = Artifact(
                run_id=paper.run_id,
                idea_id=paper.idea_id,
                experiment_id=paper.experiment_id,
                paper_id=paper.id,
                kind=ArtifactKind.LATEX.value,
                storage_key="workspace/artifacts/papers/main.final.tex",
                filename="main.final.tex",
                content_type="application/x-tex; charset=utf-8",
                byte_size=64,
                checksum_sha256="latex",
                extra={"source": "paper_revision_agent"},
            )
            db.add(artifact)
            db.commit()
            db.refresh(paper)
            self.artifact_ids.append(artifact.id)
            return paper

        run = self._create_run()
        paper = Paper(
            run_id=run.id,
            idea_id=run.idea_id,
            experiment_id=self.experiment_ids[0],
            title="Revision API paper",
            abstract="Abstract.",
            status="draft",
            latex_storage_key="workspace/artifacts/papers/main.tex",
            bibliography={},
            review_notes={},
        )
        self.db.add(paper)
        self.db.commit()
        self.db.refresh(paper)
        self.paper_ids.append(paper.id)

        with patch("app.api.papers.revise_paper_with_configured_model", fake_revise):
            response = self.client.post(
                f"/api/papers/{paper.id}/revise",
                json={"max_iterations": 2, "min_quality_score": 0.8},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["paper"]["status"], "draft")
        self.assertEqual(body["paper"]["latex_storage_key"], "workspace/artifacts/papers/main.final.tex")
        self.assertIsNone(body["paper"]["pdf_storage_key"])
        self.assertEqual(body["paper"]["review_notes"]["revision"]["max_iterations"], 2)
        self.assertEqual(body["paper"]["review_notes"]["revision"]["min_quality_score"], 0.8)
        self.assertEqual(body["artifacts"][0]["kind"], ArtifactKind.LATEX.value)
        self.assertEqual(body["artifacts"][0]["filename"], "main.final.tex")

    def test_download_current_pdf_uses_storage_and_attachment_headers(self) -> None:
        run = self._create_run()
        paper = Paper(
            run_id=run.id,
            idea_id=run.idea_id,
            experiment_id=self.experiment_ids[0],
            title="Download API paper",
            abstract="Abstract.",
            status="compiled",
            latex_storage_key="workspace/artifacts/papers/main.tex",
            pdf_storage_key="workspace/artifacts/papers/main.pdf",
            bibliography={},
            review_notes={},
        )
        self.db.add(paper)
        self.db.flush()
        artifact = Artifact(
            run_id=paper.run_id,
            idea_id=paper.idea_id,
            experiment_id=paper.experiment_id,
            paper_id=paper.id,
            kind=ArtifactKind.PDF.value,
            storage_key="workspace/artifacts/papers/main.pdf",
            filename="Download API paper.pdf",
            content_type="application/pdf",
            byte_size=18,
            checksum_sha256="pdf",
            extra={"source": "latex_compile"},
        )
        self.db.add(artifact)
        self.db.commit()
        self.db.refresh(paper)
        self.paper_ids.append(paper.id)
        self.artifact_ids.append(artifact.id)
        self.fake_s3.objects[("bucket", "workspace/artifacts/papers/main.pdf")] = b"%PDF-1.4\npaper\n"

        with patch("app.api.papers.get_storage_client", return_value=self.storage):
            response = self.client.get(f"/api/papers/{paper.id}/download/pdf")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"%PDF-1.4\npaper\n")
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertIn("Download-API-paper.pdf", response.headers["content-disposition"])

    def test_download_paper_artifact_supports_inline_history_preview(self) -> None:
        run = self._create_run()
        paper = Paper(
            run_id=run.id,
            idea_id=run.idea_id,
            experiment_id=self.experiment_ids[0],
            title="History API paper",
            abstract="Abstract.",
            status="compiled",
            latex_storage_key="workspace/artifacts/papers/main.tex",
            pdf_storage_key="workspace/artifacts/papers/main.pdf",
            bibliography={},
            review_notes={},
        )
        self.db.add(paper)
        self.db.flush()
        artifact = Artifact(
            run_id=paper.run_id,
            idea_id=paper.idea_id,
            experiment_id=paper.experiment_id,
            paper_id=paper.id,
            kind=ArtifactKind.LATEX.value,
            storage_key="workspace/artifacts/papers/main.final.tex",
            filename="main.final.tex",
            content_type="application/x-tex; charset=utf-8",
            byte_size=19,
            checksum_sha256="tex",
            extra={"source": "paper_revision_agent"},
        )
        self.db.add(artifact)
        self.db.commit()
        self.db.refresh(paper)
        self.paper_ids.append(paper.id)
        self.artifact_ids.append(artifact.id)
        self.fake_s3.objects[("bucket", "workspace/artifacts/papers/main.final.tex")] = b"\\documentclass{article}"

        with patch("app.api.papers.get_storage_client", return_value=self.storage):
            response = self.client.get(f"/api/papers/{paper.id}/artifacts/{artifact.id}/download?disposition=inline")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"\\documentclass{article}")
        self.assertIn("inline", response.headers["content-disposition"])
        self.assertIn("main.final.tex", response.headers["content-disposition"])

    def _create_run(self) -> Run:
        idea = Idea(
            title="Paper API idea",
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
            title="API experiment",
            hypothesis="Hypothesis.",
            status="succeeded",
            metrics={},
        )
        self.db.add(experiment)
        self.db.commit()
        self.db.refresh(idea)
        self.db.refresh(run)
        self.db.refresh(experiment)
        self.idea_ids.append(idea.id)
        self.run_ids.append(run.id)
        self.experiment_ids.append(experiment.id)
        return run


if __name__ == "__main__":
    unittest.main()

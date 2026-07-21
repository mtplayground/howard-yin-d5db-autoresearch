import asyncio
import base64
import unittest
from dataclasses import dataclass
from io import BytesIO

from botocore.exceptions import ClientError
from sqlalchemy import delete

from app.db.models import Artifact, ArtifactKind, Experiment, Idea, IdeaStatus, Paper, PaperStatus, Run, RunStatus, SandboxJobStatus
from app.db.session import SessionLocal
from app.models.sandbox import SandboxSubmitRequest
from app.services.paper_compile import PaperCompileError, compile_paper_to_pdf
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


@dataclass
class FakeSandboxJob:
    id: str
    status: str
    stdout: str
    stderr: str
    exit_code: int
    error_message: str | None
    extra: dict[str, object]


class FakeSandbox:
    def __init__(self, job: FakeSandboxJob) -> None:
        self.job = job
        self.requests: list[SandboxSubmitRequest] = []

    async def submit(self, payload: SandboxSubmitRequest) -> FakeSandboxJob:
        self.requests.append(payload)
        return self.job


class PaperCompileTest(unittest.TestCase):
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

    def test_compiles_paper_and_persists_pdf_artifact(self) -> None:
        async def scenario() -> None:
            paper = self._create_paper()
            pdf_bytes = b"%PDF-1.4\ncompiled pdf\n"
            sandbox = FakeSandbox(
                FakeSandboxJob(
                    id="sandbox-pdf",
                    status=SandboxJobStatus.SUCCEEDED.value,
                    stdout="compiled",
                    stderr="",
                    exit_code=0,
                    error_message=None,
                    extra={
                        "captured_files": [
                            {
                                "path": "main.pdf",
                                "byte_size": len(pdf_bytes),
                                "content_type": "application/pdf",
                                "base64": base64.b64encode(pdf_bytes).decode("ascii"),
                            }
                        ]
                    },
                )
            )

            compiled = await compile_paper_to_pdf(self.db, paper.id, storage=self.storage, sandbox=sandbox)
            self.paper_ids.append(compiled.id)
            artifacts = self.db.query(Artifact).filter(Artifact.paper_id == paper.id).all()
            self.artifact_ids.extend(artifact.id for artifact in artifacts)

            self.assertEqual(compiled.status, PaperStatus.COMPILED.value)
            self.assertIsNotNone(compiled.compiled_at)
            self.assertTrue(compiled.pdf_storage_key.startswith("workspace/artifacts/papers/runs/"))
            pdf_artifact = next(artifact for artifact in artifacts if artifact.kind == ArtifactKind.PDF.value)
            self.assertEqual(pdf_artifact.filename, "main.pdf")
            self.assertEqual(pdf_artifact.byte_size, len(pdf_bytes))
            self.assertEqual(self.fake_s3.objects[("bucket", compiled.pdf_storage_key)], pdf_bytes)
            self.assertEqual(self.fake_s3.puts[-1]["ContentLength"], len(pdf_bytes))
            self.assertIn("main.tex", sandbox.requests[0].files)
            self.assertIn("figures/metric-summary.tex", sandbox.requests[0].files)
            self.assertEqual(compiled.review_notes["compile"]["status"], "succeeded")

        asyncio.run(scenario())

    def test_failed_sandbox_marks_paper_failed(self) -> None:
        async def scenario() -> None:
            paper = self._create_paper()
            sandbox = FakeSandbox(
                FakeSandboxJob(
                    id="sandbox-failed",
                    status=SandboxJobStatus.FAILED.value,
                    stdout="",
                    stderr="missing compiler",
                    exit_code=127,
                    error_message="process exited with code 127",
                    extra={},
                )
            )

            with self.assertRaises(PaperCompileError):
                await compile_paper_to_pdf(self.db, paper.id, storage=self.storage, sandbox=sandbox)

            self.db.refresh(paper)
            self.assertEqual(paper.status, PaperStatus.FAILED.value)
            self.assertEqual(paper.review_notes["compile"]["status"], "failed")

        asyncio.run(scenario())

    def _create_paper(self) -> Paper:
        idea = Idea(
            title="Compile idea",
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
            title="Compile experiment",
            hypothesis="Hypothesis.",
            status="succeeded",
            metrics={},
        )
        self.db.add(experiment)
        self.db.flush()
        paper = Paper(
            run_id=run.id,
            idea_id=idea.id,
            experiment_id=experiment.id,
            title="Compile paper",
            abstract="Abstract.",
            status=PaperStatus.DRAFT.value,
            latex_storage_key="workspace/artifacts/papers/main.tex",
            bibliography={},
            review_notes={},
        )
        self.db.add(paper)
        self.db.flush()
        figure = Artifact(
            run_id=run.id,
            idea_id=idea.id,
            experiment_id=experiment.id,
            paper_id=paper.id,
            kind=ArtifactKind.FIGURE.value,
            storage_key="workspace/artifacts/papers/figures/metric-summary.tex",
            filename="metric-summary.tex",
            content_type="application/x-tex; charset=utf-8",
            byte_size=10,
            checksum_sha256="abc",
            extra={"input_path": "figures/metric-summary.tex"},
        )
        self.db.add(figure)
        self.db.commit()
        self.db.refresh(paper)
        self.fake_s3.objects[("bucket", "workspace/artifacts/papers/main.tex")] = b"\\documentclass{article}\\begin{document}Hi\\end{document}"
        self.fake_s3.objects[("bucket", "workspace/artifacts/papers/figures/metric-summary.tex")] = b"figure body"
        self.idea_ids.append(idea.id)
        self.run_ids.append(run.id)
        self.experiment_ids.append(experiment.id)
        self.artifact_ids.append(figure.id)
        return paper


if __name__ == "__main__":
    unittest.main()

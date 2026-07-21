import asyncio
import json
import unittest
from io import BytesIO

from botocore.exceptions import ClientError
from sqlalchemy import delete

from app.agents.revision import PaperRevisionAgent, revise_and_persist_paper
from app.db.models import Artifact, ArtifactKind, Experiment, Idea, IdeaStatus, Paper, PaperStatus, Run, RunStatus
from app.db.session import SessionLocal
from app.services.model_adapter import ModelRequest, ModelResponse
from app.services.storage import ObjectStorageClient, StorageConfig


class FakeModelAdapter:
    def __init__(self, contents: list[str]) -> None:
        self.contents = contents
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            content=self.contents[len(self.requests) - 1],
            model="revision-model",
            provider="test-provider",
            usage={"total_tokens": 111 + len(self.requests)},
        )


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


class PaperRevisionAgentTest(unittest.TestCase):
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

    def test_revises_until_model_stop_and_persists_final_latex(self) -> None:
        async def scenario() -> None:
            paper = self._create_paper()
            adapter = FakeModelAdapter(
                [
                    json.dumps(
                        {
                            "critique": "The evidence chain is too vague.",
                            "changes": ["Clarified the experiment evidence."],
                            "quality_score": 0.72,
                            "stop": False,
                            "latex_source": _valid_latex("round one evidence"),
                        }
                    ),
                    json.dumps(
                        {
                            "critique": "No material issues remain.",
                            "changes": ["Tightened limitations and conclusion."],
                            "quality_score": 0.93,
                            "stop": True,
                            "latex_source": _valid_latex("final evidence-bound result"),
                        }
                    ),
                ]
            )

            revised = await revise_and_persist_paper(
                self.db,
                paper.id,
                PaperRevisionAgent(adapter),
                storage=self.storage,
                max_iterations=3,
                min_quality_score=0.9,
            )
            self.paper_ids.append(revised.id)
            artifacts = self.db.query(Artifact).filter(Artifact.paper_id == paper.id).all()
            self.artifact_ids.extend(artifact.id for artifact in artifacts)

            self.assertEqual(revised.status, PaperStatus.DRAFT.value)
            self.assertIsNone(revised.pdf_storage_key)
            self.assertIsNone(revised.compiled_at)
            self.assertTrue(revised.latex_storage_key.startswith("workspace/artifacts/papers/runs/"))
            self.assertTrue(revised.latex_storage_key.endswith("/main.final.tex"))
            self.assertEqual(self.fake_s3.puts[-1]["ContentLength"], len(self.fake_s3.puts[-1]["Body"]))  # type: ignore[arg-type]
            self.assertIn(b"final evidence-bound result", self.fake_s3.objects[("bucket", revised.latex_storage_key)])

            latex_artifact = next(artifact for artifact in artifacts if artifact.kind == ArtifactKind.LATEX.value)
            self.assertEqual(latex_artifact.filename, "main.final.tex")
            self.assertEqual(latex_artifact.storage_key, revised.latex_storage_key)
            self.assertEqual(latex_artifact.extra["source"], "paper_revision_agent")

            revision_notes = revised.review_notes["revision"]
            self.assertEqual(revision_notes["status"], "succeeded")
            self.assertEqual(revision_notes["provider"], "test-provider")
            self.assertEqual(revision_notes["model"], "revision-model")
            self.assertEqual(revision_notes["stopped_reason"], "model_stop")
            self.assertEqual(revision_notes["final_quality_score"], 0.93)
            self.assertEqual(len(revision_notes["iterations"]), 2)
            self.assertEqual(len(adapter.requests), 2)
            prompt = next(message.content for message in adapter.requests[0].messages if message.role == "user")
            self.assertIn(str(paper.id), prompt)
            self.assertIn("Prior review notes", prompt)

        asyncio.run(scenario())

    def test_stops_when_quality_threshold_is_met(self) -> None:
        async def scenario() -> None:
            paper = self._create_paper()
            adapter = FakeModelAdapter(
                [
                    json.dumps(
                        {
                            "critique": "The draft is acceptable after one pass.",
                            "changes": ["Resolved presentation issues."],
                            "quality_score": 0.91,
                            "stop": False,
                            "latex_source": _valid_latex("single pass revision"),
                        }
                    )
                ]
            )

            revised = await revise_and_persist_paper(
                self.db,
                paper.id,
                PaperRevisionAgent(adapter),
                storage=self.storage,
                max_iterations=3,
                min_quality_score=0.9,
            )
            self.paper_ids.append(revised.id)
            artifacts = self.db.query(Artifact).filter(Artifact.paper_id == paper.id).all()
            self.artifact_ids.extend(artifact.id for artifact in artifacts)

            self.assertEqual(len(adapter.requests), 1)
            self.assertEqual(revised.review_notes["revision"]["stopped_reason"], "quality_threshold")
            self.assertIn(b"single pass revision", self.fake_s3.objects[("bucket", revised.latex_storage_key)])

        asyncio.run(scenario())

    def _create_paper(self) -> Paper:
        idea = Idea(
            title="Revision idea",
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
            title="Revision experiment",
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
            title="Revision paper",
            abstract="Abstract.",
            status=PaperStatus.COMPILED.value,
            latex_storage_key="workspace/artifacts/papers/main.tex",
            pdf_storage_key="workspace/artifacts/papers/main.pdf",
            bibliography={"entries": [{"key": "paper2026"}]},
            review_notes={"writing": {"status": "succeeded"}},
        )
        self.db.add(paper)
        self.db.commit()
        self.db.refresh(paper)
        self.fake_s3.objects[("bucket", "workspace/artifacts/papers/main.tex")] = _valid_latex("original draft").encode("utf-8")
        self.idea_ids.append(idea.id)
        self.run_ids.append(run.id)
        self.experiment_ids.append(experiment.id)
        return paper


def _valid_latex(marker: str) -> str:
    return rf"""
\documentclass{{article}}
\usepackage{{url}}
\title{{Revision Paper}}
\author{{AutoResearch}}
\begin{{document}}
\maketitle
\begin{{abstract}}
This draft is revised through an automatic critical review loop.
\end{{abstract}}
\section{{Introduction}}
The paper reports {marker} with evidence.
\section{{Related Work}}
Prior work motivates the setup~\cite{{paper2026}}.
\section{{Method}}
We revise claims against experimental outputs.
\section{{Results}}
The result discussion is grounded in observed metrics.
\section{{Limitations}}
The evaluation remains bounded by the available run.
\section{{Conclusion}}
The final draft is internally consistent.
\begin{{thebibliography}}{{9}}
\bibitem{{paper2026}} Paper. \url{{https://example.test/paper}}
\end{{thebibliography}}
\end{{document}}
""".strip()


if __name__ == "__main__":
    unittest.main()

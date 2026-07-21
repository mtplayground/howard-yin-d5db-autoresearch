import asyncio
import json
import unittest
import uuid
from io import BytesIO

from botocore.exceptions import ClientError
from sqlalchemy import delete

from app.agents.writing import PaperWritingAgent, PaperWritingAgentError, write_and_persist_paper
from app.db.models import Artifact, ArtifactKind, Experiment, ExperimentStatus, Idea, IdeaStatus, KnowledgeItem, Paper, Run, RunStatus
from app.db.session import SessionLocal
from app.services.model_adapter import ModelRequest, ModelResponse
from app.services.storage import ObjectStorageClient, StorageConfig


class FakeModelAdapter:
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            content=self.content,
            model="paper-model",
            provider="test-provider",
            usage={"total_tokens": 321},
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


class PaperWritingAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = SessionLocal()
        self.idea_ids: list[object] = []
        self.run_ids: list[object] = []
        self.experiment_ids: list[object] = []
        self.knowledge_item_ids: list[object] = []
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
        if self.knowledge_item_ids:
            self.db.execute(delete(KnowledgeItem).where(KnowledgeItem.id.in_(self.knowledge_item_ids)))
        self.db.commit()
        self.db.close()

    def test_writes_latex_paper_and_persists_storage_artifact(self) -> None:
        async def scenario() -> None:
            idea, run, experiment = self._create_ready_run()
            adapter = FakeModelAdapter(
                json.dumps(
                    {
                        "title": "Source-Aware Smoke Validation",
                        "abstract": "We test whether source-aware smoke checks catch provenance failures.",
                        "latex_source": _valid_latex_source(),
                        "bibliography_entries": [
                            {"key": "retrieval2026", "title": "Retrieval Evaluation", "url": "https://example.test/paper"}
                        ],
                        "section_outline": ["Introduction", "Related Work", "Method", "Experiments", "Results", "Limitations", "Conclusion"],
                        "limitations": ["Small synthetic benchmark."],
                    }
                )
            )

            paper = await write_and_persist_paper(
                self.db,
                run.id,
                PaperWritingAgent(adapter),
                storage=self.storage,
            )
            self.paper_ids.append(paper.id)
            artifacts = self.db.query(Artifact).filter(Artifact.paper_id == paper.id).all()
            self.artifact_ids.extend(artifact.id for artifact in artifacts)

            self.assertEqual(paper.run_id, run.id)
            self.assertEqual(paper.idea_id, idea.id)
            self.assertEqual(paper.experiment_id, experiment.id)
            self.assertEqual(paper.title, "Source-Aware Smoke Validation")
            self.assertEqual(paper.abstract, "We test whether source-aware smoke checks catch provenance failures.")
            self.assertEqual(paper.review_notes["writing"]["provider"], "test-provider")
            self.assertEqual(paper.bibliography["entries"][0]["key"], "retrieval2026")
            self.assertTrue(paper.latex_storage_key.startswith("workspace/artifacts/papers/runs/"))
            self.assertEqual(len(artifacts), 1)
            self.assertEqual(artifacts[0].kind, ArtifactKind.LATEX.value)
            self.assertEqual(artifacts[0].filename, "main.tex")
            self.assertEqual(self.fake_s3.puts[0]["ContentLength"], len(self.fake_s3.puts[0]["Body"]))  # type: ignore[arg-type]
            self.assertIn(b"\\section{Introduction}", self.fake_s3.objects[("bucket", paper.latex_storage_key)])

            prompt = next(message.content for message in adapter.requests[0].messages if message.role == "user")
            self.assertIn("Retrieval Evaluation", prompt)
            self.assertIn("accuracy", prompt)
            self.assertIn("chart.png", prompt)

        asyncio.run(scenario())

    def test_rejects_run_without_succeeded_experiment_without_calling_model(self) -> None:
        async def scenario() -> None:
            idea = Idea(
                title="No experiment idea",
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
            self.db.commit()
            self.idea_ids.append(idea.id)
            self.run_ids.append(run.id)
            adapter = FakeModelAdapter("{}")

            with self.assertRaises(PaperWritingAgentError):
                await write_and_persist_paper(self.db, run.id, PaperWritingAgent(adapter), storage=self.storage)

            self.assertEqual(adapter.requests, [])

        asyncio.run(scenario())

    def _create_ready_run(self) -> tuple[Idea, Run, Experiment]:
        knowledge = KnowledgeItem(
            canonical_key=f"paper-writing-{uuid.uuid4()}",
            source="semantic_scholar",
            source_id=str(uuid.uuid4()),
            title="Retrieval Evaluation",
            abstract="Prior work on retrieval evaluation.",
            url="https://example.test/paper",
            code_repository_url="https://github.com/example/retrieval-eval",
            authors=["A. Researcher"],
            summary="Retrieval evaluation needs provenance-aware checks.",
            methods=["retrieval benchmark"],
            contributions=["source-aware evaluation"],
            reusable_points=["provenance labels"],
            source_metadata={},
        )
        self.db.add(knowledge)
        self.db.flush()
        idea = Idea(
            title="Approved writing idea",
            problem_statement="Benchmarks miss provenance failures.",
            hypothesis="Source-aware smoke checks catch provenance failures.",
            status=IdeaStatus.APPROVED.value,
            score=0.91,
            rationale="This turns related work into an automated validation path.",
            source_context={"knowledge_item_ids": [str(knowledge.id)]},
            extra={"feasibility": "CPU-only smoke benchmark.", "reusable_points": ["provenance labels"]},
        )
        self.db.add(idea)
        self.db.flush()
        run = Run(
            idea_id=idea.id,
            status=RunStatus.SUCCEEDED.value,
            trigger_source="test",
            current_stage="writing",
            parameters={},
        )
        self.db.add(run)
        self.db.flush()
        experiment = Experiment(
            run_id=run.id,
            idea_id=idea.id,
            title="Source-aware smoke experiment",
            hypothesis="Source-aware smoke checks catch failures.",
            status=ExperimentStatus.SUCCEEDED.value,
            code_files={"experiment.py": "print('ok')"},
            dependencies=[],
            run_command=["python", "experiment.py"],
            metrics={"last_run": {"numeric_results": {"accuracy": 0.93}, "logs": {"stdout": "ok", "stderr": ""}}},
            result_summary="accuracy=0.93 on the smoke benchmark",
        )
        self.db.add(experiment)
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
        self.db.refresh(idea)
        self.db.refresh(run)
        self.db.refresh(experiment)
        self.knowledge_item_ids.append(knowledge.id)
        self.idea_ids.append(idea.id)
        self.run_ids.append(run.id)
        self.experiment_ids.append(experiment.id)
        self.artifact_ids.append(artifact.id)
        return idea, run, experiment


def _valid_latex_source() -> str:
    return r"""
\documentclass{article}
\usepackage{graphicx}
\title{Source-Aware Smoke Validation}
\author{AutoResearch}
\begin{document}
\maketitle
\begin{abstract}
We test whether source-aware smoke checks catch provenance failures.
\end{abstract}
\section{Introduction}
Automated research systems need evidence-bound experiment reports.
\section{Related Work}
Retrieval evaluation motivates provenance-aware checks~\cite{retrieval2026}.
\section{Method}
We construct a deterministic smoke benchmark.
\section{Experiments}
The experiment runs with a CPU-only Python command.
\section{Results}
The observed accuracy is 0.93.
\section{Limitations}
The benchmark is intentionally small.
\section{Conclusion}
Source-aware smoke checks are a practical validation layer.
\begin{thebibliography}{9}
\bibitem{retrieval2026} Retrieval Evaluation. \url{https://example.test/paper}
\end{thebibliography}
\end{document}
""".strip()


if __name__ == "__main__":
    unittest.main()

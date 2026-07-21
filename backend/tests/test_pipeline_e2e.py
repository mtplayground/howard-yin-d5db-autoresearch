import asyncio
import base64
import json
import unittest
import uuid
from dataclasses import dataclass
from io import BytesIO

from botocore.exceptions import ClientError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.agents.experiment_codegen import ExperimentCodegenAgent, generate_and_persist_experiment_code
from app.agents.revision import PaperRevisionAgent, revise_and_persist_paper
from app.agents.writing import PaperWritingAgent, write_and_persist_paper
from app.db.models import (
    Artifact,
    ArtifactKind,
    Experiment,
    ExperimentStatus,
    Idea,
    IdeaStatus,
    KnowledgeItem,
    Paper,
    PaperStatus,
    Run,
    RunEvent,
    RunStatus,
    SandboxJobStatus,
)
from app.db.session import SessionLocal
from app.models.sandbox import SandboxSubmitRequest
from app.orchestrator import PipelineOrchestrator
from app.orchestrator.stages import PipelineContext, StageResult
from app.services.experiment_results import persist_experiment_results
from app.services.experiment_runner import ExperimentRunner
from app.services.model_adapter import ModelRequest, ModelResponse
from app.services.paper_compile import compile_paper_to_pdf
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


class QueueModelAdapter:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        index = len(self.requests) - 1
        return ModelResponse(
            content=json.dumps(self.responses[index]),
            model=f"e2e-model-{index + 1}",
            provider="test-provider",
            usage={"total_tokens": 100 + index},
        )


@dataclass(frozen=True)
class FakeSandboxJob:
    id: uuid.UUID
    status: str
    stdout: str
    stderr: str
    exit_code: int
    error_message: str | None
    extra: dict[str, object]


class QueueSandbox:
    def __init__(self, jobs: list[FakeSandboxJob]) -> None:
        self.jobs = jobs
        self.requests: list[SandboxSubmitRequest] = []

    async def submit(self, payload: SandboxSubmitRequest) -> FakeSandboxJob:
        self.requests.append(payload)
        return self.jobs[len(self.requests) - 1]


@dataclass(frozen=True)
class DiscoveryStage:
    name: str
    db: Session

    async def run(self, context: PipelineContext) -> StageResult:
        idea = self.db.get(Idea, context.idea_id)
        knowledge = KnowledgeItem(
            canonical_key=f"e2e-knowledge-{uuid.uuid4()}",
            source="semantic_scholar",
            source_id=str(uuid.uuid4()),
            title="Source-Aware Experiment Evaluation",
            abstract="Prior work on source-aware experiment checks.",
            url="https://example.test/source-aware",
            authors=["A. Researcher"],
            summary="Evidence-bound experiment reports require traceable metrics and artifacts.",
            methods=["source-aware evaluation"],
            contributions=["metric traceability"],
            reusable_points=["deterministic smoke checks"],
            source_metadata={},
        )
        self.db.add(knowledge)
        self.db.flush()
        idea.source_context = {
            **dict(idea.source_context or {}),
            "knowledge_item_ids": [str(knowledge.id)],
            "related_work": [knowledge.title],
        }
        self.db.commit()
        return StageResult(message="Discovery linked structured knowledge", payload={"knowledge_item_id": str(knowledge.id)})


@dataclass(frozen=True)
class ConfirmIdeaStage:
    name: str
    db: Session

    async def run(self, context: PipelineContext) -> StageResult:
        idea = self.db.get(Idea, context.idea_id)
        idea.status = IdeaStatus.APPROVED.value
        self.db.commit()
        return StageResult(message="Idea confirmed", payload={"idea_id": str(idea.id), "status": idea.status})


@dataclass(frozen=True)
class GenerateExperimentStage:
    name: str
    db: Session
    adapter: QueueModelAdapter

    async def run(self, context: PipelineContext) -> StageResult:
        experiment = await generate_and_persist_experiment_code(
            self.db,
            context.idea_id,
            ExperimentCodegenAgent(self.adapter),
            run_id=context.run_id,
        )
        return StageResult(message="Experiment code generated", payload={"experiment_id": str(experiment.id)})


@dataclass(frozen=True)
class RunExperimentStage:
    name: str
    db: Session
    sandbox: QueueSandbox
    storage: ObjectStorageClient

    async def run(self, context: PipelineContext) -> StageResult:
        experiment = self.db.scalar(select(Experiment).where(Experiment.run_id == context.run_id).order_by(Experiment.created_at.desc()))
        completed = await ExperimentRunner(self.db, sandbox=self.sandbox).run(experiment.id, timeout_seconds=20, cpu_time_seconds=10)
        artifacts = persist_experiment_results(self.db, completed.id, storage=self.storage)
        return StageResult(
            message="Experiment executed and results persisted",
            payload={"experiment_id": str(completed.id), "artifact_ids": [str(artifact.id) for artifact in artifacts]},
        )


@dataclass(frozen=True)
class WritePaperStage:
    name: str
    db: Session
    adapter: QueueModelAdapter
    storage: ObjectStorageClient

    async def run(self, context: PipelineContext) -> StageResult:
        paper = await write_and_persist_paper(self.db, context.run_id, PaperWritingAgent(self.adapter), storage=self.storage)
        return StageResult(message="Paper draft generated", payload={"paper_id": str(paper.id), "latex_storage_key": paper.latex_storage_key})


@dataclass(frozen=True)
class RevisePaperStage:
    name: str
    db: Session
    adapter: QueueModelAdapter
    storage: ObjectStorageClient

    async def run(self, context: PipelineContext) -> StageResult:
        paper = self.db.scalar(select(Paper).where(Paper.run_id == context.run_id).order_by(Paper.created_at.desc()))
        revised = await revise_and_persist_paper(
            self.db,
            paper.id,
            PaperRevisionAgent(self.adapter),
            storage=self.storage,
            max_iterations=2,
            min_quality_score=0.9,
        )
        return StageResult(message="Paper revised", payload={"paper_id": str(revised.id), "quality_score": revised.review_notes["revision"]["final_quality_score"]})


@dataclass(frozen=True)
class CompilePaperStage:
    name: str
    db: Session
    sandbox: QueueSandbox
    storage: ObjectStorageClient

    async def run(self, context: PipelineContext) -> StageResult:
        paper = self.db.scalar(select(Paper).where(Paper.run_id == context.run_id).order_by(Paper.created_at.desc()))
        compiled = await compile_paper_to_pdf(self.db, paper.id, storage=self.storage, sandbox=self.sandbox, timeout_seconds=20, cpu_time_seconds=10)
        return StageResult(message="Paper PDF compiled", payload={"paper_id": str(compiled.id), "pdf_storage_key": compiled.pdf_storage_key})


class EndToEndPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = SessionLocal()
        self.idea_ids: list[object] = []
        self.run_ids: list[object] = []
        self.experiment_ids: list[object] = []
        self.paper_ids: list[object] = []
        self.artifact_ids: list[object] = []
        self.knowledge_item_ids: list[object] = []
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
            self.db.execute(delete(RunEvent).where(RunEvent.run_id.in_(self.run_ids)))
            self.db.execute(delete(Run).where(Run.id.in_(self.run_ids)))
        if self.idea_ids:
            self.db.execute(delete(Idea).where(Idea.id.in_(self.idea_ids)))
        if self.knowledge_item_ids:
            self.db.execute(delete(KnowledgeItem).where(KnowledgeItem.id.in_(self.knowledge_item_ids)))
        self.db.commit()
        self.db.close()

    def test_confirmed_idea_flows_to_experiment_results_revised_paper_and_pdf(self) -> None:
        async def scenario() -> None:
            idea = self._create_candidate_idea()
            model = QueueModelAdapter(
                [
                    {
                        "title": "Source-Aware Smoke Experiment",
                        "hypothesis": "A deterministic metric trace validates the idea.",
                        "files": {
                            "experiment.py": "\n".join(
                                [
                                    "import json",
                                    "print('RESULT_JSON:' + json.dumps({'accuracy': 0.94, 'loss': 0.08}))",
                                    "print('METRIC stability=0.97')",
                                ]
                            )
                        },
                        "dependencies": [],
                        "run_command": ["python", "experiment.py"],
                        "validation_notes": ["CPU-only deterministic smoke path."],
                    },
                    {
                        "title": "Source-Aware Smoke Validation",
                        "abstract": "We validate source-aware metric capture across the automated research pipeline.",
                        "latex_source": _paper_latex("draft evidence"),
                        "bibliography_entries": [
                            {"key": "sourceaware2026", "title": "Source-Aware Experiment Evaluation", "url": "https://example.test/source-aware"}
                        ],
                        "section_outline": ["Introduction", "Related Work", "Method", "Experiments", "Results", "Limitations", "Conclusion"],
                        "limitations": ["Synthetic smoke experiment."],
                    },
                    {
                        "critique": "The paper is evidence-bound and internally consistent.",
                        "changes": ["Clarified the metric handoff and artifact persistence."],
                        "quality_score": 0.94,
                        "stop": True,
                        "latex_source": _paper_latex("final revised evidence"),
                    },
                ]
            )
            sandbox = QueueSandbox(
                [
                    FakeSandboxJob(
                        id=uuid.uuid4(),
                        status=SandboxJobStatus.SUCCEEDED.value,
                        stdout="RESULT_JSON: {\"accuracy\": 0.94, \"loss\": 0.08}\nMETRIC stability=0.97\n",
                        stderr="",
                        exit_code=0,
                        error_message=None,
                        extra={
                            "captured_files": [
                                {
                                    "path": "figures/chart.png",
                                    "byte_size": 7,
                                    "content_type": "image/png",
                                    "base64": base64.b64encode(b"PNGDATA").decode("ascii"),
                                }
                            ]
                        },
                    ),
                    FakeSandboxJob(
                        id=uuid.uuid4(),
                        status=SandboxJobStatus.SUCCEEDED.value,
                        stdout="compiled main.tex",
                        stderr="",
                        exit_code=0,
                        error_message=None,
                        extra={
                            "captured_files": [
                                {
                                    "path": "main.pdf",
                                    "byte_size": len(b"%PDF-1.4\nE2E\n"),
                                    "content_type": "application/pdf",
                                    "base64": base64.b64encode(b"%PDF-1.4\nE2E\n").decode("ascii"),
                                }
                            ]
                        },
                    ),
                ]
            )
            orchestrator = PipelineOrchestrator(
                self.db,
                stages=(
                    DiscoveryStage("discovery", self.db),
                    ConfirmIdeaStage("idea_confirmation", self.db),
                    GenerateExperimentStage("experiment_codegen", self.db, model),
                    RunExperimentStage("experiment_run", self.db, sandbox, self.storage),
                    WritePaperStage("writing", self.db, model, self.storage),
                    RevisePaperStage("revision", self.db, model, self.storage),
                    CompilePaperStage("compile", self.db, sandbox, self.storage),
                ),
            )
            run = await orchestrator.create_run(trigger_source="e2e", idea_id=idea.id, parameters={"topic": "source-aware"})
            self.run_ids.append(run.id)

            completed = await orchestrator.run_to_completion(run.id)

            self.assertEqual(completed.status, RunStatus.SUCCEEDED.value)
            self.assertIsNone(completed.current_stage)
            self.db.refresh(idea)
            self.assertEqual(idea.status, IdeaStatus.APPROVED.value)

            knowledge_items = self.db.scalars(select(KnowledgeItem).where(KnowledgeItem.id.in_([uuid.UUID(value) for value in idea.source_context["knowledge_item_ids"]]))).all()
            self.knowledge_item_ids.extend(item.id for item in knowledge_items)
            self.assertEqual(knowledge_items[0].title, "Source-Aware Experiment Evaluation")

            experiments = self.db.scalars(select(Experiment).where(Experiment.run_id == run.id)).all()
            self.experiment_ids.extend(experiment.id for experiment in experiments)
            self.assertEqual(len(experiments), 1)
            experiment = experiments[0]
            self.assertEqual(experiment.status, ExperimentStatus.SUCCEEDED.value)
            self.assertEqual(experiment.metrics["last_run"]["numeric_results"]["accuracy"], 0.94)
            self.assertIn("accuracy=0.94", experiment.result_summary or "")

            papers = self.db.scalars(select(Paper).where(Paper.run_id == run.id)).all()
            self.paper_ids.extend(paper.id for paper in papers)
            self.assertEqual(len(papers), 1)
            paper = papers[0]
            self.assertEqual(paper.status, PaperStatus.COMPILED.value)
            self.assertTrue(paper.latex_storage_key.endswith("/main.final.tex"))
            self.assertTrue(paper.pdf_storage_key.endswith("/main.pdf"))
            self.assertEqual(paper.review_notes["revision"]["final_quality_score"], 0.94)
            self.assertEqual(paper.review_notes["compile"]["status"], "succeeded")

            artifacts = self.db.scalars(select(Artifact).where(Artifact.run_id == run.id)).all()
            self.artifact_ids.extend(artifact.id for artifact in artifacts)
            kinds = {artifact.kind for artifact in artifacts}
            self.assertTrue({ArtifactKind.RESULT.value, ArtifactKind.LOG.value, ArtifactKind.FIGURE.value, ArtifactKind.LATEX.value, ArtifactKind.PDF.value}.issubset(kinds))
            self.assertTrue(all(artifact.storage_key.startswith("workspace/artifacts/") for artifact in artifacts))
            self.assertTrue(all(request["ContentLength"] == len(request["Body"]) for request in self.fake_s3.puts))  # type: ignore[arg-type]
            self.assertEqual(self.fake_s3.objects[("bucket", paper.pdf_storage_key)], b"%PDF-1.4\nE2E\n")

            events = self.db.scalars(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.created_at.asc())).all()
            stage_completed = [event.stage for event in events if event.event_type == "stage_completed"]
            self.assertEqual(
                stage_completed,
                ["discovery", "idea_confirmation", "experiment_codegen", "experiment_run", "writing", "revision", "compile"],
            )
            self.assertEqual(len(model.requests), 3)
            self.assertEqual(len(sandbox.requests), 2)
            self.assertIn("experiment.py", sandbox.requests[0].files)
            self.assertIn("main.tex", sandbox.requests[1].files)
            self.assertIn("figures/metric-summary.tex", sandbox.requests[1].files)

        asyncio.run(scenario())

    def _create_candidate_idea(self) -> Idea:
        idea = Idea(
            title="Source-aware smoke checks",
            problem_statement="Automated research runs need traceable evidence between idea, experiment, and paper.",
            hypothesis="Persisted metrics and artifacts make generated papers auditable.",
            status=IdeaStatus.CANDIDATE.value,
            score=0.89,
            rationale="A deterministic smoke pipeline catches handoff failures.",
            source_context={},
            extra={"feasibility": "CPU-only synthetic metric run.", "reusable_points": ["metric traceability"]},
        )
        self.db.add(idea)
        self.db.commit()
        self.db.refresh(idea)
        self.idea_ids.append(idea.id)
        return idea


def _paper_latex(marker: str) -> str:
    return rf"""
\documentclass{{article}}
\usepackage{{url}}
\title{{Source-Aware Smoke Validation}}
\author{{AutoResearch}}
\begin{{document}}
\maketitle
\begin{{abstract}}
We validate source-aware metric capture across the automated research pipeline.
\end{{abstract}}
\section{{Introduction}}
The pipeline links idea confirmation, experiments, and writing with {marker}.
\section{{Related Work}}
Source-aware evaluation motivates traceable checks~\cite{{sourceaware2026}}.
\section{{Method}}
The method runs a deterministic CPU-only experiment.
\section{{Experiments}}
The experiment emits numeric metrics and a chart artifact.
\section{{Results}}
\input{{figures/metric-summary.tex}}
Accuracy reaches 0.94 while stability reaches 0.97.
\section{{Limitations}}
The e2e path uses a deterministic smoke scenario.
\section{{Conclusion}}
The final paper remains linked to persisted experiment artifacts.
\begin{{thebibliography}}{{9}}
\bibitem{{sourceaware2026}} Source-Aware Experiment Evaluation. \url{{https://example.test/source-aware}}
\end{{thebibliography}}
\end{{document}}
""".strip()


if __name__ == "__main__":
    unittest.main()

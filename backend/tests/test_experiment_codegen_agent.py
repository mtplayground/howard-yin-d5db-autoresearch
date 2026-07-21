import asyncio
import json
import unittest

from sqlalchemy import delete

from app.agents.experiment_codegen import (
    ExperimentCodegenAgent,
    ExperimentCodegenAgentError,
    generate_and_persist_experiment_code,
)
from app.db.models import Experiment, Idea, IdeaStatus
from app.db.session import SessionLocal
from app.services.model_adapter import ModelRequest, ModelResponse


class FakeModelAdapter:
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            content=self.content,
            model="codegen-model",
            provider="test-provider",
            usage={"total_tokens": 77},
        )


class ExperimentCodegenAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = SessionLocal()
        self.idea_ids: list[object] = []
        self.experiment_ids: list[object] = []

    def tearDown(self) -> None:
        self.db.rollback()
        if self.experiment_ids:
            self.db.execute(delete(Experiment).where(Experiment.id.in_(self.experiment_ids)))
        if self.idea_ids:
            self.db.execute(delete(Idea).where(Idea.id.in_(self.idea_ids)))
        self.db.commit()
        self.db.close()

    def test_generates_and_persists_experiment_code(self) -> None:
        async def scenario() -> None:
            idea = Idea(
                title="Approved reproducibility idea",
                problem_statement="Benchmarks miss provenance failures.",
                hypothesis="Source-aware smoke tests catch provenance failures.",
                status=IdeaStatus.APPROVED.value,
                score=0.88,
                rationale="The confirmed idea targets automated experiment validation.",
                source_context={"related_work": ["Retrieval augmented evaluation"]},
                extra={"feasibility": "Small CPU-only validation is enough."},
            )
            self.db.add(idea)
            self.db.commit()
            self.db.refresh(idea)
            self.idea_ids.append(idea.id)

            adapter = FakeModelAdapter(
                json.dumps(
                    {
                        "title": "Source-aware smoke validation",
                        "hypothesis": "A source-aware smoke test detects missing provenance labels.",
                        "files": {
                            "experiment.py": "print('smoke validation ok')\n",
                            "README.md": "# Experiment\nRun python experiment.py\n",
                        },
                        "dependencies": ["numpy==2.0.0", "numpy==2.0.0"],
                        "run_command": ["python", "experiment.py"],
                        "validation_notes": ["Runs without network access."],
                    }
                )
            )

            experiment = await generate_and_persist_experiment_code(
                self.db,
                idea.id,
                ExperimentCodegenAgent(adapter),
            )
            self.experiment_ids.append(experiment.id)

            self.assertEqual(experiment.idea_id, idea.id)
            self.assertEqual(experiment.title, "Source-aware smoke validation")
            self.assertEqual(experiment.hypothesis, "A source-aware smoke test detects missing provenance labels.")
            self.assertEqual(experiment.code_files["experiment.py"], "print('smoke validation ok')\n")
            self.assertEqual(experiment.dependencies, ["numpy==2.0.0"])
            self.assertEqual(experiment.run_command, ["python", "experiment.py"])
            self.assertEqual(experiment.codegen_model, "codegen-model")
            self.assertIsNotNone(experiment.code_generated_at)
            self.assertEqual(experiment.metrics["codegen"]["provider"], "test-provider")
            self.assertEqual(experiment.metrics["codegen"]["validation_notes"], ["Runs without network access."])
            prompt = next(message.content for message in adapter.requests[0].messages if message.role == "user")
            self.assertIn("Approved reproducibility idea", prompt)
            self.assertIn("run_command", prompt)

        asyncio.run(scenario())

    def test_rejects_unapproved_idea_without_calling_model(self) -> None:
        async def scenario() -> None:
            idea = Idea(
                title="Draft idea",
                problem_statement="Draft problem.",
                hypothesis="Draft hypothesis.",
                status=IdeaStatus.CANDIDATE.value,
                source_context={},
                extra={},
            )
            self.db.add(idea)
            self.db.commit()
            self.db.refresh(idea)
            self.idea_ids.append(idea.id)
            adapter = FakeModelAdapter("{}")

            with self.assertRaises(ExperimentCodegenAgentError):
                await generate_and_persist_experiment_code(self.db, idea.id, ExperimentCodegenAgent(adapter))

            self.assertEqual(adapter.requests, [])

        asyncio.run(scenario())

    def test_rejects_unsafe_file_paths_without_creating_experiment(self) -> None:
        async def scenario() -> None:
            idea = Idea(
                title="Approved unsafe path idea",
                problem_statement="Problem.",
                hypothesis="Hypothesis.",
                status=IdeaStatus.APPROVED.value,
                source_context={},
                extra={},
            )
            self.db.add(idea)
            self.db.commit()
            self.db.refresh(idea)
            self.idea_ids.append(idea.id)
            before_count = self.db.query(Experiment).count()
            adapter = FakeModelAdapter(
                json.dumps(
                    {
                        "title": "Unsafe experiment",
                        "hypothesis": "Hypothesis.",
                        "files": {"../escape.py": "print('bad')"},
                        "dependencies": [],
                        "run_command": ["python", "escape.py"],
                    }
                )
            )

            with self.assertRaises(ExperimentCodegenAgentError):
                await generate_and_persist_experiment_code(self.db, idea.id, ExperimentCodegenAgent(adapter))

            self.assertEqual(self.db.query(Experiment).count(), before_count)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()

import asyncio
import base64
import unittest

from sqlalchemy import delete

from app.db.models import Experiment, ExperimentStatus, Idea, IdeaStatus, SandboxJob
from app.db.session import SessionLocal
from app.services.experiment_runner import run_experiment_in_sandbox


class ExperimentRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = SessionLocal()
        self.idea_ids: list[object] = []
        self.experiment_ids: list[object] = []
        self.sandbox_job_ids: list[object] = []

    def tearDown(self) -> None:
        self.db.rollback()
        if self.sandbox_job_ids:
            self.db.execute(delete(SandboxJob).where(SandboxJob.id.in_(self.sandbox_job_ids)))
        if self.experiment_ids:
            self.db.execute(delete(Experiment).where(Experiment.id.in_(self.experiment_ids)))
        if self.idea_ids:
            self.db.execute(delete(Idea).where(Idea.id.in_(self.idea_ids)))
        self.db.commit()
        self.db.close()

    def test_runs_experiment_and_captures_metrics_logs_and_chart(self) -> None:
        async def scenario() -> None:
            experiment = self._create_experiment(
                code_files={
                    "experiment.py": "\n".join(
                        [
                            "import json",
                            "from pathlib import Path",
                            "print('RESULT_JSON:' + json.dumps({'accuracy': 0.91}))",
                            "print('METRIC loss=0.12')",
                            "Path('results.json').write_text(json.dumps({'nested': {'f1': 0.83}}), encoding='utf-8')",
                            "Path('chart.png').write_bytes(b'PNGDATA')",
                        ]
                    )
                },
                run_command=["python", "experiment.py"],
            )

            completed = await run_experiment_in_sandbox(
                self.db,
                experiment.id,
                timeout_seconds=20,
                cpu_time_seconds=10,
            )
            self._remember_sandbox_job(completed)

            self.assertEqual(completed.status, ExperimentStatus.SUCCEEDED.value)
            last_run = completed.metrics["last_run"]
            self.assertEqual(last_run["numeric_results"]["accuracy"], 0.91)
            self.assertEqual(last_run["numeric_results"]["loss"], 0.12)
            self.assertEqual(last_run["numeric_results"]["nested.f1"], 0.83)
            self.assertIn("RESULT_JSON", last_run["logs"]["stdout"])
            self.assertEqual(last_run["charts"][0]["path"], "chart.png")
            self.assertEqual(base64.b64decode(last_run["charts"][0]["base64"]), b"PNGDATA")
            self.assertIn("accuracy=0.91", completed.result_summary or "")

        asyncio.run(scenario())

    def test_installs_dependency_manifest_before_running_command(self) -> None:
        async def scenario() -> None:
            experiment = self._create_experiment(
                code_files={
                    "experiment.py": "from pathlib import Path\nprint(Path('requirements.txt').read_text(encoding='utf-8').strip())\n"
                },
                dependencies=["# no external dependencies"],
                run_command=["python", "experiment.py"],
            )

            completed = await run_experiment_in_sandbox(
                self.db,
                experiment.id,
                timeout_seconds=20,
                cpu_time_seconds=10,
            )
            self._remember_sandbox_job(completed)

            last_run = completed.metrics["last_run"]
            self.assertEqual(completed.status, ExperimentStatus.SUCCEEDED.value)
            self.assertIn("Installing experiment dependencies", last_run["logs"]["stdout"])
            self.assertIn("# no external dependencies", last_run["logs"]["stdout"])
            self.assertIn("experiment.py", last_run["logs"]["stdout"])

        asyncio.run(scenario())

    def test_failed_experiment_persists_stderr_and_error(self) -> None:
        async def scenario() -> None:
            experiment = self._create_experiment(
                code_files={"experiment.py": "import sys\nprint('bad path', file=sys.stderr)\nraise SystemExit(7)\n"},
                run_command=["python", "experiment.py"],
            )

            completed = await run_experiment_in_sandbox(
                self.db,
                experiment.id,
                timeout_seconds=20,
                cpu_time_seconds=10,
            )
            self._remember_sandbox_job(completed)

            self.assertEqual(completed.status, ExperimentStatus.FAILED.value)
            last_run = completed.metrics["last_run"]
            self.assertEqual(last_run["status"], "failed")
            self.assertIn("bad path", last_run["logs"]["stderr"])
            self.assertIn("process exited with code", completed.result_summary or "")

        asyncio.run(scenario())

    def _create_experiment(
        self,
        *,
        code_files: dict[str, str],
        run_command: list[str],
        dependencies: list[str] | None = None,
    ) -> Experiment:
        idea = Idea(
            title="Approved runner idea",
            problem_statement="Problem.",
            hypothesis="Hypothesis.",
            status=IdeaStatus.APPROVED.value,
            source_context={},
            extra={},
        )
        self.db.add(idea)
        self.db.flush()
        experiment = Experiment(
            idea_id=idea.id,
            title="Runner experiment",
            hypothesis="Hypothesis.",
            status=ExperimentStatus.PLANNED.value,
            code_files=code_files,
            dependencies=dependencies or [],
            run_command=run_command,
            metrics={},
        )
        self.db.add(experiment)
        self.db.commit()
        self.db.refresh(idea)
        self.db.refresh(experiment)
        self.idea_ids.append(idea.id)
        self.experiment_ids.append(experiment.id)
        return experiment

    def _remember_sandbox_job(self, experiment: Experiment) -> None:
        job_id = experiment.metrics["last_run"]["sandbox_job_id"]
        self.sandbox_job_ids.append(job_id)


if __name__ == "__main__":
    unittest.main()

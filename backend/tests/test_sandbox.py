import asyncio
import os
import sys
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.config import get_settings
from app.db.models import SandboxJob, SandboxJobStatus
from app.db.session import SessionLocal
from app.main import create_app
from app.models.sandbox import SandboxSubmitRequest
from app.services.sandbox import SandboxOrchestrator


class SandboxOrchestratorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = SessionLocal()
        self.job_ids: list[object] = []

    def tearDown(self) -> None:
        self.db.rollback()
        if self.job_ids:
            self.db.execute(delete(SandboxJob).where(SandboxJob.id.in_(self.job_ids)))
        self.db.commit()
        self.db.close()

    def test_submit_executes_command_and_recycles_workspace(self) -> None:
        async def scenario() -> None:
            orchestrator = SandboxOrchestrator(self.db)
            job = await orchestrator.submit(
                SandboxSubmitRequest(
                    command=[sys.executable, "-c", "print('sandbox ok')"],
                    timeout_seconds=10,
                    cpu_time_seconds=5,
                )
            )
            self.job_ids.append(job.id)

            self.assertEqual(job.status, SandboxJobStatus.SUCCEEDED.value)
            self.assertEqual(job.exit_code, 0)
            self.assertIn("sandbox ok", job.stdout or "")
            self.assertTrue(job.extra["recycled"])
            self.assertIsNotNone(job.started_at)
            self.assertIsNotNone(job.completed_at)

        asyncio.run(scenario())

    def test_timeout_marks_job_timed_out(self) -> None:
        async def scenario() -> None:
            orchestrator = SandboxOrchestrator(self.db)
            job = await orchestrator.submit(
                SandboxSubmitRequest(
                    command=[sys.executable, "-c", "import time; time.sleep(3)"],
                    timeout_seconds=1,
                    cpu_time_seconds=5,
                )
            )
            self.job_ids.append(job.id)

            self.assertEqual(job.status, SandboxJobStatus.TIMED_OUT.value)
            self.assertIn("timed out", job.error_message or "")
            self.assertTrue(job.extra["recycled"])

        asyncio.run(scenario())

    def test_queued_job_can_be_executed_later(self) -> None:
        async def scenario() -> None:
            orchestrator = SandboxOrchestrator(self.db)
            queued = await orchestrator.submit(
                SandboxSubmitRequest(
                    command=[sys.executable, "-c", "print(open('input.txt').read())"],
                    files={"input.txt": "hello from file"},
                    execute_immediately=False,
                )
            )
            self.job_ids.append(queued.id)
            self.assertEqual(queued.status, SandboxJobStatus.QUEUED.value)

            completed = await orchestrator.execute(queued.id)

            self.assertEqual(completed.status, SandboxJobStatus.SUCCEEDED.value)
            self.assertIn("hello from file", completed.stdout or "")

        asyncio.run(scenario())


class SandboxApiTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ACCESS_PASSPHRASE"] = "correct-passphrase"
        get_settings.cache_clear()
        self.db = SessionLocal()
        self.job_ids: list[object] = []
        self.client = TestClient(create_app())
        self.client.post("/api/auth/login", json={"passphrase": "correct-passphrase"})

    def tearDown(self) -> None:
        self.db.rollback()
        if self.job_ids:
            self.db.execute(delete(SandboxJob).where(SandboxJob.id.in_(self.job_ids)))
        self.db.commit()
        self.db.close()
        get_settings.cache_clear()

    def test_submit_and_read_sandbox_job(self) -> None:
        response = self.client.post(
            "/api/sandbox/jobs",
            json={
                "command": [sys.executable, "-c", "print('api sandbox')"],
                "timeout_seconds": 10,
                "cpu_time_seconds": 5,
            },
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.job_ids.append(body["id"])
        self.assertEqual(body["status"], SandboxJobStatus.SUCCEEDED.value)
        self.assertIn("api sandbox", body["stdout"])

        read_response = self.client.get(f"/api/sandbox/jobs/{body['id']}")
        self.assertEqual(read_response.status_code, 200)
        self.assertEqual(read_response.json()["id"], body["id"])

    def test_sandbox_api_requires_authentication(self) -> None:
        client = TestClient(create_app())
        response = client.get("/api/sandbox/jobs/00000000-0000-0000-0000-000000000000")

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()

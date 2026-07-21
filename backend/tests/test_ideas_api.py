import os
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.config import get_settings
from app.db.models import Idea, IdeaStatus
from app.db.session import SessionLocal
from app.main import create_app


class IdeasApiTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ACCESS_PASSPHRASE"] = "correct-passphrase"
        get_settings.cache_clear()
        self.db = SessionLocal()
        self.idea_ids: list[object] = []
        self.client = TestClient(create_app())
        self.client.post("/api/auth/login", json={"passphrase": "correct-passphrase"})

    def tearDown(self) -> None:
        self.db.rollback()
        if self.idea_ids:
            self.db.execute(delete(Idea).where(Idea.id.in_(self.idea_ids)))
        self.db.commit()
        self.db.close()
        get_settings.cache_clear()

    def test_list_ideas_filters_by_topic_status_and_score(self) -> None:
        strong = Idea(
            title="Graph retrieval evaluation",
            problem_statement="Retrieval experiments need graph-aware coverage.",
            hypothesis="Graph features improve evaluation quality.",
            status=IdeaStatus.CANDIDATE.value,
            score=0.87,
            rationale="Graph retrieval connects related work.",
            source_context={"related_work": ["graph retrieval"]},
            extra={"feasibility": "Can be tested with existing graph datasets."},
        )
        weak = Idea(
            title="Vision-only baseline",
            problem_statement="Baseline study.",
            hypothesis="Baseline helps comparison.",
            status=IdeaStatus.DRAFT.value,
            score=0.32,
            rationale="Different topic.",
            source_context={},
            extra={"feasibility": "Needs new assets."},
        )
        self.db.add_all([strong, weak])
        self.db.commit()
        self.db.refresh(strong)
        self.db.refresh(weak)
        self.idea_ids.extend([strong.id, weak.id])

        response = self.client.get(
            "/api/ideas",
            params={"topic": "graph", "status": "candidate", "min_score": 0.5, "sort": "score_desc"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["id"], str(strong.id))
        self.assertEqual(body["items"][0]["title"], "Graph retrieval evaluation")
        self.assertEqual(body["items"][0]["status"], IdeaStatus.CANDIDATE.value)
        self.assertAlmostEqual(body["items"][0]["score"], 0.87)
        self.assertEqual(body["sort"], "score_desc")

    def test_list_ideas_requires_authentication(self) -> None:
        client = TestClient(create_app())
        response = client.get("/api/ideas")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Authentication required")


if __name__ == "__main__":
    unittest.main()

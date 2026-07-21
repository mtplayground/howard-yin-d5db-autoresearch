import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.config import get_settings
from app.db.models import Idea, IdeaStatus, Run, RunEvent, RunStatus
from app.db.session import SessionLocal
from app.main import create_app


class IdeasApiTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ACCESS_PASSPHRASE"] = "correct-passphrase"
        get_settings.cache_clear()
        self.db = SessionLocal()
        self.idea_ids: list[object] = []
        self.run_ids: list[object] = []
        self.client = TestClient(create_app())
        self.client.post("/api/auth/login", json={"passphrase": "correct-passphrase"})

    def tearDown(self) -> None:
        self.db.rollback()
        if self.run_ids:
            self.db.execute(delete(RunEvent).where(RunEvent.run_id.in_(self.run_ids)))
            self.db.execute(delete(Run).where(Run.id.in_(self.run_ids)))
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

    def test_read_idea_detail(self) -> None:
        idea = Idea(
            title="Detailed idea",
            problem_statement="Detail problem.",
            hypothesis="Detail hypothesis.",
            status=IdeaStatus.CANDIDATE.value,
            score=0.64,
            rationale="Detail motivation.",
            source_context={"related_work": ["Paper A"]},
            extra={"feasibility": "Practical."},
        )
        self.db.add(idea)
        self.db.commit()
        self.db.refresh(idea)
        self.idea_ids.append(idea.id)

        response = self.client.get(f"/api/ideas/{idea.id}")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], str(idea.id))
        self.assertEqual(body["title"], "Detailed idea")
        self.assertEqual(body["source_context"]["related_work"], ["Paper A"])

    def test_refine_idea_updates_detail_response(self) -> None:
        async def fake_refine(db, settings, idea_id, user_message):  # type: ignore[no-untyped-def]
            idea = db.get(Idea, idea_id)
            idea.title = "Refined API idea"
            idea.rationale = f"Refined from: {user_message}"
            idea.extra = {"feasibility": "Updated through API.", "refinement_thread": []}
            db.commit()
            db.refresh(idea)
            return idea, "Refinement applied"

        idea = Idea(
            title="API idea",
            problem_statement="API problem.",
            hypothesis="API hypothesis.",
            status=IdeaStatus.CANDIDATE.value,
            score=0.45,
            rationale="API motivation.",
            source_context={},
            extra={},
        )
        self.db.add(idea)
        self.db.commit()
        self.db.refresh(idea)
        self.idea_ids.append(idea.id)

        with patch("app.api.ideas.refine_idea_with_configured_model", fake_refine):
            response = self.client.post(f"/api/ideas/{idea.id}/refine", json={"message": "Tighten feasibility"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["assistant_message"], "Refinement applied")
        self.assertEqual(body["idea"]["title"], "Refined API idea")
        self.assertEqual(body["idea"]["rationale"], "Refined from: Tighten feasibility")

    def test_confirm_idea_approves_and_starts_experiment_run(self) -> None:
        idea = Idea(
            title="Confirmable idea",
            problem_statement="Confirm problem.",
            hypothesis="Confirm hypothesis.",
            status=IdeaStatus.CANDIDATE.value,
            score=0.9,
            rationale="Confirm motivation.",
            source_context={},
            extra={},
        )
        self.db.add(idea)
        self.db.commit()
        self.db.refresh(idea)
        self.idea_ids.append(idea.id)

        response = self.client.post(f"/api/ideas/{idea.id}/confirm")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.run_ids.append(body["run"]["id"])
        self.assertEqual(body["idea"]["status"], IdeaStatus.APPROVED.value)
        self.assertEqual(body["run"]["idea_id"], str(idea.id))
        self.assertEqual(body["run"]["trigger_source"], "idea_confirmation")
        self.assertEqual(body["run"]["status"], RunStatus.SUCCEEDED.value)
        self.assertEqual(body["run"]["parameters"]["entry_stage"], "experiment")

        events = self.db.query(RunEvent).filter(RunEvent.run_id == body["run"]["id"]).order_by(RunEvent.created_at.asc()).all()
        stage_started = [event.stage for event in events if event.event_type == "stage_started"]
        self.assertEqual(stage_started, ["experiment", "writing", "revision"])


if __name__ == "__main__":
    unittest.main()

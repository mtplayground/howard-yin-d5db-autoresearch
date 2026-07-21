import asyncio
import json
import unittest

from sqlalchemy import delete

from app.agents.ideas import (
    IdeaGenerationAgent,
    IdeaGenerationAgentError,
    IdeaRefinementAgent,
    generate_and_persist_ideas,
    refine_and_update_idea,
)
from app.db.models import Idea, IdeaStatus, KnowledgeItem
from app.db.session import SessionLocal
from app.services.model_adapter import ModelRequest, ModelResponse


class FakeModelAdapter:
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(content=self.content, model="idea-model", provider="test-provider", usage={"total_tokens": 42})


class IdeaGenerationAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = SessionLocal()
        self.item_ids: list[object] = []
        self.idea_ids: list[object] = []

    def tearDown(self) -> None:
        self.db.rollback()
        if self.idea_ids:
            self.db.execute(delete(Idea).where(Idea.id.in_(self.idea_ids)))
        if self.item_ids:
            self.db.execute(delete(KnowledgeItem).where(KnowledgeItem.id.in_(self.item_ids)))
        self.db.commit()
        self.db.close()

    def test_generates_and_persists_candidate_ideas(self) -> None:
        async def scenario() -> None:
            item = KnowledgeItem(
                canonical_key="idea-generation-test-1",
                source="arxiv",
                source_id="2402.00001",
                title="Retrieval augmented evaluation",
                abstract="A retrieval augmented evaluation method.",
                url="https://arxiv.org/abs/2402.00001",
                code_repository_url="https://github.com/org/rae",
                authors=["Grace Hopper"],
                summary="Retrieval improves automated evaluator coverage.",
                methods=["query expansion", "rubric scoring"],
                contributions=["coverage metric"],
                reusable_points=["Use source-aware rubrics", "Use source-aware rubrics"],
                source_metadata={},
            )
            self.db.add(item)
            self.db.commit()
            self.db.refresh(item)
            self.item_ids.append(item.id)

            adapter = FakeModelAdapter(
                json.dumps(
                    {
                        "ideas": [
                            {
                                "title": "Source-aware evaluation loops",
                                "problem_statement": "Automated research agents miss source coverage gaps.",
                                "hypothesis": "Source-aware rubrics will improve reproducibility checks.",
                                "motivation": "Existing evaluations underweight retrieval provenance.",
                                "related_work": ["Retrieval augmented evaluation", "Retrieval augmented evaluation"],
                                "feasibility": "Can be tested with stored knowledge items and generated rubrics.",
                                "score": 0.82,
                                "reusable_points": ["Use source-aware rubrics"],
                            }
                        ]
                    }
                )
            )

            ideas = await generate_and_persist_ideas(
                self.db,
                [item.id],
                IdeaGenerationAgent(adapter),
                max_ideas=3,
            )

            self.assertEqual(len(ideas), 1)
            idea = ideas[0]
            self.idea_ids.append(idea.id)
            self.assertEqual(idea.status, IdeaStatus.CANDIDATE.value)
            self.assertEqual(idea.title, "Source-aware evaluation loops")
            self.assertEqual(idea.problem_statement, "Automated research agents miss source coverage gaps.")
            self.assertEqual(idea.hypothesis, "Source-aware rubrics will improve reproducibility checks.")
            self.assertEqual(idea.rationale, "Existing evaluations underweight retrieval provenance.")
            self.assertAlmostEqual(float(idea.score), 0.82)
            self.assertEqual(idea.source_context["knowledge_item_ids"], [str(item.id)])
            self.assertEqual(idea.source_context["related_work"], ["Retrieval augmented evaluation"])
            self.assertEqual(idea.extra["feasibility"], "Can be tested with stored knowledge items and generated rubrics.")
            self.assertEqual(idea.extra["reusable_points"], ["Use source-aware rubrics"])
            self.assertEqual(idea.extra["generation"]["provider"], "test-provider")
            self.assertEqual(idea.extra["generation"]["model"], "idea-model")
            prompt = next(message.content for message in adapter.requests[0].messages if message.role == "user")
            self.assertIn("Retrieval augmented evaluation", prompt)
            self.assertIn("Return at most 3 ideas", prompt)

        asyncio.run(scenario())

    def test_invalid_generation_response_does_not_create_ideas(self) -> None:
        async def scenario() -> None:
            item = KnowledgeItem(
                canonical_key="idea-generation-test-2",
                source="semantic_scholar",
                source_id="paper-2",
                title="Sparse experiment planning",
                abstract="A sparse planning approach.",
                url="https://example.com/paper-2",
                authors=[],
                summary="Sparse planning reduces experiment cost.",
                methods=[],
                contributions=[],
                reusable_points=[],
                source_metadata={},
            )
            self.db.add(item)
            self.db.commit()
            self.db.refresh(item)
            self.item_ids.append(item.id)
            before_count = self.db.query(Idea).count()

            with self.assertRaises(IdeaGenerationAgentError):
                await generate_and_persist_ideas(
                    self.db,
                    [item.id],
                    IdeaGenerationAgent(FakeModelAdapter('{"ideas": []}')),
                )

            self.assertEqual(self.db.query(Idea).count(), before_count)

        asyncio.run(scenario())

    def test_refines_and_updates_existing_idea(self) -> None:
        async def scenario() -> None:
            idea = Idea(
                title="Initial retrieval idea",
                problem_statement="Initial problem.",
                hypothesis="Initial hypothesis.",
                status=IdeaStatus.CANDIDATE.value,
                score=0.55,
                rationale="Initial motivation.",
                source_context={"related_work": ["Original paper"]},
                extra={"feasibility": "Initial feasibility.", "reusable_points": ["Initial point"]},
            )
            self.db.add(idea)
            self.db.commit()
            self.db.refresh(idea)
            self.idea_ids.append(idea.id)

            adapter = FakeModelAdapter(
                json.dumps(
                    {
                        "title": "Refined retrieval idea",
                        "problem_statement": "Sharper retrieval problem.",
                        "hypothesis": "A refined retrieval method improves reproducibility.",
                        "motivation": "The refined motivation targets benchmark gaps.",
                        "assistant_message": "I narrowed the idea around reproducibility.",
                        "related_work": ["Original paper", "New benchmark"],
                        "feasibility": "Can be validated with existing benchmark logs.",
                        "score": 0.76,
                        "reusable_points": ["Reuse benchmark logs"],
                    }
                )
            )

            updated, assistant_message = await refine_and_update_idea(
                self.db,
                idea.id,
                "Make this more reproducibility focused.",
                IdeaRefinementAgent(adapter),
            )

            self.assertEqual(assistant_message, "I narrowed the idea around reproducibility.")
            self.assertEqual(updated.title, "Refined retrieval idea")
            self.assertEqual(updated.problem_statement, "Sharper retrieval problem.")
            self.assertEqual(updated.hypothesis, "A refined retrieval method improves reproducibility.")
            self.assertEqual(updated.rationale, "The refined motivation targets benchmark gaps.")
            self.assertAlmostEqual(float(updated.score), 0.76)
            self.assertEqual(updated.source_context["related_work"], ["Original paper", "New benchmark"])
            self.assertEqual(updated.extra["feasibility"], "Can be validated with existing benchmark logs.")
            self.assertEqual(updated.extra["reusable_points"], ["Reuse benchmark logs"])
            self.assertEqual(updated.extra["last_refinement"]["provider"], "test-provider")
            self.assertEqual(updated.extra["refinement_thread"][-2]["role"], "user")
            self.assertEqual(updated.extra["refinement_thread"][-1]["content"], "I narrowed the idea around reproducibility.")
            prompt = next(message.content for message in adapter.requests[0].messages if message.role == "user")
            self.assertIn("Initial retrieval idea", prompt)
            self.assertIn("Make this more reproducibility focused.", prompt)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()

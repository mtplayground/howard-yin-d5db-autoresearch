import asyncio
import json
import unittest

from app.agents.extraction import ExtractionAgentError, KnowledgeExtractionAgent, extract_and_update_knowledge_item
from app.db.models import KnowledgeItem
from app.db.session import SessionLocal
from app.services.model_adapter import ModelRequest, ModelResponse


class FakeModelAdapter:
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(content=self.content, model="model-a", provider="test-provider", usage={"total_tokens": 10})


class ExtractionAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = SessionLocal()
        self.item_ids: list[object] = []

    def tearDown(self) -> None:
        self.db.rollback()
        for item_id in self.item_ids:
            self.db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).delete(synchronize_session=False)
        self.db.commit()
        self.db.close()

    def test_extracts_and_updates_knowledge_item(self) -> None:
        async def scenario() -> None:
            item = KnowledgeItem(
                canonical_key="extraction-test-1",
                source="arxiv",
                source_id="2401.00001",
                title="Retrieval augmented testing",
                abstract="A method for retrieval augmented testing.",
                url="https://arxiv.org/abs/2401.00001",
                code_repository_url="https://github.com/org/retrieval-testing",
                authors=["Ada Lovelace"],
                source_metadata={"field": "value"},
            )
            self.db.add(item)
            self.db.commit()
            self.db.refresh(item)
            self.item_ids.append(item.id)
            adapter = FakeModelAdapter(
                json.dumps(
                    {
                        "summary": "Tests retrieval augmented systems.",
                        "methods": ["retrieval pipeline", "evaluation harness"],
                        "contributions": ["benchmark design"],
                        "reusable_points": ["Use query perturbations", "Use query perturbations"],
                    }
                )
            )
            agent = KnowledgeExtractionAgent(adapter)

            updated = await extract_and_update_knowledge_item(self.db, item.id, agent)

            self.assertEqual(updated.summary, "Tests retrieval augmented systems.")
            self.assertEqual(updated.methods, ["retrieval pipeline", "evaluation harness"])
            self.assertEqual(updated.contributions, ["benchmark design"])
            self.assertEqual(updated.reusable_points, ["Use query perturbations"])
            self.assertEqual(updated.extraction_model, "model-a")
            self.assertIsNotNone(updated.extracted_at)
            self.assertEqual(updated.source_metadata["extraction"]["provider"], "test-provider")
            prompt = next(message.content for message in adapter.requests[0].messages if message.role == "user")
            self.assertIn("Retrieval augmented testing", prompt)
            self.assertIn("https://github.com/org/retrieval-testing", prompt)

        asyncio.run(scenario())

    def test_invalid_model_json_does_not_update_item(self) -> None:
        async def scenario() -> None:
            item = KnowledgeItem(
                canonical_key="extraction-test-2",
                source="github",
                source_id="repo-1",
                title="Repository",
                abstract=None,
                url="https://github.com/org/repo",
                authors=[],
                source_metadata={},
            )
            self.db.add(item)
            self.db.commit()
            self.db.refresh(item)
            self.item_ids.append(item.id)
            agent = KnowledgeExtractionAgent(FakeModelAdapter("not json"))

            with self.assertRaises(ExtractionAgentError):
                await extract_and_update_knowledge_item(self.db, item.id, agent)
            self.db.refresh(item)
            self.assertIsNone(item.summary)
            self.assertIsNone(item.extracted_at)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()

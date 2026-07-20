import unittest

from app.db.models import KnowledgeItem
from app.db.session import SessionLocal
from app.services.knowledge import ingest_source_results, normalize_source_result
from app.services.source_connectors import SourceResult


class KnowledgeNormalizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = SessionLocal()
        self.canonical_keys: list[str] = []

    def tearDown(self) -> None:
        self.db.rollback()
        for canonical_key in self.canonical_keys:
            self.db.query(KnowledgeItem).filter(KnowledgeItem.canonical_key == canonical_key).delete(synchronize_session=False)
        self.db.commit()
        self.db.close()

    def test_normalizes_github_result_as_code_repository(self) -> None:
        item = normalize_source_result(
            SourceResult(
                source="github",
                external_id="123",
                title="  org/repo  ",
                url="https://github.com/Org/Repo/",
                abstract=" Useful code ",
                authors=["Org", "org"],
            )
        )
        self.canonical_keys.append(item.canonical_key)

        self.assertEqual(item.title, "org/repo")
        self.assertEqual(item.url, "https://github.com/Org/Repo")
        self.assertEqual(item.code_repository_url, "https://github.com/Org/Repo")
        self.assertEqual(item.authors, ["Org"])

    def test_ingest_deduplicates_arxiv_results_across_sources(self) -> None:
        arxiv = SourceResult(
            source="arxiv",
            external_id="2401.00001v2",
            title="Retrieval augmented testing",
            url="https://arxiv.org/abs/2401.00001v2",
            authors=["Ada Lovelace"],
            metadata={"raw_id": "https://arxiv.org/abs/2401.00001v2"},
        )
        semantic = SourceResult(
            source="semantic_scholar",
            external_id="paper-1",
            title="Retrieval augmented testing",
            url="https://www.semanticscholar.org/paper/paper-1",
            abstract="A better abstract",
            authors=["Grace Hopper"],
            metadata={"external_ids": {"ArXiv": "2401.00001"}},
        )

        result = ingest_source_results(self.db, [arxiv, semantic])
        self.canonical_keys.extend(item.canonical_key for item in result.items)

        self.assertEqual(result.created_count, 1)
        self.assertEqual(result.updated_count, 1)
        rows = self.db.query(KnowledgeItem).filter(KnowledgeItem.canonical_key == result.items[0].canonical_key).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].abstract, "A better abstract")
        self.assertEqual(rows[0].authors, ["Ada Lovelace", "Grace Hopper"])
        self.assertEqual(set(rows[0].source_metadata["source_observations"].keys()), {"arxiv", "semantic_scholar"})

    def test_reingest_same_source_updates_existing_row(self) -> None:
        first = SourceResult(
            source="papers_with_code",
            external_id="pwc-1",
            title="Benchmark paper",
            url="https://paperswithcode.com/paper/benchmark",
            metadata={"repository_url": "https://github.com/org/benchmark"},
        )
        second = SourceResult(
            source="papers_with_code",
            external_id="pwc-1",
            title="Benchmark paper updated",
            url="https://paperswithcode.com/paper/benchmark",
            abstract="Updated abstract",
            metadata={"repository_url": "https://github.com/org/benchmark"},
        )

        first_result = ingest_source_results(self.db, [first])
        second_result = ingest_source_results(self.db, [second])
        self.canonical_keys.extend(item.canonical_key for item in [*first_result.items, *second_result.items])

        self.assertEqual(first_result.created_count, 1)
        self.assertEqual(second_result.created_count, 0)
        self.assertEqual(second_result.updated_count, 1)
        self.db.refresh(first_result.items[0])
        self.assertEqual(first_result.items[0].title, "Benchmark paper updated")
        self.assertEqual(first_result.items[0].code_repository_url, "https://github.com/org/benchmark")


if __name__ == "__main__":
    unittest.main()

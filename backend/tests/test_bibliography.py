import unittest
import uuid
from datetime import UTC, datetime

from app.db.models import KnowledgeItem
from app.services.bibliography import assemble_bibliography, citation_key_for_knowledge_item, inject_bibliography


class BibliographyAssemblyTest(unittest.TestCase):
    def test_deduplicates_model_and_knowledge_entries_and_rewrites_bibliography(self) -> None:
        item = KnowledgeItem(
            id=uuid.uuid4(),
            canonical_key="semantic-scholar-test",
            source="semantic_scholar",
            source_id="S2-123",
            title="Retrieval Evaluation for Research Agents",
            abstract="Abstract.",
            url="https://example.test/retrieval",
            authors=["Ada Lovelace", "Grace Hopper"],
            summary="Summary.",
            methods=[],
            contributions=[],
            reusable_points=[],
            published_at=datetime(2026, 1, 2, tzinfo=UTC),
            source_metadata={},
        )

        assembly = assemble_bibliography(
            [item],
            [{"key": "retrieval2026", "title": "Retrieval Evaluation for Research Agents", "url": "https://example.test/retrieval"}],
        )
        latex = inject_bibliography(
            "\\documentclass{article}\n\\begin{document}\n\\section{Related Work}\nPrior work matters.\n"
            "\\begin{thebibliography}{1}\n\\bibitem{old} Old.\n\\end{thebibliography}\n\\end{document}",
            assembly,
        )

        self.assertEqual(len(assembly.entries), 1)
        self.assertEqual(assembly.entries[0].key, "retrieval2026")
        self.assertEqual(assembly.entries[0].authors, ["Ada Lovelace", "Grace Hopper"])
        self.assertIn("@misc{retrieval2026", assembly.bibtex)
        self.assertIn("\\cite{retrieval2026}", latex)
        self.assertIn("\\bibitem{retrieval2026}", latex)
        self.assertNotIn("\\bibitem{old}", latex)

    def test_generates_stable_key_for_uncited_knowledge_item(self) -> None:
        item = KnowledgeItem(
            id=uuid.uuid4(),
            canonical_key="arxiv-test",
            source="arxiv",
            source_id="2601.12345",
            title="Compact Benchmarks for Automated Discovery",
            abstract="Abstract.",
            url="https://arxiv.org/abs/2601.12345",
            authors=["Jane Doe"],
            summary="Summary.",
            methods=[],
            contributions=[],
            reusable_points=[],
            published_at=datetime(2026, 2, 1, tzinfo=UTC),
            source_metadata={},
        )

        key = citation_key_for_knowledge_item(item)
        assembly = assemble_bibliography([item], [])
        latex = inject_bibliography(
            "\\documentclass{article}\n\\begin{document}\n\\section{Related Work}\nNo citations yet.\n\\end{document}",
            assembly,
        )

        self.assertEqual(assembly.entries[0].key, key)
        self.assertIn(f"\\cite{{{key}}}", latex)
        self.assertIn(f"\\bibitem{{{key}}}", latex)
        self.assertLess(latex.index(f"\\cite{{{key}}}"), latex.index("No citations yet."))


if __name__ == "__main__":
    unittest.main()

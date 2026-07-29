from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import evaluate


ROOT = Path(__file__).resolve().parents[1]


class EvaluateTests(unittest.TestCase):
    def test_canonical_removes_fragment_before_trailing_slash(self) -> None:
        self.assertEqual(
            "https://example.test/guide",
            evaluate.canonical("https://example.test/guide/#part"),
        )
        self.assertEqual(
            evaluate.canonical("https://example.test/guide/"),
            evaluate.canonical("https://example.test/guide#part"),
        )

    def test_compact_index_is_preferred_with_catalogue_fallback(self) -> None:
        with tempfile.TemporaryDirectory(prefix=".test-evaluate-", dir=ROOT) as name:
            bundle = Path(name)
            index_path = bundle / "data" / "search" / "index.json"
            index_path.parent.mkdir(parents=True)
            compact_record = {
                "id": "compact",
                "title": "Compact result",
                "url": "https://example.test/compact",
                "heading_tokens": ["compact"],
                "body_tokens": ["compact", "result"],
            }
            index_path.write_text(
                json.dumps(
                    {
                        "schema": evaluate.SEARCH_INDEX_SCHEMA,
                        "snapshot_id": "compact-snapshot",
                        "record_count": 1,
                        "records": [compact_record],
                    }
                ),
                encoding="utf-8",
            )
            catalogue_path = bundle / "data" / "catalogue.json"
            catalogue_path.parent.mkdir(parents=True, exist_ok=True)
            catalogue_path.write_text(
                json.dumps(
                    {
                        "schema": evaluate.CATALOGUE_SCHEMA,
                        "snapshot_id": "catalogue-snapshot",
                        "record_count": 0,
                        "records": [],
                    }
                ),
                encoding="utf-8",
            )

            records, source = evaluate.load_records(bundle)
            self.assertEqual([compact_record], records)
            self.assertEqual("compact-search-index", source["kind"])

            index_path.unlink()
            records, source = evaluate.load_records(bundle)
            self.assertEqual([], records)
            self.assertEqual("catalogue-fallback", source["kind"])

    def test_question_success_and_expected_target_recall_are_distinct(self) -> None:
        contract = json.loads(evaluate.SEARCH_CONTRACT.read_text(encoding="utf-8"))
        records = [
            {
                "id": "a",
                "title": "Alpha",
                "url": "https://example.test/a/#fragment",
                "source_urls": ["https://example.test/b"],
                "curation": "source-native",
                "heading_tokens": ["alpha"],
                "body_tokens": ["alpha"],
            },
            {
                "id": "c",
                "title": "Charlie",
                "url": "https://example.test/c",
                "curation": "source-native",
                "heading_tokens": ["charlie"],
                "body_tokens": ["charlie"],
            },
        ]
        questions = [
            {
                "id": "q1",
                "query": "alpha",
                "expected_sources": [
                    {"canonical_url": "https://example.test/a"},
                    {"canonical_url": "https://example.test/b"},
                ],
            },
            {
                "id": "q2",
                "query": "missing",
                "expected_sources": [
                    {"canonical_url": "https://example.test/c"},
                ],
            },
        ]

        rows, metrics = evaluate.evaluate_questions(questions, records, contract, 1)

        self.assertTrue(rows[0]["expected_source_success_at_k"])
        self.assertEqual(0.5, rows[0]["expected_target_recall_at_k"])
        self.assertFalse(rows[1]["expected_source_success_at_k"])
        self.assertEqual(0.5, metrics["expected_source_success_at_k"])
        self.assertAlmostEqual(1 / 3, metrics["expected_target_recall_at_k"])


if __name__ == "__main__":
    unittest.main()

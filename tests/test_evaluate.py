from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts import evaluate


ROOT = Path(__file__).resolve().parents[1]


class EvaluateTests(unittest.TestCase):
    def test_runtime_tree_identity_matches_pinned_explorer_receipt(self) -> None:
        receipt = json.loads(
            (
                ROOT
                / "validation"
                / "candidate-v0.2.0"
                / "explorer-search-runtime-collation-fixed-0fdab21a.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            receipt["bundle"]["tree"],
            evaluate.bundle_tree_identity(ROOT / "bundle"),
        )

    def test_runtime_tree_identity_uses_locked_explorer_collation(self) -> None:
        with tempfile.TemporaryDirectory(prefix=".test-tree-", dir=ROOT) as name:
            bundle = Path(name)
            ordered_names = [
                "access_state.json",
                "access.json",
                "catalogue-index.html",
                "CHECKSUMS.sha256",
            ]
            for filename in reversed(ordered_names):
                (bundle / filename).write_text(filename, encoding="utf-8")
            rows = [
                (
                    f"{hashlib.sha256(filename.encode()).hexdigest()}  "
                    f"{filename}"
                )
                for filename in ordered_names
            ]
            expected = hashlib.sha256(
                ("\n".join(rows) + "\n").encode()
            ).hexdigest()
            self.assertEqual(
                expected,
                evaluate.bundle_tree_identity(bundle)["sha256"],
            )

    def test_locked_worker_calibration_manifest_covers_question_suite(self) -> None:
        questions_path = ROOT / "evaluation" / "questions.json"
        questions_bytes = questions_path.read_bytes()
        questions = json.loads(questions_bytes)
        runtime = json.loads(
            (ROOT / "evaluation" / "explorer-search-calibration-v0.2.0.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(
            hashlib.sha256(questions_bytes).hexdigest(),
            runtime["calibration_suite_sha256"],
        )
        self.assertEqual(
            {question["id"] for question in questions["questions"]},
            {journey["calibration_question_id"] for journey in runtime["journeys"]},
        )
        self.assertEqual(26, len(runtime["journeys"]))
        question_by_id = {
            question["id"]: question for question in questions["questions"]
        }
        for journey in runtime["journeys"]:
            question = question_by_id[journey["calibration_question_id"]]
            assertions = journey["assertions"]
            expected_urls = {
                source["canonical_url"]
                for source in question["expected_sources"]
            }
            self.assertIn(journey["runtime_expected_source_url"], expected_urls)
            self.assertTrue(
                any(
                    assertion.get("type") == "attribute"
                    and assertion.get("name") == "href"
                    and assertion.get("equals")
                    == journey["runtime_expected_source_url"]
                    for assertion in assertions
                )
            )
            asserted_caveats = {
                assertion.get("includes")
                for assertion in assertions
                if assertion.get("type") == "text"
            }
            self.assertLessEqual(set(journey["required_caveat_ids"]), asserted_caveats)
        for question in questions["questions"]:
            declared = {
                caveat_id
                for journey in runtime["journeys"]
                if journey["calibration_question_id"] == question["id"]
                for caveat_id in journey["required_caveat_ids"]
            }
            self.assertEqual(set(question["required_caveat_ids"]), declared)

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
                "source_urls": ["https://example.test/compact"],
                "equivalent_urls": [],
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
                "equivalent_urls": ["https://example.test/a-alias"],
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
                    {"canonical_url": "https://example.test/a-alias"},
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
        self.assertEqual(1.0, rows[0]["expected_target_recall_at_k"])
        self.assertTrue(rows[0]["all_expected_targets_at_k"])
        self.assertFalse(rows[1]["expected_source_success_at_k"])
        self.assertEqual(0.5, metrics["expected_source_success_at_k"])
        self.assertAlmostEqual(2 / 3, metrics["expected_target_recall_at_k"])
        self.assertEqual(0.5, metrics["all_expected_targets_success_at_k"])

    def test_supporting_source_urls_do_not_satisfy_expected_targets(self) -> None:
        contract = json.loads(evaluate.SEARCH_CONTRACT.read_text(encoding="utf-8"))
        record = {
            "id": "source-separation",
            "title": "Safe route separation",
            "url": "https://example.test/primary",
            "source_urls": ["https://example.test/related-evidence"],
            "equivalent_urls": ["https://example.test/true-alias"],
            "curation": "reviewed",
            "heading_tokens": ["safe"],
            "body_tokens": [],
        }
        questions = [
            {
                "id": "related",
                "query": "safe",
                "expected_sources": [
                    {"canonical_url": "https://example.test/related-evidence"}
                ],
            },
            {
                "id": "alias",
                "query": "safe",
                "expected_sources": [
                    {"canonical_url": "https://example.test/true-alias"}
                ],
            },
        ]

        rows, _metrics = evaluate.evaluate_questions(
            questions, [record], contract, 10
        )

        self.assertFalse(rows[0]["expected_source_success_at_k"])
        self.assertTrue(rows[1]["expected_source_success_at_k"])

    def test_multi_term_search_rejects_single_common_term_noise(self) -> None:
        contract = json.loads(evaluate.SEARCH_CONTRACT.read_text(encoding="utf-8"))
        records = [
            {
                "id": "specific",
                "title": "Local land charges automation terms",
                "url": "https://example.test/specific",
                "curation": "reviewed",
                "heading_tokens": ["local", "land", "charges", "automation", "terms"],
                "body_tokens": [],
            },
            {
                "id": "noise",
                "title": "Unrelated land publication",
                "url": "https://example.test/noise",
                "curation": "source-native",
                "heading_tokens": ["land"],
                "body_tokens": [],
            },
        ]
        ranked = evaluate.rank("local land charges automation", records, contract)
        self.assertEqual(["specific"], [record["id"] for record in ranked])

    def test_forbidden_targets_are_executable_negative_assertions(self) -> None:
        contract = json.loads(evaluate.SEARCH_CONTRACT.read_text(encoding="utf-8"))
        records = [
            {
                "id": "wrong-jurisdiction",
                "title": "Property register",
                "url": "https://www.ros.gov.uk/",
                "curation": "source-native",
                "heading_tokens": ["property", "register"],
                "body_tokens": [],
            }
        ]
        questions = [
            {
                "id": "q-negative",
                "query": "property register",
                "expected_sources": [],
                "required_caveat_ids": ["CAV-BOUNDED-COVERAGE"],
                "must_not_retrieve": [
                    {
                        "target_id": "NEG-CROSS-JURISDICTION",
                        "canonical_url": "https://www.ros.gov.uk/",
                        "max_rank": 5,
                    }
                ],
            }
        ]
        rows, metrics = evaluate.evaluate_questions(questions, records, contract, 5)
        self.assertFalse(rows[0]["must_not_retrieve_passed"])
        self.assertEqual(1, rows[0]["must_not_retrieve_hits"][0]["rank"])
        self.assertEqual(1, metrics["must_not_retrieve_hit_count"])
        self.assertEqual(0.0, metrics["must_not_retrieve_pass_rate"])

    def test_question_contract_requires_caveats_and_forbidden_targets(self) -> None:
        payload = {
            "suite_partition": "calibration",
            "caveat_registry": [{"id": "CAV-ONE", "text": "One"}],
            "questions": [
                {
                    "id": "Q1",
                    "expected_sources": [
                        {"canonical_url": "https://example.test/right"}
                    ],
                    "runtime_expected_source_url": "https://example.test/right",
                    "required_caveat_ids": ["CAV-ONE"],
                    "must_not_retrieve": [
                        {
                            "target_id": "NEG-ONE",
                            "canonical_url": "https://example.test/wrong",
                            "max_rank": 5,
                            "reason": "A bounded executable near miss.",
                        }
                    ],
                }
            ],
        }
        evaluate.validate_question_contract(payload)
        payload["questions"][0]["must_not_retrieve"] = []
        with self.assertRaisesRegex(ValueError, "forbidden targets"):
            evaluate.validate_question_contract(payload)

    def test_acceptance_review_requires_exact_independent_coverage(self) -> None:
        questions = [
            {
                "id": "Q1",
                "hard_failure_ids": ["HF-AUTHORITY", "HF-COVERAGE"],
                "required_caveat_ids": ["CAV-AUTHORITY"],
            }
        ]
        review = {
            "schema": evaluate.ACCEPTANCE_REVIEW_SCHEMA,
            "status": "pass",
            "suite_sha256": "a" * 64,
            "bundle_release_root_sha256": "b" * 64,
            "reviewer": {
                "role": "independent evaluation reviewer",
                "kind": "AI-assisted independent process",
                "reviewed_at": "2026-07-29",
                "independent_of_retrieval_implementation": True,
            },
            "question_reviews": [
                {
                    "question_id": "Q1",
                    "source_resolution": True,
                    "expected_propositions_verified": True,
                    "near_miss_rule_verified": True,
                    "caveat_coverage": True,
                    "hard_failure_ids_reviewed": [
                        "HF-AUTHORITY",
                        "HF-COVERAGE",
                    ],
                    "required_caveat_ids_verified": ["CAV-AUTHORITY"],
                    "hard_failures_observed": [],
                }
            ],
            "held_out_adversarial": [
                {
                    "id": f"ADV-{index}",
                    "status": "pass",
                    "new_critical_category": False,
                    "precision_acceptable": True,
                    "safety_behavior_verified": True,
                }
                for index in range(1, 7)
            ],
        }

        result = evaluate.validate_acceptance_review(
            review, questions, "a" * 64, "b" * 64
        )
        self.assertEqual(0, result["hard_failure_count"])
        self.assertEqual(
            1.0, result["independent_review_caveat_coverage"]
        )
        self.assertEqual(
            1.0, result["independent_review_source_resolution_coverage"]
        )

        review["question_reviews"][0]["hard_failures_observed"] = ["HF-COVERAGE"]
        with self.assertRaisesRegex(ValueError, "hard failure was observed"):
            evaluate.validate_acceptance_review(
                review, questions, "a" * 64, "b" * 64
            )

    def test_forbidden_targets_must_exist_in_candidate_and_fit_k(self) -> None:
        questions = [
            {
                "id": "Q1",
                "expected_sources": [
                    {"canonical_url": "https://example.test/right"}
                ],
                "must_not_retrieve": [
                    {
                        "target_id": "NEG-ONE",
                        "canonical_url": "https://example.test/wrong",
                        "max_rank": 5,
                    }
                ],
            }
        ]
        records = [
            {
                "url": "https://example.test/right",
                "equivalent_urls": [],
            }
        ]
        with self.assertRaisesRegex(ValueError, "absent from the candidate"):
            evaluate.validate_forbidden_targets(questions, records, 10)
        records.append(
            {
                "url": "https://example.test/wrong",
                "equivalent_urls": [],
            }
        )
        evaluate.validate_forbidden_targets(questions, records, 10)
        with self.assertRaisesRegex(ValueError, "cannot execute"):
            evaluate.validate_forbidden_targets(questions, records, 4)

    def test_runtime_evidence_measures_source_and_caveat_assertions(self) -> None:
        questions = [
            {
                "id": "Q1",
                "runtime_expected_source_url": "https://example.test/right",
                "expected_sources": [
                    {"canonical_url": "https://example.test/right"}
                ],
                "required_caveat_ids": ["CAV-ONE"],
            }
        ]
        with tempfile.TemporaryDirectory(prefix=".test-runtime-", dir=ROOT) as name:
            root = Path(name)
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "okf-explorer.json").write_text("{}\n", encoding="utf-8")
            manifest = {
                "schema": evaluate.RUNTIME_JOURNEY_SCHEMA,
                "calibration_suite_sha256": "a" * 64,
                "journeys": [
                    {
                        "id": "q1",
                        "calibration_question_id": "Q1",
                        "runtime_expected_source_url": "https://example.test/right",
                        "required_caveat_ids": ["CAV-ONE"],
                        "assertions": [
                            {
                                "type": "attribute",
                                "name": "href",
                                "equals": "https://example.test/right",
                            },
                            {"type": "text", "includes": "CAV-ONE"},
                        ],
                    }
                ],
            }
            manifest_path = root / "journeys.json"
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
            )
            receipt = {
                "schema": evaluate.RUNTIME_RECEIPT_SCHEMA,
                "status": "passed",
                "journey_manifest": {
                    "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                },
                "bundle": {"tree": evaluate.bundle_tree_identity(bundle)},
                "journeys": [
                    {"id": "q1", "terminal": {"status": "passed"}}
                ],
            }
            receipt_path = root / "receipt.json"
            receipt_path.write_text(
                json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8"
            )
            result = evaluate.validate_runtime_evidence(
                manifest_path,
                receipt_path,
                questions,
                "a" * 64,
                bundle,
            )
        self.assertEqual(1.0, result["source_resolution_coverage"])
        self.assertEqual(1.0, result["required_caveat_assertion_coverage"])


if __name__ == "__main__":
    unittest.main()

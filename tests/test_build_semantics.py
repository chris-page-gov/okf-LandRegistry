from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build import (
    ROOT,
    ai_usage_projection,
    load_ai_model_usage,
    load_build_config,
    normal_record,
    normalize_govuk,
    write_search_and_shards,
)


class BuildSemanticsTests(unittest.TestCase):
    def test_ai_usage_ledger_is_explicit_about_unknown_and_subscription_costs(
        self,
    ) -> None:
        ledger = load_ai_model_usage(load_build_config())
        scope = ledger["measurement_scope"]
        self.assertEqual("unavailable", scope["pre_tracking_usage"]["status"])
        self.assertIsNone(scope["pre_tracking_usage"]["total_tokens"])
        costs = ledger["cost_accounting"]
        self.assertIsNone(costs["subscription_fee_allocation"]["amount"])
        self.assertEqual(0.0, costs["separately_billed_openai_api"]["amount"])
        self.assertIsNone(costs["rate_card_equivalent"]["amount"])
        self.assertIsNone(costs["rate_card_equivalent"]["rate_card_source"])

        path = ROOT / "governance" / "ai-model-usage.json"
        projection = ai_usage_projection(ledger, path)
        self.assertEqual("governance/ai-model-usage.json", projection["source"]["path"])
        self.assertEqual(64, len(projection["source"]["sha256"]))
        self.assertEqual(ledger, projection["ledger"])

    def test_boundary_discovery_record_gets_visible_general_boundary_caveat(self) -> None:
        record = normalize_govuk(
            {
                "link": "/government/publications/exact-line-of-boundary-registration-db",
                "content_id": "fixture-boundary",
                "title": "Exact line of boundary: registration",
                "description": "Application form for a boundary process.",
                "content_store_document_type": "form",
                "organisations": [{"title": "HM Land Registry"}],
            },
            "2026-07-29T09:19:15Z",
        )
        self.assertIn("general boundaries", record["caveats"][0])
        self.assertIn("not a boundary conclusion", record["caveats"][0])

    def test_non_boundary_record_keeps_generic_discovery_caveat(self) -> None:
        record = normalize_govuk(
            {
                "link": "/government/publications/example",
                "content_id": "fixture-example",
                "title": "Example publication",
                "description": "A public discovery record.",
                "content_store_document_type": "publication",
            },
            "2026-07-29T09:19:15Z",
        )
        self.assertTrue(record["caveats"][0].startswith("Search metadata"))

    def test_search_projection_keeps_evidence_and_equivalent_routes_separate(
        self,
    ) -> None:
        record = normal_record(
            {
                "id": "fixture-routes",
                "title": "Route fixture",
                "url": "https://www.gov.uk/example/primary",
                "record_type": "guidance",
                "source_family": "govuk-hmlr",
                "source_urls": [
                    "https://www.gov.uk/example/primary",
                    "https://www.gov.uk/example/supporting-evidence",
                ],
                "equivalent_urls": [
                    "https://www.gov.uk/example/primary/",
                    "https://www.gov.uk/example/true-alias",
                ],
            }
        )
        self.assertEqual(
            ["https://www.gov.uk/example/true-alias"],
            record["equivalent_urls"],
        )

        with tempfile.TemporaryDirectory(prefix=".test-build-", dir=".") as name:
            output = Path(name)
            _search, _shards = write_search_and_shards(output, [record])
            payload = json.loads(
                (output / "data" / "search" / "index.json").read_text(
                    encoding="utf-8"
                )
            )

        compact = payload["records"][0]
        self.assertEqual(record["source_urls"], compact["source_urls"])
        self.assertEqual(record["equivalent_urls"], compact["equivalent_urls"])


if __name__ == "__main__":
    unittest.main()

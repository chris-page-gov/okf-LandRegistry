from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build import (
    ROOT,
    ai_usage_projection,
    load_publisher_registry,
    load_type_kind_crosswalk,
    load_ai_model_usage,
    load_build_config,
    normal_record,
    normalize_cddo,
    normalize_govuk,
    record_id_for,
    write_search_and_shards,
)


class BuildSemanticsTests(unittest.TestCase):
    def test_type_crosswalk_is_exhaustive_for_every_current_source_type(self) -> None:
        mapping, allowed = load_type_kind_crosswalk()
        govuk = json.loads(
            (
                ROOT
                / "source"
                / "snapshots"
                / "2026-07-29T091915Z"
                / "govuk-search.json"
            ).read_text()
        )
        curated = json.loads((ROOT / "source" / "curated-records.json").read_text())
        source_types = {
            item.get("content_store_document_type")
            or item.get("format")
            or "govuk-content"
            for item in govuk["results"]
        }
        source_types.update(record["record_type"] for record in curated["records"])
        source_types.update({"api-catalogue-record", "software-repository"})
        self.assertFalse(source_types - set(mapping))
        self.assertFalse(set(mapping.values()) - allowed)

    def test_record_ids_and_publisher_ids_are_stable_and_collision_checked(self) -> None:
        first = record_id_for("govuk-search", "native-id")
        self.assertEqual(first, record_id_for("govuk-search", "native-id"))
        self.assertNotEqual(first, record_id_for("govuk-content", "native-id"))
        self.assertRegex(first, r"^hmlr-[0-9a-f]{24}$")
        registry = load_publisher_registry()
        self.assertEqual(len(registry), len(set(registry.values())))
        self.assertTrue(all(value.startswith("https://") for value in registry.values()))

    def test_prose_placeholders_become_null_with_controlled_states(self) -> None:
        record = normal_record(
            {
                "id": "fixture-placeholders",
                "title": "Placeholder fixture",
                "url": "https://www.gov.uk/example/placeholders",
                "record_type": "guidance",
                "source_family": "govuk-hmlr",
                "jurisdiction": (
                    "Source-specific; HM Land Registry normally covers England "
                    "and Wales"
                ),
                "licence": "check-source",
                "cadence": "not stated",
            }
        )
        for field in ("jurisdiction", "licence", "cadence"):
            self.assertIsNone(record[field])
            self.assertEqual("unknown", record[f"{field}_state"])
        self.assertEqual([], record["languages"])
        self.assertEqual("unknown", record["language_state"])

    def test_language_aliases_are_normalized_to_bcp47(self) -> None:
        record = normal_record(
            {
                "id": "fixture-languages",
                "title": "Language fixture",
                "url": "https://www.gov.uk/example/languages",
                "record_type": "guidance",
                "source_family": "govuk-hmlr",
                "languages": ["English", "Welsh"],
            }
        )
        self.assertEqual(["cy", "en"], record["languages"])

    def test_business_gateway_catalogue_rows_fail_closed_as_restricted(self) -> None:
        record = normalize_cddo(
            {
                "name": "Official Copy Document Availability",
                "description": "Automate a production request against an endpoint.",
                "url": "https://businessgateway.landregistry.gov.uk/bg2/s1/v1",
            },
            "2026-07-29T09:19:15Z",
        )
        self.assertIn("Business e-services approval", record["authentication"])
        self.assertIn("restricted", record["description"].casefold())
        self.assertNotIn("automate a production request", record["description"])
        self.assertTrue(
            any("do not authenticate" in caveat for caveat in record["caveats"])
        )

    def test_welsh_translation_and_placeholder_absence_in_candidate(self) -> None:
        catalogue = json.loads(
            (ROOT / "bundle" / "data" / "catalogue.json").read_text()
        )
        records = catalogue["records"]
        groups: dict[str, list[dict]] = {}
        for record in records:
            if record.get("translation_group"):
                groups.setdefault(record["translation_group"], []).append(record)
        self.assertTrue(groups)
        self.assertTrue(
            any(
                {"en", "cy"}
                <= {
                    language
                    for record in group
                    for language in record["languages"]
                }
                for group in groups.values()
            )
        )
        forbidden = {
            "check-source",
            "not stated",
            "not declared in repository metadata",
            "source-specific; hm land registry normally covers england and wales",
            "technical source; jurisdiction is project-specific",
            "check publisher-operated contract",
        }
        rendered = json.dumps(records, ensure_ascii=False).casefold()
        for placeholder in forbidden:
            self.assertNotIn(f'"{placeholder}"', rendered)
        relationships = []
        manifest = json.loads(
            (ROOT / "bundle" / "data" / "explorer" / "manifest.json").read_text()
        )
        for reference in manifest["chunks"]["relationships"]:
            relationships.extend(
                json.loads((ROOT / "bundle" / reference["path"]).read_text())
            )
        self.assertIn(
            "translation_of",
            {relationship["predicate"] for relationship in relationships},
        )

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

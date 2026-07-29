from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "bundle"
LOCK_PATH = ROOT / "contracts" / "okf-explorer.consumer-lock.json"


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain an object")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compatibility_outcome(descriptor: dict[str, Any]) -> str:
    if (
        descriptor.get("schema") != "okf-explorer-large-corpus.v1"
        or descriptor.get("kind") != "okf-large-corpus"
    ):
        return "fail-closed"
    entrypoints = descriptor.get("entrypoints")
    integrity = descriptor.get("entrypoint_integrity")
    required = {
        "data_manifest",
        "overview_index",
        "analysis_overview",
        "record_locator",
        "search_manifest",
    }
    if not isinstance(entrypoints, dict) or not required <= set(entrypoints):
        return "degraded"
    if not isinstance(integrity, dict) or not required <= set(integrity):
        return "degraded"
    return "executable"


class ExplorerConsumerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.lock = load_json(LOCK_PATH)

    def test_consumer_and_harness_are_exactly_locked(self) -> None:
        consumer = self.lock["consumer"]
        self.assertEqual(consumer["release_tag"], "v0.5.7")
        self.assertEqual(consumer["version"], "0.5.7")
        self.assertEqual(
            consumer["commit_sha"],
            "afd940b6de2d09809ae94dfc77c128936ac7928a",
        )
        build = consumer["executable_build"]
        self.assertEqual(build["files"], 16)
        for field in (
            "tree_sha256",
            "build_manifest_sha256",
            "index_sha256",
        ):
            self.assertRegex(build[field], r"^[0-9a-f]{64}$")
        for source in consumer["contract_sources"]:
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
        harness = self.lock["runtime_harness"]
        self.assertEqual(harness["browser"], "chromium")
        self.assertEqual(
            harness["command"][:5],
            ["pnpm", "--dir", "apps/okf-explorer", "acceptance:bundle", "--"],
        )

    def test_released_v010_descriptor_is_explicitly_degraded(self) -> None:
        result = subprocess.run(
            ["git", "show", "v0.1.0:bundle/okf-explorer.json"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        descriptor = json.loads(result.stdout)
        self.assertEqual(descriptor["version"], "0.1.0")
        self.assertEqual(compatibility_outcome(descriptor), "degraded")

    def test_malformed_descriptors_fail_closed(self) -> None:
        self.assertEqual(compatibility_outcome({}), "fail-closed")
        self.assertEqual(
            compatibility_outcome(
                {"schema": "unknown", "kind": "okf-large-corpus"}
            ),
            "fail-closed",
        )

    def test_candidate_has_one_integrity_bound_runtime_search(self) -> None:
        descriptor = load_json(BUNDLE / "okf-explorer.json")
        self.assertEqual(descriptor["version"], "0.2.0")
        self.assertEqual(compatibility_outcome(descriptor), "executable")
        self.assertNotIn("catalogue_search_manifest", descriptor["entrypoints"])
        runtime_search = descriptor["entrypoint_integrity"]["search_manifest"]
        self.assertEqual(
            descriptor["entrypoints"]["search_manifest"],
            runtime_search["path"],
        )
        self.assertEqual(sha256(BUNDLE / runtime_search["path"]), runtime_search["sha256"])

    def test_projection_rows_keep_governed_identity_and_no_publisher_edges(self) -> None:
        manifest = load_json(BUNDLE / "data" / "explorer" / "manifest.json")
        datasets: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []
        for reference in manifest["chunks"]["datasets"]:
            path = BUNDLE / reference["path"]
            self.assertEqual(sha256(path), reference["sha256"])
            datasets.extend(json.loads(path.read_text(encoding="utf-8")))
        for reference in manifest["chunks"]["relationships"]:
            path = BUNDLE / reference["path"]
            self.assertEqual(sha256(path), reference["sha256"])
            relationships.extend(json.loads(path.read_text(encoding="utf-8")))
        self.assertTrue(datasets)
        for record in datasets:
            self.assertEqual(record["schema"], "okf-hmlr-record.v2")
            self.assertRegex(record["record_id"], r"^hmlr-[0-9a-f]{24}$")
            self.assertIn(
                record["kind"],
                {
                    "guidance",
                    "form",
                    "dataset",
                    "service",
                    "API",
                    "repository",
                    "statistics",
                    "news",
                    "corporate",
                    "legislation",
                    "other",
                },
            )
            self.assertTrue(record["source_native_id"])
            self.assertTrue(record["source_native_type"])
            self.assertTrue(record["publisher_id"].startswith("https://"))
        self.assertNotIn(
            "published_by",
            {relationship["predicate"] for relationship in relationships},
        )

    def test_runtime_search_indexes_governed_caveat_text(self) -> None:
        manifest = load_json(BUNDLE / "data" / "explorer" / "manifest.json")
        datasets = [
            record
            for reference in manifest["chunks"]["datasets"]
            for record in json.loads((BUNDLE / reference["path"]).read_text())
        ]
        accessibility = next(
            record
            for record in datasets
            if record["url"]
            == "https://use-land-property-data.service.gov.uk/accessibility-statement"
        )
        search = load_json(BUNDLE / "data" / "explorer" / "search" / "manifest.json")
        lexicon_path = search["entrypoints"]["lexicon"]["we"]
        lexicon = json.loads((BUNDLE / lexicon_path).read_text())
        welsh = next(row for row in lexicon if row["token"] == "welsh")
        postings = load_json(BUNDLE / welsh["postings"])
        row = next(
            row
            for row in postings["tokens"]["welsh"]
            if row[0] == accessibility["ordinal"]
        )
        self.assertTrue(row[2] & 4, "caveat match must use the description mask")

    def test_runtime_search_materializes_every_locked_filter_key(self) -> None:
        manifest = load_json(
            BUNDLE / "data" / "explorer" / "search" / "manifest.json"
        )
        filters = manifest["entrypoints"]["filter_postings"]
        self.assertEqual(
            set(self.lock["search"]["required_filter_keys"]),
            set(filters),
        )
        for path in filters.values():
            self.assertTrue((BUNDLE / path).is_file(), path)
        kind = load_json(BUNDLE / filters["kind"])
        self.assertEqual(kind["key"], "kind")
        self.assertEqual(
            {
                "guidance",
                "form",
                "dataset",
                "service",
                "API",
                "repository",
                "statistics",
                "news",
                "corporate",
                "legislation",
            },
            set(kind["values"]),
        )


if __name__ == "__main__":
    unittest.main()

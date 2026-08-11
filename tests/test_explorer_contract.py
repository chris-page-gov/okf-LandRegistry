from __future__ import annotations

import hashlib
import gzip
import json
import subprocess
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "bundle"
LOCK_PATH = ROOT / "contracts" / "okf-explorer.consumer-lock.json"
PRODUCT_JOURNEYS = ROOT / "evaluation" / "explorer-v0.3.0-journeys.json"
SEARCH_JOURNEYS = (
    ROOT / "evaluation" / "explorer-search-calibration-v0.3.0.json"
)
ACTION_TYPES = {
    "goto",
    "click",
    "fill",
    "press",
    "wait_for",
    "wait_for_ranked_result",
}
ASSERTION_TYPES = {
    "attribute",
    "console_clean",
    "count",
    "hidden",
    "not_requested",
    "not_text",
    "requested",
    "text",
    "url_hash",
    "url_param",
    "visible",
}


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
        "relationship_runtime",
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
        self.assertEqual(consumer["release_tag"], "v0.6.2")
        self.assertEqual(consumer["version"], "0.6.2")
        self.assertEqual(
            consumer["commit_sha"],
            "9430b3931f96bd9e6e06165c15b522742611f3e9",
        )
        self.assertEqual(
            consumer["git_tree"],
            "9d13ee9c2b174819feea2d732420674d4df5273b",
        )
        self.assertEqual(
            "43e53f36d869ba7ca2420990191a0834a969dcd2",
            consumer["annotated_tag_object_sha"],
        )
        self.assertEqual(
            {
                "id": 368773937,
                "immutable": True,
                "published_at": "2026-08-11T18:33:08Z",
                "url": (
                    "https://github.com/chris-page-gov/okf-explorer/"
                    "releases/tag/v0.6.2"
                ),
            },
            consumer["immutable_release"],
        )
        build = consumer["executable_build"]
        self.assertEqual(
            {
                "algorithm": "sha256-canonical-json-materials-v1",
                "files": 16,
                "tree_sha256": (
                    "85c8fd14f085e888670edc05980f6ae355fabd5d1d73579007c5c968dac4585e"
                ),
                "build_manifest_sha256": (
                    "30e41e79cbd3dba525fafdd42979a227b88d85328a7ae3bdd200456c7b629c83"
                ),
                "index_sha256": (
                    "def59642390d40356ca7e2319a4d6aa120c926e42f6285a9209cc945f6133eb9"
                ),
            },
            build,
        )
        self.assertEqual(25, len(consumer["contract_sources"]))
        for source in consumer["contract_sources"]:
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
        harness = self.lock["runtime_harness"]
        self.assertEqual(harness["browser"], "chromium")
        self.assertEqual(
            harness["command"][:5],
            ["pnpm", "--dir", "apps/okf-explorer", "acceptance:bundle", "--"],
        )
        compatibility = self.lock["compatibility_window"]
        self.assertEqual(compatibility["policy"], "exact-version-only")
        self.assertEqual(compatibility["minimum_version"], "0.6.2")
        self.assertEqual(compatibility["maximum_version"], "0.6.2")
        self.assertIn("full-candidate browser journeys", compatibility["rationale"])
        semantic_profile = consumer["semantic_profile"]
        self.assertEqual(16, semantic_profile["files"])
        self.assertEqual(
            {
                "repository": (
                    "https://github.com/chris-page-gov/okf-explorer"
                ),
                "version": "0.6.0",
                "tag": "v0.6.0",
                "tag_object": (
                    "d256a74419c2593c2bf2f3f5749c606fad5daf9d"
                ),
                "commit": "4bb7b92a64b7ba69bde9b1e86786217338cd166d",
                "git_tree": "d26ae9a818041ff74c469e653ec714632ddbfc2a",
            },
            semantic_profile["source_release"],
        )
        self.assertEqual(
            sha256(ROOT / semantic_profile["local_vendor_lock"]),
            semantic_profile["local_vendor_lock_sha256"],
        )
        vendor = load_json(ROOT / semantic_profile["local_vendor_lock"])
        self.assertEqual(semantic_profile["source_release"], vendor["release"])

    def test_acceptance_executable_materials_are_complete_and_bound(self) -> None:
        consumer = self.lock["consumer"]
        expected = {
            "runner": (
                "apps/okf-explorer/scripts/run_external_bundle_acceptance.mjs",
                42509,
                "8584977b57e3a683f859efeaf34b1f1b63ce33931e9c14878b68133020a4828a",
            ),
            "wrapper": (
                "apps/okf-explorer/scripts/run_acceptance_invocation.mjs",
                2489,
                "95b2d3287f16698f221221b615392421b5654c17c98b909b205727dfa55c3772",
            ),
            "invocation_lock_module": (
                "apps/okf-explorer/scripts/acceptance_invocation_lock.mjs",
                30724,
                "1f4741365c3a61c4d11f0e3844aff05bdc8aa3e9e71ff0b426667abe6050e4e7",
            ),
            "contract_module": (
                "apps/okf-explorer/scripts/external_bundle_acceptance_contract.mjs",
                21747,
                "5d2ead8e09fd62e63addf5fc0cec3b1912831f215f7d1b43325a29fcd120960e",
            ),
            "app_build_manifest_module": (
                "apps/okf-explorer/scripts/app_build_manifest.mjs",
                22932,
                "6750dfe9ab09eeea945525473c9919e4d308e7f9a87f748244cbcd13e8b1d867",
            ),
            "deterministic_build_script": (
                "apps/okf-explorer/scripts/check_deterministic_build.mjs",
                3309,
                "a4ee3e440aef1732da4e8b2de277c4cfe11573e4f35d16cb4c4348e84a6ff3e6",
            ),
        }
        materials = consumer["acceptance_executable_materials"]
        self.assertEqual(list(expected), list(materials))
        source_digests = {
            row["path"]: row["sha256"]
            for row in consumer["contract_sources"]
        }
        for name, (path, byte_count, digest) in expected.items():
            self.assertEqual(
                {"path": path, "bytes": byte_count, "sha256": digest},
                materials[name],
            )
            self.assertEqual(digest, source_digests[path])

    def test_predicate_registry_v2_profile_is_exactly_vendored(self) -> None:
        contract = self.lock["consumer"]["predicate_registry"]
        self.assertEqual(
            ["okf-predicate-registry.v1", "okf-predicate-registry.v2"],
            contract["supported_schemas"],
        )
        self.assertEqual(
            "okf-predicate-registry.v2",
            contract["required_projection_schema"],
        )
        self.assertEqual(
            {
                "repository": (
                    "https://github.com/chris-page-gov/okf-explorer"
                ),
                "version": "0.6.1",
                "tag": "v0.6.1",
                "annotated_tag_object_sha": (
                    "b5918192b1e3969ca2b069a4d56b3d26884ea96c"
                ),
                "commit_sha": (
                    "839d4ba4c2d02abc6ef02b3ca1dcbf6a4008e7c8"
                ),
                "immutable_release_id": 368556872,
                "published_at": "2026-08-11T12:34:04Z",
            },
            contract["source_release"],
        )
        lock_reference = contract["profile_lock"]
        self.assertEqual(
            "https://chris-page-gov.github.io/okf-explorer/profile/"
            "predicate-registry/v2.lock.json",
            lock_reference["url"],
        )
        lock_path = ROOT / lock_reference["local_path"]
        lock_bytes = lock_path.read_bytes()
        self.assertEqual(lock_reference["bytes"], len(lock_bytes))
        self.assertEqual(lock_reference["sha256"], sha256(lock_path))
        profile_lock = json.loads(lock_bytes)
        self.assertEqual(contract["profile"], profile_lock["profile"])
        self.assertEqual(2, profile_lock["file_count"])
        profile_root = lock_path.parent / "v2"
        identity_lines = []
        for row in profile_lock["files"]:
            material = profile_root / row["path"]
            self.assertEqual(row["bytes"], len(material.read_bytes()))
            self.assertEqual(row["sha256"], sha256(material))
            identity_lines.append(
                f"{row['path']}\t{row['bytes']}\t{row['sha256']}\n"
            )
        identity = hashlib.sha256("".join(identity_lines).encode()).hexdigest()
        self.assertEqual(profile_lock["identity"]["sha256"], identity)
        self.assertEqual(lock_reference["identity_sha256"], identity)
        schema_reference = contract["schema"]
        self.assertEqual(
            "https://chris-page-gov.github.io/okf-explorer/profile/"
            "predicate-registry/v2/predicate-registry.schema.json",
            schema_reference["url"],
        )
        schema_path = ROOT / schema_reference["local_path"]
        self.assertEqual(schema_reference["bytes"], len(schema_path.read_bytes()))
        self.assertEqual(schema_reference["sha256"], sha256(schema_path))
        self.assertEqual(
            schema_reference["url"],
            load_json(schema_path)["$id"],
        )

    def test_v062_rich_runtime_limits_and_query_policy_are_exact(self) -> None:
        self.assertEqual(
            {
                "maximum_json_bytes": 67_108_864,
                "maximum_full_index_records": 50_000,
                "maximum_relationship_rows": 300_000,
                "maximum_manifest_shard_references": 4_096,
                "maximum_postings_per_token": 50_000,
                "maximum_result_limit": 500,
                "maximum_rich_relationship_route_chunks": 64,
                "maximum_rich_relationship_route_rows": 100_000,
                "maximum_rich_relationship_chunk_rows": 50_000,
                "maximum_rich_relationship_chunk_bytes": 8_388_608,
                "maximum_rich_relationship_decoded_chunk_bytes": 67_108_864,
                "maximum_rich_relationship_hydration_compressed_bytes": (
                    67_108_864
                ),
                "maximum_rich_relationship_retained_text_units": 33_554_432,
                "maximum_rich_relationship_row_text_units": 32_768,
                "maximum_rich_relationship_evidence_items": 16,
                "maximum_rich_relationship_supporting_assertions": 128,
                "maximum_rich_relationship_cached_chunks": 16,
                "maximum_rich_relationship_planes": 16,
                "maximum_rich_relationship_chunks": 10_000,
            },
            self.lock["limits"],
        )
        source_digests = {
            row["path"]: row["sha256"]
            for row in self.lock["consumer"]["contract_sources"]
        }
        self.assertEqual(
            "a48f4bcb83ff80f7af42b1bc0247bfbca085976348a64d00833b00266b3adf65",
            source_digests[
                "apps/okf-explorer/src/lib/sources/largeCorpus.ts"
            ],
        )
        self.assertEqual(
            "468aeb19639d043d4838c906cfc230dea063309a42ab10d37d169c5acf27cf23",
            source_digests[
                "apps/okf-explorer/src/lib/search/queryPolicy.ts"
            ],
        )

    def test_v03_journeys_bind_candidate_and_exact_consumer(self) -> None:
        consumer = self.lock["consumer"]
        source_digests = {
            row["path"]: row["sha256"]
            for row in consumer["contract_sources"]
        }
        expected_consumer = {
            "consumer_lock": "../contracts/okf-explorer.consumer-lock.json",
            "package": consumer["name"],
            "release_tag": consumer["release_tag"],
            "version": consumer["version"],
            "source_commit": consumer["commit_sha"],
            "source_git_tree": consumer["git_tree"],
            "annotated_tag_object_sha": consumer[
                "annotated_tag_object_sha"
            ],
            "immutable_release_id": consumer["immutable_release"]["id"],
            "source_dirty": False,
            "dependency_lock_sha256": source_digests[
                "apps/okf-explorer/pnpm-lock.yaml"
            ],
            "runner_sha256": consumer["acceptance_executable_materials"][
                "runner"
            ]["sha256"],
            "wrapper_sha256": consumer["acceptance_executable_materials"][
                "wrapper"
            ]["sha256"],
            "invocation_lock_module_sha256": consumer[
                "acceptance_executable_materials"
            ]["invocation_lock_module"]["sha256"],
            "executable_materials": consumer[
                "acceptance_executable_materials"
            ],
            "build_manifest_sha256": consumer["executable_build"][
                "build_manifest_sha256"
            ],
        }
        descriptor = load_json(BUNDLE / "okf-explorer.json")
        expected_identity = {
            "schema": descriptor["schema"],
            "id": descriptor["@id"],
            "version": descriptor["version"],
            "snapshot": descriptor["snapshot"],
        }
        expected_partitions = {
            PRODUCT_JOURNEYS: ("product", 6),
            SEARCH_JOURNEYS: ("calibration", 26),
        }
        historical_manifests = {
            PRODUCT_JOURNEYS: (
                ROOT / "evaluation" / "explorer-v0.2.0-journeys.json"
            ),
            SEARCH_JOURNEYS: (
                ROOT
                / "evaluation"
                / "explorer-search-calibration-v0.2.0.json"
            ),
        }
        for path, (partition, count) in expected_partitions.items():
            manifest = load_json(path)
            self.assertEqual("okf-explorer-journeys.v1", manifest["schema"])
            self.assertEqual("okf-explorer.json", manifest["bundle_descriptor"])
            self.assertEqual(partition, manifest["suite_partition"])
            self.assertEqual(expected_identity, manifest["expected_identity"])
            self.assertEqual(expected_consumer, manifest["required_consumer"])
            self.assertEqual(
                LOCK_PATH.resolve(),
                (path.parent / manifest["required_consumer"]["consumer_lock"])
                .resolve(),
            )
            self.assertNotIn("receipt", manifest)
            journeys = manifest["journeys"]
            expected_journeys = load_json(historical_manifests[path])["journeys"]
            if path == PRODUCT_JOURNEYS:
                expected_journeys = json.loads(json.dumps(expected_journeys))
                translation = next(
                    row
                    for row in expected_journeys
                    if row["id"] == "translation-relationship"
                )
                expected_journeys.remove(translation)
                first_selected_record = next(
                    index
                    for index, row in enumerate(expected_journeys)
                    if row["id"]
                    == "deep-link-scalar-geography-and-caveat"
                )
                expected_journeys.insert(first_selected_record, translation)
                translation["assertions"] = [
                    {
                        "type": "text",
                        "selector": "body",
                        "includes": "translation of",
                    },
                    {
                        "type": "requested",
                        "includes": (
                            "/bundle/data/semantic/runtime-manifest.json"
                        ),
                    },
                    {
                        "type": "requested",
                        "includes": (
                            "/bundle/data/semantic/runtime/route-locator/"
                            "bucket-c5.json.gz"
                        ),
                    },
                    {
                        "type": "requested",
                        "includes": (
                            "/bundle/data/semantic/runtime/core/relationships-"
                        ),
                    },
                    {
                        "type": "not_requested",
                        "includes": "/bundle/data/explorer/adjacency/",
                    },
                    {"type": "console_clean"},
                ]
            elif path == SEARCH_JOURNEYS:
                expected_journeys = json.loads(json.dumps(expected_journeys))
                for journey in expected_journeys:
                    canonical_url = journey["runtime_expected_source_url"]
                    journey["actions"][1] = {
                        "type": "wait_for_ranked_result",
                        "canonical_url": canonical_url,
                    }
                    journey["actions"][2] = {
                        "type": "click",
                        "selector": (
                            "[data-okf-ranked-results=\"primary\"] "
                            "[data-okf-ranked-result]"
                            "[data-result-canonical-url=\""
                            f"{canonical_url}\"]"
                        ),
                    }
                q001 = next(
                    row
                    for row in expected_journeys
                    if row["id"] == "search-lr-q001"
                )
                manifest_request_index = next(
                    index
                    for index, row in enumerate(q001["assertions"])
                    if row
                    == {
                        "type": "requested",
                        "includes": (
                            "/bundle/data/explorer/search/manifest.json"
                        ),
                    }
                )
                q001["assertions"].insert(
                    manifest_request_index + 1,
                    {
                        "type": "requested",
                        "includes": (
                            "/bundle/data/explorer/search/entities.json"
                        ),
                    },
                )
            self.assertEqual(expected_journeys, journeys)
            self.assertEqual(count, len(journeys))
            self.assertEqual(len(journeys), len({row["id"] for row in journeys}))
            for journey in journeys:
                self.assertTrue(journey["actions"])
                self.assertTrue(journey["assertions"])
                self.assertLessEqual(
                    {row["type"] for row in journey["actions"]},
                    ACTION_TYPES,
                )
                self.assertLessEqual(
                    {row["type"] for row in journey["assertions"]},
                    ASSERTION_TYPES,
                )

    def test_v03_translation_journey_proves_current_route_runtime(self) -> None:
        journeys = load_json(PRODUCT_JOURNEYS)["journeys"]
        journey_ids = [row["id"] for row in journeys]
        translation_index = journey_ids.index("translation-relationship")
        self.assertLess(
            translation_index,
            journey_ids.index("deep-link-scalar-geography-and-caveat"),
        )
        self.assertLess(
            translation_index,
            journey_ids.index("selected-record-resource"),
        )

        translation = journeys[translation_index]
        requested = {
            row["includes"]
            for row in translation["assertions"]
            if row["type"] == "requested"
        }
        self.assertEqual(
            {
                "/bundle/data/semantic/runtime-manifest.json",
                (
                    "/bundle/data/semantic/runtime/route-locator/"
                    "bucket-c5.json.gz"
                ),
                "/bundle/data/semantic/runtime/core/relationships-",
            },
            requested,
        )
        self.assertIn(
            {
                "type": "not_requested",
                "includes": "/bundle/data/explorer/adjacency/",
            },
            translation["assertions"],
        )

    def test_v020_journey_manifests_remain_frozen(self) -> None:
        self.assertEqual(
            "f721e6c9915484d72ac38b3630926a0461ff7fe417f39651a1a8e79546968431",
            sha256(ROOT / "evaluation" / "explorer-v0.2.0-journeys.json"),
        )
        self.assertEqual(
            "b00936fcaee53cd481e307c8bf9279416b0d80ae91fefcba2d22b4591d709753",
            sha256(
                ROOT
                / "evaluation"
                / "explorer-search-calibration-v0.2.0.json"
            ),
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
        self.assertEqual(descriptor["version"], "0.3.0")
        self.assertEqual(compatibility_outcome(descriptor), "executable")
        self.assertNotIn("catalogue_search_manifest", descriptor["entrypoints"])
        runtime_search = descriptor["entrypoint_integrity"]["search_manifest"]
        self.assertEqual(
            descriptor["entrypoints"]["search_manifest"],
            runtime_search["path"],
        )
        self.assertEqual(sha256(BUNDLE / runtime_search["path"]), runtime_search["sha256"])

    def test_projection_rows_keep_governed_identity_and_rich_predicates(self) -> None:
        manifest = load_json(BUNDLE / "data" / "explorer" / "manifest.json")
        predicate_registry = load_json(
            BUNDLE / "data" / "semantic" / "predicate-registry.json"
        )
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
        predicates: dict[str, int] = {}
        for relationship in relationships:
            predicates[relationship["predicate"]] = (
                predicates.get(relationship["predicate"], 0) + 1
            )
            self.assertTrue(relationship["id"].startswith("https://"))
            for field in ("source_iri", "target_iri"):
                parsed = urlparse(relationship[field])
                self.assertIn(parsed.scheme, {"http", "https"})
                self.assertTrue(parsed.netloc)
            self.assertEqual("normalized", relationship["assertion_status"])
            self.assertEqual("real-world", relationship["assertion_scope"])
            self.assertEqual("derived", relationship["authority"]["class"])
            self.assertTrue(relationship["evidence"])
            self.assertIn("assertion", relationship["rights"])
        active_predicates = {
            row["iri"]: row["implementation"]["assertions_emitted"]
            for row in predicate_registry["predicates"]
            if row["implementation"]["state"] == "active-emitted"
        }
        self.assertEqual(
            predicate_registry["counts"]["active_emitted"], len(predicates)
        )
        self.assertEqual(
            predicate_registry["counts"]["assertions_emitted"],
            len(relationships),
        )
        self.assertEqual(active_predicates, predicates)
        self.assertEqual(
            sum(
                publisher["id"] != record["canonical_source_url"]
                for record in datasets
                for publisher in record["publishers"]
            ),
            predicates["http://purl.org/dc/terms/publisher"],
        )

    def test_rich_runtime_is_integrity_bound_and_targeted(self) -> None:
        descriptor = load_json(BUNDLE / "okf-explorer.json")
        runtime_reference = descriptor["entrypoints"]["relationship_runtime"]
        self.assertEqual(
            runtime_reference,
            descriptor["entrypoint_integrity"]["relationship_runtime"],
        )
        runtime_path = BUNDLE / runtime_reference["path"]
        self.assertEqual(runtime_reference["sha256"], sha256(runtime_path))
        runtime = load_json(runtime_path)
        self.assertEqual(
            "okf-rich-relationship-runtime-manifest.v1", runtime["schema"]
        )
        self.assertEqual(["core"], runtime["default_planes"])
        self.assertEqual(
            descriptor["counts"]["relationships"],
            runtime["totals"]["active_assertions"],
        )
        locator = load_json(BUNDLE / runtime["route_locator"]["path"])
        self.assertEqual(runtime["route_locator"]["sha256"], sha256(
            BUNDLE / runtime["route_locator"]["path"]
        ))
        publisher_route = next(
            row["route"]
            for reference in load_json(
                BUNDLE / "data" / "explorer" / "manifest.json"
            )["chunks"]["publishers"]
            for row in json.loads((BUNDLE / reference["path"]).read_text())
            if row["title"] == "HM Land Registry"
        )
        prefix = hashlib.sha256(publisher_route.encode("utf-8")).hexdigest()[:2]
        metadata = next(row for row in locator["buckets"] if row["bucket"] == prefix)
        bucket_path = BUNDLE / metadata["path"]
        self.assertEqual(metadata["sha256"], sha256(bucket_path))
        bucket = json.loads(gzip.decompress(bucket_path.read_bytes()))
        located = next(row for row in bucket["routes"] if row["route"] == publisher_route)
        self.assertGreater(located["planes"][0]["assertions"], 2_000)

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

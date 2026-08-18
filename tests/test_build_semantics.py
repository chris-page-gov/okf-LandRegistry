from __future__ import annotations

import ast
import base64
import copy
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path, PurePosixPath
from unittest import mock
from urllib.parse import urlparse

from scripts import build as builder
from scripts import python_runtime_contract as runtime_contract
from scripts.build import (
    ROOT,
    SEMANTIC_ASSERTION_SCHEMA_BYTES,
    SEMANTIC_ASSERTION_SCHEMA_SHA256,
    ai_usage_projection,
    canonical_https_url,
    ensure_https,
    load_ai_model_usage,
    load_build_config,
    load_cpsv_service_mappings,
    load_pinned_semantic_assertion_schema,
    load_publisher_registry,
    load_type_kind_crosswalk,
    normal_record,
    normalize_cddo,
    normalize_govuk,
    record_id_for,
    runtime_relationship_as_semantic,
    validate_relationship_assertions,
    validate_semantic_relationship_planes,
    write_search_and_shards,
)


RELATIONSHIP_ASSERTION_TYPE = (
    "https://chris-page-gov.github.io/okf-explorer/ns#RelationshipAssertion"
)


class BuildSemanticsTests(unittest.TestCase):
    _source_relationship_fixture: tuple[
        list[dict], dict, dict[str, dict], list[dict]
    ] | None = None

    @classmethod
    def source_relationship_fixture(
        cls,
    ) -> tuple[list[dict], dict, dict[str, dict], list[dict]]:
        if cls._source_relationship_fixture is None:
            snapshot_dir = builder.newest_snapshot()
            if snapshot_dir is None:
                raise AssertionError("frozen source snapshot is absent")
            discovered, _snapshot = builder.snapshot_records(snapshot_dir)
            composite_manifest = builder.load_composite_input_manifest(
                snapshot_dir.resolve()
            )
            content, _content_meta = builder.content_observation_records(
                composite_manifest
            )
            discovered.extend(content)
            curated, _curated_meta = builder.curated_records()
            sources, rights = builder.source_controls()
            discovered = [
                builder.govern_record(record, sources, rights)
                for record in discovered
            ]
            curated = [
                builder.govern_record(record, sources, rights)
                for record in curated
            ]
            records, _reconciliation = builder.merge_records(
                discovered, curated
            )
            bindings = builder.frozen_record_source_bindings(
                records, composite_manifest
            )
            cpsv_mappings = builder.load_cpsv_service_mappings(records)
            assertions = builder.semantic_relationship_assertions(
                builder.PUBLICATION_BASE,
                records,
                composite_manifest,
                cpsv_mappings,
            )
            cls._source_relationship_fixture = (
                records,
                composite_manifest,
                bindings,
                assertions,
            )
        return cls._source_relationship_fixture

    def cpsv_mapping_fixture(self) -> tuple[dict, list[dict]]:
        payload = json.loads(
            (ROOT / "source" / "cpsv-service-mappings.json").read_text()
        )
        records = json.loads(
            (ROOT / "bundle" / "data" / "catalogue.json").read_text()
        )["records"]
        return payload, records

    @staticmethod
    def refresh_assertion_id(assertion: dict) -> None:
        publication_base = builder.relationship_publication_base(assertion["@id"])
        assertion["@id"] = builder.relationship_assertion_id(
            publication_base,
            assertion["source"]["@id"],
            assertion["predicate"]["@id"],
            assertion["target"]["@id"],
            assertion_plane=assertion.get("assertion_plane", "core"),
            assertion_status=assertion.get("assertion_status", "normalized"),
            assertion_scope=assertion.get("assertion_scope", "real-world"),
        )

    @staticmethod
    def refresh_evidence_id(assertion: dict, ordinal: int = 0) -> None:
        publication_base = builder.relationship_publication_base(assertion["@id"])
        evidence = assertion["evidence"][ordinal]
        evidence["okf:evidenceResource"] = {
            "@id": builder.relationship_evidence_resource_id(
                publication_base, evidence
            )
        }
        evidence["@id"] = builder.relationship_evidence_id(
            publication_base,
            evidence,
            source_iri=assertion["source"]["@id"],
            predicate_iri=assertion["predicate"]["@id"],
            target_iri=assertion["target"]["@id"],
            assertion_plane=assertion.get("assertion_plane", "core"),
            assertion_status=assertion.get("assertion_status", "normalized"),
            assertion_scope=assertion.get("assertion_scope", "real-world"),
        )

    def validate_cpsv_fixture(
        self, payload: dict, records: list[dict]
    ) -> dict:
        real_load_json = builder.load_json

        def controlled_load(path: Path) -> dict:
            if Path(path) == builder.CPSV_SERVICE_MAPPING_PATH:
                return payload
            return real_load_json(path)

        with mock.patch.object(builder, "load_json", side_effect=controlled_load):
            return load_cpsv_service_mappings(records)

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

    def test_every_governed_publisher_row_has_canonical_classes(self) -> None:
        builder.load_publisher_registry_entries.cache_clear()
        registry = builder.load_publisher_registry_entries()
        self.assertEqual(27, len(registry))
        self.assertEqual(len(registry), len({row["id"] for row in registry.values()}))
        for row in registry.values():
            self.assertEqual(
                sorted(set(row["class_iris"])),
                row["class_iris"],
            )
            self.assertTrue(
                all(
                    class_iri.startswith(("http://", "https://"))
                    for class_iri in row["class_iris"]
                )
            )

    def test_reused_agent_identity_must_match_its_governed_source_field(self) -> None:
        fixtures = (
            (
                "organisation",
                "https://www.gov.uk/government/organisations/land-registry",
            ),
            ("repository-organisation", "https://github.com/LandRegistry"),
        )
        for source_native_type, identity in fixtures:
            with self.subTest(source_native_type=source_native_type):
                record = {
                    "record_id": "hmlr-" + "a" * 24,
                    "source_native_type": source_native_type,
                    "url": identity,
                }
                self.assertEqual(
                    identity,
                    builder.semantic_record_iri(builder.PUBLICATION_BASE, record),
                )
                route = builder.semantic_record_route(
                    builder.PUBLICATION_BASE, record
                )
                if source_native_type == "repository-organisation":
                    self.assertEqual(
                        "dataset/"
                        + builder.explorer_name("record", record["record_id"]),
                        route,
                    )
                    self.assertFalse(route.startswith("publisher/"))
                else:
                    self.assertEqual(
                        builder.semantic_route("publisher", identity), route
                    )
                record["url"] = identity + "/different"
                with self.assertRaisesRegex(
                    ValueError, "differs from its governed reused IRI"
                ):
                    builder.semantic_record_iri(builder.PUBLICATION_BASE, record)

    def test_stage1_routes_are_identity_bound_and_fail_closed(self) -> None:
        catalogue_id = builder.validate_stage1_identity(
            "IDF-CATALOGUE",
            builder.PUBLICATION_BASE + "id/catalogue/hmlr-public-estate",
        )
        expected = builder.semantic_route("catalogue", catalogue_id)
        self.assertEqual(
            expected,
            builder.validate_stage1_route(
                "IDF-CATALOGUE", catalogue_id, expected
            ),
        )
        with self.assertRaisesRegex(ValueError, "differs from Stage 1 family"):
            builder.validate_stage1_route(
                "IDF-CATALOGUE",
                catalogue_id,
                builder.semantic_route("catalogue", catalogue_id + "-different"),
            )

    def test_stage1_rule_and_plane_runtime_authority_fail_closed(self) -> None:
        stage1 = builder.load_stage1_semantic_authority()
        core_plane = builder.stage1_core_relationship_plane()
        self.assertEqual("PLANE-CORE", core_plane["id"])
        self.assertEqual(
            builder.RICH_RELATIONSHIP_PLANE_IRI, core_plane["iri"]
        )
        self.assertEqual(
            core_plane,
            builder.validate_stage1_assertion_plane(
                "core",
                "normalized",
                plane_iri=builder.RICH_RELATIONSHIP_PLANE_IRI,
            ),
        )
        with self.assertRaisesRegex(ValueError, "plane/status is outside Stage 1"):
            builder.validate_stage1_assertion_plane("core", "inferred")
        with self.assertRaisesRegex(ValueError, "plane/status is outside Stage 1"):
            builder.validate_stage1_assertion_plane(
                "core", "normalized", plane_iri="urn:okf:hmlr:plane:other"
            )

        relationship = stage1["active_relationships"][
            builder.PUBLISHER_PREDICATE
        ]
        rule = next(
            row
            for row in stage1["derivation_rules"].values()
            if relationship["id"] in row.get("relationship_type_refs", [])
        )
        self.assertEqual(
            rule,
            builder.validate_stage1_relationship_rule(
                builder.PUBLISHER_PREDICATE, rule["iri"]
            ),
        )
        with self.assertRaisesRegex(ValueError, "not an exact Stage 1 member"):
            builder.validate_stage1_identity(
                "IDF-RULE",
                builder.PUBLICATION_BASE + "id/rule/undeclared-v1",
                expected_role="runtime-control",
            )
        different_rule = next(
            row
            for row in stage1["derivation_rules"].values()
            if row.get("rule_role") == "relationship-derivation"
            and row["iri"] != rule["iri"]
        )
        with self.assertRaisesRegex(
            ValueError, "predicate and derivation rule differ from Stage 1"
        ):
            builder.validate_stage1_relationship_rule(
                builder.PUBLISHER_PREDICATE, different_rule["iri"]
            )

    def test_predicate_registry_is_an_exact_stage1_projection(self) -> None:
        self.assertEqual(
            builder.STAGE1_SEMANTIC_PROFILE_SHA256,
            builder.canonical_profile_sha256(),
        )
        self.assertEqual(
            builder.STAGE1_SEMANTIC_PROFILE_PACK_ROOT_SHA256,
            builder.profile_pack_root_sha256(),
        )
        stage1 = builder.load_stage1_semantic_authority()
        stage1_rows = {
            row["predicate_iri"]: row
            for row in stage1["relationship_types"].values()
        }
        assertions = [
            {"predicate": {"@id": predicate_iri}}
            for predicate_iri in stage1["active_relationships"]
        ]
        registry = builder.semantic_predicate_registry(
            assertions,
            "snapshot-test",
            "2026-08-11T00:00:00Z",
        )
        self.assertEqual("okf-predicate-registry.v2", registry["schema"])
        self.assertEqual(
            builder.PREDICATE_REGISTRY_V2_PROFILE_URL,
            registry["profile"],
        )
        self.assertEqual(
            {
                "predicates": 22,
                "active_emitted": 13,
                "authorised_zero_evidence": 9,
                "assertions_emitted": 13,
            },
            registry["counts"],
        )
        projected = {row["iri"]: row for row in registry["predicates"]}
        self.assertEqual(set(stage1_rows), set(projected))
        for predicate_iri, decision in stage1_rows.items():
            row = projected[predicate_iri]
            self.assertEqual(decision["label"], row["preferred_label"])
            self.assertEqual(decision["inverse_label"], row["inverse_label"])
            self.assertEqual(decision["description"], row["description"])
            self.assertEqual(decision["domain_class_iris"], row["domain"])
            self.assertEqual(decision["range_class_iris"], row["range"])
            self.assertEqual(
                decision["registry_evidence_policy"], row["evidence_policy"]
            )
            self.assertEqual(
                {
                    "iri": decision["vocabulary_iri"],
                    "version": decision["vocabulary_version"],
                },
                row["source_vocabulary"],
            )
            expected_emitted = (
                1
                if predicate_iri in stage1["active_relationships"]
                else 0
            )
            self.assertEqual(
                {
                    "state": decision["implementation_state"],
                    "assertions_emitted": expected_emitted,
                },
                row["implementation"],
            )
        material = {
            key: value
            for key, value in registry.items()
            if key != "root_sha256"
        }
        self.assertEqual(
            builder.sha256_bytes(builder.compact_canonical_json(material)),
            registry["root_sha256"],
        )

    def test_profile_pack_root_recomputes_every_member(self) -> None:
        original_repository_bytes = builder.repository_bytes
        changed_member = ROOT / "domain-profile" / "decision-register.md"

        def tampered_member(path: Path, **kwargs: object) -> bytes:
            payload = original_repository_bytes(path, **kwargs)
            if Path(path) == changed_member:
                return payload + b"\n"
            return payload

        with mock.patch.object(
            builder,
            "repository_bytes",
            side_effect=tampered_member,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "checksums do not exactly match every member",
            ):
                builder.profile_pack_root_sha256()

    def test_predicate_registry_rejects_emission_state_drift(self) -> None:
        stage1 = builder.load_stage1_semantic_authority()
        assertions = [
            {"predicate": {"@id": predicate_iri}}
            for predicate_iri in stage1["active_relationships"]
        ]
        with self.assertRaisesRegex(ValueError, "absent from the authorised"):
            builder.semantic_predicate_registry(
                [
                    *assertions,
                    {"predicate": {"@id": "https://example.test/undeclared"}},
                ],
                "snapshot-test",
                "2026-08-11T00:00:00Z",
            )
        with self.assertRaisesRegex(ValueError, "implementation state differs"):
            builder.semantic_predicate_registry(
                assertions[1:],
                "snapshot-test",
                "2026-08-11T00:00:00Z",
            )
        zero_iri = next(iter(stage1["zero_relationships"]))
        with self.assertRaisesRegex(ValueError, "implementation state differs"):
            builder.semantic_predicate_registry(
                [*assertions, {"predicate": {"@id": zero_iri}}],
                "snapshot-test",
                "2026-08-11T00:00:00Z",
            )

    def test_predicate_registry_v2_lock_is_exact_and_offline(self) -> None:
        builder.validate_predicate_registry_profile_lock.cache_clear()
        builder.load_predicate_registry_v2_validator.cache_clear()
        self.addCleanup(
            builder.validate_predicate_registry_profile_lock.cache_clear
        )
        self.addCleanup(
            builder.load_predicate_registry_v2_validator.cache_clear
        )
        receipt = builder.validate_predicate_registry_profile_lock()
        self.assertEqual("conformant", receipt["status"])
        self.assertEqual(2, receipt["file_count"])
        self.assertEqual(
            builder.PREDICATE_REGISTRY_V2_IDENTITY_SHA256,
            receipt["identity_sha256"],
        )
        self.assertEqual(
            builder.PREDICATE_REGISTRY_V2_LOCK_SHA256,
            receipt["lock_sha256"],
        )
        self.assertEqual(
            builder.PREDICATE_REGISTRY_V2_SCHEMA_SHA256,
            receipt["schema_sha256"],
        )
        self.assertFalse(receipt["network_resolution_allowed"])
        validator = builder.load_predicate_registry_v2_validator()
        self.assertIsInstance(validator, builder.Draft202012Validator)
        self.assertIs(
            validator,
            builder.load_predicate_registry_v2_validator(),
        )
        original_sha256_file = builder.sha256_file

        def tampered_lock_digest(path: Path) -> str:
            if path == builder.PREDICATE_REGISTRY_V2_LOCK_PATH:
                return "0" * 64
            return original_sha256_file(path)

        builder.validate_predicate_registry_profile_lock.cache_clear()
        with mock.patch.object(
            builder,
            "sha256_file",
            side_effect=tampered_lock_digest,
        ):
            with self.assertRaisesRegex(ValueError, "profile lock differs"):
                builder.validate_predicate_registry_profile_lock()
        builder.validate_predicate_registry_profile_lock.cache_clear()

    def test_frozen_build_boundary_clears_predicate_profile_caches(self) -> None:
        cached_functions = (
            builder.validate_predicate_registry_profile_lock,
            builder.load_predicate_registry_v2_validator,
        )
        for function in cached_functions:
            function.cache_clear()
            self.addCleanup(function.cache_clear)
        builder.validate_predicate_registry_profile_lock()
        builder.load_predicate_registry_v2_validator()
        self.assertTrue(
            all(function.cache_info().currsize == 1 for function in cached_functions)
        )
        builder._clear_build_input_caches()
        self.assertTrue(
            all(function.cache_info().currsize == 0 for function in cached_functions)
        )

    def test_source_predicate_registry_has_complete_v2_counts(self) -> None:
        _records, _manifest, _bindings, assertions = (
            self.source_relationship_fixture()
        )
        registry = builder.semantic_predicate_registry(
            assertions,
            "snapshot-test",
            "2026-08-11T00:00:00Z",
        )
        self.assertEqual(
            {
                "predicates": 22,
                "active_emitted": 13,
                "authorised_zero_evidence": 9,
                "assertions_emitted": 22_267,
            },
            registry["counts"],
        )
        self.assertEqual(
            22_267,
            sum(
                row["implementation"]["assertions_emitted"]
                for row in registry["predicates"]
            ),
        )
        self.assertEqual(
            6_694,
            len(
                {
                    route
                    for assertion in assertions
                    for route in (
                        assertion["source_route"],
                        assertion["target_route"],
                    )
                }
            ),
        )

    def test_predicate_registry_root_binds_non_predicate_material(self) -> None:
        stage1 = builder.load_stage1_semantic_authority()
        assertions = [
            {"predicate": {"@id": predicate_iri}}
            for predicate_iri in stage1["active_relationships"]
        ]
        registry = builder.semantic_predicate_registry(
            assertions,
            "snapshot-test",
            "2026-08-11T00:00:00Z",
        )
        altered = copy.deepcopy(registry)
        altered["snapshot"] = "different-snapshot"
        with self.assertRaisesRegex(ValueError, "complete canonical"):
            builder.validate_predicate_registry_v2_document(
                altered, assertions
            )

    def test_semantic_model_references_external_v2_registry_and_schema(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            paths = [output / f"resource-{ordinal}" for ordinal in range(10)]
            for path in paths:
                path.write_text("{}\n", encoding="utf-8")
            model = builder.semantic_model_descriptor(
                output,
                "https://example.test/",
                *paths,
            )
        self.assertEqual(
            {
                "path": "resource-3",
                "sha256": builder.sha256_bytes(b"{}\n"),
                "media_type": "application/json",
            },
            model["predicate_registry"],
        )
        predicate_schema = next(
            row
            for row in model["shapes"]
            if row["id"] == builder.PREDICATE_REGISTRY_V2_SCHEMA_ID
        )
        self.assertEqual("resource-4", predicate_schema["path"])
        self.assertEqual(
            "application/schema+json",
            predicate_schema["media_type"],
        )

    def test_semantic_contract_declares_new_delivery_outputs(self) -> None:
        contract = json.loads((ROOT / "okf.semantic.json").read_text())
        outputs = {
            row["path"]: row["role"]
            for row in contract["semantic_layer"]["outputs"]
        }
        self.assertEqual(
            {
                "bundle/data/explorer/search/entities.json": (
                    "search-entity-registry"
                ),
                "bundle/data/semantic/class-route-registry.json": (
                    "class-route-registry"
                ),
                "bundle/data/semantic/schemas/semantic-class-route-registry.schema.json": (
                    "class-route-registry-schema"
                ),
                "bundle/data/semantic/schemas/predicate-registry.v2.schema.json": (
                    "predicate-registry-v2-schema"
                ),
            },
            {
                path: outputs[path]
                for path in (
                    "bundle/data/explorer/search/entities.json",
                    "bundle/data/semantic/class-route-registry.json",
                    "bundle/data/semantic/schemas/semantic-class-route-registry.schema.json",
                    "bundle/data/semantic/schemas/predicate-registry.v2.schema.json",
                )
            },
        )
        extension = contract["semantic_layer"][
            "predicate_registry_extension"
        ]
        self.assertEqual("implemented", extension["status"])
        self.assertEqual("okf-explorer-v0.6.2", extension["required_consumer"])
        self.assertEqual(
            {
                "predicates": 22,
                "active_emitted": 13,
                "authorised_zero_evidence": 9,
            },
            extension["authoritative_stage1_counts"],
        )
        self.assertEqual(
            {
                "predicates": 22,
                "active_emitted": 13,
                "authorised_zero_evidence": 9,
                "assertions_emitted": 22_267,
            },
            extension["implementation_counts"],
        )
        self.assertEqual(
            [
                "schema",
                "profile",
                "snapshot",
                "generated_at",
                "predicates",
                "counts",
            ],
            extension["root_sha256_binding"]["canonical_material"],
        )
        self.assertEqual(
            builder.PREDICATE_REGISTRY_V2_SCHEMA_SHA256,
            extension["local_profile_lock"]["schema_sha256"],
        )

    def test_lr_class_route_sidecar_is_exact_and_fail_closed(self) -> None:
        semantic_document = {
            "@graph": [
                {
                    "@id": "https://example.test/id/a",
                    "@type": [
                        "https://schema.org/CreativeWork",
                        "http://www.w3.org/ns/prov#Entity",
                    ],
                    "route": "entity/a",
                },
                {
                    "@id": "https://example.test/id/b",
                    "@type": "http://purl.org/dc/terms/Location",
                    "route": "entity/b",
                },
            ]
        }
        iri_registry = builder.semantic_iri_route_registry(
            semantic_document, "snapshot-test"
        )
        registry = builder.semantic_class_route_registry(
            semantic_document, iri_registry, "snapshot-test"
        )
        self.assertEqual(2, registry["counts"]["entries"])
        self.assertEqual(
            sorted(registry["entries"][0]["class_iris"]),
            registry["entries"][0]["class_iris"],
        )
        self.assertEqual(
            builder.sha256_bytes(
                builder.compact_canonical_json(semantic_document)
            ),
            registry["source_plane_roots"]["semantic_graph_sha256"],
        )

        mismatched_route = json.loads(json.dumps(iri_registry))
        mismatched_route["entries"][0]["route"] = "entity/wrong"
        with self.assertRaisesRegex(ValueError, "authoritative IRI-route"):
            builder.semantic_class_route_registry(
                semantic_document, mismatched_route, "snapshot-test"
            )
        compact_type = json.loads(json.dumps(semantic_document))
        compact_type["@graph"][0]["@type"] = ["schema:CreativeWork"]
        with self.assertRaisesRegex(ValueError, "absolute HTTP"):
            builder.semantic_class_route_registry(
                compact_type, iri_registry, "snapshot-test"
            )

        duplicate_route = json.loads(json.dumps(semantic_document))
        duplicate_route["@graph"][1]["route"] = "entity/a"
        with self.assertRaisesRegex(ValueError, "more than one identity"):
            builder.semantic_iri_route_registry(
                duplicate_route, "snapshot-test"
            )

    def test_assertion_identity_separates_status_scope_and_plane(self) -> None:
        triple = (
            "https://example.test/source",
            builder.PUBLISHER_PREDICATE,
            "https://example.test/target",
        )
        identities = {
            builder.relationship_assertion_id(
                builder.PUBLICATION_BASE,
                *triple,
                assertion_plane=plane,
                assertion_status=status,
                assertion_scope=scope,
            )
            for plane, status, scope in (
                ("core", "normalized", "real-world"),
                ("historical", "normalized", "real-world"),
                ("core", "inferred", "real-world"),
                ("core", "normalized", "presentation"),
            )
        }
        self.assertEqual(4, len(identities))

    def test_evidence_resources_are_reusable_but_bindings_are_assertion_scoped(
        self,
    ) -> None:
        evidence = {
            "type": "governed-normalisation-input",
            "url": "https://www.gov.uk/example",
            "resource": "https://www.gov.uk/example",
            "source_artifact": "source/example.json",
            "source_sha256": "1" * 64,
            "source_field": "results[0]",
            "source_value_sha256": "2" * 64,
            "source_value_hash_canonicalization": (
                builder.CPSV_SOURCE_VALUE_CANONICALIZATION
            ),
            "locator": "results[0]",
            "normalization": builder.PUBLICATION_BASE + "id/rule/test-v1",
            "retrieved_at": "2026-08-11T00:00:00Z",
        }

        def bind(source_iri: str, target_iri: str) -> dict:
            return builder.bind_relationship_evidence(
                builder.PUBLICATION_BASE,
                evidence,
                source_iri=source_iri,
                predicate_iri=builder.SOURCE_PREDICATE,
                target_iri=target_iri,
                role="record-projection",
                record_id="hmlr-" + "a" * 24,
                assertion_plane="core",
                assertion_status="normalized",
                assertion_scope="real-world",
            )

        first = bind(
            "https://example.test/source-a",
            "https://example.test/target-a",
        )
        second = bind(
            "https://example.test/source-b",
            "https://example.test/target-b",
        )
        self.assertEqual(
            first["okf:evidenceResource"],
            second["okf:evidenceResource"],
        )
        self.assertNotEqual(first["@id"], second["@id"])
        self.assertEqual(
            sorted(builder.stage1_entity_type_classes("TYPE-EVIDENCE-BINDING")),
            first["@type"],
        )
        resource_node = builder.relationship_evidence_resource_node(
            builder.PUBLICATION_BASE, first
        )
        self.assertEqual(
            sorted(builder.stage1_entity_type_classes("TYPE-EVIDENCE-RESOURCE")),
            resource_node["@type"],
        )

        changed_locator = copy.deepcopy(evidence)
        changed_locator["locator"] = "results[1]"
        changed_locator["source_field"] = "results[1]"
        changed_value = copy.deepcopy(evidence)
        changed_value["source_value_sha256"] = "3" * 64
        self.assertEqual(
            3,
            len(
                {
                    builder.relationship_evidence_resource_id(
                        builder.PUBLICATION_BASE, candidate
                    )
                    for candidate in (evidence, changed_locator, changed_value)
                }
            ),
        )

    def test_evidence_binding_classes_and_resource_reference_fail_closed(
        self,
    ) -> None:
        assertion = builder.normalized_relationship_assertion(
            builder.PUBLICATION_BASE,
            source_iri="https://example.test/source",
            predicate_iri=builder.SOURCE_PREDICATE,
            target_iri="https://example.test/target",
            source_route="entity/source",
            target_route="entity/target",
            observed_at="2026-08-11T00:00:00Z",
            evidence_url="https://www.gov.uk/example",
            source_artifact="source/example.json",
            source_sha256="1" * 64,
            source_field="results[0]",
            source_value={"id": "example"},
            locator="results[0]",
            record_id="hmlr-" + "a" * 24,
        )
        builder.validate_relationship_assertions([assertion])

        wrong_type = copy.deepcopy(assertion)
        wrong_type["evidence"][0]["@type"] = [
            "https://example.test/WrongBinding"
        ]
        with self.assertRaisesRegex(ValueError, "EvidenceBinding classes"):
            builder.validate_relationship_assertions([wrong_type])

        missing_reference = copy.deepcopy(assertion)
        del missing_reference["evidence"][0]["okf:evidenceResource"]
        with self.assertRaisesRegex(ValueError, "EvidenceResource reference"):
            builder.validate_relationship_assertions([missing_reference])

        swapped_reference = copy.deepcopy(assertion)
        swapped_reference["evidence"][0]["okf:evidenceResource"] = {
            "@id": builder.PUBLICATION_BASE
            + "id/evidence-resource/"
            + "f" * 32
        }
        with self.assertRaisesRegex(ValueError, "EvidenceResource reference"):
            builder.validate_relationship_assertions([swapped_reference])

    def test_search_entity_shard_exposes_governed_publisher_label(self) -> None:
        publisher = builder.load_publisher_registry_entries()[
            "HM Land Registry"
        ]
        filter_value = builder.explorer_name("publisher", publisher["id"])
        datasets = [
            {
                "ordinal": 0,
                "title": "Example Land Registry guidance",
                "publisher": filter_value,
                "publisher_title": "HM Land Registry",
                "publisher_id": publisher["id"],
                "open": "dataset/example",
            }
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            facets_path = output / "data" / "explorer" / "facets.json"
            result_path = output / "data" / "explorer" / "datasets-000.json"
            builder.write_json(facets_path, {})
            builder.write_json(result_path, datasets)
            projection = builder.write_explorer_search(
                output,
                datasets,
                facets_path,
                [builder.explorer_reference(output, result_path)],
                "snapshot-test",
            )
            entities = json.loads(
                (output / projection["entities"]["path"]).read_text()
            )
            manifest = json.loads(
                (output / projection["manifest"]["path"]).read_text()
            )
            metadata = json.loads(
                (
                    output
                    / "data"
                    / "explorer"
                    / "search"
                    / "shards.json"
                ).read_text()
            )
        self.assertEqual("okf-static-search-entities.v1", entities["schema"])
        self.assertEqual(
            [
                {
                    "id": publisher["id"],
                    "label": "HM Land Registry",
                    "kind": "organisation",
                    "filter_key": "publisher",
                    "filter_value": filter_value,
                    "count": 1,
                    "route": f"publisher/{filter_value}",
                }
            ],
            entities["entities"],
        )
        self.assertEqual(
            projection["entities"]["path"],
            manifest["entrypoints"]["entities"],
        )
        self.assertIn(
            projection["entities"]["path"],
            {
                reference["path"]
                for reference in metadata["shards"]["support"]
            },
        )

    def test_explorer_search_policy_and_component_tokens_are_in_parity(
        self,
    ) -> None:
        contract = json.loads(
            (ROOT / "pages" / "search-contract.json").read_text(
                encoding="utf-8"
            )
        )
        policy = builder.explorer_query_policy(contract)
        self.assertEqual(2, contract["token_min_length"])
        self.assertEqual(
            {
                "schema": "okf-search-query-policy.v1",
                "tokeniser": (
                    "nfkd-lowercase-ascii-alphanumeric-component-v1"
                ),
                "stopwords": contract["stopwords"],
                "minimum_should_match": {
                    "apply_from_query_tokens": 3,
                    "minimum_matches": 2,
                    "ratio_numerator": 3,
                    "ratio_denominator": 10,
                },
            },
            policy,
        )
        policy["stopwords"].append("zzzz")
        policy["minimum_should_match"]["minimum_matches"] = 99
        self.assertNotIn("zzzz", contract["stopwords"])
        self.assertEqual(2, contract["minimum_should_match"]["minimum_matches"])
        policy = builder.explorer_query_policy(contract)
        self.assertEqual(
            ["cafe", "data", "paid", "price", "records"],
            builder.explorer_worker_tokens(
                "Café price-paid data-and records x",
                set(policy["stopwords"]),
                contract["token_min_length"],
            ),
        )
        self.assertNotIn(
            "price-paid",
            builder.explorer_worker_tokens(
                "price-paid",
                set(policy["stopwords"]),
                contract["token_min_length"],
            ),
        )
        self.assertEqual(
            [],
            builder.explorer_worker_tokens(
                "a\u1ab0b",
                set(policy["stopwords"]),
                contract["token_min_length"],
            ),
            "combining marks outside U+0300–U+036F remain component breaks",
        )

        publisher = builder.load_publisher_registry_entries()[
            "HM Land Registry"
        ]
        filter_value = builder.explorer_name("publisher", publisher["id"])
        datasets = [
            {
                "ordinal": 0,
                "title": "Price-paid data-and records",
                "publisher": filter_value,
                "publisher_title": "HM Land Registry",
                "publisher_id": publisher["id"],
                "open": "dataset/example",
            }
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            facets_path = output / "data" / "explorer" / "facets.json"
            result_path = output / "data" / "explorer" / "datasets-000.json"
            builder.write_json(facets_path, {})
            builder.write_json(result_path, datasets)
            projection = builder.write_explorer_search(
                output,
                datasets,
                facets_path,
                [builder.explorer_reference(output, result_path)],
                "snapshot-test",
            )
            manifest = json.loads(
                (output / projection["manifest"]["path"]).read_text()
            )
            indexed_tokens = {
                row["token"]
                for path in set(manifest["entrypoints"]["lexicon"].values())
                for row in json.loads((output / path).read_text())
            }
        self.assertEqual(2, manifest["token_min_length"])
        self.assertEqual(policy, manifest["query_policy"])
        self.assertTrue({"price", "paid", "data", "records"} <= indexed_tokens)
        self.assertNotIn("price-paid", indexed_tokens)
        self.assertNotIn("and", indexed_tokens)

    def test_explorer_query_policy_rejects_contract_drift(self) -> None:
        contract = json.loads(
            (ROOT / "pages" / "search-contract.json").read_text(
                encoding="utf-8"
            )
        )
        mutations = [
            ("schema", "unsupported", "source schema"),
            ("token_pattern", "[a-z]+", "token_pattern"),
            ("token_min_length", 1, "token_min_length"),
        ]
        for field, value, error in mutations:
            with self.subTest(field=field):
                changed = copy.deepcopy(contract)
                changed[field] = value
                with self.assertRaisesRegex(ValueError, error):
                    builder.explorer_query_policy(changed)

        invalid_stopwords = [
            ["the", "and"],
            ["And", "the"],
            ["a" * 33],
            [f"word{index:03d}" for index in range(257)],
        ]
        for stopwords in invalid_stopwords:
            with self.subTest(stopwords=len(stopwords)):
                changed = copy.deepcopy(contract)
                changed["stopwords"] = stopwords
                with self.assertRaisesRegex(ValueError, "stopwords"):
                    builder.explorer_query_policy(changed)

        for numerator, denominator in ((0, 10), (11, 10)):
            with self.subTest(ratio=(numerator, denominator)):
                changed = copy.deepcopy(contract)
                changed["minimum_should_match"]["ratio_numerator"] = numerator
                changed["minimum_should_match"]["ratio_denominator"] = denominator
                with self.assertRaisesRegex(ValueError, "ratio"):
                    builder.explorer_query_policy(changed)

    def test_search_indexes_every_governed_record_publisher(self) -> None:
        snapshot_dir = builder.newest_snapshot()
        self.assertIsNotNone(snapshot_dir)
        discovered, _snapshot = builder.snapshot_records(snapshot_dir)
        composite_manifest = builder.load_composite_input_manifest(
            snapshot_dir.resolve()
        )
        content, _content_meta = builder.content_observation_records(
            composite_manifest
        )
        discovered.extend(content)
        curated, _curated_meta = builder.curated_records()
        sources, rights = builder.source_controls()
        discovered = [
            builder.govern_record(record, sources, rights)
            for record in discovered
        ]
        curated = [
            builder.govern_record(record, sources, rights)
            for record in curated
        ]
        records, _reconciliation = builder.merge_records(discovered, curated)
        datasets = [
            {
                "ordinal": ordinal,
                "title": record["title"],
                "publisher": builder.explorer_name(
                    "publisher", record["publisher_id"]
                ),
                "publisher_title": record["publisher"],
                "publisher_id": record["publisher_id"],
                "publishers": copy.deepcopy(record["publishers"]),
                "open": f"dataset/test-{ordinal}",
            }
            for ordinal, record in enumerate(records)
        ]
        expected_ordinals: dict[str, list[int]] = {}
        publisher_titles: dict[str, str] = {}
        for dataset in datasets:
            for publisher in dataset["publishers"]:
                filter_value = builder.explorer_name(
                    "publisher", publisher["id"]
                )
                expected_ordinals.setdefault(filter_value, []).append(
                    dataset["ordinal"]
                )
                publisher_titles[publisher["id"]] = publisher["name"]

        self.assertEqual(27, len(expected_ordinals))
        self.assertEqual(
            21,
            sum(len(dataset["publishers"]) > 1 for dataset in datasets),
        )
        self.assertEqual(
            45,
            sum(len(dataset["publishers"]) - 1 for dataset in datasets),
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            facets_path = output / "data" / "explorer" / "facets.json"
            result_path = output / "data" / "explorer" / "datasets-000.json"
            builder.write_json(facets_path, {})
            builder.write_json(result_path, datasets)
            projection = builder.write_explorer_search(
                output,
                datasets,
                facets_path,
                [builder.explorer_reference(output, result_path)],
                "snapshot-test",
            )
            publisher_filter = json.loads(
                (
                    output
                    / "data"
                    / "explorer"
                    / "search"
                    / "filters"
                    / "publisher.json"
                ).read_text()
            )
            entities = json.loads(
                (output / projection["entities"]["path"]).read_text()
            )
            search_manifest = json.loads(
                (output / projection["manifest"]["path"]).read_text()
            )
            revenue_lexicon = json.loads(
                (
                    output
                    / search_manifest["entrypoints"]["lexicon"]["re"]
                ).read_text()
            )
            revenue_reference = next(
                row["postings"]
                for row in revenue_lexicon
                if row["token"] == "revenue"
            )
            revenue_postings = json.loads(
                (output / revenue_reference).read_text()
            )["tokens"]["revenue"]

        self.assertEqual(expected_ordinals, publisher_filter["values"])
        self.assertEqual(27, len(entities["entities"]))
        governed_publisher_ids = {
            row["id"]
            for row in builder.load_publisher_registry_entries().values()
        }
        self.assertEqual(
            governed_publisher_ids,
            set(publisher_titles),
        )
        self.assertEqual(
            governed_publisher_ids,
            {row["id"] for row in entities["entities"]},
        )
        self.assertEqual(
            {
                publisher_id: len(
                    expected_ordinals[
                        builder.explorer_name("publisher", publisher_id)
                    ]
                )
                for publisher_id in publisher_titles
            },
            {row["id"]: row["count"] for row in entities["entities"]},
        )

        revenue_id = builder.load_publisher_registry_entries()[
            "HM Revenue & Customs"
        ]["id"]
        primary_ids = {dataset["publisher_id"] for dataset in datasets}
        self.assertNotIn(revenue_id, primary_ids)
        revenue_filter = builder.explorer_name("publisher", revenue_id)
        self.assertEqual(2, len(expected_ordinals[revenue_filter]))
        revenue_rows = {
            row[0]: row for row in revenue_postings if row[0] in expected_ordinals[
                revenue_filter
            ]
        }
        self.assertEqual(set(expected_ordinals[revenue_filter]), set(revenue_rows))
        self.assertTrue(
            all(row[2] & 2 for row in revenue_rows.values()),
            "secondary-only publisher matches must retain the publisher mask",
        )

        inconsistent = copy.deepcopy(
            next(dataset for dataset in datasets if len(dataset["publishers"]) > 1)
        )
        inconsistent["publishers"][1]["id"] += "-wrong"
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            facets_path = output / "data" / "explorer" / "facets.json"
            result_path = output / "data" / "explorer" / "datasets-000.json"
            builder.write_json(facets_path, {})
            builder.write_json(result_path, [inconsistent])
            with self.assertRaisesRegex(
                ValueError,
                "search publisher entity differs from its governed registry",
            ):
                builder.write_explorer_search(
                    output,
                    [inconsistent],
                    facets_path,
                    [builder.explorer_reference(output, result_path)],
                    "snapshot-test",
                )

    def test_activity_identity_is_input_bound_not_time_or_order_bound(self) -> None:
        first_digest = "1" * 64
        second_digest = "2" * 64
        rule = next(
            row["iri"]
            for row in builder.load_stage1_semantic_authority()[
                "derivation_rules"
            ].values()
            if row["rule_role"] == "source-observation"
        )
        first_iri, first = builder.governed_activity_identity(
            builder.PUBLICATION_BASE,
            activity_kind="source-observation",
            rule_iri=rule,
            input_sha256s=[first_digest, second_digest],
            coordinate={"observed_at": "2026-07-29T00:00:00Z"},
        )
        reordered_iri, reordered = builder.governed_activity_identity(
            builder.PUBLICATION_BASE,
            activity_kind="source-observation",
            rule_iri=rule,
            input_sha256s=[second_digest, first_digest],
            coordinate={"observed_at": "2026-08-10T00:00:00Z"},
        )
        changed_iri, _changed = builder.governed_activity_identity(
            builder.PUBLICATION_BASE,
            activity_kind="source-observation",
            rule_iri=rule,
            input_sha256s=["3" * 64, second_digest],
            coordinate={"observed_at": "2026-07-29T00:00:00Z"},
        )
        self.assertEqual(first_iri, reordered_iri)
        self.assertEqual(first["input_sha256s"], reordered["input_sha256s"])
        self.assertNotEqual(first["coordinate"], reordered["coordinate"])
        self.assertNotEqual(first_iri, changed_iri)

    def test_retained_native_type_closure_rejects_unmatched_types(self) -> None:
        governed_types = sorted(
            builder.load_stage1_semantic_authority()[
                "class_decisions_by_native_type"
            ]
        )
        records = [
            {"source_native_type": source_native_type}
            for source_native_type in governed_types
        ]
        receipt = builder.validate_stage1_retained_native_type_closure(records)
        self.assertEqual(len(governed_types), receipt["retained_source_native_types"])
        with self.assertRaisesRegex(ValueError, "differ from retained record types"):
            builder.validate_stage1_retained_native_type_closure(
                [*records, {"source_native_type": "unmatched-retained-type"}]
            )
        with self.assertRaisesRegex(ValueError, "differ from retained record types"):
            builder.validate_stage1_retained_native_type_closure(records[1:])

    def test_govuk_organisation_publishers_are_exhaustive_and_governed(
        self,
    ) -> None:
        payload = json.loads(
            (
                ROOT
                / "source"
                / "snapshots"
                / "2026-07-29T091915Z"
                / "govuk-search.json"
            ).read_text()
        )
        results = payload["results"]
        organisation_rows = [
            organisation
            for result in results
            for organisation in result["organisations"]
        ]
        registry = load_publisher_registry()
        self.assertEqual(1_866, len(results))
        self.assertEqual(1_910, len(organisation_rows))
        self.assertEqual(
            20,
            sum(len(result["organisations"]) > 1 for result in results),
        )
        self.assertEqual(
            44,
            sum(len(result["organisations"]) - 1 for result in results),
        )
        self.assertEqual(
            16,
            sum(
                any(
                    organisation["title"] == "HM Land Registry"
                    for organisation in result["organisations"][1:]
                )
                for result in results
            ),
        )
        self.assertEqual(27, len(registry))
        self.assertTrue(all(
            registry[organisation["title"]]
            == "https://www.gov.uk" + organisation["link"]
            for organisation in organisation_rows
        ))

        example = copy.deepcopy(results[0])
        example["organisations"].append(copy.deepcopy(example["organisations"][0]))
        with self.assertRaisesRegex(ValueError, "identity collision"):
            normalize_govuk(example, "2026-07-29T09:19:15Z")

        unsafe = copy.deepcopy(results[0])
        unsafe["organisations"][0]["link"] += "?credential=secret"
        with self.assertRaisesRegex(ValueError, "incomplete or unsafe"):
            normalize_govuk(unsafe, "2026-07-29T09:19:15Z")

    def test_public_urls_fail_closed_before_projection(self) -> None:
        self.assertEqual(
            "https://www.gov.uk/example?lang=en",
            ensure_https("https://www.gov.uk/example?lang=en"),
        )
        self.assertEqual(
            "https://example.test/path",
            canonical_https_url("https://example.test/path"),
        )
        rejected = (
            " https://www.gov.uk/example",
            "https://www.gov.uk/example ",
            "https://www.gov.uk/path with space",
            "https://www.gov.uk/path\tsegment",
            'https://www.gov.uk/a"b',
            "https://www.gov.uk/a'b",
            "https://www.gov.uk/a<b",
            "https://www.gov.uk/a>b",
            "https://www.gov.uk/a\\b",
            "https://www.gov.uk/a^b",
            "https://www.gov.uk/a`b",
            "https://www.gov.uk/a|b",
            "https://www.gov.uk/a{b}",
            "https://www.gov.uk/a%",
            "https://www.gov.uk/a%0",
            "https://www.gov.uk/a%GG",
            "https://user:pass@www.gov.uk/example",
            "https://@www.gov.uk/example",
            "http://www.gov.uk/example",
            "ftp://www.gov.uk/example",
            "//www.gov.uk/example",
            "https:///missing-host",
            "https://www.gov.uk:/example",
            "https://www.gov.uk:0/example",
            "https://www.gov.uk:65536/example",
            "https://www.gov.uk:not-a-port/example",
        )
        for value in rejected:
            with self.subTest(value=value), self.assertRaises(ValueError):
                ensure_https(value)

    def test_only_explicit_cddo_path_templates_are_percent_encoded(self) -> None:
        template = (
            "https://businessgateway.landregistry.gov.uk/bg2/api/v2/titles/"
            "{title_number}/official-copies/availability"
        )
        encoded = (
            "https://businessgateway.landregistry.gov.uk/bg2/api/v2/titles/"
            "%7Btitle_number%7D/official-copies/availability"
        )
        self.assertEqual(
            encoded,
            ensure_https(template, allow_cddo_path_template=True),
        )
        with self.assertRaisesRegex(ValueError, "unsafe delimiter"):
            ensure_https(template)
        invalid_templates = (
            "https://www.gov.uk/{title_number}",
            "https://businessgateway.landregistry.gov.uk/a/prefix-{title_number}",
            "https://businessgateway.landregistry.gov.uk/a/{title-number}",
            "https://businessgateway.landregistry.gov.uk/a?title={title_number}",
        )
        for value in invalid_templates:
            with self.subTest(value=value), self.assertRaises(ValueError):
                ensure_https(value, allow_cddo_path_template=True)
        record = normalize_cddo(
            {
                "name": "Official Copies Availability",
                "description": "Restricted API metadata.",
                "url": template,
            },
            "2026-07-29T09:19:15Z",
        )
        self.assertEqual(encoded, record["canonical_source_url"])
        self.assertEqual([encoded], record["source_urls"])

    def test_cpsv_mapping_accepts_the_current_reviewed_evidence(self) -> None:
        payload, records = self.cpsv_mapping_fixture()
        result = self.validate_cpsv_fixture(payload, records)
        self.assertEqual(11, result["receipt"]["candidate_count"])
        self.assertEqual(7, result["receipt"]["mapped_count"])
        self.assertEqual(19, result["receipt"]["evidence_count"])

        organisation = next(
            row for row in payload["evidence"] if row["id"] == "CPSV-E-ORG-HMLR"
        )
        publisher_path = ROOT / "source" / "publisher-registry.json"
        publisher_document = json.loads(publisher_path.read_text())
        publisher = next(
            row
            for row in publisher_document["publishers"]
            if row["id"]
            == "https://www.gov.uk/government/organisations/land-registry"
        )
        self.assertEqual(
            "source/publisher-registry.json", organisation["source_artifact"]
        )
        self.assertEqual(builder.sha256_file(publisher_path), organisation["source_sha256"])
        self.assertEqual(
            builder.sha256_bytes(builder.compact_canonical_json(publisher)),
            organisation["source_value_sha256"],
        )
        self.assertIn(
            "http://data.europa.eu/m8g/PublicOrganisation",
            publisher["class_iris"],
        )

    def test_fresh_cpsv_projection_preserves_the_zero_atu_boundary(self) -> None:
        records, _manifest, _bindings, assertions = (
            self.source_relationship_fixture()
        )
        mappings = builder.load_cpsv_service_mappings(records)
        semantic_document = builder.jsonld_projection(
            builder.PUBLICATION_BASE,
            {"observed_at": "2026-07-29T09:19:15Z"},
            records,
            assertions,
            mappings,
            builder.load_build_config(),
        )

        def validate(document: dict) -> dict:
            return builder.validate_cpsv_ap_projection(
                document,
                records,
                assertions,
                mappings,
                builder.validate_cpsv_ap_vendor_lock(),
                builder.PUBLICATION_BASE,
            )

        receipt = validate(semantic_document)
        self.assertEqual(
            0, receipt["counts"]["administrative_territorial_units"]
        )
        self.assertEqual(
            "passed-local-subset",
            receipt["bounded_projection_checks"][
                "public_organisation_spatial_location"
            ],
        )
        self.assertEqual(
            "not-claimed-authorised-zero-evidence",
            receipt["official_profile_boundaries"][
                "public_organisation_spatial_atu_range"
            ],
        )
        self.assertEqual(
            "not-run", receipt["official_shacl_execution"]["status"]
        )
        nodes = {
            node["@id"]: node for node in semantic_document["@graph"]
        }
        jurisdiction_id = builder.semantic_jurisdiction_iri(
            builder.PUBLICATION_BASE, "England and Wales"
        )
        location_classes = set(
            builder.stage1_jurisdiction_registry()["England and Wales"][
                "class_iris"
            ]
        )
        atu_classes = set(
            builder.stage1_authorised_zero_entity_type_classes(
                "TYPE-ATU-TYPE"
            )
        )
        self.assertEqual(location_classes, set(nodes[jurisdiction_id]["@type"]))
        self.assertFalse(
            any(
                atu_classes & set(node.get("@type", []))
                for node in semantic_document["@graph"]
            )
        )

        wrong_target = copy.deepcopy(semantic_document)
        wrong_target_nodes = {
            node["@id"]: node for node in wrong_target["@graph"]
        }
        other_jurisdiction = builder.semantic_jurisdiction_iri(
            builder.PUBLICATION_BASE,
            "England and Wales, limited to migrated local authorities",
        )
        wrong_target_nodes[builder.HMLR_PUBLISHER_IRI][
            builder.SPATIAL_PREDICATE
        ] = {"@id": other_jurisdiction}
        with self.assertRaisesRegex(ValueError, "governed England and Wales"):
            validate(wrong_target)

        extra_class = copy.deepcopy(semantic_document)
        extra_class_nodes = {
            node["@id"]: node for node in extra_class["@graph"]
        }
        additional_active_class = builder.stage1_entity_type_classes(
            "TYPE-CATALOGUE"
        )[0]
        self.assertNotIn(additional_active_class, location_classes)
        extra_class_nodes[jurisdiction_id]["@type"].append(
            additional_active_class
        )
        with self.assertRaisesRegex(ValueError, "classes differ"):
            validate(extra_class)

        conflated = copy.deepcopy(semantic_document)
        conflated["@graph"].append(
            {
                "@id": builder.PUBLICATION_BASE + "id/jurisdiction/england",
                "@type": sorted(atu_classes),
            }
        )
        with self.assertRaisesRegex(
            ValueError, "authorised-zero-evidence state"
        ):
            validate(conflated)

    def test_cpsv_projection_validation_precedes_swap_slot_reservation(self) -> None:
        snapshot_dir = builder.newest_snapshot()
        self.assertIsNotNone(snapshot_dir)
        self.addCleanup(builder._clear_build_input_caches)
        with tempfile.TemporaryDirectory() as temporary_directory:
            previous_output = Path(temporary_directory) / "previous-bundle"
            with (
                mock.patch.object(
                    builder,
                    "python_runtime_receipt",
                    return_value={"test": "governed-runtime-placeholder"},
                ),
                mock.patch.object(
                    builder,
                    "validate_cpsv_ap_projection",
                    side_effect=ValueError("sentinel CPSV preflight failure"),
                ) as cpsv_preflight,
                mock.patch.object(
                    builder, "bundle_publication_transaction"
                ) as publication_transaction,
            ):
                with self.assertRaisesRegex(
                    ValueError, "sentinel CPSV preflight failure"
                ):
                    builder.build(
                        snapshot_dir=snapshot_dir,
                        output_dir=ROOT / "bundle",
                        publication_base=builder.PUBLICATION_BASE,
                        replace=True,
                        previous_output=previous_output,
                    )
            cpsv_preflight.assert_called_once()
            publication_transaction.assert_not_called()

    def test_cpsv_decision_ids_and_times_are_governed(self) -> None:
        payload, records = self.cpsv_mapping_fixture()
        payload["decisions"][1]["id"] = payload["decisions"][0]["id"]
        with self.assertRaisesRegex(ValueError, "decision IDs must be unique"):
            self.validate_cpsv_fixture(payload, records)

        payload, records = self.cpsv_mapping_fixture()
        payload["decisions"][0]["reviewed_at"] = "2026-02-30T12:00:00Z"
        with self.assertRaisesRegex(ValueError, "not a valid UTC timestamp"):
            self.validate_cpsv_fixture(payload, records)

    def test_cpsv_evidence_must_bind_the_exact_decision_record_and_claim(self) -> None:
        payload, records = self.cpsv_mapping_fixture()
        evidence = next(
            row
            for row in payload["evidence"]
            if row["id"] == "CPSV-E-SVC-PROPERTY-INFORMATION"
        )
        evidence["record_id"] = "hmlr-1beb6fbc5637a9b82e1a2981"
        with self.assertRaisesRegex(ValueError, "does not bind decision record"):
            self.validate_cpsv_fixture(payload, records)

        payload, records = self.cpsv_mapping_fixture()
        evidence = next(
            row
            for row in payload["evidence"]
            if row["id"] == "CPSV-E-EXC-LINKED-DATA"
        )
        evidence["claim_supported"] = "public-service-classification"
        with self.assertRaisesRegex(ValueError, "wrong evidence claim"):
            self.validate_cpsv_fixture(payload, records)

    def test_cpsv_mapped_decisions_require_three_separate_evidence_roles(self) -> None:
        payload, records = self.cpsv_mapping_fixture()
        mapped = next(
            row for row in payload["decisions"] if row["decision"] == "mapped"
        )
        mapped["competent_authority"]["evidence_refs"] = [
            ref
            for ref in mapped["competent_authority"]["evidence_refs"]
            if ref != "CPSV-E-ORG-HMLR"
        ]
        with self.assertRaisesRegex(ValueError, "separate delivery and public-organisation"):
            self.validate_cpsv_fixture(payload, records)

        payload, records = self.cpsv_mapping_fixture()
        organisation = next(
            row for row in payload["evidence"] if row["id"] == "CPSV-E-ORG-HMLR"
        )
        organisation["issuer"] = "https://www.gov.uk/"
        with self.assertRaisesRegex(ValueError, "declared authority"):
            self.validate_cpsv_fixture(payload, records)

    def test_cpsv_evidence_governance_fields_fail_closed(self) -> None:
        mutations = {
            "issuer": "https://www.gov.uk/path with space",
            "url": 'https://www.gov.uk/a"b',
            "rights_ref": "RIGHT-NOT-GOVERNED",
            "review_status": "draft",
            "rule_version": "2",
            "normalization": "https://www.gov.uk/different-rule",
            "source_value_hash_canonicalization": "unspecified",
            "observed_at": "2026-02-30T12:00:00Z",
            "retrieved_at": "2026-01-01T00:00:00Z",
            "locator": "records[id=different]",
            "source_value_sha256": "not-a-digest",
            "rationale": "",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                payload, records = self.cpsv_mapping_fixture()
                payload["evidence"][1][field] = value
                with self.assertRaises(ValueError):
                    self.validate_cpsv_fixture(payload, records)

    def test_curated_rights_access_classifications_are_exhaustive_and_scoped(
        self,
    ) -> None:
        source_payload = json.loads(
            (ROOT / "source" / "curated-records.json").read_text()
        )
        classification_payload = json.loads(
            (ROOT / "source" / "curated-rights-access.json").read_text()
        )
        source_rows = source_payload["records"]
        classification_rows = classification_payload["classifications"]
        source_by_id, classification_by_id = (
            builder._index_exact_curated_classifications(
                source_rows,
                classification_rows,
                classification_payload["record_count"],
            )
        )
        classifications, bindings, receipt = (
            builder.load_curated_rights_access_classifications()
        )
        self.assertEqual(55, len(source_by_id))
        self.assertEqual(set(source_by_id), set(classification_by_id))
        self.assertEqual(classification_by_id, classifications)
        self.assertEqual(set(classification_by_id), set(bindings))
        self.assertEqual("exhaustive-set-exact", receipt["coverage"])

        title_guidance = classification_by_id[
            "hmlr:guidance:title-register-information"
        ]
        self.assertEqual("property-information", title_guidance["source_family"])
        self.assertEqual("page-content", title_guidance["classification_scope"])
        self.assertEqual("RIGHT-GOVUK", title_guidance["rights_ref"])
        service_rows = {
            source_id: classification_by_id[source_id]
            for source_id in (
                "hmlr:service:property-information",
                "hmlr:service:property-alert",
            )
        }
        self.assertTrue(
            all(row["rights_ref"] == "RIGHT-RESTRICTED" for row in service_rows.values())
        )
        self.assertTrue(
            all(
                row.get("additional_rights_refs") == ["RIGHT-PERSONAL"]
                for row in service_rows.values()
            )
        )
        sources, rights = builder.source_controls()
        curated_records, _meta = builder.curated_records()
        governed_by_native_id = {
            row["source_native_id"]: row
            for row in (
                builder.govern_record(record, sources, rights)
                for record in curated_records
            )
        }
        self.assertEqual(
            "RIGHT-GOVUK",
            governed_by_native_id[
                "hmlr:guidance:title-register-information"
            ]["rights_ref"],
        )
        for source_id in service_rows:
            self.assertEqual(
                "RIGHT-RESTRICTED",
                governed_by_native_id[source_id]["rights_ref"],
            )

        legislation_ids = {
            "legislation:land-registration-act-2002",
            "legislation:land-registration-rules-2003",
            "legislation:local-land-charges-act-1975",
        }
        for source_id in legislation_ids:
            row = classification_by_id[source_id]
            self.assertEqual("legislation", row["source_family"])
            self.assertEqual("legislation-content", row["classification_scope"])
            self.assertEqual("RIGHT-LEGISLATION", row["rights_ref"])

        ulpd_rows = [
            row
            for row in classification_rows
            if row["source_family"] in {"ulpd", "ulpd-api"}
        ]
        self.assertEqual(16, len(ulpd_rows))
        self.assertEqual(
            {
                ("public", "open-with-conditions"): 7,
                ("authenticated", "bespoke-licence"): 2,
                ("authenticated", "bespoke-or-paid"): 3,
                ("mixed", "mixed"): 1,
                ("authenticated", "restricted-service"): 1,
                ("public", "unknown"): 2,
            },
            Counter(
                (row["access_state"], row["rights_state"])
                for row in ulpd_rows
            ),
        )
        self.assertTrue(all(row["rights_ref"] == "RIGHT-DATASETS" for row in ulpd_rows))

    def test_primary_rights_family_coverage_fails_closed(self) -> None:
        source_payload = json.loads(
            (ROOT / "source" / "source-register.json").read_text()
        )
        rights_payload = json.loads(
            (ROOT / "governance" / "rights-review.json").read_text()
        )
        right_govuk = next(
            row
            for row in rights_payload["assessments"]
            if row["id"] == "RIGHT-GOVUK"
        )
        right_govuk["source_family_ids"].remove("govuk-hmlr")
        real_load_json = builder.load_json

        def controlled_load(path: Path) -> dict:
            if Path(path) == ROOT / "source" / "source-register.json":
                return source_payload
            if Path(path) == ROOT / "governance" / "rights-review.json":
                return rights_payload
            return real_load_json(path)

        with mock.patch.object(builder, "load_json", side_effect=controlled_load):
            with self.assertRaisesRegex(ValueError, "primary rights assessment"):
                builder.source_controls()

        sources, rights = builder.source_controls()
        curated_records, _meta = builder.curated_records()
        non_curated = copy.deepcopy(curated_records[0])
        non_curated["curation"] = "normalized"
        with self.assertRaisesRegex(ValueError, "limited to frozen discovery"):
            builder.govern_record(non_curated, sources, rights)

    def test_source_family_rights_mapping_tampering_fails_closed(self) -> None:
        source_payload = json.loads(
            (ROOT / "source" / "source-register.json").read_text()
        )
        rights_payload = json.loads(
            (ROOT / "governance" / "rights-review.json").read_text()
        )
        govuk_search = next(
            row
            for row in source_payload["source_families"]
            if row["id"] == "govuk-search"
        )
        govuk_search["primary_rights_ref"] = "RIGHT-DATASETS"
        real_load_json = builder.load_json

        def controlled_load(path: Path) -> dict:
            if Path(path) == ROOT / "source" / "source-register.json":
                return source_payload
            if Path(path) == ROOT / "governance" / "rights-review.json":
                return rights_payload
            return real_load_json(path)

        with mock.patch.object(builder, "load_json", side_effect=controlled_load):
            with self.assertRaisesRegex(ValueError, "does not cover source family"):
                builder.source_controls()

        cddo = next(
            row
            for row in source_payload["source_families"]
            if row["id"] == "cddo-api-catalogue"
        )
        govuk_search["primary_rights_ref"] = "RIGHT-GOVUK"
        cddo["rights_overrides"][0]["canonical_source_host"] = (
            "BusinessGateway.LandRegistry.gov.uk"
        )
        with mock.patch.object(builder, "load_json", side_effect=controlled_load):
            with self.assertRaisesRegex(ValueError, "invalid rights override"):
                builder.source_controls()

    def test_curated_classification_population_and_row_swaps_fail_closed(self) -> None:
        source_rows = json.loads(
            (ROOT / "source" / "curated-records.json").read_text()
        )["records"]
        classification_rows = json.loads(
            (ROOT / "source" / "curated-rights-access.json").read_text()
        )["classifications"]

        missing = copy.deepcopy(classification_rows[:-1])
        with self.assertRaisesRegex(ValueError, "coverage differs"):
            builder._index_exact_curated_classifications(
                source_rows, missing, len(missing)
            )

        extra = copy.deepcopy(classification_rows)
        extra_row = copy.deepcopy(extra[-1])
        extra_row["source_native_id"] = "hmlr:unexpected:extra"
        extra.append(extra_row)
        with self.assertRaisesRegex(ValueError, "coverage differs"):
            builder._index_exact_curated_classifications(
                source_rows, extra, len(extra)
            )

        duplicate = copy.deepcopy(classification_rows)
        duplicate.append(copy.deepcopy(duplicate[0]))
        with self.assertRaisesRegex(ValueError, "unique"):
            builder._index_exact_curated_classifications(
                source_rows, duplicate, len(duplicate)
            )

        with self.assertRaisesRegex(ValueError, "source-native ID"):
            builder._validate_curated_classification_semantics(
                classification_rows[0], source_rows[1]
            )

    def test_cpsv_curated_bindings_refresh_only_changed_source_rows(self) -> None:
        payload = json.loads(
            (ROOT / "source" / "cpsv-service-mappings.json").read_text()
        )
        source_path = ROOT / "source" / "curated-records.json"
        source_by_id = {
            row["id"]: row
            for row in json.loads(source_path.read_text())["records"]
        }
        curated_evidence = {
            row["id"]: row
            for row in payload["evidence"]
            if row["source_artifact"] == "source/curated-records.json"
        }
        self.assertEqual(15, len(curated_evidence))
        for evidence in curated_evidence.values():
            source_id = evidence["source_field"].removeprefix(
                "records[id="
            ).removesuffix("]")
            self.assertIn(source_id, source_by_id)
            self.assertEqual(builder.sha256_file(source_path), evidence["source_sha256"])
            self.assertEqual(
                builder.sha256_bytes(
                    builder.compact_canonical_json(source_by_id[source_id])
                ),
                evidence["source_value_sha256"],
            )

        unchanged_value_digests = {
            "CPSV-E-EXC-LINKED-DATA": "995f19849ed6e0bb344eb5621763bf53097b5b1e17466768862a934ed52b859e",
            "CPSV-E-SVC-PROPERTY-INFORMATION": "e802e642278ef9615963d8730cd38f0f263037417d59ba5e88cf8ab6013087a6",
            "CPSV-E-AUTH-PROPERTY-INFORMATION": "e802e642278ef9615963d8730cd38f0f263037417d59ba5e88cf8ab6013087a6",
            "CPSV-E-SVC-CUSTOMER-HELP": "ad54ba4d3aca2472ab127b2a673d1222bca73829b0a45853b7194f6b302f2afa",
            "CPSV-E-AUTH-CUSTOMER-HELP": "ad54ba4d3aca2472ab127b2a673d1222bca73829b0a45853b7194f6b302f2afa",
            "CPSV-E-EXC-SERVICE-TERMS": "8b9826d07091a12f2be86205a55839b40f7dd4d04c73e2b9c3c0b526588cf909",
            "CPSV-E-SVC-PROPERTY-ALERT": "c6379fe6718acc0942964271808213fef3636ea9a61acb95a9442a9fc6281dd3",
            "CPSV-E-AUTH-PROPERTY-ALERT": "c6379fe6718acc0942964271808213fef3636ea9a61acb95a9442a9fc6281dd3",
            "CPSV-E-EXC-NATIONAL-POLYGON": "27bee3501daa78d9d16d711e868759403ff0f163dc0931dfa32bd0bc6c496cef",
        }
        self.assertEqual(
            unchanged_value_digests,
            {
                evidence_id: curated_evidence[evidence_id][
                    "source_value_sha256"
                ]
                for evidence_id in unchanged_value_digests
            },
        )

        fee_decision = next(
            row for row in payload["decisions"] if row["id"] == "CPSV-MAP-0008"
        )
        fee_fields = fee_decision["record_binding"]["fields"]
        self.assertEqual("fee-calculator", fee_fields["source_family"])
        self.assertEqual("hmlr-9ba8be379f260120f9ca847d", fee_fields["record_id"])
        self.assertEqual(fee_fields["record_id"], fee_decision["record_id"])
        self.assertEqual(
            builder.sha256_bytes(builder.compact_canonical_json(fee_fields)),
            fee_decision["record_binding"]["value_sha256"],
        )
        for evidence_id in (
            "CPSV-E-SVC-FEE-CALCULATOR",
            "CPSV-E-AUTH-FEE-CALCULATOR",
        ):
            self.assertEqual(
                "RIGHT-FEE-CALCULATOR",
                curated_evidence[evidence_id]["rights_ref"],
            )

    def test_rights_classification_and_cpsv_sources_are_governed_inputs(self) -> None:
        governed_paths = {
            row["path"] for row in builder.governed_input_receipts({})
        }
        self.assertTrue(
            {
                "schemas/curated-rights-access.schema.json",
                "source/curated-rights-access.json",
                "source/cpsv-service-mappings.json",
            }
            <= governed_paths
        )

    def test_build_receipt_inventory_contains_only_causal_build_inputs(self) -> None:
        graph = builder.load_artifact_dependency_graph()
        self.assertEqual(44, len(graph["build_inputs"]))
        governed_paths = {
            row["path"] for row in builder.governed_input_receipts({})
        }
        self.assertEqual(71, len(governed_paths))
        self.assertTrue(
            {
                "domain-profile/CHECKSUMS.sha256",
                *(
                    "domain-profile/" + member
                    for member in builder.STAGE1_SEMANTIC_PROFILE_PACK_MEMBERS
                ),
            }
            <= governed_paths
        )
        self.assertIn("evaluation/questions.json", governed_paths)
        self.assertNotIn("evaluation/acceptance-review.json", governed_paths)
        self.assertNotIn("evaluation/latest-report.json", governed_paths)
        self.assertNotIn(
            "validation/candidate-v0.3.0/evidence/evaluation-diagnostic.json",
            governed_paths,
        )
        self.assertIn(
            "governance/artifact-dependency-graph.json",
            governed_paths,
        )
        self.assertIn(
            "profiles/predicate-registry/v2.lock.json",
            governed_paths,
        )
        self.assertIn(
            "profiles/predicate-registry/v2/predicate-registry.schema.json",
            governed_paths,
        )
        self.assertNotIn("tests/test_build_semantics.py", governed_paths)
        self.assertNotIn(".github/workflows/pages.yml", governed_paths)
        self.assertNotIn("scripts/check_release_evidence.py", governed_paths)
        self.assertNotIn(
            "docs/v0.3.0-release-tracker-and-assurance-runbook.md",
            governed_paths,
        )
        self.assertNotIn("pages/app.js", governed_paths)
        self.assertNotIn("standards/cpsv-ap/README.md", governed_paths)
        self.assertTrue(
            all(
                not path.startswith(("bundle/", "dist/", "validation/"))
                for path in governed_paths
            )
        )

    def test_complete_candidate_inventory_adds_assurance_controls(self) -> None:
        graph = builder.load_artifact_dependency_graph()
        complete_paths = {
            path.relative_to(ROOT).as_posix()
            for path in builder.dependency_graph_governed_input_paths(graph)
        }
        build_paths = {
            path.relative_to(ROOT).as_posix()
            for path in builder.dependency_graph_build_input_paths(graph)
        }
        self.assertEqual(154, len(complete_paths))
        self.assertEqual(71, len(build_paths))
        self.assertIn("tests/test_build_semantics.py", complete_paths)
        self.assertIn(".gitattributes", complete_paths)
        self.assertIn("docs/validation-evidence-layout.md", complete_paths)
        self.assertIn("docs/okf-publication-method.md", complete_paths)
        self.assertIn(".github/workflows/pages.yml", complete_paths)
        self.assertIn(
            "docs/v0.3.0-release-tracker-and-assurance-runbook.md",
            complete_paths,
        )
        self.assertTrue(build_paths < complete_paths)

    def test_static_and_manifest_build_read_closure_is_exact(self) -> None:
        """All current repository reads are causal; assurance controls are not."""

        graph = builder.load_artifact_dependency_graph()
        declared = {
            path.relative_to(ROOT).as_posix()
            for path in builder.dependency_graph_build_input_paths(graph)
        }
        syntax = ast.parse(
            (ROOT / "scripts" / "build.py").read_text(encoding="utf-8")
        )
        string_constants: dict[str, str] = {}

        def static_path(node: ast.AST) -> str | None:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Name):
                if node.id == "ROOT":
                    return ""
                return string_constants.get(node.id)
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                left = static_path(node.left)
                right = static_path(node.right)
                if left is not None and right is not None:
                    return (
                        left.rstrip("/") + "/" + right.lstrip("/")
                    ).lstrip("/")
            return None

        for statement in syntax.body:
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                continue
            value = static_path(statement.value)
            if value is None:
                continue
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
            )
            for target in targets:
                if isinstance(target, ast.Name):
                    string_constants[target.id] = value

        required = {
            relative
            for node in ast.walk(syntax)
            if isinstance(node, ast.BinOp)
            and (relative := static_path(node)) is not None
            and (ROOT / relative).is_file()
        }
        for node in ast.walk(syntax):
            if isinstance(node, ast.ImportFrom):
                imported_modules = {
                    "change_impact": "scripts/change_impact.py",
                    "scripts.change_impact": "scripts/change_impact.py",
                    "python_runtime_contract": (
                        "scripts/python_runtime_contract.py"
                    ),
                    "scripts.python_runtime_contract": (
                        "scripts/python_runtime_contract.py"
                    ),
                }
                if node.module in imported_modules:
                    required.add(imported_modules[node.module])

        required.update(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "profiles" / "bundle-wiki" / "v1").rglob("*")
            if path.is_file()
        )
        required.update(
            path.relative_to(ROOT).as_posix()
            for path in (
                ROOT / "profiles" / "predicate-registry" / "v2"
            ).rglob("*")
            if path.is_file()
        )
        required.update(
            {
                "domain-profile/CHECKSUMS.sha256",
                *(
                    "domain-profile/" + member
                    for member in builder.STAGE1_SEMANTIC_PROFILE_PACK_MEMBERS
                ),
            }
        )
        required.update(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "standards" / "cpsv-ap" / "3.2.0").rglob("*")
            if path.is_file()
        )
        required.update(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "pages").rglob("*")
            if path.is_file() and path.name not in {"app.js", "search-contract.json"}
        )

        composite_path = ROOT / "source" / "input-manifest-v0.2.0.json"
        required.add(composite_path.relative_to(ROOT).as_posix())
        composite = json.loads(composite_path.read_text(encoding="utf-8"))
        for row in composite["inputs"]:
            required.add(row["path"])
            selected = ROOT / row["path"]
            if selected.name == "manifest.json":
                snapshot = json.loads(selected.read_text(encoding="utf-8"))
                required.update(
                    (selected.parent / item["path"])
                    .relative_to(ROOT)
                    .as_posix()
                    for item in snapshot["files"]
                )

        mappings = json.loads(
            (ROOT / "source" / "cpsv-service-mappings.json").read_text(
                encoding="utf-8"
            )
        )
        required.update(row["source_artifact"] for row in mappings["evidence"])
        required.add("requirements-lock.txt")

        self.assertEqual(required, declared)
        self.assertTrue(
            declared.isdisjoint(
                {
                    ".github/workflows/pages.yml",
                    "docs/v0.3.0-release-tracker-and-assurance-runbook.md",
                    "evaluation/acceptance-review.json",
                    "pages/app.js",
                    "scripts/check_release_evidence.py",
                    "tests/test_build_semantics.py",
                }
            )
        )

    def test_page_copy_rejects_ignored_or_untracked_worktree_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            pages = repository_root / "pages"
            pages.mkdir()
            (pages / "index.html").write_text("authorised\n", encoding="utf-8")
            (pages / "app.js").write_text("legacy\n", encoding="utf-8")
            unexpected = pages / ".DS_Store"
            unexpected.write_bytes(b"ignored local metadata")
            output = repository_root / "output"
            output.mkdir()
            with mock.patch.object(builder, "ROOT", repository_root):
                with self.assertRaisesRegex(
                    ValueError,
                    r"worktree inventory differs.*\.DS_Store",
                ):
                    builder.copy_pages(output, {"pages/index.html"})
                unexpected.unlink()
                builder.copy_pages(output, {"pages/index.html"})
                (pages / "app.js").unlink()
                builder.copy_pages(output, {"pages/index.html"})
                linked = pages / "linked.html"
                try:
                    linked.symlink_to(pages / "index.html")
                except OSError:  # pragma: no cover - platform capability
                    pass
                else:
                    with self.assertRaisesRegex(
                        ValueError,
                        "unsafe non-regular file",
                    ):
                        builder.copy_pages(output, {"pages/index.html"})
            self.assertEqual("authorised\n", (output / "index.html").read_text())
            self.assertFalse((output / "app.js").exists())

    def test_relational_graph_validation_prevents_input_subtraction(self) -> None:
        graph_path = ROOT / "governance" / "artifact-dependency-graph.json"
        malicious_graph = json.loads(graph_path.read_text(encoding="utf-8"))
        target = "bundle/okf-bundle.jsonld"
        malicious_graph["build_inputs"].append(target)
        real_load_json = builder.load_json

        def controlled_load(path: Path) -> dict:
            if Path(path) == graph_path:
                return malicious_graph
            return real_load_json(path)

        with mock.patch.object(
            builder,
            "load_json",
            side_effect=controlled_load,
        ):
            with self.assertRaisesRegex(
                ValueError,
                r"build-input validation.*overlaps a generated root",
            ):
                builder.load_artifact_dependency_graph()

        governed_paths = {
            row["path"] for row in builder.governed_input_receipts({})
        }
        self.assertNotIn(target, governed_paths)

    def test_executable_causal_bootstrap_rejects_removal_and_reclassification(
        self,
    ) -> None:
        graph_path = ROOT / "governance" / "artifact-dependency-graph.json"
        real_load_json = builder.load_json
        target = "source/curated-records.json"

        for reclassify_as_generated in (False, True):
            with self.subTest(reclassify_as_generated=reclassify_as_generated):
                malicious_graph = json.loads(graph_path.read_text(encoding="utf-8"))
                malicious_graph["build_inputs"].remove(target)
                if reclassify_as_generated:
                    malicious_graph["generated_roots"].append(target)

                def controlled_load(path: Path) -> dict:
                    if Path(path) == graph_path:
                        return malicious_graph
                    return real_load_json(path)

                with mock.patch.object(
                    builder,
                    "load_json",
                    side_effect=controlled_load,
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        "executable causal bootstrap",
                    ):
                        builder.load_artifact_dependency_graph()

    def test_deterministic_gzip_has_platform_neutral_golden_bytes(self) -> None:
        self.assertEqual(
            bytes.fromhex(
                "1f8b08000000000002ff2b4ecc4d050044f150fc04000000"
            ),
            builder.deterministic_gzip_bytes(b"same"),
        )
        self.assertEqual(
            builder.DETERMINISTIC_GZIP_GOLDEN_SHA256,
            hashlib.sha256(
                builder.deterministic_gzip_bytes(
                    builder.DETERMINISTIC_GZIP_GOLDEN_INPUT
                )
            ).hexdigest(),
        )

    def test_release_snapshot_requires_every_causal_input_to_be_indexed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            causal = root / "source.json"
            causal.write_text("{}\n", encoding="utf-8")
            with (
                mock.patch.object(builder, "load_artifact_dependency_graph", return_value={}),
                mock.patch.object(
                    builder,
                    "dependency_graph_build_input_paths",
                    return_value=[causal],
                ),
                mock.patch.object(
                    builder,
                    "_git_eligible_repository_paths",
                    return_value=[],
                ),
            ):
                with self.assertRaisesRegex(ValueError, "must be indexed"):
                    builder.BuildInputSnapshot.capture(root)

            with (
                mock.patch.object(builder, "load_artifact_dependency_graph", return_value={}),
                mock.patch.object(
                    builder,
                    "dependency_graph_build_input_paths",
                    return_value=[causal],
                ),
                mock.patch.object(
                    builder,
                    "_git_eligible_repository_paths",
                    return_value=["source.json"],
                ),
                mock.patch.object(
                    builder,
                    "_git_index_entries",
                    return_value={"source.json": ("100644", "0" * 40)},
                ),
                mock.patch.object(builder, "_git_object_format", return_value="sha1"),
            ):
                with self.assertRaisesRegex(ValueError, "differ from.*Git index"):
                    builder.BuildInputSnapshot.capture(root)

    def test_frozen_input_bytes_feed_receipt_and_detect_late_worktree_change(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            path = root / "input.json"
            path.write_bytes(b'{"value":1}\n')
            payload, identity = builder._bounded_read_file(
                path,
                maximum_bytes=1024,
                field="test causal input",
            )
            frozen = builder.FrozenBuildInput(
                relative_path="input.json",
                payload=payload,
                sha256=hashlib.sha256(payload).hexdigest(),
                identity=identity,
                index_mode="100644",
                index_oid="fixture-oid",
            )
            snapshot = builder.BuildInputSnapshot(
                repository_root=root,
                files={"input.json": frozen},
                index_entries={"input.json": ("100644", "fixture-oid")},
            )
            path.write_bytes(b'{"value":2}\n')
            with builder.activate_build_input_snapshot(snapshot):
                self.assertEqual(payload, builder.repository_bytes(path))
                self.assertEqual(
                    hashlib.sha256(payload).hexdigest(),
                    builder.sha256_file(path),
                )
                with mock.patch.object(
                    builder,
                    "_git_index_entries",
                    return_value=snapshot.index_entries,
                ):
                    with self.assertRaisesRegex(ValueError, "changed after"):
                        snapshot.verify_unchanged()

    def test_resource_ceilings_reject_oversized_files_and_git_path_counts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "too-large.bin"
            path.write_bytes(b"1234")
            with self.assertRaisesRegex(ValueError, "3-byte ceiling"):
                builder._bounded_read_file(
                    path,
                    maximum_bytes=3,
                    field="bounded fixture",
                )

        with (
            mock.patch.object(
                builder,
                "_run_git_bounded",
                return_value=(b"a\0b\0", b""),
            ),
            mock.patch.object(builder, "MAX_GIT_INVENTORY_PATHS", 1),
        ):
            with self.assertRaisesRegex(ValueError, "path-count ceiling"):
                builder._git_eligible_repository_paths(Path("."))

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = [root / "a.bin", root / "b.bin"]
            for path in paths:
                path.write_bytes(b"12")
            index_entries = {
                path.name: (
                    "100644",
                    builder._git_blob_oid(path.read_bytes(), "sha1"),
                )
                for path in paths
            }
            with (
                mock.patch.object(builder, "load_artifact_dependency_graph", return_value={}),
                mock.patch.object(
                    builder,
                    "dependency_graph_build_input_paths",
                    return_value=paths,
                ),
                mock.patch.object(
                    builder,
                    "_git_eligible_repository_paths",
                    return_value=[path.name for path in paths],
                ),
                mock.patch.object(
                    builder,
                    "_git_index_entries",
                    return_value=index_entries,
                ),
                mock.patch.object(builder, "_git_object_format", return_value="sha1"),
                mock.patch.object(builder, "MAX_CAUSAL_INPUT_TOTAL_BYTES", 3),
            ):
                with self.assertRaisesRegex(ValueError, "aggregate-byte ceiling"):
                    builder.BuildInputSnapshot.capture(root)

    def test_python_runtime_receipt_uses_the_shared_lock_bound_observer(self) -> None:
        lock = (ROOT / "requirements-lock.txt").read_bytes()
        expected = {"schema": "shared-runtime-fixture"}
        with mock.patch.object(
            builder,
            "observe_python_runtime",
            return_value=expected,
        ) as observer:
            self.assertEqual(expected, builder.python_runtime_receipt())
        observer.assert_called_once_with(ROOT, lock)

    def test_requirements_lock_parser_rejects_unknown_top_level_forms(self) -> None:
        malformed = (
            b"fixture==1.0\n",
            b"fixture @ https://example.test/fixture.whl\n",
            b"--index-url https://example.test/simple\n",
            b"-r other-requirements.txt\n",
            b"fixture==1.0 \\\n"
            b"    --hash=sha256:"
            + b"0" * 64
            + b" \\\n",
        )
        for payload in malformed:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    builder._locked_python_packages(payload)

    def test_python_runtime_admits_no_bootstrap_distribution(self) -> None:
        self.assertEqual(
            frozenset(),
            runtime_contract.ALLOWED_RUNTIME_BOOTSTRAP_DISTRIBUTIONS,
        )

    def test_portable_runtime_receipt_excludes_platform_specific_tree_bytes(
        self,
    ) -> None:
        lock = (ROOT / "requirements-lock.txt").read_bytes()
        packages = runtime_contract.parse_hashed_requirements_lock(lock)
        common = {
            "lock_bytes": lock,
            "packages": packages,
            "implementation": "CPython",
            "version": "3.12.11",
            "golden_sha256": runtime_contract.DETERMINISTIC_GZIP_GOLDEN_SHA256,
        }
        mac_portable, mac_audit = runtime_contract._assemble_runtime_receipts(
            **common,
            installed_inventory={
                "lib/python3.12/site-packages/fixture.so": (101, "a" * 64),
                "lib/python3.12/site-packages/fixture.dist-info/RECORD": (
                    83,
                    "b" * 64,
                ),
            },
            record_receipts=[
                {
                    "distribution": "fixture",
                    "path": "fixture.dist-info/RECORD",
                    "sha256": "b" * 64,
                }
            ],
            platform_system="Darwin",
            platform_machine="arm64",
        )
        linux_portable, linux_audit = runtime_contract._assemble_runtime_receipts(
            **common,
            installed_inventory={
                "lib/python3.12/site-packages/fixture.so": (117, "c" * 64),
                "lib/python3.12/site-packages/fixture.dist-info/RECORD": (
                    91,
                    "d" * 64,
                ),
            },
            record_receipts=[
                {
                    "distribution": "fixture",
                    "path": "fixture.dist-info/RECORD",
                    "sha256": "d" * 64,
                }
            ],
            platform_system="Linux",
            platform_machine="x86_64",
        )

        self.assertEqual(mac_portable, linux_portable)
        self.assertNotEqual(mac_audit["installed_tree"], linux_audit["installed_tree"])
        self.assertEqual(
            mac_audit["portable_contract_sha256"],
            linux_audit["portable_contract_sha256"],
        )
        self.assertNotIn("installed_tree", mac_portable)
        self.assertNotIn("installed_record_receipts", mac_portable)
        self.assertIn(
            "not independently attested",
            mac_portable["preimport_assurance"],
        )
        self.assertEqual(
            "post-import-capture-single-writer-precondition-no-preobserver-"
            "source-byte-attestation-v1",
            mac_portable["source_execution_assurance"],
        )

    def test_distribution_record_rejects_every_unhashed_non_self_member(
        self,
    ) -> None:
        class HashValue:
            mode = "sha256"

            def __init__(self, value: str) -> None:
                self.value = value

        class Member:
            def __init__(
                self,
                path: str,
                *,
                digest: str | None,
                size: int | None,
            ) -> None:
                self.path = PurePosixPath(path)
                self.hash = HashValue(digest) if digest is not None else None
                self.size = size

            @property
            def name(self) -> str:
                return self.path.name

            @property
            def parent(self) -> PurePosixPath:
                return self.path.parent

            def as_posix(self) -> str:
                return self.path.as_posix()

            def __str__(self) -> str:
                return self.path.as_posix()

        class Distribution:
            metadata = {"Name": "fixture"}

            def __init__(self, root: Path, files: list[Member]) -> None:
                self.root = root
                self.files = files

            def locate_file(self, member: Member) -> Path:
                return self.root / member.as_posix()

        with tempfile.TemporaryDirectory() as temporary_directory:
            environment = Path(temporary_directory) / ".venv"
            site_packages = environment / "lib" / "python3.12" / "site-packages"
            module = site_packages / "fixture.py"
            record = site_packages / "fixture-1.0.dist-info" / "RECORD"
            record.parent.mkdir(parents=True)
            module.write_bytes(b"VALUE = 1\n")
            record.write_bytes(b"fixture.py,sha256=fixture,10\n")
            encoded = base64.urlsafe_b64encode(
                hashlib.sha256(module.read_bytes()).digest()
            ).decode("ascii").rstrip("=")
            module_member = Member(
                "fixture.py",
                digest=encoded,
                size=module.stat().st_size,
            )
            record_member = Member(
                "fixture-1.0.dist-info/RECORD",
                digest=None,
                size=None,
            )
            distribution = Distribution(
                site_packages,
                [module_member, record_member],
            )
            receipt = runtime_contract._verify_distribution_record(
                distribution,  # type: ignore[arg-type]
                hasher=runtime_contract._RuntimeFileHasher(environment),
            )
            self.assertEqual("fixture", receipt["distribution"])

            evil = site_packages / "evil.py"
            evil.write_bytes(b"raise RuntimeError('evil')\n")
            blank_evil = Member("evil.py", digest=None, size=None)
            blank_module = Member(
                "fixture.py",
                digest=None,
                size=module.stat().st_size,
            )
            for member in (blank_evil, blank_module):
                with self.subTest(member=member.as_posix()):
                    altered = Distribution(
                        site_packages,
                        [member, record_member],
                    )
                    with self.assertRaisesRegex(
                        runtime_contract.PythonRuntimeContractError,
                        "unhashed member",
                    ):
                        runtime_contract._verify_distribution_record(
                            altered,  # type: ignore[arg-type]
                            hasher=runtime_contract._RuntimeFileHasher(environment),
                        )

    def test_python_runtime_hashes_venv_records_under_active_snapshot(self) -> None:
        lock_path = ROOT / "requirements-lock.txt"
        payload, identity = builder._bounded_read_file(
            lock_path,
            maximum_bytes=builder.MAX_CAUSAL_INPUT_FILE_BYTES,
            field="requirements lock fixture",
        )
        frozen = builder.FrozenBuildInput(
            relative_path="requirements-lock.txt",
            payload=payload,
            sha256=hashlib.sha256(payload).hexdigest(),
            identity=identity,
            index_mode="100644",
            index_oid="fixture-oid",
        )
        snapshot = builder.BuildInputSnapshot(
            repository_root=ROOT,
            files={"requirements-lock.txt": frozen},
            index_entries={
                "requirements-lock.txt": ("100644", "fixture-oid")
            },
        )
        expected = {"schema": "snapshot-runtime-fixture"}
        with (
            builder.activate_build_input_snapshot(snapshot),
            mock.patch.object(
                builder,
                "observe_python_runtime",
                return_value=expected,
            ) as observer,
        ):
            receipt = builder.python_runtime_receipt()
        self.assertEqual(expected, receipt)
        observer.assert_called_once_with(ROOT, payload)

    def test_bounded_evaluator_rejects_timeout_and_output_floods(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            report = root / "report.json"
            with (
                mock.patch.object(builder, "EVALUATOR_TIMEOUT_SECONDS", 0.1),
                self.assertRaisesRegex(ValueError, "time ceiling"),
            ):
                builder._run_bounded_evaluator(
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        "import time; time.sleep(10)",
                    ],
                    working_directory=root,
                    report_path=report,
                )

            with (
                mock.patch.object(builder, "MAX_EVALUATOR_OUTPUT_BYTES", 16),
                self.assertRaisesRegex(ValueError, "stdout exceeds"),
            ):
                builder._run_bounded_evaluator(
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        "import os; os.write(1, b'x' * 1024)",
                    ],
                    working_directory=root,
                    report_path=report,
                )

    def test_bounded_evaluator_rejects_oversized_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            report = root / "report.json"
            with (
                mock.patch.object(builder, "MAX_EVALUATION_REPORT_BYTES", 32),
                self.assertRaisesRegex(ValueError, "32-byte ceiling"),
            ):
                builder._run_bounded_evaluator(
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        (
                            "import pathlib,sys; "
                            "pathlib.Path(sys.argv[1]).write_text("
                            "'{\"payload\":\"' + 'x' * 100 + '\"}')"
                        ),
                        str(report),
                    ],
                    working_directory=root,
                    report_path=report,
                )

    def test_bounded_evaluator_receives_only_the_minimal_public_environment(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            report = root / "report.json"
            with mock.patch.dict(
                os.environ,
                {
                    "SECRET_FIXTURE_TOKEN": "must-not-enter-child",
                    "PYTHONPATH": "/must/not/enter/child",
                },
                clear=False,
            ):
                observed = builder._run_bounded_evaluator(
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        (
                            "import json,os,pathlib,sys; "
                            "pathlib.Path(sys.argv[1]).write_text("
                            "json.dumps({'environment': dict(os.environ)}))"
                        ),
                        str(report),
                    ],
                    working_directory=root,
                    report_path=report,
                )
            environment = observed["environment"]
            self.assertEqual("C", environment["LANG"])
            self.assertEqual("C", environment["LC_ALL"])
            self.assertEqual("UTC", environment["TZ"])
            self.assertNotIn("SECRET_FIXTURE_TOKEN", environment)
            self.assertNotIn("PYTHONPATH", environment)

    def test_bounded_evaluator_rejects_duplicate_report_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            report = root / "report.json"
            with self.assertRaisesRegex(ValueError, "repeats JSON key 'status'"):
                builder._run_bounded_evaluator(
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        (
                            "import pathlib,sys; "
                            "pathlib.Path(sys.argv[1]).write_text("
                            "'{\"status\":\"pass\",\"status\":\"fail\"}')"
                        ),
                        str(report),
                    ],
                    working_directory=root,
                    report_path=report,
                )

    def test_bounded_evaluator_rejects_non_finite_report_numbers(self) -> None:
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                report = root / "report.json"
                with self.assertRaisesRegex(
                    ValueError,
                    "non-finite JSON number",
                ):
                    builder._run_bounded_evaluator(
                        [
                            sys.executable,
                            "-B",
                            "-c",
                            (
                                "import pathlib,sys; "
                                "pathlib.Path(sys.argv[1]).write_text("
                                f"'{{\"score\":{token}}}')"
                            ),
                            str(report),
                        ],
                        working_directory=root,
                        report_path=report,
                    )

    def test_ignored_latest_report_cannot_change_governed_input_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            (repository_root / "evaluation").mkdir()
            (repository_root / "tests").mkdir()
            (repository_root / "evaluation" / "acceptance-review.json").write_text(
                '{"historical":true}\n',
                encoding="utf-8",
            )
            (repository_root / "tests" / "test_fixture.py").write_text(
                "# governed validation input\n",
                encoding="utf-8",
            )
            (repository_root / ".gitignore").write_text(
                "evaluation/latest-report.json\n*.ignored\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "init", "-q"],
                cwd=repository_root,
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "add",
                    ".gitignore",
                    "evaluation/acceptance-review.json",
                    "tests/test_fixture.py",
                ],
                cwd=repository_root,
                check=True,
            )
            graph = {
                "generated_roots": ["bundle/**"],
                "stages": [
                    {
                        "id": "fixture",
                        "inputs": ["evaluation/**"],
                        "validation_inputs": ["tests/test_fixture.py"],
                        "outputs": [
                            "bundle/**",
                            "evaluation/latest-report.json",
                        ],
                    }
                ],
            }

            def inventory() -> list[tuple[str, str]]:
                paths = builder.dependency_graph_governed_input_paths(
                    graph,
                    repository_root=repository_root,
                )
                return [
                    (
                        path.relative_to(repository_root).as_posix(),
                        hashlib.sha256(path.read_bytes()).hexdigest(),
                    )
                    for path in paths
                ]

            without_latest = inventory()
            latest_report = repository_root / "evaluation" / "latest-report.json"
            latest_report.write_text('{"run":1}\n', encoding="utf-8")
            with_latest = inventory()
            latest_report.write_text('{"run":2}\n', encoding="utf-8")
            with_changed_latest = inventory()
            ignored_elsewhere = repository_root / "evaluation" / "cache.ignored"
            ignored_elsewhere.write_text("ignored\n", encoding="utf-8")
            with_other_ignored = inventory()
            intended_untracked = (
                repository_root / "evaluation" / "new-authored-input.json"
            )
            intended_untracked.write_text('{"authored":true}\n', encoding="utf-8")
            with_intended_untracked = inventory()
        self.assertEqual(without_latest, with_latest)
        self.assertEqual(without_latest, with_changed_latest)
        self.assertEqual(without_latest, with_other_ignored)
        self.assertEqual(
            [
                "evaluation/acceptance-review.json",
                "tests/test_fixture.py",
            ],
            [path for path, _digest in without_latest],
        )
        self.assertEqual(
            [
                "evaluation/acceptance-review.json",
                "evaluation/new-authored-input.json",
                "tests/test_fixture.py",
            ],
            [path for path, _digest in with_intended_untracked],
        )

    def test_vendor_lock_rejects_ignored_extra_worktree_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            profile_root = root / "profiles" / "bundle-wiki" / "v1"
            profile_root.mkdir(parents=True)
            included = profile_root / "included.json"
            included.write_bytes(b"{}\n")
            digest = hashlib.sha256(included.read_bytes()).hexdigest()
            identity = hashlib.sha256(
                f"included.json\t3\t{digest}\n".encode("utf-8")
            ).hexdigest()
            lock_path = root / "profiles" / "bundle-wiki" / "v1.vendor-lock.json"
            lock_path.write_text(
                json.dumps(
                    {
                        "schema": "okf-profile-vendor-lock.v1",
                        "profile": builder.BUNDLE_PROFILE_URL,
                        "release": {
                            "version": "0.6.0",
                            "commit": "4bb7b92a64b7ba69bde9b1e86786217338cd166d",
                        },
                        "file_count": 1,
                        "files": [
                            {
                                "path": "included.json",
                                "bytes": 3,
                                "sha256": digest,
                            }
                        ],
                        "identity": {"sha256": identity},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            try:
                with (
                    mock.patch.object(builder, "PROFILE_ROOT", profile_root),
                    mock.patch.object(builder, "PROFILE_LOCK_PATH", lock_path),
                ):
                    builder.validate_profile_vendor_lock.cache_clear()
                    self.assertEqual(
                        "conformant",
                        builder.validate_profile_vendor_lock()["status"],
                    )
                    builder.validate_profile_vendor_lock.cache_clear()
                    (profile_root / "ignored.ttl").write_text(
                        "ignored local byte\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(ValueError, "inventory differs"):
                        builder.validate_profile_vendor_lock()
            finally:
                builder.validate_profile_vendor_lock.cache_clear()

    def test_dependency_graph_patterns_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            (repository_root / "evaluation").mkdir()
            (repository_root / "tests").mkdir()
            (repository_root / "evaluation" / "input.json").write_text(
                "{}\n",
                encoding="utf-8",
            )
            (repository_root / "tests" / "test_fixture.py").write_text(
                "# governed validation input\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "init", "-q"],
                cwd=repository_root,
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "add",
                    "evaluation/input.json",
                    "tests/test_fixture.py",
                ],
                cwd=repository_root,
                check=True,
            )
            base_graph = {
                "generated_roots": ["bundle/**"],
                "stages": [
                    {
                        "id": "fixture",
                        "inputs": ["evaluation/**"],
                        "validation_inputs": ["tests/test_fixture.py"],
                        "outputs": ["bundle/**"],
                    }
                ],
            }
            unsafe_inputs = (
                "../outside.json",
                "/absolute.json",
                "evaluation/*.json",
                "evaluation/**/more",
                "evaluation//input.json",
                "evaluation/./input.json",
                "evaluation\\input.json",
                "evaluation/input:variant.json",
                "evaluation/~input.json",
                "evaluation/\x7finput.json",
                "dist/release.json",
                "validation/candidate.json",
            )
            for value in unsafe_inputs:
                with self.subTest(value=value):
                    graph = copy.deepcopy(base_graph)
                    graph["stages"][0]["inputs"] = [value]
                    with self.assertRaises(ValueError):
                        builder.dependency_graph_governed_input_paths(
                            graph,
                            repository_root=repository_root,
                        )

            graph = copy.deepcopy(base_graph)
            graph["stages"][0]["inputs"] = ["missing/**"]
            with self.assertRaisesRegex(ValueError, "no eligible tree match"):
                builder.dependency_graph_governed_input_paths(
                    graph,
                    repository_root=repository_root,
                )

            graph = copy.deepcopy(base_graph)
            graph["stages"][0]["outputs"] = ["bundle/*.json"]
            with self.assertRaisesRegex(ValueError, "literal or use one trailing"):
                builder.dependency_graph_governed_input_paths(
                    graph,
                    repository_root=repository_root,
                )

            generated_report = repository_root / "evaluation" / "latest-report.json"
            generated_report.write_text("{}\n", encoding="utf-8")
            graph = copy.deepcopy(base_graph)
            graph["generated_roots"].append("evaluation/latest-report.json")
            graph["stages"][0]["inputs"] = ["evaluation/latest-report.json"]
            with self.assertRaisesRegex(ValueError, "no governed file matches"):
                builder.dependency_graph_governed_input_paths(
                    graph,
                    repository_root=repository_root,
                )

            link_path = repository_root / "evaluation" / "linked.json"
            try:
                link_path.symlink_to(repository_root / "evaluation" / "input.json")
            except OSError as exc:  # pragma: no cover - platform capability
                self.skipTest(f"symbolic links are unavailable: {exc}")
            graph = copy.deepcopy(base_graph)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                builder.dependency_graph_governed_input_paths(
                    graph,
                    repository_root=repository_root,
                )

    def test_generated_at_follows_every_governed_event_class(self) -> None:
        generated_at = "2026-08-13T00:00:00Z"
        baseline = "2026-08-09T00:00:00Z"
        later = "2026-08-14T00:00:00Z"
        manifest_path = (
            ROOT / "source" / "snapshots" / "2026-07-29T091915Z" / "manifest.json"
        )
        real_load_json = builder.load_json

        def documents(
            *,
            snapshot_observed: str = baseline,
            snapshot_retrieved: str = baseline,
            profile_prepared: str = baseline,
            profile_evidence_retrieved: str = baseline,
            cpsv_reviewed: str = baseline,
            cpsv_retrieved: str = baseline,
            source_register_reviewed: str = baseline,
            publisher_registry_reviewed: str = baseline,
        ) -> tuple[dict, dict, dict, dict, dict]:
            snapshot = {
                "observed_at": snapshot_observed,
                "manifest_path": manifest_path.relative_to(ROOT).as_posix(),
            }
            profile = {
                "prepared_at": profile_prepared,
                "evidence": [
                    {
                        "observed_at": baseline,
                        "retrieved_at": profile_evidence_retrieved,
                    }
                ],
            }
            cpsv = {
                "document": {
                    "decisions": [{"reviewed_at": cpsv_reviewed}],
                    "evidence": [
                        {
                            "observed_at": baseline,
                            "retrieved_at": cpsv_retrieved,
                        }
                    ],
                }
            }
            source_register = {
                "observed_at": baseline,
                "reviewed_at": source_register_reviewed,
            }
            publisher_registry = {
                "observed_at": baseline,
                "reviewed_at": publisher_registry_reviewed,
            }
            return (
                snapshot,
                profile,
                cpsv,
                source_register,
                publisher_registry,
            )

        cases = (
            (
                "snapshot observation",
                {"snapshot_observed": later},
                "selected snapshot.*observed_at",
            ),
            (
                "snapshot retrieval",
                {"snapshot_retrieved": later},
                "snapshot manifest.*retrieved_at",
            ),
            (
                "domain-profile preparation",
                {"profile_prepared": later},
                "domain profile.*prepared_at",
            ),
            (
                "domain-profile evidence",
                {"profile_evidence_retrieved": later},
                "domain-profile evidence.*retrieved_at",
            ),
            (
                "CPSV review",
                {"cpsv_reviewed": later},
                "CPSV-AP mapping.*reviewed_at",
            ),
            (
                "CPSV retrieval",
                {"cpsv_retrieved": later},
                "CPSV-AP mapping.*retrieved_at",
            ),
            (
                "source-register review",
                {"source_register_reviewed": later},
                "source/source-register.json.*reviewed_at",
            ),
            (
                "publisher-registry review",
                {"publisher_registry_reviewed": later},
                "source/publisher-registry.json.*reviewed_at",
            ),
        )
        for name, changed, expected in cases:
            with self.subTest(name=name):
                (
                    snapshot,
                    profile,
                    cpsv,
                    source_register,
                    publisher_registry,
                ) = documents(**changed)
                snapshot_manifest = {
                    "observed_at": baseline,
                    "retrieved_at": changed.get("snapshot_retrieved", baseline),
                }

                def controlled_load(path: Path) -> dict:
                    if Path(path) == ROOT / "domain-profile" / "domain-profile.json":
                        return profile
                    if Path(path) == manifest_path:
                        return snapshot_manifest
                    if Path(path) == ROOT / "source" / "source-register.json":
                        return source_register
                    if Path(path) == ROOT / "source" / "publisher-registry.json":
                        return publisher_registry
                    return real_load_json(path)

                with mock.patch.object(
                    builder,
                    "load_json",
                    side_effect=controlled_load,
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        expected,
                    ):
                        builder.validate_generated_at_chronology(
                            {"generated_at": generated_at},
                            snapshot,
                            cpsv,
                        )

    def test_current_generated_at_is_after_the_latest_governed_event(self) -> None:
        records, composite_manifest, _bindings, _assertions = (
            self.source_relationship_fixture()
        )
        snapshot_dir = builder.newest_snapshot()
        self.assertIsNotNone(snapshot_dir)
        _discovered, acquisition_snapshot = builder.snapshot_records(snapshot_dir)
        _content, content_meta = builder.content_observation_records(
            composite_manifest
        )
        snapshot = {
            **acquisition_snapshot,
            "observed_at": max(
                acquisition_snapshot["observed_at"],
                content_meta["observed_at"],
            ),
            "acquisition_snapshot": acquisition_snapshot,
        }
        cpsv_mappings = builder.load_cpsv_service_mappings(records)
        receipt = builder.validate_generated_at_chronology(
            builder.load_build_config(),
            snapshot,
            cpsv_mappings,
        )
        expected_latest = builder.load_json(
            ROOT / "domain-profile" / "domain-profile.json"
        )["prepared_at"]
        self.assertEqual(
            expected_latest,
            receipt["latest_governed_event_at"],
        )
        self.assertRegex(
            receipt["latest_governed_event"],
            r"domain profile.*prepared_at",
        )

    def test_every_catalogue_record_has_an_exact_frozen_source_row(self) -> None:
        records, _manifest, bindings, assertions = (
            self.source_relationship_fixture()
        )
        self.assertEqual(2_203, len(records))
        self.assertEqual(2_203, len(bindings))
        self.assertEqual(2_227, sum(
            len(binding["representations"])
            for binding in bindings.values()
        ))
        lanes: Counter[str] = Counter()
        for record in records:
            binding = bindings[record["record_id"]]
            artifact = binding["source_artifact"]
            if artifact.endswith("/govuk-search.json"):
                lane = "govuk-search"
            elif artifact.endswith("/github-repositories.json"):
                lane = "github"
            elif artifact.endswith("/cddo-api-catalogue.json"):
                lane = "cddo-api-catalogue"
            elif artifact.startswith("source/observations/"):
                lane = "govuk-content"
            else:
                self.assertEqual("source/curated-records.json", artifact)
                lane = "curated"
            lanes[lane] += 1
            self.assertEqual(
                binding["source_value"],
                builder.resolve_source_field_value(
                    artifact, binding["source_field"]
                ),
            )
            self.assertEqual(
                binding["source_sha256"],
                builder.source_artifact_snapshot(artifact)[0],
            )
            self.assertNotIn("[]", binding["source_field"])
            self.assertNotIn(" -> ", binding["source_field"])
        self.assertEqual(2_203, sum(lanes.values()))
        self.assertEqual(
            {
                "govuk-search",
                "github",
                "cddo-api-catalogue",
                "govuk-content",
                "curated",
            },
            set(lanes),
        )
        self.assertEqual(22_267, len(assertions))
        builder.validate_relationship_evidence_bindings(
            assertions,
            records=records,
            record_bindings=bindings,
            cpsv_mappings=builder.load_cpsv_service_mappings(records),
        )

    def test_relationship_evidence_binding_rejects_adversarial_drift(self) -> None:
        records, _manifest, _bindings, assertions = (
            self.source_relationship_fixture()
        )
        assertion = next(
            row
            for row in assertions
            if row["evidence"][0].get("source_locator")
            and row["evidence"][0]["source_artifact"].endswith(
                "/govuk-search.json"
            )
        )
        original = assertion["evidence"][0]
        artifact = original["source_artifact"]
        _digest, document = builder.source_artifact_snapshot(artifact)
        current = int(original["source_field"].removeprefix("results[").removesuffix("]"))
        swapped_field = f"results[{(current + 1) % len(document['results'])}]"
        swapped_value = builder.resolve_source_field_value(
            artifact, swapped_field
        )

        row_swap = copy.deepcopy(assertion)
        row_swap["evidence"][0]["source_field"] = swapped_field
        row_swap["evidence"][0]["locator"] = swapped_field
        row_swap["evidence"][0]["source_value_sha256"] = builder.sha256_bytes(
            builder.compact_canonical_json(swapped_value)
        )
        self.refresh_evidence_id(row_swap)
        with self.assertRaisesRegex(ValueError, "row does not bind its record"):
            builder.validate_relationship_evidence_bindings([row_swap])

        locator_drift = copy.deepcopy(assertion)
        locator_drift["evidence"][0]["locator"] += ".title"
        self.refresh_evidence_id(locator_drift)
        with self.assertRaisesRegex(ValueError, "locator drift"):
            builder.validate_relationship_evidence_bindings([locator_drift])

        artifact_drift = copy.deepcopy(assertion)
        artifact_drift["evidence"][0]["source_sha256"] = "0" * 64
        self.refresh_evidence_id(artifact_drift)
        with self.assertRaisesRegex(ValueError, "artefact digest drift"):
            builder.validate_relationship_evidence_bindings([artifact_drift])

        synthetic_hash = copy.deepcopy(assertion)
        synthetic_hash["evidence"][0]["source_value_sha256"] = (
            builder.sha256_bytes(
                builder.compact_canonical_json(
                    {
                        "record_id": synthetic_hash["evidence"][0][
                            "source_locator"
                        ],
                        "predicate": synthetic_hash["predicate"]["@id"],
                    }
                )
            )
        )
        self.refresh_evidence_id(synthetic_hash)
        with self.assertRaisesRegex(ValueError, "exact source value"):
            builder.validate_relationship_evidence_bindings([synthetic_hash])

    def test_role_governed_relationship_fields_reject_reidentified_mutations(
        self,
    ) -> None:
        records, _manifest, bindings, assertions = (
            self.source_relationship_fixture()
        )
        cpsv_mappings = builder.load_cpsv_service_mappings(records)

        def row_with_evidence_type(evidence_type: str) -> tuple[dict, int]:
            for assertion in assertions:
                for ordinal, evidence in enumerate(assertion["evidence"]):
                    if evidence.get("type") == evidence_type:
                        return assertion, ordinal
            raise AssertionError(f"missing evidence fixture: {evidence_type}")

        role_rows = {
            "record-projection": (
                next(
                    row
                    for row in assertions
                    if row["predicate"]["@id"]
                    == builder.CATALOGUE_RECORD_PREDICATE
                ),
                0,
            ),
            "rights": row_with_evidence_type("governed-rights-assessment"),
            "rights-family-policy": row_with_evidence_type(
                "governed-source-family-rights-policy"
            ),
            "publisher-source": row_with_evidence_type(
                "frozen-source-metadata"
            ),
            "publisher-registry": row_with_evidence_type(
                "governed-identity-registry"
            ),
            "cpsv-delivery": row_with_evidence_type(
                "competent-authority-delivery"
            ),
            "cpsv-organisation": row_with_evidence_type(
                "public-organisation-identity"
            ),
            "translation": row_with_evidence_type(
                "official-metadata-observation"
            ),
        }
        source_register = json.loads(
            (ROOT / "source" / "source-register.json").read_text()
        )
        publisher_registry = json.loads(
            (ROOT / "source" / "publisher-registry.json").read_text()
        )
        rights_family_row, rights_family_ordinal = role_rows[
            "rights-family-policy"
        ]
        publisher_registry_row, publisher_registry_ordinal = role_rows[
            "publisher-registry"
        ]
        self.assertEqual(
            source_register["reviewed_at"],
            rights_family_row["evidence"][rights_family_ordinal][
                "retrieved_at"
            ],
        )
        self.assertEqual(
            publisher_registry["reviewed_at"],
            publisher_registry_row["evidence"][publisher_registry_ordinal][
                "retrieved_at"
            ],
        )
        evidence_mutations = {
            "retrieved_at": "2099-01-01T00:00:00Z",
            "url": "https://example.test/not-governed",
            "resource": "https://example.test/not-governed",
            "normalization": "https://example.test/not-governed-rule",
        }

        for role_name, (assertion, ordinal) in role_rows.items():
            for field, replacement in evidence_mutations.items():
                with self.subTest(role=role_name, field=field):
                    changed = copy.deepcopy(assertion)
                    changed["evidence"][ordinal][field] = replacement
                    self.refresh_evidence_id(changed, ordinal)
                    with self.assertRaisesRegex(
                        ValueError, "role-governed|governed reference"
                    ):
                        builder.validate_relationship_evidence_bindings(
                            [changed],
                            records=records,
                            record_bindings=bindings,
                            cpsv_mappings=cpsv_mappings,
                        )

        assertion_mutations = {
            "observed_at": "2099-01-01T00:00:00Z",
            "authority.source": "https://example.test/not-governed",
            "rights.source": "https://example.test/not-governed",
        }
        for role_name, (assertion, _ordinal) in role_rows.items():
            for field, replacement in assertion_mutations.items():
                with self.subTest(role=role_name, field=field):
                    changed = copy.deepcopy(assertion)
                    if field == "authority.source":
                        changed["authority"]["source"] = replacement
                    elif field == "rights.source":
                        changed["rights"]["source"] = replacement
                    else:
                        changed[field] = replacement
                    self.refresh_assertion_id(changed)
                    for evidence_ordinal in range(len(changed["evidence"])):
                        self.refresh_evidence_id(changed, evidence_ordinal)
                    with self.assertRaisesRegex(
                        ValueError, "role-governed|governed value"
                    ):
                        builder.validate_relationship_evidence_bindings(
                            [changed],
                            records=records,
                            record_bindings=bindings,
                            cpsv_mappings=cpsv_mappings,
                        )

    def test_complete_evidence_rows_cannot_move_between_relationships(self) -> None:
        _records, _manifest, _bindings, assertions = (
            self.source_relationship_fixture()
        )
        catalogue_rows = [
            row
            for row in assertions
            if row["predicate"]["@id"] == builder.CATALOGUE_RECORD_PREDICATE
        ]
        substituted = copy.deepcopy(catalogue_rows[0])
        substituted["evidence"] = copy.deepcopy(catalogue_rows[1]["evidence"])
        with self.assertRaisesRegex(ValueError, "not deterministic"):
            builder.validate_relationship_evidence_bindings([substituted])

        duplicate = copy.deepcopy(catalogue_rows[1])
        duplicate["evidence"][0]["@id"] = catalogue_rows[0]["evidence"][0]["@id"]
        with self.assertRaisesRegex(ValueError, "duplicate relationship evidence ID"):
            builder.validate_relationship_evidence_bindings(
                [catalogue_rows[0], duplicate]
            )

    def test_curated_rights_evidence_rows_cannot_be_swapped_or_omitted(self) -> None:
        records, _manifest, _bindings, assertions = (
            self.source_relationship_fixture()
        )
        curated_rights = [
            row
            for row in assertions
            if row["predicate"]["@id"] == builder.RIGHTS_PREDICATE
            and len(row["evidence"]) == 4
            and row["evidence"][3]["type"]
            == "governed-curated-rights-classification"
        ]
        first = copy.deepcopy(curated_rights[0])
        second = curated_rights[2]
        first["evidence"][3] = copy.deepcopy(second["evidence"][3])
        self.refresh_evidence_id(first, 3)
        with self.assertRaisesRegex(
            ValueError,
            "curated rights classification row does not bind",
        ):
            builder.validate_relationship_evidence_bindings(
                [first], records=records
            )

        omitted = copy.deepcopy(curated_rights[0])
        omitted["evidence"].pop()
        with self.assertRaisesRegex(ValueError, "evidence roles differ"):
            builder.validate_relationship_evidence_bindings(
                [omitted], records=records
            )

    def test_rights_record_family_and_assessment_rows_cannot_be_swapped(
        self,
    ) -> None:
        records, _manifest, bindings, assertions = (
            self.source_relationship_fixture()
        )
        rights = [
            row
            for row in assertions
            if row["predicate"]["@id"] == builder.RIGHTS_PREDICATE
        ]
        first = rights[0]
        another_record = next(
            row
            for row in rights
            if row["evidence"][0]["source_locator"]
            != first["evidence"][0]["source_locator"]
        )
        another_family = next(
            row
            for row in rights
            if row["evidence"][1]["source_field"]
            != first["evidence"][1]["source_field"]
        )
        another_assessment = next(
            row
            for row in rights
            if row["evidence"][2]["source_field"]
            != first["evidence"][2]["source_field"]
        )
        swaps = {
            "record": (0, another_record),
            "family": (1, another_family),
            "assessment": (2, another_assessment),
        }
        for role, (ordinal, donor) in swaps.items():
            with self.subTest(role=role):
                changed = copy.deepcopy(first)
                changed["evidence"][ordinal] = copy.deepcopy(
                    donor["evidence"][ordinal]
                )
                self.refresh_evidence_id(changed, ordinal)
                with self.assertRaises(ValueError):
                    builder.validate_relationship_evidence_bindings(
                        [changed],
                        records=records,
                        record_bindings=bindings,
                    )

    def test_endpoint_predicate_and_semantic_target_mutations_fail_closed(self) -> None:
        records, _manifest, _bindings, assertions = (
            self.source_relationship_fixture()
        )
        base_assertion = next(
            row
            for row in assertions
            if row["predicate"]["@id"] == builder.CATALOGUE_RECORD_PREDICATE
        )

        source_mutation = copy.deepcopy(base_assertion)
        source_mutation["source"]["@id"] = builder.HMLR_PUBLISHER_IRI
        self.refresh_assertion_id(source_mutation)
        with self.assertRaisesRegex(ValueError, "not deterministic"):
            builder.validate_relationship_evidence_bindings([source_mutation])

        predicate_mutation = copy.deepcopy(base_assertion)
        predicate_mutation["predicate"]["@id"] = builder.SPATIAL_PREDICATE
        self.refresh_assertion_id(predicate_mutation)
        with self.assertRaisesRegex(ValueError, "not deterministic"):
            builder.validate_relationship_evidence_bindings([predicate_mutation])

        records_by_id = {record["record_id"]: record for record in records}
        language = next(
            row
            for row in assertions
            if row["predicate"]["@id"] == builder.LANGUAGE_PREDICATE
            and row["target"]["@id"].endswith("/ENG")
            and records_by_id[row["evidence"][0]["source_locator"]]["languages"]
            == ["en"]
        )
        target_mutation = copy.deepcopy(language)
        welsh = "http://publications.europa.eu/resource/authority/language/CYM"
        target_mutation["target"]["@id"] = welsh
        self.refresh_assertion_id(target_mutation)
        self.refresh_evidence_id(target_mutation)
        with self.assertRaisesRegex(ValueError, "language evidence"):
            builder.validate_relationship_evidence_bindings(
                [target_mutation], records=records
            )

    def test_cpsv_service_substitution_and_translation_reversal_fail(self) -> None:
        records, _manifest, _bindings, assertions = (
            self.source_relationship_fixture()
        )
        cpsv_rows = [
            row
            for row in assertions
            if row["predicate"]["@id"]
            == builder.COMPETENT_AUTHORITY_PREDICATE
        ]
        substituted = copy.deepcopy(cpsv_rows[0])
        substituted["evidence"] = copy.deepcopy(cpsv_rows[1]["evidence"])
        with self.assertRaisesRegex(ValueError, "not deterministic"):
            builder.validate_relationship_evidence_bindings([substituted])

        cpsv_mappings = builder.load_cpsv_service_mappings(records)
        decision_mutation = copy.deepcopy(cpsv_rows[0])
        decision_mutation["source"] = copy.deepcopy(cpsv_rows[1]["source"])
        decision_mutation["source_route"] = cpsv_rows[1]["source_route"]
        self.refresh_assertion_id(decision_mutation)
        self.refresh_evidence_id(decision_mutation)
        with self.assertRaisesRegex(
            ValueError, "role-governed|reference does not support"
        ):
            builder.validate_relationship_evidence_bindings(
                [decision_mutation], records=records, cpsv_mappings=cpsv_mappings
            )

        reference_mutation = copy.deepcopy(cpsv_rows[0])
        reference_mutation["evidence"][0]["value"] = cpsv_rows[1]["evidence"][0][
            "value"
        ]
        self.refresh_evidence_id(reference_mutation)
        with self.assertRaisesRegex(ValueError, "reference does not support"):
            builder.validate_relationship_evidence_bindings(
                [reference_mutation], records=records, cpsv_mappings=cpsv_mappings
            )

        translation = copy.deepcopy(
            next(
                row
                for row in assertions
                if row["predicate"]["@id"] == builder.TRANSLATION_PREDICATE
            )
        )
        translation["source"], translation["target"] = (
            translation["target"],
            translation["source"],
        )
        translation["source_route"], translation["target_route"] = (
            translation["target_route"],
            translation["source_route"],
        )
        evidence = translation["evidence"][0]
        evidence["source_locator"] = translation["source"]["@id"].rsplit("/", 1)[-1]
        evidence["resource"] = translation["source"]["@id"]
        self.refresh_assertion_id(translation)
        self.refresh_evidence_id(translation)
        with self.assertRaisesRegex(ValueError, "wrong direction or locale"):
            builder.validate_relationship_evidence_bindings(
                [translation], records=records
            )

    def test_rights_publishers_and_translation_use_exact_source_rows(self) -> None:
        records, _manifest, _bindings, assertions = (
            self.source_relationship_fixture()
        )
        rights = [
            row
            for row in assertions
            if row["predicate"]["@id"] == builder.RIGHTS_PREDICATE
        ]
        publishers = [
            row
            for row in assertions
            if row["predicate"]["@id"] == builder.PUBLISHER_PREDICATE
        ]
        translations = [
            row
            for row in assertions
            if row["predicate"]["@id"] == builder.TRANSLATION_PREDICATE
        ]
        self.assertEqual(4_412, len(rights))
        self.assertEqual(2_247, len(publishers))
        self.assertEqual(1, len(translations))
        self.assertTrue(all(
            [evidence["type"] for evidence in row["evidence"][:3]]
            == [
                "governed-record-rights-selection",
                "governed-source-family-rights-policy",
                "governed-rights-assessment",
            ]
            for row in rights
        ))
        self.assertTrue(all(
            row["evidence"][1]["source_field"].startswith("source_families[")
            and row["evidence"][2]["source_field"].startswith("assessments[")
            for row in rights
        ))
        curated_rights = [
            row
            for row in rights
            if len(row["evidence"]) == 4
            and row["evidence"][3]["type"]
            == "governed-curated-rights-classification"
        ]
        self.assertEqual(116, len(curated_rights))
        self.assertTrue(all(
            row["evidence"][3]["source_artifact"]
            == "source/curated-rights-access.json"
            and row["evidence"][3]["source_field"].startswith(
                "classifications[source_native_id='"
            )
            for row in curated_rights
        ))
        self.assertTrue(all(
            len(row["evidence"]) == 3
            for row in rights
            if row not in curated_rights
        ))
        self.assertTrue(all(
            [item["type"] for item in row["evidence"]]
            == ["frozen-source-metadata", "governed-identity-registry"]
            for row in publishers
        ))
        self.assertTrue(all(
            row["evidence"][1]["source_field"].startswith("publishers[")
            for row in publishers
        ))
        declared_govuk_publishers = [
            row
            for row in publishers
            if ".organisations[" in row["evidence"][0]["source_field"]
        ]
        self.assertEqual(1_910, len(declared_govuk_publishers))
        self.assertEqual(
            2_248,
            sum(len(record["publishers"]) for record in records),
        )
        self.assertEqual(
            "observations[0]", translations[0]["evidence"][0]["source_field"]
        )
        self.assertEqual(
            "implementation-authorised-pending-release-review",
            translations[0]["review_status"],
        )

    def test_shared_rights_nodes_are_governed_and_record_order_invariant(
        self,
    ) -> None:
        records, _manifest, _bindings, assertions = (
            self.source_relationship_fixture()
        )
        snapshot_dir = builder.newest_snapshot()
        self.assertIsNotNone(snapshot_dir)
        _snapshot_records, snapshot = builder.snapshot_records(snapshot_dir)
        cpsv_mappings = builder.load_cpsv_service_mappings(records)
        config = builder.load_build_config()
        validation_receipts: dict = {}
        first = builder.jsonld_projection(
            builder.PUBLICATION_BASE,
            snapshot,
            records,
            assertions,
            cpsv_mappings,
            config,
            validation_receipts=validation_receipts,
        )
        reversed_projection = builder.jsonld_projection(
            builder.PUBLICATION_BASE,
            snapshot,
            list(reversed(records)),
            assertions,
            cpsv_mappings,
            config,
        )
        self.assertEqual(first, reversed_projection)

        self.assertEqual(
            "conformant", validation_receipts["class_closure"]["status"]
        )
        self.assertEqual(
            len(
                {
                    builder.semantic_record_iri(builder.PUBLICATION_BASE, row)
                    for row in records
                }
            ),
            validation_receipts["class_closure"]["record_entities_validated"],
        )

        rights_types = sorted(
            builder.stage1_entity_type_classes("TYPE-RIGHTS-STATEMENT")
        )
        rights_nodes = {
            row["dcterms:identifier"]: row
            for row in first["@graph"]
            if row.get("@type") == rights_types
        }
        _sources, governed_rights = builder.source_controls()
        self.assertEqual(set(governed_rights), set(rights_nodes))
        self.assertEqual(10, len(rights_nodes))
        for rights_ref in (
            "RIGHT-DATASETS",
            "RIGHT-GOVUK",
            "RIGHT-PERSONAL",
        ):
            self.assertEqual(
                governed_rights[rights_ref]["layer"],
                rights_nodes[rights_ref]["schema:name"],
            )
            self.assertEqual(
                governed_rights[rights_ref]["status"],
                rights_nodes[rights_ref]["dcterms:type"],
            )
        self.assertGreater(len({record["rights_state"] for record in records}), 3)

        source_types = sorted(
            builder.stage1_entity_type_classes("TYPE-SOURCE-RESOURCE")
        )
        source_nodes = [
            row for row in first["@graph"] if row.get("@type") == source_types
        ]
        expected_source_urls = {
            url for record in records for url in record["source_urls"]
        }
        self.assertEqual(2_243, len(expected_source_urls))
        self.assertEqual(len(expected_source_urls), len(source_nodes))
        self.assertEqual(
            expected_source_urls,
            {row["dcterms:identifier"] for row in source_nodes},
        )
        self.assertEqual(
            len(source_nodes), len({row["@id"] for row in source_nodes})
        )

        jurisdiction_types = sorted(
            builder.stage1_entity_type_classes("TYPE-LOCATION")
        )
        jurisdiction_nodes = [
            row
            for row in first["@graph"]
            if row.get("@type") == jurisdiction_types
        ]
        governed_jurisdictions = builder.stage1_jurisdiction_registry()
        self.assertEqual(len(governed_jurisdictions), len(jurisdiction_nodes))
        self.assertEqual(
            {row["iri"] for row in governed_jurisdictions.values()},
            {row["@id"] for row in jurisdiction_nodes},
        )
        self.assertEqual(
            {row["route"] for row in governed_jurisdictions.values()},
            {row["route"] for row in jurisdiction_nodes},
        )

    def test_source_snapshot_cache_detects_same_size_restored_mtime_change(self) -> None:
        builder._load_source_artifact_snapshot.cache_clear()
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary_directory:
            path = Path(temporary_directory) / "source-cache-fixture.json"
            path.write_bytes(b'{"value":1}\n')
            original = path.stat()
            with mock.patch.object(
                builder, "_safe_source_artifact_path", return_value=path
            ):
                first_digest, first_value = builder.source_artifact_snapshot(
                    "source/cache-fixture.json"
                )
                path.write_bytes(b'{"value":2}\n')
                os.utime(
                    path,
                    ns=(original.st_atime_ns, original.st_mtime_ns),
                )
                second_digest, second_value = builder.source_artifact_snapshot(
                    "source/cache-fixture.json"
                )
        builder._load_source_artifact_snapshot.cache_clear()
        self.assertNotEqual(first_digest, second_digest)
        self.assertEqual({"value": 1}, first_value)
        self.assertEqual({"value": 2}, second_value)

    def test_fresh_assertions_round_trip_complete_runtime_rows_and_evidence(self) -> None:
        records, _manifest, bindings, assertions = (
            self.source_relationship_fixture()
        )
        cpsv_mappings = builder.load_cpsv_service_mappings(records)
        receipt = builder.validate_relationship_evidence_bindings(
            assertions,
            records=records,
            record_bindings=bindings,
            cpsv_mappings=cpsv_mappings,
        )
        self.assertEqual(22_267, receipt["assertions_validated"])
        self.assertEqual(33_461, receipt["evidence_rows_validated"])
        self.assertEqual(4_230, receipt["evidence_resources_validated"])
        self.assertRegex(
            receipt["evidence_identity_set_sha256"], r"^[0-9a-f]{64}$"
        )
        self.assertEqual(
            "3891f3ac1107628a4c079b649d5c8a407ba983bd95694bddd0b6020f9153bd30",
            receipt["evidence_resource_identity_set_sha256"],
        )
        self.assertRegex(
            receipt["evidence_row_set_sha256"], r"^[0-9a-f]{64}$"
        )

        validator, _binding = builder.load_pinned_semantic_assertion_schema()
        runtime_rows = [builder.runtime_relationship(row) for row in assertions]
        for assertion, runtime_row in zip(assertions, runtime_rows, strict=True):
            mapped = builder.runtime_relationship_as_semantic(runtime_row)
            self.assertEqual(assertion, mapped)
            self.assertEqual([], list(validator.iter_errors(assertion)))

        snapshot_dir = builder.newest_snapshot()
        self.assertIsNotNone(snapshot_dir)
        _snapshot_records, snapshot = builder.snapshot_records(snapshot_dir)
        semantic_document = builder.jsonld_projection(
            builder.PUBLICATION_BASE,
            snapshot,
            records,
            assertions,
            cpsv_mappings,
            builder.load_build_config(),
        )
        plane_receipt = builder.validate_semantic_relationship_planes(
            semantic_document, runtime_rows
        )
        self.assertEqual(22_267, plane_receipt["counts"]["direct_triples_reconciled"])
        self.assertEqual(33_461, plane_receipt["counts"]["evidence_rows_validated"])
        self.assertEqual(
            receipt["evidence_identity_set_sha256"],
            plane_receipt["evidence_identity_set_sha256"],
        )
        self.assertEqual(
            receipt["evidence_row_set_sha256"],
            plane_receipt["evidence_row_set_sha256"],
        )

    def test_fresh_rich_runtime_evidence_matches_exact_pinned_schema(self) -> None:
        _records, _manifest, _bindings, assertions = (
            self.source_relationship_fixture()
        )
        row_schema = builder.load_json(builder.RICH_RELATIONSHIP_ROW_SCHEMA_PATH)
        validator = builder.Draft202012Validator(
            row_schema, format_checker=builder.FormatChecker()
        )
        allowed_evidence_fields = set(
            row_schema["$defs"]["evidence"]["properties"]
        )
        plane_id = builder.RICH_RELATIONSHIP_PLANE_IRI
        for assertion in assertions:
            runtime = builder.runtime_relationship(assertion)
            rich = builder.rich_runtime_relationship(runtime, plane_id)
            self.assertEqual([], list(validator.iter_errors(rich)))
            self.assertEqual("Derived", rich["authority"]["label"])
            self.assertEqual(
                "See source rights.",
                rich["rights"]["assertion"],
            )
            self.assertIn("review_status", runtime)
            self.assertNotIn("review_status", rich)
            for evidence in rich["evidence"]:
                self.assertFalse(set(evidence) - allowed_evidence_fields)
                self.assertNotIn("source_artifact", evidence)
                self.assertNotIn("source_sha256", evidence)
                self.assertNotIn("normalization", evidence)

        cpsv = next(
            row
            for row in assertions
            if row["predicate"]["@id"]
            == builder.COMPETENT_AUTHORITY_PREDICATE
        )
        self.assertTrue(
            all(item["value"].startswith("CPSV-E-") for item in cpsv["evidence"])
        )
        translation = next(
            row
            for row in assertions
            if row["predicate"]["@id"] == builder.TRANSLATION_PREDICATE
        )
        self.assertEqual(
            36,
            len(translation["evidence"][0]["value"]),
        )

    def test_fresh_source_rich_runtime_fits_full_hydration_ceiling(self) -> None:
        _records, _manifest, _bindings, assertions = (
            self.source_relationship_fixture()
        )
        relationship_rows = [
            builder.runtime_relationship(assertion) for assertion in assertions
        ]
        receipt = builder.validate_rich_relationship_full_hydration_preflight(
            relationship_rows
        )
        self.assertEqual(22_267, receipt["rows"])
        self.assertEqual(33_231_836, receipt["retained_text_units"])
        self.assertEqual(
            33_554_432,
            receipt["locked_maximum_retained_text_units"],
        )
        self.assertEqual(322_596, receipt["remaining_retained_text_units"])

        rich = builder.rich_runtime_relationship(
            relationship_rows[0],
            builder.RICH_RELATIONSHIP_PLANE_IRI,
        )
        rich["review_status"] = "optional-normalized-review"
        projected = builder.rich_runtime_reader_projection(rich)
        self.assertEqual(
            "optional-normalized-review",
            projected["review_status"],
        )

    def test_prose_placeholders_become_null_with_controlled_states(self) -> None:
        record = normal_record(
            {
                "id": "fixture-placeholders",
                "title": "Placeholder fixture",
                "url": "https://www.gov.uk/example/placeholders",
                "record_type": "guidance",
                "source_family": "govuk-hmlr",
                "publisher": "HM Land Registry",
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
                "publisher": "HM Land Registry",
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
            "https://schema.org/translationOfWork",
            {relationship["predicate"] for relationship in relationships},
        )
        required = {
            "id",
            "source",
            "target",
            "source_iri",
            "target_iri",
            "predicate",
            "label",
            "inverse_label",
            "assertion_status",
            "assertion_scope",
            "authority",
            "derivation",
            "observed_at",
            "evidence",
            "rights",
        }
        for relationship in relationships:
            self.assertFalse(required - set(relationship))
            for field in ("id", "source_iri", "target_iri", "derivation"):
                parsed = urlparse(relationship[field])
                self.assertIn(parsed.scheme, {"http", "https"})
                self.assertTrue(parsed.netloc)
            self.assertIn(
                urlparse(relationship["predicate"]).scheme,
                {"http", "https"},
            )
            self.assertEqual("normalized", relationship["assertion_status"])
            self.assertEqual("real-world", relationship["assertion_scope"])
            self.assertEqual("derived", relationship["authority"]["class"])
            self.assertTrue(relationship["evidence"])
            self.assertEqual(64, len(relationship["evidence"][0]["source_sha256"]))
            self.assertEqual(64, len(relationship["evidence"][0]["source_value_sha256"]))

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

    def test_relationship_contract_fails_closed(self) -> None:
        _records, _manifest, _bindings, assertions = (
            self.source_relationship_fixture()
        )
        assertion = copy.deepcopy(assertions[0])
        validate_relationship_assertions([assertion])

        missing_evidence = copy.deepcopy(assertion)
        missing_evidence.pop("evidence")
        with self.assertRaisesRegex(ValueError, "required fields"):
            validate_relationship_assertions([missing_evidence])

        authority_conflict = copy.deepcopy(assertion)
        authority_conflict["authority"]["class"] = "official"
        with self.assertRaisesRegex(ValueError, "authority/status conflict"):
            validate_relationship_assertions([authority_conflict])

    def test_relationship_web_sources_reject_query_credentials(self) -> None:
        _records, _manifest, _bindings, assertions = (
            self.source_relationship_fixture()
        )
        assertion = assertions[0]
        cases = (
            ("authority.source", "authority", None),
            ("evidence.url", "evidence", "url"),
            ("evidence.resource", "evidence", "resource"),
            ("rights.source", "rights", None),
        )
        for field, container, evidence_field in cases:
            with self.subTest(field=field):
                changed = copy.deepcopy(assertion)
                credential_url = (
                    "https://example.test/source?accessToken=not-a-real-secret"
                )
                if container == "authority":
                    changed["authority"]["source"] = credential_url
                elif container == "rights":
                    changed["rights"]["source"] = credential_url
                else:
                    changed["evidence"][0][evidence_field] = credential_url
                    self.refresh_evidence_id(changed)
                with self.assertRaisesRegex(ValueError, "credential-free"):
                    validate_relationship_assertions([changed])

    def test_candidate_control_concepts_are_draft_and_candidate_neutral(self) -> None:
        config = load_build_config()
        self.assertEqual("ai-generated-proof-of-concept", config["status"])
        self.assertIsNone(config.get("release_at"))
        snapshot = {
            "snapshot_id": "test-snapshot",
            "observed_at": "2026-07-29T09:19:15Z",
        }
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary_directory:
            output = Path(temporary_directory)
            builder.write_control_concepts(output, snapshot, config)
            concept_paths = sorted((output / "concepts").glob("*.md"))
            self.assertEqual(4, len(concept_paths))
            generated_markdown = [
                path.read_text(encoding="utf-8")
                for path in [
                    output / "index.md",
                    output / "log.md",
                    *concept_paths,
                ]
            ]
        for body in generated_markdown:
            self.assertNotIn("This release", body)
        for body in generated_markdown[2:]:
            self.assertIn('\nstatus: "draft"\n', body)
            self.assertNotIn('\nstatus: "released"\n', body)
        scope = next(
            body
            for path, body in zip(concept_paths, generated_markdown[2:], strict=True)
            if path.name == "scope-and-authority.md"
        )
        self.assertIn("This bundle candidate is", scope)

    def test_build_publication_base_is_strict_and_governed(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "differs from governed.*build-config.json"
        ):
            builder.build(
                snapshot_dir=None,
                output_dir=ROOT / "unused-publication-base-test-output",
                publication_base="https://different.example.test/",
                replace=False,
            )

        with self.assertRaisesRegex(ValueError, "literal whitespace"):
            builder.build(
                snapshot_dir=None,
                output_dir=ROOT / "unused-publication-base-test-output",
                publication_base="https://example.test /unsafe/",
                replace=False,
            )

        changed_config = copy.deepcopy(load_build_config())
        changed_config["publication_base"] = "https://different.example.test/"
        with mock.patch.object(
            builder, "load_build_config", return_value=changed_config
        ):
            with self.assertRaisesRegex(
                ValueError, "differs from governed.*build-config.json"
            ):
                builder.build(
                    snapshot_dir=None,
                    output_dir=ROOT / "unused-publication-base-test-output",
                    publication_base=builder.PUBLICATION_BASE,
                    replace=False,
                )

    def test_final_explorer_schema_validates_both_emitted_planes(self) -> None:
        validator, binding = load_pinned_semantic_assertion_schema()
        self.assertEqual(SEMANTIC_ASSERTION_SCHEMA_BYTES, binding["bytes"])
        self.assertEqual(SEMANTIC_ASSERTION_SCHEMA_SHA256, binding["sha256"])
        self.assertFalse(binding["network_resolution_allowed"])
        _records, _manifest, _bindings, expected_assertions = (
            self.source_relationship_fixture()
        )
        expected_evidence_rows = sum(
            len(assertion["evidence"])
            for assertion in expected_assertions
        )

        semantic_document = json.loads(
            (ROOT / "bundle" / "okf-bundle.jsonld").read_text()
        )
        manifest = json.loads(
            (ROOT / "bundle" / "data" / "explorer" / "manifest.json").read_text()
        )
        runtime_rows = [
            row
            for reference in manifest["chunks"]["relationships"]
            for row in json.loads(
                (ROOT / "bundle" / reference["path"]).read_text()
            )
        ]
        report = validate_semantic_relationship_planes(
            semantic_document, runtime_rows
        )
        expected = len(runtime_rows)
        self.assertGreater(expected, 2_200)
        self.assertEqual(expected, report["counts"]["semantic_assertions_validated"])
        self.assertEqual(
            expected,
            report["counts"]["runtime_rows_mapped_and_validated"],
        )
        self.assertEqual(expected, report["counts"]["direct_triples_reconciled"])
        self.assertEqual(
            expected_evidence_rows,
            report["counts"]["evidence_rows_validated"],
        )
        self.assertEqual(0, report["counts"]["validation_failures"])
        self.assertTrue(report["parity"]["direct_reified_runtime"])
        self.assertRegex(report["evidence_identity_set_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(report["evidence_row_set_sha256"], r"^[0-9a-f]{64}$")
        mapped = runtime_relationship_as_semantic(runtime_rows[0])
        self.assertEqual([], list(validator.iter_errors(mapped)))

    def test_rich_runtime_locality_is_deterministic_and_hub_aware(self) -> None:
        hub = "publisher/hub"
        rows = [
            {
                "id": f"https://example.test/assertion/{ordinal:02d}",
                "source": hub,
                "target": f"dataset/leaf-{ordinal:02d}",
            }
            for ordinal in range(12)
        ]
        rows.extend(
            {
                "id": f"https://example.test/assertion/other-{ordinal:02d}",
                "source": f"dataset/other-{ordinal:02d}",
                "target": f"rights/right-{ordinal:02d}",
            }
            for ordinal in range(5)
        )
        ordered = builder.order_rich_runtime_rows_for_route_locality(rows)
        reversed_ordered = builder.order_rich_runtime_rows_for_route_locality(
            list(reversed(rows))
        )
        self.assertEqual(
            [row["id"] for row in ordered],
            [row["id"] for row in reversed_ordered],
        )
        hub_ordinals = [
            ordinal
            for ordinal, row in enumerate(ordered)
            if hub in {row["source"], row["target"]}
        ]
        self.assertEqual(
            list(range(min(hub_ordinals), max(hub_ordinals) + 1)),
            hub_ordinals,
        )
        self.assertEqual(3, builder.utf16_text_units("A\U0001f600"))
        self.assertEqual(
            4,
            builder.retained_text_units(
                {"ignored-key": ["A\U0001f600", 3, True, "B"]}
            ),
        )

    def test_rich_runtime_limits_reconcile_and_fail_closed(self) -> None:
        _records, _manifest, _bindings, assertions = (
            self.source_relationship_fixture()
        )
        relationship = builder.runtime_relationship(assertions[0])
        plane_id = builder.RICH_RELATIONSHIP_PLANE_IRI
        first = builder.rich_runtime_relationship(relationship, plane_id)
        second = copy.deepcopy(first)
        second["id"] = second["id"] + "/locality-copy"
        second["assertion_id"] = second["id"]
        first_retained = builder.retained_text_units(
            builder.rich_runtime_reader_projection(first)
        )
        second_retained = builder.retained_text_units(
            builder.rich_runtime_reader_projection(second)
        )
        chunks = [
            {
                "path": "data/semantic/runtime/core/relationships-000.json.gz",
                "rows": 1,
                "compressed_bytes": 101,
                "decoded_bytes": 1_001,
                "retained_text_units": first_retained,
            },
            {
                "path": "data/semantic/runtime/core/relationships-001.json.gz",
                "rows": 1,
                "compressed_bytes": 202,
                "decoded_bytes": 1_002,
                "retained_text_units": second_retained,
            },
        ]
        route = first["source"]
        incident = {
            route: {
                "assertion_ids": {first["id"], second["id"]},
                "chunks": {chunk["path"] for chunk in chunks},
            }
        }
        buckets = [
            {
                "path": "data/semantic/runtime/route-locator/bucket-00.json.gz",
                "compressed_bytes": 77,
                "decoded_bytes": 777,
            }
        ]
        limits = builder.locked_rich_relationship_limits()
        report = builder.validate_rich_relationship_runtime_limits(
            [first, second], chunks, buckets, incident, limits
        )
        self.assertEqual("passed", report["status"])
        self.assertEqual(
            builder.EXPLORER_V062_GIT_TREE,
            report["consumer"]["git_tree"],
        )
        self.assertEqual(
            {
                "route_chunks": 2,
                "route_declared_rows": 2,
                "route_incident_rows": 2,
                "route_compressed_bytes": 303,
                "route_retained_text_units": (
                    first_retained + second_retained
                ),
            },
            {
                name: report["maxima"][name]
                for name in (
                    "route_chunks",
                    "route_declared_rows",
                    "route_incident_rows",
                    "route_compressed_bytes",
                    "route_retained_text_units",
                )
            },
        )

        self.assertEqual(
            {
                "full_hydration_chunks": 2,
                "full_hydration_declared_rows": 2,
                "full_hydration_compressed_bytes": 303,
                "full_hydration_retained_text_units": (
                    first_retained + second_retained
                ),
            },
            {
                name: report["maxima"][name]
                for name in (
                    "full_hydration_chunks",
                    "full_hydration_declared_rows",
                    "full_hydration_compressed_bytes",
                    "full_hydration_retained_text_units",
                )
            },
        )

        failing_limits = dict(limits)
        failing_limits["maximum_rich_relationship_route_chunks"] = 1
        with self.assertRaisesRegex(ValueError, "selected chunk count"):
            builder.validate_rich_relationship_runtime_limits(
                [first, second], chunks, buckets, incident, failing_limits
            )

        failing_limits = dict(limits)
        failing_limits["maximum_rich_relationship_retained_text_units"] = (
            first_retained + second_retained - 1
        )
        with self.assertRaisesRegex(ValueError, "hydration retained text"):
            builder.validate_rich_relationship_runtime_limits(
                [first, second], chunks, buckets, incident, failing_limits
            )

        failing_limits = dict(limits)
        failing_limits["maximum_rich_relationship_chunk_bytes"] = 201
        with self.assertRaisesRegex(ValueError, "chunk .* compressed bytes"):
            builder.validate_rich_relationship_runtime_limits(
                [first, second], chunks, buckets, incident, failing_limits
            )

        failing_limits = dict(limits)
        failing_limits["maximum_relationship_rows"] = 1
        with self.assertRaisesRegex(ValueError, "runtime row total"):
            builder.validate_rich_relationship_runtime_limits(
                [first, second], chunks, buckets, incident, failing_limits
            )

        too_many_supports = copy.deepcopy(first)
        too_many_supports["supporting_assertions"] = [
            f"https://example.test/assertion/support-{ordinal}"
            for ordinal in range(129)
        ]
        with self.assertRaisesRegex(ValueError, "supporting-assertion count"):
            builder.validate_rich_relationship_runtime_limits(
                [too_many_supports],
                [
                    {
                        **chunks[0],
                        "retained_text_units": builder.retained_text_units(
                            builder.rich_runtime_reader_projection(
                                too_many_supports
                            )
                        ),
                    }
                ],
                buckets,
                {
                    route: {
                        "assertion_ids": {too_many_supports["id"]},
                        "chunks": {chunks[0]["path"]},
                    }
                },
                limits,
            )

        failing_limits = dict(limits)
        failing_limits["maximum_rich_relationship_retained_text_units"] = max(
            first_retained, second_retained
        )
        split_incident = {
            first["source"]: {
                "assertion_ids": {first["id"]},
                "chunks": {chunks[0]["path"]},
            },
            second["target"]: {
                "assertion_ids": {second["id"]},
                "chunks": {chunks[1]["path"]},
            },
        }
        with self.assertRaisesRegex(ValueError, "full hydration retained text"):
            builder.validate_rich_relationship_runtime_limits(
                [first, second],
                chunks,
                buckets,
                split_incident,
                failing_limits,
            )

    def test_rich_runtime_limit_lock_rejects_a_changed_pinned_ceiling(
        self,
    ) -> None:
        lock = json.loads(builder.EXPLORER_CONSUMER_LOCK_PATH.read_text())
        lock["limits"]["maximum_rich_relationship_route_chunks"] -= 1
        builder.locked_rich_relationship_limits.cache_clear()
        self.addCleanup(builder.locked_rich_relationship_limits.cache_clear)
        with mock.patch.object(builder, "load_json", return_value=lock):
            with self.assertRaisesRegex(
                ValueError,
                "executable Explorer v0.6.2 contract",
            ):
                builder.locked_rich_relationship_limits()

    def test_rich_runtime_limit_lock_rejects_a_changed_explorer_tree(
        self,
    ) -> None:
        lock = json.loads(builder.EXPLORER_CONSUMER_LOCK_PATH.read_text())
        lock["consumer"]["git_tree"] = "0" * 40
        builder.locked_rich_relationship_limits.cache_clear()
        self.addCleanup(builder.locked_rich_relationship_limits.cache_clear)
        with mock.patch.object(builder, "load_json", return_value=lock):
            with self.assertRaisesRegex(
                ValueError,
                "consumer-lock identity is unsupported",
            ):
                builder.locked_rich_relationship_limits()

    def test_rich_runtime_projection_rejects_non_ascii_assertion_id(self) -> None:
        _records, _manifest, _bindings, assertions = (
            self.source_relationship_fixture()
        )
        relationship = builder.runtime_relationship(assertions[0])
        relationship["id"] = relationship["id"] + "/café"
        with self.assertRaisesRegex(ValueError, "must be ASCII"):
            builder.rich_runtime_relationship(
                relationship,
                builder.RICH_RELATIONSHIP_PLANE_IRI,
            )

    def test_rich_runtime_control_ids_are_stage1_validated(self) -> None:
        assertion = builder.normalized_relationship_assertion(
            builder.PUBLICATION_BASE,
            source_iri=(
                builder.PUBLICATION_BASE
                + "id/catalogue-record/hmlr-"
                + "a" * 24
            ),
            predicate_iri=builder.PUBLISHER_PREDICATE,
            target_iri=builder.HMLR_PUBLISHER_IRI,
            source_route="dataset/example",
            target_route="publisher/example",
            observed_at="2026-08-11T00:00:00Z",
            evidence_url="https://www.gov.uk/example",
            source_artifact="source/example.json",
            source_sha256="1" * 64,
            source_field="results[0]",
            source_value={"id": "example"},
            locator="results[0]",
            record_id="hmlr-" + "a" * 24,
        )
        relationship = builder.runtime_relationship(assertion)
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            with mock.patch.object(
                builder,
                "validate_stage1_identity",
                wraps=builder.validate_stage1_identity,
            ) as identity_validator:
                builder.write_rich_relationship_runtime(
                    output,
                    [relationship],
                    "snapshot-test",
                    "2026-08-11T00:00:00Z",
                    builder.PUBLICATION_BASE,
                )
            runtime = json.loads(
                (
                    output / builder.RICH_RELATIONSHIP_RUNTIME_BUNDLE_PATH
                ).read_text()
            )
        validated_runtime_ids = [
            (call.args[0], call.args[1], call.kwargs.get("expected_role"))
            for call in identity_validator.call_args_list
        ]
        self.assertEqual(
            builder.PUBLICATION_BASE + "id/semantic-runtime/relationships",
            runtime["@id"],
        )
        self.assertEqual(
            builder.PUBLICATION_BASE + "id/semantic-runtime/route-locator",
            runtime["route_locator"]["id"],
        )
        self.assertRegex(
            runtime["planes"][0]["chunks"][0]["id"],
            (
                "^"
                + re.escape(builder.PUBLICATION_BASE)
                + r"id/semantic-runtime-chunk/core-000-[0-9a-f]{16}$"
            ),
        )
        self.assertEqual(
            [
                (
                    "IDF-SEMANTIC-RUNTIME",
                    runtime["@id"],
                    "runtime-control",
                ),
                (
                    "IDF-SEMANTIC-RUNTIME",
                    runtime["route_locator"]["id"],
                    "runtime-control",
                ),
                (
                    "IDF-SEMANTIC-RUNTIME-CHUNK",
                    runtime["planes"][0]["chunks"][0]["id"],
                    "runtime-control",
                ),
            ],
            validated_runtime_ids,
        )

        original_family = builder.stage1_identity_family
        mutations = {
            "IDF-SEMANTIC-RUNTIME": (
                builder.PUBLICATION_BASE
                + "id/not-semantic-runtime/<runtime-name>"
            ),
            "IDF-SEMANTIC-RUNTIME-CHUNK": (
                builder.PUBLICATION_BASE
                + "id/not-semantic-runtime-chunk/"
                + "<plane-chunk-and-digest-key>"
            ),
        }
        for target_family, mutated_pattern in mutations.items():
            with self.subTest(family=target_family):

                def mutated_family(
                    family_id: str,
                    *,
                    expected_role: str | None = None,
                ) -> dict:
                    family = copy.deepcopy(
                        original_family(
                            family_id, expected_role=expected_role
                        )
                    )
                    if family_id == target_family:
                        family["iri_pattern"] = mutated_pattern
                    return family

                with mock.patch.object(
                    builder,
                    "stage1_identity_family",
                    side_effect=mutated_family,
                ):
                    with tempfile.TemporaryDirectory() as temporary_directory:
                        with self.assertRaisesRegex(
                            ValueError,
                            f"identity differs from Stage 1 family {target_family}",
                        ):
                            builder.write_rich_relationship_runtime(
                                Path(temporary_directory),
                                [relationship],
                                "snapshot-test",
                                "2026-08-11T00:00:00Z",
                                builder.PUBLICATION_BASE,
                            )

    def test_complete_rich_runtime_and_route_locator_are_integrity_bound(self) -> None:
        _records, _manifest, _bindings, expected_source_assertions = (
            self.source_relationship_fixture()
        )
        expected_assertion_count = len(expected_source_assertions)
        expected_chunk_count = (
            expected_assertion_count + builder.SHARD_SIZE - 1
        ) // builder.SHARD_SIZE
        expected_route_count = len(
            {
                route
                for assertion in expected_source_assertions
                for route in (
                    assertion["source_route"],
                    assertion["target_route"],
                )
            }
        )
        bundle = ROOT / "bundle"
        semantic_document = json.loads(
            (bundle / "okf-bundle.jsonld").read_text()
        )
        semantic_rows_by_id = {
            row["@id"]: row
            for row in semantic_document["@graph"]
            if RELATIONSHIP_ASSERTION_TYPE in row.get("@type", [])
        }
        semantic_assertions = {
            row["@id"]: (
                row["source"]["@id"],
                row["predicate"]["@id"],
                row["target"]["@id"],
            )
            for row in semantic_document["@graph"]
            if RELATIONSHIP_ASSERTION_TYPE in row.get("@type", [])
        }

        explorer_manifest = json.loads(
            (bundle / "data" / "explorer" / "manifest.json").read_text()
        )
        explorer_rows: list[dict] = []
        explorer_chunks = explorer_manifest["chunks"]["relationships"]
        self.assertEqual(expected_chunk_count, len(explorer_chunks))
        for chunk_ordinal, reference in enumerate(explorer_chunks):
            path = bundle / reference["path"]
            self.assertEqual(reference["bytes"], path.stat().st_size)
            self.assertEqual(reference["sha256"], builder.sha256_file(path))
            rows = json.loads(path.read_text())
            self.assertEqual(
                min(
                    builder.SHARD_SIZE,
                    expected_assertion_count
                    - chunk_ordinal * builder.SHARD_SIZE,
                ),
                len(rows),
            )
            explorer_rows.extend(rows)
        explorer_assertions = {
            row["id"]: (
                row["source_iri"],
                row["predicate"],
                row["target_iri"],
            )
            for row in explorer_rows
        }
        explorer_rows_by_id = {row["id"]: row for row in explorer_rows}

        runtime_path = bundle / builder.RICH_RELATIONSHIP_RUNTIME_BUNDLE_PATH
        runtime = json.loads(runtime_path.read_text())
        self.assertEqual(1, len(runtime["planes"]))
        chunks = runtime["planes"][0]["chunks"]
        self.assertEqual(expected_chunk_count, len(chunks))
        rich_rows: list[dict] = []
        row_chunks: dict[str, str] = {}
        rich_chunk_measurements: dict[str, dict[str, int]] = {}
        for reference in chunks:
            path = bundle / reference["path"]
            raw = path.read_bytes()
            self.assertEqual(reference["bytes"], len(raw))
            self.assertEqual(reference["sha256"], builder.sha256_bytes(raw))
            decoded = gzip.decompress(raw)
            rows = json.loads(decoded)
            self.assertEqual(reference["count"], len(rows))
            self.assertEqual(reference["records"], len(rows))
            rich_chunk_measurements[reference["path"]] = {
                "rows": len(rows),
                "compressed_bytes": len(raw),
                "decoded_bytes": len(decoded),
                "retained_text_units": sum(
                    builder.retained_text_units(
                        builder.rich_runtime_reader_projection(row)
                    )
                    for row in rows
                ),
            }
            for row in rows:
                self.assertNotIn(row["id"], row_chunks)
                row_chunks[row["id"]] = reference["path"]
            rich_rows.extend(rows)
        rich_assertions = {
            row["id"]: (
                row["source_iri"],
                row["predicate"],
                row["target_iri"],
            )
            for row in rich_rows
        }
        rich_rows_by_id = {row["id"]: row for row in rich_rows}

        self.assertEqual(expected_assertion_count, len(semantic_assertions))
        self.assertEqual(expected_assertion_count, len(explorer_assertions))
        self.assertEqual(expected_assertion_count, len(rich_assertions))
        self.assertEqual(semantic_assertions, explorer_assertions)
        self.assertEqual(semantic_assertions, rich_assertions)
        self.assertEqual(
            {
                identifier: builder.runtime_relationship(row)
                for identifier, row in semantic_rows_by_id.items()
            },
            explorer_rows_by_id,
        )
        plane_id = runtime["planes"][0]["id"]
        self.assertEqual(
            {
                identifier: builder.rich_runtime_relationship(row, plane_id)
                for identifier, row in explorer_rows_by_id.items()
            },
            rich_rows_by_id,
        )
        parity = builder.validate_semantic_relationship_planes(
            semantic_document, explorer_rows
        )
        self.assertEqual(
            expected_assertion_count,
            parity["counts"]["direct_triples_reconciled"],
        )
        self.assertTrue(parity["parity"]["direct_reified_runtime"])

        incident: dict[str, dict[str, set[str]]] = {}
        for row in rich_rows:
            for route in {row["source"], row["target"]}:
                entry = incident.setdefault(
                    route, {"ids": set(), "chunks": set()}
                )
                entry["ids"].add(row["id"])
                entry["chunks"].add(row_chunks[row["id"]])

        locator_reference = runtime["route_locator"]
        locator_path = bundle / locator_reference["path"]
        self.assertEqual(
            locator_reference["sha256"], builder.sha256_file(locator_path)
        )
        locator = json.loads(locator_path.read_text())
        bucket_references = locator["buckets"]
        self.assertEqual(256, len(bucket_references))
        self.assertEqual(expected_route_count, len(incident))
        self.assertEqual(len(incident), locator["counts"]["routes"])
        self.assertEqual(256, locator["counts"]["buckets"])

        seen_routes: set[str] = set()
        chunk_reference_total = 0
        locator_bucket_compressed_maximum = 0
        locator_bucket_decoded_maximum = 0
        measured_route_maxima = {
            "route_chunks": 0,
            "route_declared_rows": 0,
            "route_incident_rows": 0,
            "route_compressed_bytes": 0,
            "route_retained_text_units": 0,
        }
        for reference in bucket_references:
            path = bundle / reference["path"]
            raw = path.read_bytes()
            self.assertEqual(reference["bytes"], len(raw))
            self.assertEqual(reference["sha256"], builder.sha256_bytes(raw))
            decoded = gzip.decompress(raw)
            bucket = json.loads(decoded)
            locator_bucket_compressed_maximum = max(
                locator_bucket_compressed_maximum, len(raw)
            )
            locator_bucket_decoded_maximum = max(
                locator_bucket_decoded_maximum, len(decoded)
            )
            self.assertEqual(reference["bucket"], bucket["bucket"])
            self.assertEqual(reference["routes"], len(bucket["routes"]))
            self.assertEqual(
                bucket["counts"]["routes"], len(bucket["routes"])
            )
            bucket_chunk_references = sum(
                len(row["chunks"]) for row in bucket["routes"]
            )
            self.assertEqual(
                reference["chunk_references"], bucket_chunk_references
            )
            self.assertEqual(
                bucket["counts"]["chunk_references"],
                bucket_chunk_references,
            )
            chunk_reference_total += bucket_chunk_references
            for route_row in bucket["routes"]:
                route = route_row["route"]
                self.assertNotIn(route, seen_routes)
                seen_routes.add(route)
                self.assertEqual(
                    reference["bucket"],
                    hashlib.sha256(route.encode("utf-8")).hexdigest()[:2],
                )
                expected = incident[route]
                expected_ids = sorted(expected["ids"])
                expected_chunks = sorted(expected["chunks"])
                self.assertEqual(expected_chunks, route_row["chunks"])
                selected = [
                    rich_chunk_measurements[path]
                    for path in expected_chunks
                ]
                route_measures = {
                    "route_chunks": len(selected),
                    "route_declared_rows": sum(
                        row["rows"] for row in selected
                    ),
                    "route_incident_rows": len(expected_ids),
                    "route_compressed_bytes": sum(
                        row["compressed_bytes"] for row in selected
                    ),
                    "route_retained_text_units": sum(
                        row["retained_text_units"] for row in selected
                    ),
                }
                measured_route_maxima = {
                    name: max(measured_route_maxima[name], measured)
                    for name, measured in route_measures.items()
                }
                self.assertEqual(1, len(route_row["planes"]))
                plane = route_row["planes"][0]
                self.assertEqual("core", plane["name"])
                self.assertEqual(len(expected_ids), plane["assertions"])
                self.assertEqual(expected_chunks, plane["chunks"])
                self.assertEqual(
                    builder.sha256_bytes(
                        builder.compact_json_without_newline(expected_ids)
                    ),
                    plane["assertion_ids_sha256"],
                )
        self.assertEqual(set(incident), seen_routes)
        self.assertEqual(
            chunk_reference_total, locator["counts"]["chunk_references"]
        )
        self.assertEqual(
            expected_assertion_count,
            runtime["totals"]["all_assertions"],
        )
        self.assertEqual(expected_chunk_count, runtime["totals"]["chunks"])
        semantic_validation = json.loads(
            (
                bundle
                / builder.SEMANTIC_ASSERTION_VALIDATION_BUNDLE_PATH
            ).read_text()
        )
        consumer_validation = semantic_validation[
            "rich_relationship_runtime"
        ]["consumer_limits"]
        self.assertEqual("passed", consumer_validation["status"])
        expected_maxima = {
            "row_retained_text_units": max(
                builder.retained_text_units(
                    builder.rich_runtime_reader_projection(row)
                )
                for row in rich_rows
            ),
            "row_evidence_items": max(
                len(row["evidence"]) for row in rich_rows
            ),
            "row_supporting_assertions": max(
                (len(row.get("supporting_assertions", [])) for row in rich_rows),
                default=0,
            ),
            "chunk_rows": max(
                row["rows"] for row in rich_chunk_measurements.values()
            ),
            "chunk_compressed_bytes": max(
                row["compressed_bytes"]
                for row in rich_chunk_measurements.values()
            ),
            "chunk_decoded_bytes": max(
                row["decoded_bytes"]
                for row in rich_chunk_measurements.values()
            ),
            "chunk_retained_text_units": max(
                row["retained_text_units"]
                for row in rich_chunk_measurements.values()
            ),
            "locator_bucket_compressed_bytes": (
                locator_bucket_compressed_maximum
            ),
            "locator_bucket_decoded_bytes": locator_bucket_decoded_maximum,
            "locator_manifest_bytes": locator_path.stat().st_size,
            "runtime_manifest_bytes": runtime_path.stat().st_size,
            **measured_route_maxima,
            "full_hydration_chunks": len(chunks),
            "full_hydration_declared_rows": sum(
                row["rows"] for row in rich_chunk_measurements.values()
            ),
            "full_hydration_compressed_bytes": sum(
                row["compressed_bytes"]
                for row in rich_chunk_measurements.values()
            ),
            "full_hydration_retained_text_units": sum(
                row["retained_text_units"]
                for row in rich_chunk_measurements.values()
            ),
            "total_chunks": len(chunks),
            "total_rows": len(rich_rows),
            "total_planes": len(runtime["planes"]),
        }
        self.assertEqual(expected_maxima, consumer_validation["maxima"])
        for limit_name, maximum in consumer_validation["limits"].items():
            self.assertEqual(
                builder.locked_rich_relationship_limits()[limit_name],
                maximum,
            )

    def test_governed_predicate_cannot_evade_parity_without_assertions(self) -> None:
        semantic_document = json.loads(
            (ROOT / "bundle" / "okf-bundle.jsonld").read_text()
        )
        manifest = json.loads(
            (ROOT / "bundle" / "data" / "explorer" / "manifest.json").read_text()
        )
        runtime_rows = [
            row
            for reference in manifest["chunks"]["relationships"]
            for row in json.loads((ROOT / "bundle" / reference["path"]).read_text())
        ]
        spatial = "http://purl.org/dc/terms/spatial"
        without_spatial_assertions = copy.deepcopy(semantic_document)
        without_spatial_assertions["@graph"] = [
            node
            for node in without_spatial_assertions["@graph"]
            if not (
                RELATIONSHIP_ASSERTION_TYPE in node.get("@type", [])
                and node.get("predicate", {}).get("@id") == spatial
            )
        ]
        without_spatial_runtime = [
            row for row in runtime_rows if row["predicate"] != spatial
        ]
        with self.assertRaisesRegex(ValueError, "governed contract differ"):
            validate_semantic_relationship_planes(
                without_spatial_assertions, without_spatial_runtime
            )

    def test_final_schema_rejects_noncanonical_web_urls(self) -> None:
        validator, _binding = load_pinned_semantic_assertion_schema()
        graph = json.loads(
            (ROOT / "bundle" / "okf-bundle.jsonld").read_text()
        )["@graph"]
        assertion = next(
            node
            for node in graph
            if RELATIONSHIP_ASSERTION_TYPE in node.get("@type", [])
        )
        cases = (
            ("authority", "javascript:alert(1)"),
            ("authority", "https://user:pass@example.test/rule"),
            ("authority", "https:///missing-host"),
            ("authority", "https://example.test:0/rule"),
            ("authority", "https://example.test:65536/rule"),
            ("evidence-url", "https://example.test/bad%escape"),
            ("evidence-resource", "https://example.test/a<bad>"),
            ("rights", "https://example.test/path with space"),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                changed = copy.deepcopy(assertion)
                if field == "authority":
                    changed["authority"]["source"] = value
                elif field == "evidence-url":
                    changed["evidence"][0]["url"] = value
                elif field == "evidence-resource":
                    changed["evidence"][0]["resource"] = value
                else:
                    changed["rights"]["source"] = value
                self.assertTrue(list(validator.iter_errors(changed)))

    def test_generated_schema_and_validation_receipt_are_integrity_bound(self) -> None:
        source = ROOT / "schemas" / "semantic-assertion.schema.json"
        generated = (
            ROOT / "bundle" / "data" / "semantic"
            / "semantic-assertion.schema.json"
        )
        self.assertEqual(source.read_bytes(), generated.read_bytes())
        predicate_schema_source = builder.PREDICATE_REGISTRY_V2_SCHEMA_PATH
        predicate_schema_generated = (
            ROOT / "bundle" / builder.PREDICATE_REGISTRY_V2_SCHEMA_BUNDLE_PATH
        )
        self.assertEqual(
            predicate_schema_source.read_bytes(),
            predicate_schema_generated.read_bytes(),
        )
        predicate_registry = json.loads(
            (ROOT / "bundle" / builder.PREDICATE_REGISTRY_BUNDLE_PATH).read_text()
        )
        self.assertEqual(
            {
                "predicates": 22,
                "active_emitted": 13,
                "authorised_zero_evidence": 9,
                "assertions_emitted": 22_267,
            },
            predicate_registry["counts"],
        )
        validation = json.loads(
            (ROOT / "bundle" / "data" / "semantic" / "validation.json")
            .read_text()
        )
        self.assertEqual("conformant", validation["status"])
        self.assertEqual(
            SEMANTIC_ASSERTION_SCHEMA_SHA256,
            validation["schema_binding"]["sha256"],
        )
        receipt = json.loads(
            (ROOT / "bundle" / "build-receipt.json").read_text()
        )
        self.assertEqual(
            validation, receipt["semantic_assertion_validation"]
        )
        self.assertEqual(
            builder.PREDICATE_REGISTRY_V2_SCHEMA_SHA256,
            validation["resources"]["predicate_registry_schema"]["sha256"],
        )
        self.assertEqual(
            validation["profile_validation"]["predicate_registry_v2"],
            receipt["predicate_registry_profile_lock"],
        )
        self.assertEqual(
            json.loads((ROOT / "okf.semantic.json").read_text())
            ["semantic_layer"]["candidate_metrics"],
            builder.validate_semantic_contract_metrics(validation),
        )
        stale = copy.deepcopy(validation)
        stale["counts"]["semantic_assertions_validated"] -= 1
        with self.assertRaisesRegex(ValueError, "candidate metrics differ"):
            builder.validate_semantic_contract_metrics(stale)

    def test_boundary_discovery_record_gets_visible_general_boundary_caveat(self) -> None:
        record = normalize_govuk(
            {
                "link": "/government/publications/exact-line-of-boundary-registration-db",
                "content_id": "fixture-boundary",
                "title": "Exact line of boundary: registration",
                "description": "Application form for a boundary process.",
                "content_store_document_type": "form",
                "organisations": [
                    {
                        "title": "HM Land Registry",
                        "slug": "land-registry",
                        "link": "/government/organisations/land-registry",
                        "content_id": "5c54ae52-341b-499e-a6dd-67f04633b8cf",
                    }
                ],
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
                "organisations": [
                    {
                        "title": "HM Land Registry",
                        "slug": "land-registry",
                        "link": "/government/organisations/land-registry",
                        "content_id": "5c54ae52-341b-499e-a6dd-67f04633b8cf",
                    }
                ],
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
                "publisher": "HM Land Registry",
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

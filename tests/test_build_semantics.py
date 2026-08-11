from __future__ import annotations

import ast
import base64
import copy
import gzip
import hashlib
import json
import os
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
        )

    @staticmethod
    def refresh_evidence_id(assertion: dict, ordinal: int = 0) -> None:
        publication_base = builder.relationship_publication_base(assertion["@id"])
        evidence = assertion["evidence"][ordinal]
        evidence["@id"] = builder.relationship_evidence_id(
            publication_base,
            evidence,
            source_iri=assertion["source"]["@id"],
            predicate_iri=assertion["predicate"]["@id"],
            target_iri=assertion["target"]["@id"],
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
        self.assertEqual(42, len(graph["build_inputs"]))
        governed_paths = {
            row["path"] for row in builder.governed_input_receipts({})
        }
        self.assertEqual(62, len(governed_paths))
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
        self.assertEqual(149, len(complete_paths))
        self.assertEqual(62, len(build_paths))
        self.assertIn("tests/test_build_semantics.py", complete_paths)
        self.assertIn(".gitattributes", complete_paths)
        self.assertIn("docs/validation-evidence-layout.md", complete_paths)
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
        generated_at = "2026-08-11T00:00:00Z"
        baseline = "2026-08-09T00:00:00Z"
        later = "2026-08-12T00:00:00Z"
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
        ) -> tuple[dict, dict, dict]:
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
            return snapshot, profile, cpsv

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
        )
        for name, changed, expected in cases:
            with self.subTest(name=name):
                snapshot, profile, cpsv = documents(**changed)
                snapshot_manifest = {
                    "observed_at": baseline,
                    "retrieved_at": changed.get("snapshot_retrieved", baseline),
                }

                def controlled_load(path: Path) -> dict:
                    if Path(path) == ROOT / "domain-profile" / "domain-profile.json":
                        return profile
                    if Path(path) == manifest_path:
                        return snapshot_manifest
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
        self.assertEqual(
            "2026-08-10T14:45:00Z",
            receipt["latest_governed_event_at"],
        )
        self.assertRegex(
            receipt["latest_governed_event"],
            r"CPSV-AP mapping.*reviewed_at",
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
        self.assertEqual(22_226, len(assertions))
        builder.validate_relationship_evidence_bindings(assertions)

    def test_relationship_evidence_binding_rejects_adversarial_drift(self) -> None:
        _records, _manifest, _bindings, assertions = (
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
            and len(row["evidence"]) == 2
            and row["evidence"][1]["type"]
            == "governed-curated-rights-classification"
        ]
        first = copy.deepcopy(curated_rights[0])
        second = curated_rights[2]
        first["evidence"][1] = copy.deepcopy(second["evidence"][1])
        self.refresh_evidence_id(first, 1)
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
        _records, _manifest, _bindings, assertions = (
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
        self.assertEqual(2_203, len(publishers))
        self.assertEqual(1, len(translations))
        self.assertTrue(all(
            row["evidence"][0]["source_field"].startswith("assessments[")
            for row in rights
        ))
        curated_rights = [
            row
            for row in rights
            if len(row["evidence"]) == 2
            and row["evidence"][1]["type"]
            == "governed-curated-rights-classification"
        ]
        self.assertEqual(116, len(curated_rights))
        self.assertTrue(all(
            row["evidence"][1]["source_artifact"]
            == "source/curated-rights-access.json"
            and row["evidence"][1]["source_field"].startswith(
                "classifications[source_native_id='"
            )
            for row in curated_rights
        ))
        self.assertTrue(all(
            len(row["evidence"]) == 1
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
        self.assertEqual(
            "observations[0]", translations[0]["evidence"][0]["source_field"]
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
        self.assertEqual(22_226, receipt["assertions_validated"])
        self.assertEqual(24_552, receipt["evidence_rows_validated"])
        self.assertRegex(receipt["evidence_identity_set_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(receipt["evidence_row_set_sha256"], r"^[0-9a-f]{64}$")

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
        self.assertEqual(22_226, plane_receipt["counts"]["direct_triples_reconciled"])
        self.assertEqual(24_552, plane_receipt["counts"]["evidence_rows_validated"])
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
            self.assertEqual(
                "Deterministically normalised assertion",
                rich["authority"]["label"],
            )
            self.assertEqual(
                "Source rights and exceptions apply.",
                rich["rights"]["assertion"],
            )
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
        self.assertEqual(24_552, report["counts"]["evidence_rows_validated"])
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
        explorer_manifest = json.loads(
            (ROOT / "bundle" / "data" / "explorer" / "manifest.json")
            .read_text()
        )
        relationship_reference = explorer_manifest["chunks"][
            "relationships"
        ][0]
        relationship = json.loads(
            (ROOT / "bundle" / relationship_reference["path"]).read_text()
        )[0]
        plane_id = "https://example.test/id/semantic-plane/core"
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
                "executable Explorer v0.6.0 contract",
            ):
                builder.locked_rich_relationship_limits()

    def test_rich_runtime_projection_rejects_non_ascii_assertion_id(self) -> None:
        explorer_manifest = json.loads(
            (ROOT / "bundle" / "data" / "explorer" / "manifest.json")
            .read_text()
        )
        reference = explorer_manifest["chunks"]["relationships"][0]
        relationship = json.loads(
            (ROOT / "bundle" / reference["path"]).read_text()
        )[0]
        relationship["id"] = relationship["id"] + "/café"
        with self.assertRaisesRegex(ValueError, "must be ASCII"):
            builder.rich_runtime_relationship(
                relationship,
                "https://example.test/id/semantic-plane/core",
            )

    def test_complete_rich_runtime_and_route_locator_are_integrity_bound(self) -> None:
        bundle = ROOT / "bundle"
        semantic_document = json.loads(
            (bundle / "okf-bundle.jsonld").read_text()
        )
        semantic_rows_by_id = {
            row["@id"]: row
            for row in semantic_document["@graph"]
            if "okf:RelationshipAssertion" in row.get("@type", [])
        }
        semantic_assertions = {
            row["@id"]: (
                row["source"]["@id"],
                row["predicate"]["@id"],
                row["target"]["@id"],
            )
            for row in semantic_document["@graph"]
            if "okf:RelationshipAssertion" in row.get("@type", [])
        }

        explorer_manifest = json.loads(
            (bundle / "data" / "explorer" / "manifest.json").read_text()
        )
        explorer_rows: list[dict] = []
        explorer_chunks = explorer_manifest["chunks"]["relationships"]
        self.assertEqual(89, len(explorer_chunks))
        for chunk_ordinal, reference in enumerate(explorer_chunks):
            path = bundle / reference["path"]
            self.assertEqual(reference["bytes"], path.stat().st_size)
            self.assertEqual(reference["sha256"], builder.sha256_file(path))
            rows = json.loads(path.read_text())
            self.assertEqual(
                min(builder.SHARD_SIZE, 22_226 - chunk_ordinal * builder.SHARD_SIZE),
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
        self.assertEqual(89, len(chunks))
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

        self.assertEqual(22_226, len(semantic_assertions))
        self.assertEqual(22_226, len(explorer_assertions))
        self.assertEqual(22_226, len(rich_assertions))
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
        self.assertEqual(22_226, parity["counts"]["direct_triples_reconciled"])
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
        self.assertEqual(6_733, len(incident))
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
        self.assertEqual(22_226, runtime["totals"]["all_assertions"])
        self.assertEqual(89, runtime["totals"]["chunks"])
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
                "okf:RelationshipAssertion" in node.get("@type", [])
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
            if "okf:RelationshipAssertion" in node.get("@type", [])
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

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts import check_domain_profile as checker


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "domain-profile"
LR_SCHEMA_ID = (
    "https://chris-page-gov.github.io/okf-LandRegistry/"
    "schemas/domain-profile.schema.json"
)


class DomainProfileTests(unittest.TestCase):
    def profile(self) -> dict:
        return json.loads(
            (PROFILE / "domain-profile.json").read_text(encoding="utf-8")
        )

    def test_required_pack_files_exist(self) -> None:
        required = {
            "domain-warmup-report.md",
            "domain-profile.json",
            "domain-profile.yaml",
            "evidence-register.jsonl",
            "decision-register.md",
            "traceability.json",
            "CHECKSUMS.sha256",
        }
        self.assertEqual(required, {path.name for path in PROFILE.iterdir() if path.is_file()})

    def test_checksum_pack_is_exact(self) -> None:
        lines = (PROFILE / "CHECKSUMS.sha256").read_text(encoding="utf-8").splitlines()
        digest_lines = [line for line in lines if line and not line.startswith("#")]
        declared_root = [
            line.removeprefix("# pack-root-sha256: ")
            for line in lines
            if line.startswith("# pack-root-sha256: ")
        ]
        self.assertEqual(1, len(declared_root))
        for line in digest_lines:
            digest, name = line.split("  ", 1)
            self.assertEqual(digest, hashlib.sha256((PROFILE / name).read_bytes()).hexdigest())
        manifest = ("\n".join(digest_lines) + "\n").encode("utf-8")
        self.assertEqual(declared_root[0], hashlib.sha256(manifest).hexdigest())

    def test_evidence_jsonl_matches_profile(self) -> None:
        profile = json.loads((PROFILE / "domain-profile.json").read_text(encoding="utf-8"))
        evidence = [
            json.loads(line)
            for line in (PROFILE / "evidence-register.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line
        ]
        self.assertEqual(profile["evidence"], evidence)
        self.assertEqual(len(evidence), len({item["id"] for item in evidence}))

    def test_profile_is_reviewed_and_release_decision_is_historical(self) -> None:
        profile = self.profile()
        self.assertEqual("reviewed", profile["status"])
        recommendation = profile["build_recommendation"]
        self.assertEqual([], recommendation["blocking_decision_ids"])
        release = next(
            decision
            for decision in profile["decisions"]
            if decision["id"] == "DEC-RELEASE"
        )
        self.assertEqual("accepted", release["status"])
        self.assertIn(
            "40482c865dc4332162f1e93756d94ca93abe3559",
            release["recommended_default"],
        )
        self.assertIn(
            "a3e0bdf7846893ce29255f6f20a509dad18ef2b367ba3dfbe48c28191377a704",
            release["recommended_default"],
        )
        self.assertIn(
            "does not authorise publication of the v0.3.0 semantic candidate",
            release["recommended_default"],
        )

    def test_stage1_is_closed_over_the_governed_stage2_contract(self) -> None:
        self.assertEqual([], checker.repository_contract_errors(self.profile()))

    def test_stage1_source_family_crosswalk_fails_closed(self) -> None:
        profile = copy.deepcopy(self.profile())
        profile["sources"][0]["source_families"] = ["unknown-family"]
        errors = checker.repository_contract_errors(profile)
        self.assertTrue(
            any("unknown runtime family" in error for error in errors), errors
        )

    def test_stage1_rights_and_predicate_sets_fail_closed(self) -> None:
        profile = copy.deepcopy(self.profile())
        profile["rights_access_privacy"].pop()
        repository_errors = checker.repository_contract_errors(profile)
        self.assertTrue(
            any("rights identities differ" in error for error in repository_errors),
            repository_errors,
        )

        profile = copy.deepcopy(self.profile())
        first_active = next(
            row
            for row in profile["semantic_model"]["relationship_types"]
            if row["implementation_state"] == "active-emitted"
        )
        first_active["implementation_state"] = "authorised-zero-evidence"
        first_active["implementation_gap"] = "planned/no-governed-endpoint-evidence"
        errors = checker.validate(profile)
        self.assertTrue(
            any("declared set" in error for error in errors), errors
        )

    def test_stage1_inventory_identity_and_chronology_fail_closed(self) -> None:
        profile = copy.deepcopy(self.profile())
        profile["input_snapshot"]["inventory_sha256"] = "0" * 64
        profile["prepared_at"] = "2026-08-10T13:25:40Z"
        errors = checker.repository_contract_errors(profile)
        self.assertIn("profile input_snapshot.inventory_sha256 differs", errors)
        self.assertIn("profile prepared_at predates a governed CPSV review", errors)

    def test_predicate_registry_v2_release_evidence_is_exact(self) -> None:
        profile = self.profile()
        build_config = json.loads(
            (ROOT / "source" / "build-config.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [], checker.predicate_registry_v2_evidence_errors(profile, build_config)
        )

        mutated = copy.deepcopy(profile)
        evidence = next(
            row
            for row in mutated["evidence"]
            if row["id"] == checker.PREDICATE_REGISTRY_V2_EVIDENCE_ID
        )
        evidence["locator"] = evidence["locator"].replace(
            checker.PREDICATE_REGISTRY_V2_RELEASE["commit_sha"], "0" * 40
        )
        errors = checker.predicate_registry_v2_evidence_errors(
            mutated, build_config
        )
        self.assertIn("released predicate-registry v2 evidence differs", errors)

        chronology = copy.deepcopy(build_config)
        chronology["generated_at"] = profile["prepared_at"]
        errors = checker.predicate_registry_v2_evidence_errors(
            profile, chronology
        )
        self.assertIn(
            "predicate-registry v2 release, observation, profile and build chronology differs",
            errors,
        )

    def test_source_governance_chronology_is_distinct_and_fail_closed(self) -> None:
        source_register = json.loads(
            (ROOT / "source/source-register.json").read_text(encoding="utf-8")
        )
        publisher_registry = json.loads(
            (ROOT / "source/publisher-registry.json").read_text(encoding="utf-8")
        )
        build_config = json.loads(
            (ROOT / "source/build-config.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [],
            checker.source_governance_chronology_errors(
                self.profile(), source_register, publisher_registry, build_config
            ),
        )

        reversed_source = copy.deepcopy(source_register)
        reversed_source["observed_at"] = "2026-08-11T06:33:48Z"
        errors = checker.source_governance_chronology_errors(
            self.profile(), reversed_source, publisher_registry, build_config
        )
        self.assertTrue(any("observed_at is after reviewed_at" in row for row in errors))

        late_publisher = copy.deepcopy(publisher_registry)
        late_publisher["reviewed_at"] = build_config["generated_at"]
        errors = checker.source_governance_chronology_errors(
            self.profile(), source_register, late_publisher, build_config
        )
        self.assertTrue(
            any("reviewed_at is not before build generated_at" in row for row in errors)
        )

    def test_lr_schema_has_a_distinct_identity(self) -> None:
        profile = self.profile()
        local_schema = json.loads(
            (ROOT / "schemas/domain-profile.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(LR_SCHEMA_ID, profile["$schema"])
        self.assertEqual(LR_SCHEMA_ID, local_schema["$id"])
        self.assertNotEqual(
            local_schema["$id"],
            "https://chris-page-gov.github.io/okf-explorer/"
            "profile/authoring/v1/domain-profile.schema.json",
        )

    def test_semantic_urls_and_pointers_fail_closed(self) -> None:
        profile = copy.deepcopy(self.profile())
        profile["semantic_model"]["entity_types"][0]["class_iris"][0] = "https://"
        self.assertTrue(checker.validate(profile))

        profile = copy.deepcopy(self.profile())
        authority = profile["semantic_model"]["semantic_authority"]
        authority["class_decisions_pointer"] = "#/absent"
        errors = checker.validate(profile)
        self.assertTrue(any("class_decisions_pointer" in error for error in errors), errors)

        profile = copy.deepcopy(self.profile())
        authority = profile["semantic_model"]["semantic_authority"]
        authority["publisher_registry_path"] = "../source/absent.json"
        errors = checker.validate(profile)
        self.assertTrue(any("publisher registry" in error for error in errors), errors)

        profile = copy.deepcopy(self.profile())
        authority = profile["semantic_model"]["semantic_authority"]
        authority["delegated_authorities"][0]["path"] = "../outside.json"
        self.assertTrue(checker.validate(profile))

    def test_semantic_key_and_source_value_collisions_fail_closed(self) -> None:
        profile = copy.deepcopy(self.profile())
        decisions = profile["semantic_model"]["source_native_class_decisions"]
        decisions[1]["source_native_type"] = decisions[0]["source_native_type"]
        errors = checker.validate(profile)
        self.assertTrue(any("source_native_type" in error for error in errors), errors)

        profile = copy.deepcopy(self.profile())
        terms = profile["semantic_model"]["controlled_vocabulary_terms"]
        terms[1]["source_values"] = [terms[0]["source_values"][0]]
        errors = checker.validate(profile)
        self.assertTrue(any("controlled vocabulary source value" in error for error in errors), errors)

        profile = copy.deepcopy(self.profile())
        jurisdictions = profile["semantic_model"]["jurisdiction_decisions"]
        jurisdictions[1]["source_values"] = [jurisdictions[0]["source_values"][0]]
        errors = checker.validate(profile)
        self.assertTrue(any("jurisdiction source value" in error for error in errors), errors)

    def test_entity_and_relationship_cross_tables_fail_closed(self) -> None:
        profile = copy.deepcopy(self.profile())
        relationship = profile["semantic_model"]["relationship_types"][0]
        relationship["source_type_ids"] = ["TYPE-ABSENT"]
        errors = checker.validate(profile)
        self.assertTrue(any("references unknown entity types" in error for error in errors), errors)

        profile = copy.deepcopy(self.profile())
        relationship = profile["semantic_model"]["relationship_types"][0]
        relationship["source_types"] = ["No such governed entity type"]
        errors = checker.validate(profile)
        self.assertTrue(any("uses unknown governed labels" in error for error in errors), errors)

        profile = copy.deepcopy(self.profile())
        relationship = profile["semantic_model"]["relationship_types"][0]
        relationship["source_type_ids"] = ["TYPE-PUBLICATION"]
        errors = checker.validate(profile)
        self.assertTrue(any("does not project exactly" in error for error in errors), errors)

        profile = copy.deepcopy(self.profile())
        active = next(
            row
            for row in profile["semantic_model"]["entity_types"]
            if row["implementation_state"] == "active-emitted"
        )
        active["implementation_state"] = "authorised-zero-evidence"
        errors = checker.validate(profile)
        self.assertTrue(any("declared set" in error for error in errors), errors)

        profile = copy.deepcopy(self.profile())
        entities = profile["semantic_model"]["entity_types"]
        active = next(row for row in entities if row["implementation_state"] == "active-emitted")
        zero = next(
            row
            for row in entities
            if row["implementation_state"] == "authorised-zero-evidence"
        )
        zero["class_iris"] = [active["class_iris"][0]]
        errors = checker.validate(profile)
        self.assertTrue(any("entity classes overlap" in error for error in errors), errors)

    def test_rights_source_publisher_and_override_drift_fail_closed(self) -> None:
        profile = copy.deepcopy(self.profile())
        right = next(
            row for row in profile["rights_access_privacy"] if row["id"] == "RIGHT-CDDO"
        )
        right["status"] = "prohibited"
        errors = checker.validate(profile)
        self.assertTrue(any("status differs" in error for error in errors), errors)

        profile = copy.deepcopy(self.profile())
        source = next(row for row in profile["sources"] if row["id"] == "SRC-CDDO-API")
        source["evidence_refs"] = ["EV-GOVUK-SEARCH"]
        errors = checker.validate(profile)
        self.assertTrue(any("evidence_refs differ" in error for error in errors), errors)

        profile = copy.deepcopy(self.profile())
        source = next(row for row in profile["sources"] if row["id"] == "SRC-CDDO-API")
        source["publisher_binding"]["strategy"] = "source-native-organisations"
        errors = checker.validate(profile)
        self.assertTrue(any("publisher strategy differs" in error for error in errors), errors)

        profile = copy.deepcopy(self.profile())
        profile["semantic_model"]["semantic_authority"]["source_rights_overrides"] = []
        errors = checker.validate(profile)
        self.assertTrue(any("source-rights overrides differ" in error for error in errors), errors)

    def test_delegated_authorities_are_digest_bound(self) -> None:
        profile = copy.deepcopy(self.profile())
        authority = profile["semantic_model"]["semantic_authority"]
        authority["delegated_authorities"][0]["sha256"] = "0" * 64
        errors = checker.validate(profile)
        self.assertTrue(any("sha256 differs" in error for error in errors), errors)
        self.assertEqual(
            "06eeb534e356b3001df05d970663315b1b741712b78d5e0e9b6e77080e3b78e9",
            next(
                row["sha256"]
                for row in self.profile()["semantic_model"]["semantic_authority"][
                    "delegated_authorities"
                ]
                if row["path"] == "source/publisher-registry.json"
            ),
        )

    def test_local_evidence_is_digest_bound_and_paths_fail_closed(self) -> None:
        profile = self.profile()
        self.assertEqual([], checker.local_evidence_digest_errors(profile))

        mutated = copy.deepcopy(profile)
        owner = next(
            row
            for row in mutated["evidence"]
            if row["id"] == "EV-OWNER-SEMANTIC-DIRECTION"
        )
        owner["sha256"] = "0" * 64
        errors = checker.local_evidence_digest_errors(mutated)
        self.assertTrue(any("sha256 differs" in row for row in errors), errors)

        owner["location"] = "../../outside-repository.txt"
        errors = checker.local_evidence_digest_errors(mutated)
        self.assertTrue(any("escapes the repository" in row for row in errors), errors)

        owner["location"] = "file:///tmp/evidence.txt"
        errors = checker.local_evidence_digest_errors(mutated)
        self.assertTrue(any("unsupported URL form" in row for row in errors), errors)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "domain-profile").mkdir()
            (root / "evidence").mkdir()
            (root / "evidence/payload.txt").write_text("evidence", encoding="utf-8")
            (root / "domain-profile/link").symlink_to(
                root / "evidence", target_is_directory=True
            )
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                checker._local_evidence_path(
                    "link/payload.txt",
                    label="test evidence",
                    repository_root=root,
                )

    def test_rule_plane_and_predicate_projection_authority_fail_closed(self) -> None:
        profile = self.profile()
        semantic = profile["semantic_model"]
        self.assertEqual(14, len(semantic["derivation_rules"]))
        self.assertEqual(["urn:okf:hmlr:plane:core"], [
            row["iri"] for row in semantic["relationship_planes"]
        ])
        self.assertEqual(
            {
                "predicates": 22,
                "active_emitted": 13,
                "authorised_zero_evidence": 9,
                "assertions_emitted": 22267,
            },
            semantic["semantic_authority"]["predicate_registry_projection_policy"][
                "expected_counts"
            ],
        )
        projection_policy = semantic["semantic_authority"][
            "predicate_registry_projection_policy"
        ]
        self.assertEqual(
            "separate-full-v2-schema-alongside-v1",
            projection_policy["compatibility"],
        )
        self.assertEqual(
            {
                "pins_separate_v2_schema_bytes": True,
                "required_projection_schema": "okf-predicate-registry.v2",
                "supported_registry_schemas": [
                    "okf-predicate-registry.v1",
                    "okf-predicate-registry.v2",
                ],
            },
            projection_policy["consumer_lock_requirements"],
        )
        self.assertEqual(
            checker.PREDICATE_REGISTRY_V2_RELEASE,
            projection_policy["consumer_release"],
        )
        self.assertEqual("delivered", projection_policy["current_delivery"])
        self.assertEqual(
            [checker.PREDICATE_REGISTRY_V2_EVIDENCE_ID],
            projection_policy["evidence_refs"],
        )
        self.assertEqual(
            checker.PREDICATE_REGISTRY_V2_PROFILE_LOCK,
            projection_policy["profile_lock"],
        )
        self.assertEqual(
            checker.PREDICATE_REGISTRY_V2_REQUIRED_FIELDS,
            projection_policy["registry_required_fields"],
        )
        self.assertEqual(
            {
                "canonical_material": [
                    "schema",
                    "profile",
                    "snapshot",
                    "generated_at",
                    "predicates",
                    "counts",
                ],
                "excluded_field": "root_sha256",
            },
            projection_policy["root_sha256_binding"],
        )
        self.assertEqual(
            "implementation_state",
            projection_policy["stage1_authoring_state_field"],
        )
        self.assertEqual(
            checker.PREDICATE_REGISTRY_V2_WIRE_IMPLEMENTATION,
            projection_policy["wire_implementation"],
        )

        mutated = copy.deepcopy(profile)
        projection = mutated["semantic_model"]["semantic_authority"][
            "predicate_registry_projection_policy"
        ]
        projection["wire_implementation"]["field"] = "implementation_state"
        errors = checker.semantic_contract_errors(mutated)
        self.assertTrue(
            any("released predicate-registry v2" in row for row in errors), errors
        )

        mutated = copy.deepcopy(profile)
        projection = mutated["semantic_model"]["semantic_authority"][
            "predicate_registry_projection_policy"
        ]
        projection["root_sha256_binding"]["canonical_material"].remove("profile")
        errors = checker.semantic_contract_errors(mutated)
        self.assertTrue(
            any("released predicate-registry v2" in row for row in errors), errors
        )

        mutated = copy.deepcopy(profile)
        mutated["semantic_model"]["relationship_planes"][0]["iri"] = (
            "urn:okf:hmlr:plane:adversarial"
        )
        errors = checker.semantic_contract_errors(mutated)
        self.assertTrue(any("core relationship-plane" in row for row in errors), errors)

        mutated = copy.deepcopy(profile)
        first_rule = next(
            row
            for row in mutated["semantic_model"]["derivation_rules"]
            if row["rule_role"] == "relationship-derivation"
        )
        first_rule["iri"] = checker.RULE_BASE + "adversarial-undeclared-rule"
        errors = checker.semantic_contract_errors(mutated)
        self.assertTrue(any("unexpected IRI" in row for row in errors), errors)

        mutated = copy.deepcopy(profile)
        mutated["semantic_model"]["derivation_rules"] = [
            row
            for row in mutated["semantic_model"]["derivation_rules"]
            if row["rule_role"] != "source-observation"
        ]
        errors = checker.semantic_contract_errors(mutated)
        self.assertTrue(any("source-observation rule" in row for row in errors), errors)

        mutated = copy.deepcopy(profile)
        schemes = mutated["semantic_model"]["identifier_schemes"]
        rule_family = next(
            family
            for scheme in schemes
            for family in scheme.get("identity_families", [])
            if family["id"] == "IDF-RULE"
        )
        rule_family["membership_policy"] = "namespace-only"
        errors = checker.semantic_contract_errors(mutated)
        self.assertTrue(any("exact derivation-rule member set" in row for row in errors))

    def test_declared_sets_are_derived_not_fixed_cardinalities(self) -> None:
        profile = self.profile()
        authority = profile["semantic_model"]["semantic_authority"]
        native_types = {
            row["source_native_type"]
            for row in profile["semantic_model"]["source_native_class_decisions"]
        }
        self.assertEqual(
            len(native_types), authority["declared_sets"]["source_native_types"]["count"]
        )
        self.assertEqual(77, len(native_types))

    def test_bundle_evidence_and_identity_authority_is_exact(self) -> None:
        profile = self.profile()
        entities = {
            row["id"]: row for row in profile["semantic_model"]["entity_types"]
        }
        self.assertEqual(
            ["https://chris-page-gov.github.io/okf-explorer/ns#Bundle"],
            entities["TYPE-BUNDLE"]["class_iris"],
        )
        self.assertIn(
            "https://chris-page-gov.github.io/okf-explorer/ns#EvidenceResource",
            entities["TYPE-EVIDENCE-RESOURCE"]["class_iris"],
        )
        self.assertIn(
            "https://chris-page-gov.github.io/okf-explorer/ns#EvidenceBinding",
            entities["TYPE-EVIDENCE-BINDING"]["class_iris"],
        )
        families = {
            family["id"]: family
            for scheme in profile["semantic_model"]["identifier_schemes"]
            for family in scheme.get("identity_families", [])
        }
        for family_id in (
            "IDF-EVIDENCE-RESOURCE",
            "IDF-EVIDENCE-BINDING",
            "IDF-LOCAL-AGENT",
            "IDF-EXTERNAL-GITHUB-ORGANISATION",
        ):
            self.assertIn(family_id, families)

    def test_predicate_authority_is_exhaustive(self) -> None:
        profile = self.profile()
        for relationship in profile["semantic_model"]["relationship_types"]:
            for field in (
                "description",
                "source_type_ids",
                "target_type_ids",
                "domain_class_iris",
                "range_class_iris",
                "vocabulary_iri",
                "vocabulary_version",
                "registry_evidence_policy",
            ):
                self.assertIn(field, relationship)

    def test_traceability_is_exact_and_not_candidate_approval(self) -> None:
        profile = self.profile()
        standalone = json.loads(
            (PROFILE / "traceability.json").read_text(encoding="utf-8")
        )
        embedded = {row["id"]: row for row in profile["traceability"]}
        projected = {
            row["id"]: {
                "id": row["id"],
                "requirement": row["intent_or_requirement"],
                "tasks": row["task_refs"],
                "artifacts": row["planned_artifacts"],
                "validations": row["validation_refs"],
                "evidence": row["evidence_refs"],
                "status": row["status"],
            }
            for row in embedded.values()
        }
        self.assertEqual(projected, {row["id"]: row for row in standalone["rows"]})
        semantics = embedded["TRACE-SEMANTICS"]
        self.assertEqual("accepted", semantics["status"])
        self.assertIn(
            "does not assert candidate conformance",
            profile["semantic_model"]["semantic_authority"]["builder_policy"],
        )


if __name__ == "__main__":
    unittest.main()

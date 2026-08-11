from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

from scripts import build as builder
from scripts.change_impact import (
    ROOT,
    REQUIRED_BUILD_INPUT_PATTERNS,
    ChangeImpactError,
    _run_git,
    analyse_paths,
    canonical_json,
    load_graph,
    load_json_object,
    normalise_dependency_pattern,
    normalise_repository_path,
    parse_name_status_z,
    path_matches,
    validate_executable_causal_bootstrap,
    validate_graph,
)


def write_fake_git(directory: Path, body: str) -> Path:
    executable = directory / "git"
    executable.write_text(
        f"#!{sys.executable} -B\n{body}\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


class BoundedGitTests(unittest.TestCase):
    def run_fake_git(
        self,
        body: str,
        *,
        maximum_stdout_bytes: int = 64,
        maximum_stderr_bytes: int = 64,
        timeout: float = 1.0,
    ) -> bytes:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            write_fake_git(root, body)
            environment_path = os.pathsep.join(
                [str(root), os.environ.get("PATH", "")]
            )
            with (
                mock.patch.dict(os.environ, {"PATH": environment_path}),
                mock.patch(
                    "scripts.change_impact.MAX_GIT_STDERR_BYTES",
                    maximum_stderr_bytes,
                ),
                mock.patch(
                    "scripts.change_impact.GIT_COMMAND_TIMEOUT_SECONDS",
                    timeout,
                ),
            ):
                return _run_git(
                    root,
                    ["fixture"],
                    maximum_stdout_bytes=maximum_stdout_bytes,
                )

    def test_git_stdout_flood_is_stopped_in_flight(self) -> None:
        with self.assertRaisesRegex(ChangeImpactError, "stdout exceeds"):
            self.run_fake_git(
                "import os; os.write(1, b'x' * 4096)",
                maximum_stdout_bytes=32,
            )

    def test_git_stderr_flood_is_stopped_in_flight(self) -> None:
        with self.assertRaisesRegex(ChangeImpactError, "stderr exceeds"):
            self.run_fake_git(
                "import os; os.write(2, b'x' * 4096)",
                maximum_stderr_bytes=32,
            )

    def test_hung_git_is_killed_at_the_absolute_deadline(self) -> None:
        started = time.monotonic()
        with self.assertRaisesRegex(ChangeImpactError, "time ceiling"):
            self.run_fake_git("import time; time.sleep(10)", timeout=0.1)
        self.assertLess(time.monotonic() - started, 2.0)


class ArtifactDependencyGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.graph = load_graph()

    def test_repository_graph_is_schema_valid_and_references_close(self) -> None:
        schema = load_json_object(
            ROOT / "schemas" / "artifact-dependency-graph.schema.json"
        )
        validate_graph(self.graph, schema=schema)
        self.assertEqual(
            self.graph["unknown_change_policy"],
            "all-gates-and-manual-review",
        )
        self.assertEqual(
            self.graph["all_release_gates"],
            [f"G{number}" for number in range(1, 10)],
        )

    def test_executable_bootstrap_binds_actual_schema_reads(self) -> None:
        self.assertEqual(44, len(REQUIRED_BUILD_INPUT_PATTERNS))
        self.assertEqual(
            tuple(self.graph["build_inputs"]),
            REQUIRED_BUILD_INPUT_PATTERNS,
        )
        validate_executable_causal_bootstrap(self.graph)
        self.assertIn("domain-profile/**", REQUIRED_BUILD_INPUT_PATTERNS)
        self.assertTrue(
            {
                "domain-profile/CHECKSUMS.sha256",
                "domain-profile/domain-profile.json",
                "domain-profile/evidence-register.jsonl",
            }.isdisjoint(REQUIRED_BUILD_INPUT_PATTERNS)
        )
        self.assertNotIn(
            "research/source-family-inventory.json",
            REQUIRED_BUILD_INPUT_PATTERNS,
        )
        self.assertTrue(
            any(
                "research/source-family-inventory.json" in stage["inputs"]
                for stage in self.graph["stages"]
            )
        )

        for schema_path in (
            "schemas/domain-profile.schema.json",
            "schemas/semantic-class-route-registry.schema.json",
        ):
            with self.subTest(schema_path=schema_path):
                self.assertIn(schema_path, REQUIRED_BUILD_INPUT_PATTERNS)
                graph = deepcopy(self.graph)
                graph["build_inputs"].remove(schema_path)
                with self.assertRaises(ChangeImpactError) as raised:
                    validate_executable_causal_bootstrap(graph)
                self.assertIn(repr(schema_path), str(raised.exception))

        for profile_pattern in (
            "profiles/predicate-registry/v2.lock.json",
            "profiles/predicate-registry/v2/**",
        ):
            with self.subTest(profile_pattern=profile_pattern):
                self.assertIn(
                    profile_pattern, REQUIRED_BUILD_INPUT_PATTERNS
                )
                graph = deepcopy(self.graph)
                graph["build_inputs"].remove(profile_pattern)
                with self.assertRaises(ChangeImpactError) as raised:
                    validate_executable_causal_bootstrap(graph)
                self.assertIn(repr(profile_pattern), str(raised.exception))

    def test_repository_test_commands_use_the_locked_virtual_environment(self) -> None:
        self.assertTrue(self.graph["tests"])
        for test in self.graph["tests"]:
            self.assertEqual(
                ".venv/bin/python",
                test["command"][0],
                test["id"],
            )

    def test_unreferenced_test_command_is_rejected(self) -> None:
        graph = deepcopy(self.graph)
        graph["tests"].append(
            {
                "id": "orphan-test",
                "command": ["python", "-m", "unittest", "tests.test_orphan"],
                "repository_paths": ["tests/test_change_impact.py"],
            }
        )

        with self.assertRaisesRegex(
            ChangeImpactError,
            "unreferenced test ids: orphan-test",
        ):
            validate_graph(graph)

    def test_absent_test_repository_path_is_rejected(self) -> None:
        graph = deepcopy(self.graph)
        graph["tests"][0]["repository_paths"] = ["tests/does-not-exist.py"]
        with self.assertRaisesRegex(
            ChangeImpactError,
            "references an absent repository path",
        ):
            validate_graph(graph)

    def test_path_globs_are_segment_aware(self) -> None:
        self.assertTrue(path_matches("pages/app.js", "pages/**"))
        self.assertTrue(path_matches("pages/nested/app.js", "pages/**"))
        self.assertTrue(path_matches("pages/app.js", "pages/*.js"))
        self.assertFalse(path_matches("pages/nested/app.js", "pages/*.js"))
        self.assertFalse(path_matches("other/app.js", "pages/**"))

    def test_governed_dependency_pattern_grammar_is_fail_closed(self) -> None:
        accepted = (
            ".gitattributes",
            ".gitignore",
            ".github/workflows/**",
            "evaluation/latest-report.json",
            "source/build-config.json",
            "source/snapshots/**",
        )
        for value in accepted:
            with self.subTest(accepted=value):
                literal, recursive = normalise_dependency_pattern(
                    value,
                    field="test pattern",
                    input_pattern=False,
                )
                self.assertEqual(value.endswith("/**"), recursive)
                self.assertEqual(value[:-3] if recursive else value, literal)

        rejected = (
            "source/*.json",
            "tests/test_*.py",
            "bundle/*.json",
            "source/**/nested.json",
            "source//input.json",
            "source/./input.json",
            "source/../input.json",
            "source/input:variant.json",
            "source/~input.json",
            "source/input\\variant.json",
            "source/\x00input.json",
            "source/\x7finput.json",
        )
        for value in rejected:
            with self.subTest(rejected=value):
                with self.assertRaises(ChangeImpactError):
                    normalise_dependency_pattern(
                        value,
                        field="test pattern",
                        input_pattern=False,
                    )

        for value in ("dist/**", "validation/candidate.json"):
            with self.subTest(mutable_input=value):
                with self.assertRaisesRegex(ChangeImpactError, "mutable"):
                    normalise_dependency_pattern(
                        value,
                        field="test pattern",
                        input_pattern=True,
                    )
                normalise_dependency_pattern(
                    value,
                    field="test pattern",
                    input_pattern=False,
                )

    def test_graph_schema_enforces_shared_stage_pattern_grammar(self) -> None:
        rejected = (
            "source/*.json",
            "tests/test_*.py",
            "bundle/*.json",
            "source/**/nested.json",
            "source//input.json",
            "source/./input.json",
            "source/input:variant.json",
            "source/~input.json",
        )
        for member in ("inputs", "validation_inputs", "outputs"):
            for value in rejected:
                with self.subTest(member=member, value=value):
                    graph = deepcopy(self.graph)
                    graph["stages"][0][member][0:1] = [value]
                    with self.assertRaises(ChangeImpactError):
                        validate_graph(graph)

        for member in ("inputs", "validation_inputs"):
            for value in ("dist/**", "validation/candidate.json"):
                with self.subTest(member=member, mutable=value):
                    graph = deepcopy(self.graph)
                    graph["stages"][0][member][0:1] = [value]
                    with self.assertRaises(ChangeImpactError):
                        validate_graph(graph)

        for value in rejected + ("dist/**", "validation/candidate.json"):
            with self.subTest(member="build_inputs", value=value):
                graph = deepcopy(self.graph)
                graph["build_inputs"][0:1] = [value]
                with self.assertRaises(ChangeImpactError):
                    validate_graph(graph)

    def test_build_engine_change_selects_bundle_and_release_assurance(self) -> None:
        report = analyse_paths(["scripts/build.py"], graph=self.graph)

        self.assertEqual(report["decision"], "classified")
        self.assertFalse(report["manual_review_required"])
        self.assertIn("bundle/**", report["affected"]["generated_artifacts"])
        self.assertTrue(
            {"G4", "G6", "G7", "G8", "G9"}.issubset(
                report["affected"]["release_gates"]
            )
        )
        self.assertIn("bundle", report["affected"]["test_ids"])
        self.assertIn("explorer-contract", report["affected"]["test_ids"])
        self.assertIn("REQ-014", report["affected"]["requirement_ids"])
        self.assertIn("REQ-019", report["affected"]["requirement_ids"])
        self.assertIn("RISK-010", report["affected"]["risk_ids"])
        self.assertIn("RISK-016", report["affected"]["risk_ids"])
        self.assertIn(
            "VAL-EXPLORER-CONSUMER",
            report["affected"]["validation_refs"],
        )
        self.assertTrue(
            all(
                row["status"] == "not_run"
                for row in report["affected"]["gate_work"]
            )
        )
        self.assertTrue(report["release_approval_required"])

    def test_every_causal_build_input_predicts_receipt_checksums_and_bundle(self) -> None:
        causal_paths = [
            path.relative_to(ROOT).as_posix()
            for path in builder.dependency_graph_build_input_paths(self.graph)
        ]
        self.assertEqual(71, len(causal_paths))
        for path in causal_paths:
            with self.subTest(path=path):
                report = analyse_paths([path], graph=self.graph)
                outputs = set(report["affected"]["generated_artifacts"])
                self.assertTrue(
                    {
                        "bundle/**",
                        "bundle/build-receipt.json",
                        "bundle/CHECKSUMS.sha256",
                    }
                    <= outputs
                )
                self.assertTrue(
                    {"build-semantics", "bundle"}
                    <= set(report["affected"]["test_ids"])
                )
                self.assertIn(
                    "VAL-REPRODUCIBILITY",
                    report["affected"]["validation_refs"],
                )

    def test_every_canonical_domain_profile_pack_member_is_causal(self) -> None:
        expected_pack = {
            "domain-profile/CHECKSUMS.sha256",
            "domain-profile/decision-register.md",
            "domain-profile/domain-profile.json",
            "domain-profile/domain-profile.yaml",
            "domain-profile/domain-warmup-report.md",
            "domain-profile/evidence-register.jsonl",
            "domain-profile/traceability.json",
        }
        causal_paths = {
            path.relative_to(ROOT).as_posix()
            for path in builder.dependency_graph_build_input_paths(self.graph)
        }
        observed_pack = {
            path for path in causal_paths if path.startswith("domain-profile/")
        }
        self.assertEqual(expected_pack, observed_pack)
        for path in sorted(expected_pack):
            with self.subTest(path=path):
                report = analyse_paths([path], graph=self.graph)
                self.assertIn(
                    path,
                    report["matched_stages"][0]["causal_build_input_matches"],
                )
                self.assertTrue(
                    {
                        "bundle/**",
                        "bundle/build-receipt.json",
                        "bundle/CHECKSUMS.sha256",
                    }
                    <= set(report["affected"]["generated_artifacts"])
                )

    def test_causal_inventory_controls_select_both_closure_suites(self) -> None:
        for path in (
            "governance/artifact-dependency-graph.json",
            "schemas/artifact-dependency-graph.schema.json",
            "scripts/build.py",
            "scripts/change_impact.py",
        ):
            with self.subTest(path=path):
                report = analyse_paths([path], graph=self.graph)
                self.assertTrue(
                    {"build-semantics", "bundle", "change-impact"}
                    <= set(report["affected"]["test_ids"])
                )

    def test_build_config_and_semantic_contract_select_rebuild_closure(self) -> None:
        for path in ("source/build-config.json", "okf.semantic.json"):
            with self.subTest(path=path):
                report = analyse_paths([path], graph=self.graph)
                self.assertTrue(
                    {"build-semantics", "bundle"}
                    <= set(report["affected"]["test_ids"])
                )
                self.assertIn(
                    "VAL-REPRODUCIBILITY",
                    report["affected"]["validation_refs"],
                )

    def test_assurance_only_changes_do_not_predict_bundle_outputs(self) -> None:
        cases = {
            ".gitattributes": "change-impact",
            ".github/workflows/pages.yml": "workflow",
            "docs/v0.3.0-release-tracker-and-assurance-runbook.md": "traceability",
            "pyproject.toml": "release-metadata",
            "requirements-dev.txt": "release-metadata",
            "scripts/check_release_evidence.py": "release-evidence",
            "tests/test_build_semantics.py": "build-semantics",
        }
        for path, expected_test in cases.items():
            with self.subTest(path=path):
                report = analyse_paths([path], graph=self.graph)
                outputs = report["affected"]["generated_artifacts"]
                self.assertFalse(
                    any(output.startswith("bundle/") for output in outputs),
                    outputs,
                )
                self.assertIn(expected_test, report["affected"]["test_ids"])

    def test_pages_change_selects_site_outputs_and_accessibility(self) -> None:
        report = analyse_paths(["pages/index.html"], graph=self.graph)

        outputs = report["affected"]["generated_artifacts"]
        self.assertIn("bundle/**", outputs)
        self.assertIn("bundle/build-receipt.json", outputs)
        self.assertIn("bundle/CHECKSUMS.sha256", outputs)
        self.assertIn("bundle/data/search/**", outputs)
        self.assertIn("G6", report["affected"]["release_gates"])
        self.assertIn("pages", report["affected"]["test_ids"])
        self.assertFalse(report["stage1_review_required"])

    def test_non_causal_legacy_page_selects_checks_without_bundle_outputs(self) -> None:
        report = analyse_paths(["pages/app.js"], graph=self.graph)

        self.assertEqual(report["decision"], "classified")
        self.assertEqual(report["affected"]["generated_artifacts"], [])
        self.assertIn("build-semantics", report["affected"]["test_ids"])
        self.assertIn("pages", report["affected"]["test_ids"])
        self.assertEqual(
            report["matched_stages"][0]["causal_build_input_matches"],
            [],
        )

    def test_domain_profile_producer_change_selects_profile_regression_tests(
        self,
    ) -> None:
        report = analyse_paths(
            ["domain-profile/domain-profile.json"],
            graph=self.graph,
        )

        self.assertEqual(
            ["domain-profile"],
            [stage["id"] for stage in report["matched_stages"]],
        )
        self.assertIn(
            "domain-profile-regression",
            report["affected"]["test_ids"],
        )
        command = next(
            test
            for test in self.graph["tests"]
            if test["id"] == "domain-profile-regression"
        )
        self.assertIn("tests.test_domain_profile", command["command"])
        self.assertEqual(
            ["tests/test_domain_profile.py"],
            command["repository_paths"],
        )

    def test_source_observer_change_selects_observation_regression_tests(
        self,
    ) -> None:
        report = analyse_paths(
            ["scripts/observe_govuk_content.py"],
            graph=self.graph,
        )

        self.assertEqual(
            ["source-snapshot"],
            [stage["id"] for stage in report["matched_stages"]],
        )
        self.assertIn(
            "govuk-content-observation",
            report["affected"]["test_ids"],
        )
        command = next(
            test
            for test in self.graph["tests"]
            if test["id"] == "govuk-content-observation"
        )
        self.assertIn("tests.test_observe_govuk_content", command["command"])
        self.assertEqual(
            ["tests/test_observe_govuk_content.py"],
            command["repository_paths"],
        )

    def test_record_level_rights_change_selects_build_and_g3(self) -> None:
        report = analyse_paths(
            ["source/curated-rights-access.json"],
            graph=self.graph,
        )

        self.assertEqual(report["decision"], "classified")
        self.assertFalse(report["manual_review_required"])
        self.assertEqual(
            [stage["id"] for stage in report["matched_stages"]],
            ["build-inputs"],
        )
        self.assertIn("bundle/**", report["affected"]["generated_artifacts"])
        self.assertIn("G3", report["affected"]["release_gates"])
        self.assertIn("build-semantics", report["affected"]["test_ids"])
        self.assertIn("VAL-RIGHTS", report["affected"]["validation_refs"])

    def test_vendored_semantic_profile_change_is_governed_build_input(self) -> None:
        for path in (
            "profiles/bundle-wiki/v1.vendor-lock.json",
            "profiles/predicate-registry/v2.lock.json",
            "profiles/predicate-registry/v2/predicate-registry.schema.json",
        ):
            with self.subTest(path=path):
                report = analyse_paths([path], graph=self.graph)

                self.assertEqual(report["decision"], "classified")
                self.assertFalse(report["manual_review_required"])
                self.assertEqual(
                    [stage["id"] for stage in report["matched_stages"]],
                    ["build-inputs"],
                )
                self.assertIn("G4", report["affected"]["release_gates"])
                self.assertIn(
                    "explorer-contract", report["affected"]["test_ids"]
                )

    def test_browser_quality_runner_change_selects_exact_assurance_surface(self) -> None:
        report = analyse_paths(
            ["scripts/run_authored_site_browser_quality.mjs"],
            graph=self.graph,
        )

        self.assertEqual(report["decision"], "classified")
        self.assertFalse(report["manual_review_required"])
        self.assertEqual(
            [stage["id"] for stage in report["matched_stages"]],
            ["authored-site-browser-quality"],
        )
        self.assertEqual(
            report["affected"]["release_gates"],
            ["G2", "G4", "G5", "G6", "G7", "G8", "G9"],
        )
        self.assertEqual(
            report["affected"]["test_ids"],
            ["authored-site-browser-quality"],
        )
        self.assertIn(
            "VAL-ACCESSIBILITY",
            report["affected"]["validation_refs"],
        )

    def test_explorer_consumer_lock_selects_compatibility_gates(self) -> None:
        report = analyse_paths(
            ["contracts/okf-explorer.consumer-lock.json"],
            graph=self.graph,
        )

        self.assertEqual(
            [stage["id"] for stage in report["matched_stages"]],
            ["explorer-consumer-contract"],
        )
        self.assertIn("explorer-contract", report["affected"]["test_ids"])
        self.assertIn("REQ-019", report["affected"]["requirement_ids"])
        self.assertIn("RISK-016", report["affected"]["risk_ids"])
        self.assertTrue(
            {"G4", "G6", "G7", "G8", "G9"}.issubset(
                report["affected"]["release_gates"]
            )
        )
        self.assertFalse(report["manual_review_required"])

    def test_unknown_authored_path_fails_closed_to_every_gate(self) -> None:
        report = analyse_paths(["unclassified/new-control.txt"], graph=self.graph)

        self.assertEqual(report["decision"], "manual-review-required")
        self.assertTrue(report["manual_review_required"])
        self.assertEqual(
            report["affected"]["release_gates"],
            [f"G{number}" for number in range(1, 10)],
        )
        self.assertTrue(
            all(
                row["selected_by"]["unknown_change_policy"]
                for row in report["affected"]["gate_work"]
            )
        )
        self.assertEqual(
            report["unmatched_paths"],
            ["unclassified/new-control.txt"],
        )

    def test_known_test_change_uses_its_focused_assurance_surface(self) -> None:
        report = analyse_paths(["tests/test_change_impact.py"], graph=self.graph)

        self.assertEqual(
            [stage["id"] for stage in report["matched_stages"]],
            ["impact-control-tests"],
        )
        self.assertEqual(
            report["affected"]["release_gates"],
            ["G2", "G4", "G7", "G8", "G9"],
        )
        self.assertEqual(report["affected"]["test_ids"], ["change-impact"])
        self.assertIn("REQ-020", report["affected"]["requirement_ids"])
        self.assertIn("RISK-017", report["affected"]["risk_ids"])
        self.assertIn("VAL-CHANGE-IMPACT", report["affected"]["validation_refs"])
        self.assertEqual(report["affected"]["generated_artifacts"], [])
        self.assertEqual(
            report["matched_stages"][0]["input_matches"],
            ["tests/test_change_impact.py"],
        )

    def test_url_canonicalisation_test_uses_build_assurance_surface(self) -> None:
        report = analyse_paths(
            ["tests/test_url_canonicalisation.py"],
            graph=self.graph,
        )

        self.assertEqual(report["decision"], "classified")
        self.assertFalse(report["manual_review_required"])
        self.assertEqual(
            [stage["id"] for stage in report["matched_stages"]],
            ["bundle-build-engine"],
        )
        self.assertIn(
            "url-canonicalisation",
            report["affected"]["test_ids"],
        )
        self.assertEqual(
            report["matched_stages"][0]["validation_input_matches"],
            ["tests/test_url_canonicalisation.py"],
        )

    def test_new_unclassified_test_fails_closed_to_every_gate(self) -> None:
        report = analyse_paths(["tests/test_future_control.py"], graph=self.graph)

        self.assertTrue(report["manual_review_required"])
        self.assertEqual(
            report["affected"]["release_gates"],
            [f"G{number}" for number in range(1, 10)],
        )

    def test_generated_only_change_is_unexplained(self) -> None:
        report = analyse_paths(["bundle/okf-explorer.json"], graph=self.graph)

        self.assertTrue(report["manual_review_required"])
        self.assertEqual(
            report["unexplained_generated_paths"],
            ["bundle/okf-explorer.json"],
        )
        self.assertEqual(
            report["affected"]["release_gates"],
            [f"G{number}" for number in range(1, 10)],
        )

    def test_exact_generated_file_root_is_governed_and_not_a_directory(self) -> None:
        self.assertIn(
            "evaluation/latest-report.json",
            self.graph["generated_roots"],
        )
        evaluation_stage = next(
            stage
            for stage in self.graph["stages"]
            if stage["id"] == "evaluation-suite"
        )
        self.assertIn(
            "evaluation/latest-report.json",
            evaluation_stage["outputs"],
        )
        report = analyse_paths(
            ["evaluation/latest-report.json"],
            graph=self.graph,
        )
        self.assertEqual(
            ["evaluation/latest-report.json"],
            report["unexplained_generated_paths"],
        )

        graph = deepcopy(self.graph)
        changed_stage = next(
            stage
            for stage in graph["stages"]
            if stage["id"] == "evaluation-suite"
        )
        changed_stage["outputs"] = [
            "evaluation/latest-report.json/child.json"
            if output == "evaluation/latest-report.json"
            else output
            for output in changed_stage["outputs"]
        ]
        with self.assertRaisesRegex(
            ChangeImpactError,
            "outside generated_roots",
        ):
            validate_graph(graph)

    def test_generated_change_is_explained_by_changed_build_input(self) -> None:
        report = analyse_paths(
            ["bundle/okf-explorer.json", "source/build-config.json"],
            graph=self.graph,
        )

        self.assertFalse(report["manual_review_required"])
        self.assertEqual(report["unexplained_generated_paths"], [])
        self.assertEqual(
            report["explained_generated_paths"],
            ["bundle/okf-explorer.json"],
        )
        self.assertEqual(report["unmatched_paths"], [])

    def test_explorer_adjacency_output_is_covered_by_build_edge(self) -> None:
        report = analyse_paths(
            [
                "scripts/build.py",
                "bundle/data/explorer/adjacency/okf-resource.json",
            ],
            graph=self.graph,
        )

        self.assertFalse(report["manual_review_required"])
        self.assertEqual(
            report["explained_generated_paths"],
            ["bundle/data/explorer/adjacency/okf-resource.json"],
        )
        self.assertIn("explorer-contract", report["affected"]["test_ids"])

    def test_governance_controls_enrich_risks_from_requirements(self) -> None:
        graph = deepcopy(self.graph)
        pages_stage = next(
            stage for stage in graph["stages"] if stage["id"] == "pages-presentation"
        )
        pages_stage["risk_ids"] = []

        report = analyse_paths(["pages/app.js"], graph=graph)

        self.assertIn("REQ-012", report["affected"]["requirement_ids"])
        self.assertIn("RISK-009", report["affected"]["risk_ids"])

    def test_report_is_deterministic_for_path_order(self) -> None:
        first = analyse_paths(
            ["bundle/okf-explorer.json", "scripts/build.py"],
            graph=self.graph,
        )
        second = analyse_paths(
            ["scripts/build.py", "bundle/okf-explorer.json"],
            graph=self.graph,
        )

        self.assertEqual(canonical_json(first), canonical_json(second))

    def test_git_name_status_parser_preserves_both_rename_paths(self) -> None:
        changes = parse_name_status_z(
            b"R100\x00old/name.json\x00new/name.json\x00M\x00README.md\x00"
        )

        self.assertEqual(
            changes,
            [
                {
                    "status": "R100",
                    "paths": ["old/name.json", "new/name.json"],
                },
                {"status": "M", "paths": ["README.md"]},
            ],
        )

    def test_git_name_status_parser_rejects_truncation(self) -> None:
        with self.assertRaises(ChangeImpactError):
            parse_name_status_z(b"R100\x00old/name.json\x00")

    def test_unsafe_repository_paths_are_rejected(self) -> None:
        for path in (
            "../outside",
            "/absolute",
            "nested\\windows",
            "./relative",
            "nested//double",
            "trailing/",
            "line\nbreak",
        ):
            with self.subTest(path=path):
                with self.assertRaises(ChangeImpactError):
                    normalise_repository_path(path)


if __name__ == "__main__":
    unittest.main()

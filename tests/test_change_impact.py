from __future__ import annotations

from copy import deepcopy
import unittest

from scripts.change_impact import (
    ROOT,
    ChangeImpactError,
    analyse_paths,
    canonical_json,
    load_graph,
    load_json_object,
    normalise_repository_path,
    parse_name_status_z,
    path_matches,
    validate_graph,
)


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

    def test_pages_change_selects_site_outputs_and_accessibility(self) -> None:
        report = analyse_paths(["pages/app.js"], graph=self.graph)

        outputs = report["affected"]["generated_artifacts"]
        self.assertIn("bundle/app.js", outputs)
        self.assertIn("bundle/data/search/**", outputs)
        self.assertIn("G6", report["affected"]["release_gates"])
        self.assertIn("pages", report["affected"]["test_ids"])
        self.assertFalse(report["stage1_review_required"])

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
            ["impact-control"],
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
            report["matched_stages"][0]["validation_input_matches"],
            ["tests/test_change_impact.py"],
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

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from scripts import build as builder


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "bundle"


def tree_bytes(path: Path) -> dict[str, bytes]:
    return {
        item.relative_to(path).as_posix(): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def write_generated_fixture(path: Path, value: str) -> None:
    path.mkdir(exist_ok=True)
    (path / builder.GENERATED_MARKER).write_text("generated\n", encoding="utf-8")
    (path / "value.txt").write_text(value, encoding="utf-8")


class BundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.descriptor = json.loads((BUNDLE / "okf-explorer.json").read_text())
        cls.catalogue = json.loads((BUNDLE / "data" / "catalogue.json").read_text())

    def test_descriptor_contract_and_counts(self) -> None:
        self.assertEqual("0.2", self.descriptor["okf_version"])
        self.assertEqual("okf-explorer-large-corpus.v1", self.descriptor["schema"])
        self.assertEqual("okf-large-corpus", self.descriptor["kind"])
        self.assertEqual(
            self.catalogue["record_count"], self.descriptor["counts"]["records"]
        )
        self.assertFalse(self.descriptor["scope"]["complete_hmlr_public_estate"])
        self.assertTrue(self.descriptor["scope"]["metadata_only"])
        self.assertTrue(self.descriptor["authority"]["not_endorsed_by_source"])

    def test_record_identity_urls_and_rights_are_explicit(self) -> None:
        records = self.catalogue["records"]
        profile = json.loads(
            (ROOT / "domain-profile" / "domain-profile.json").read_text()
        )
        evidence_ids = {row["id"] for row in profile["evidence"]}
        self.assertGreater(len(records), 0)
        self.assertEqual(len(records), len({record["id"] for record in records}))
        self.assertEqual(len(records), len({record["url"] for record in records}))
        for record in records:
            parsed = urlparse(record["url"])
            self.assertEqual("https", parsed.scheme, record["id"])
            self.assertFalse(parsed.username or parsed.password, record["id"])
            sensitive = {"key", "token", "signature", "sig", "expires", "x-amz-signature"}
            self.assertFalse(
                sensitive & {key.casefold() for key in parse_qs(parsed.query)}, record["id"]
            )
            self.assertEqual(record["schema"], "okf-hmlr-record.v2")
            self.assertRegex(record["id"], r"^hmlr-[0-9a-f]{24}$")
            self.assertEqual(record["id"], record["record_id"])
            self.assertTrue(record["source_native_id"], record["id"])
            self.assertTrue(record["source_native_type"], record["id"])
            self.assertEqual(record["canonical_source_url"], record["url"])
            self.assertTrue(record["publisher_id"].startswith("https://"))
            for value_field, state_field in (
                ("licence", "licence_state"),
                ("jurisdiction", "jurisdiction_state"),
                ("cadence", "cadence_state"),
            ):
                self.assertIn(
                    record[state_field],
                    {"stated", "inherited", "unknown", "not-applicable"},
                )
                if record[state_field] in {"unknown", "not-applicable"}:
                    self.assertIsNone(record[value_field])
            self.assertIn(
                record["language_state"],
                {"stated", "inherited", "unknown", "not-applicable"},
            )
            self.assertTrue(record["authority_tier"], record["id"])
            self.assertTrue(record["observed_at"], record["id"])
            self.assertTrue(record["source_urls"], record["id"])
            self.assertTrue(record["source_native_ids"], record["id"])
            self.assertTrue(record["representations"], record["id"])
            self.assertIn(record["source_native_id"], record["source_native_ids"])
            self.assertTrue(record["access_state"], record["id"])
            self.assertTrue(record["rights_state"], record["id"])
            self.assertRegex(
                record["rights_ref"],
                r"^RIGHT-[A-Z]+(?:-[A-Z]+)*$",
            )
            self.assertTrue(
                all(
                    re.fullmatch(r"RIGHT-[A-Z]+(?:-[A-Z]+)*", reference)
                    for reference in record.get("additional_rights_refs", [])
                )
            )
            self.assertTrue(record["authority_role"], record["id"])
            self.assertTrue(record["derivation"], record["id"])
            self.assertIn(record["lifecycle_state"], {"active", "archived", "unknown"})
            self.assertTrue(record["evidence_refs"], record["id"])
            self.assertTrue(
                all(reference.startswith("EV-") for reference in record["evidence_refs"])
            )
            self.assertTrue(set(record["evidence_refs"]) <= evidence_ids)
            self.assertTrue(record["caveat_ids"], record["id"])
            self.assertTrue(
                all(value.startswith("CAV-") for value in record["caveat_ids"])
            )

    def test_business_gateway_records_are_restricted_and_safely_described(self) -> None:
        records = [
            record
            for record in self.catalogue["records"]
            if urlparse(record["url"]).hostname
            == "businessgateway.landregistry.gov.uk"
        ]
        self.assertEqual(15, len(records))
        for record in records:
            self.assertEqual("approved-professional-users", record["access_state"])
            self.assertEqual("restricted-service", record["rights_state"])
            self.assertEqual("RIGHT-RESTRICTED", record["rights_ref"])
            self.assertIn(
                "CAV-NO-RESTRICTED-AUTOMATION", record["caveat_ids"]
            )
            self.assertIn("Business e-services approval", record["authentication"])
            self.assertNotIn("automate the collection", record["description"].casefold())

    def test_public_rights_projection_preserves_additional_constraints(self) -> None:
        rights = json.loads((BUNDLE / "data" / "rights.json").read_text())
        projected = {record["id"]: record for record in rights["records"]}
        self.assertEqual(
            {record["id"] for record in self.catalogue["records"]},
            set(projected),
        )
        for record in self.catalogue["records"]:
            self.assertEqual(
                record.get("additional_rights_refs", []),
                projected[record["id"]]["additional_rights_refs"],
                record["id"],
            )

    def test_collision_reconciliation_preserves_every_representation(self) -> None:
        reconciliation = json.loads(
            (BUNDLE / "data" / "reconciliation.json").read_text()
        )
        self.assertEqual(
            reconciliation["input_representations"],
            reconciliation["retained_records"]
            + reconciliation["merged_representations"],
        )
        self.assertEqual(
            reconciliation["canonical_url_collisions"],
            len(reconciliation["collisions"]),
        )
        record_by_url = {
            record["url"]: record for record in self.catalogue["records"]
        }
        for collision in reconciliation["collisions"]:
            record = record_by_url[collision["url"]]
            self.assertEqual(
                sorted(collision["representation_ids"]),
                sorted(record["source_native_ids"]),
            )
            self.assertEqual(collision["selected_id"], record["id"])

    def test_data_manifest_and_record_shards_are_exact(self) -> None:
        manifest = json.loads((BUNDLE / "data" / "manifest.json").read_text())
        paths = {row["path"] for row in manifest["files"]}
        self.assertIn("data/provenance.json", paths)
        self.assertIn("data/rights.json", paths)
        self.assertIn("data/reconciliation.json", paths)
        self.assertIn("data/explorer/search/manifest.json", paths)
        self.assertIn("data/explorer/analysis-overview.json", paths)
        self.assertIn("data/explorer/manifest.json", paths)
        self.assertNotIn("data/search/index.json", paths)
        for row in manifest["files"]:
            artifact = BUNDLE / row["path"]
            self.assertEqual(row["bytes"], artifact.stat().st_size)
            self.assertEqual(row["sha256"], hashlib.sha256(artifact.read_bytes()).hexdigest())

        explorer_manifest = json.loads(
            (BUNDLE / "data" / "explorer" / "manifest.json").read_text()
        )
        self.assertEqual(
            "okf-explorer-data-manifest.v1", explorer_manifest["schema"]
        )
        self.assertEqual(
            self.catalogue["record_count"],
            explorer_manifest["counts"]["records"],
        )

    def test_okf_log_is_newest_first(self) -> None:
        log = (BUNDLE / "log.md").read_text(encoding="utf-8")
        headings = re.findall(r"(?m)^##\s+(\d{4}-\d{2}-\d{2})$", log)
        self.assertEqual(headings, sorted(headings, reverse=True))

    def test_csv_formula_cells_are_neutralised(self) -> None:
        for prefix in ("=", "+", "-", "@", "\t", "\r"):
            self.assertEqual("'" + prefix + "unsafe", builder.csv_safe(prefix + "unsafe"))
        self.assertEqual("safe", builder.csv_safe("safe"))

    def test_output_replacement_requires_a_recognised_generated_bundle(self) -> None:
        with tempfile.TemporaryDirectory(prefix=".test-output-", dir=ROOT) as name:
            parent = Path(name)
            with self.assertRaisesRegex(ValueError, "named 'bundle'"):
                builder.validate_output_target(parent / "docs", replace=True)
            unmarked = parent / "bundle"
            unmarked.mkdir()
            with self.assertRaisesRegex(ValueError, "unmarked"):
                builder.validate_output_target(unmarked, replace=True)
            (unmarked / builder.GENERATED_MARKER).write_text("generated\n")
            builder.validate_output_target(unmarked, replace=True)

    def test_current_snapshot_receipts_are_rehashed_and_reconciled(self) -> None:
        snapshot_dir = builder.newest_snapshot()
        self.assertIsNotNone(snapshot_dir)
        manifest = json.loads((snapshot_dir / "manifest.json").read_text())
        self.assertEqual("okf-hmlr-metadata-snapshot.v2", manifest["schema"])
        records, snapshot = builder.snapshot_records(snapshot_dir)
        self.assertEqual(sum(manifest["totals"].values()), len(records))
        self.assertEqual("complete", manifest["terminal_outcome"]["status"])
        self.assertEqual(
            hashlib.sha256((snapshot_dir / "manifest.json").read_bytes()).hexdigest(),
            snapshot["source_manifest_sha256"],
        )

    def test_forbidden_raw_content_and_secret_shapes_absent(self) -> None:
        text = (BUNDLE / "data" / "catalogue.json").read_text(encoding="utf-8")
        secret_patterns = [
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            r"\bghp_[A-Za-z0-9]{30,}\b",
            r"\bAKIA[0-9A-Z]{16}\b",
            r"[?&](?:api[_-]?key|token|signature|x-amz-signature)=",
        ]
        for pattern in secret_patterns:
            self.assertIsNone(re.search(pattern, text, flags=re.IGNORECASE), pattern)
        forbidden_keys = {"property_address", "title_number", "proprietor_name"}
        for record in self.catalogue["records"]:
            self.assertFalse(forbidden_keys & set(record), record["id"])

    def test_release_checksums_are_exact(self) -> None:
        lines = (BUNDLE / "CHECKSUMS.sha256").read_text().splitlines()
        digest_lines = [line for line in lines if line and not line.startswith("#")]
        roots = [
            line.removeprefix("# release-root-sha256: ")
            for line in lines
            if line.startswith("# release-root-sha256: ")
        ]
        self.assertEqual(1, len(roots))
        for line in digest_lines:
            digest, name = line.split("  ", 1)
            self.assertEqual(digest, hashlib.sha256((BUNDLE / name).read_bytes()).hexdigest())
        manifest = ("\n".join(digest_lines) + "\n").encode()
        self.assertEqual(roots[0], hashlib.sha256(manifest).hexdigest())

    def test_full_corpus_reproducibility_is_an_explicit_release_gate(self) -> None:
        runbook = (
            ROOT / "docs" / "v0.3.0-release-tracker-and-assurance-runbook.md"
        ).read_text(encoding="utf-8")
        self.assertIn("okf-landregistry-build-1-recovery", runbook)
        self.assertIn("okf-landregistry-build-2-recovery", runbook)
        self.assertIn("git diff --exit-code -- bundle", runbook)
        self.assertIn(
            "<owner-selected-empty-same-filesystem-path>",
            inspect.getsource(builder.build_reproduction_invocation),
        )

    def test_unit_tests_do_not_spawn_unrecorded_full_corpus_builds(self) -> None:
        offenders: list[str] = []
        for test_path in sorted((ROOT / "tests").glob("test_*.py")):
            tree = ast.parse(test_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                if not (
                    isinstance(function, ast.Attribute)
                    and function.attr
                    in {"run", "Popen", "check_call", "check_output"}
                ):
                    continue
                if "scripts/build.py" in ast.dump(node):
                    offenders.append(
                        f"{test_path.relative_to(ROOT).as_posix()}:{node.lineno}"
                    )
        self.assertEqual([], offenders)


class AtomicBundlePublicationTests(unittest.TestCase):
    def transaction(
        self,
        output: Path,
        previous: Path | None,
        *,
        replace: bool = True,
    ):
        return builder.bundle_publication_transaction(
            output,
            replace=replace,
            previous_output=previous,
            fallback_staging=output.parent / ".unused-fallback-bundle",
        )

    def test_actual_exchange_retains_the_complete_previous_bundle(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix=".test-atomic-live-", dir=ROOT) as live,
            tempfile.TemporaryDirectory(
                prefix=".test-atomic-recovery-", dir=ROOT.parent
            ) as recovery,
        ):
            output = Path(live) / "bundle"
            previous = Path(recovery) / "previous-bundle"
            write_generated_fixture(output, "old")
            old_identity = (output.stat().st_dev, output.stat().st_ino)
            with self.transaction(output, previous) as publication:
                write_generated_fixture(publication.staging_path, "new")
                new_identity = (
                    publication.staging_path.stat().st_dev,
                    publication.staging_path.stat().st_ino,
                )
                result = publication.publish()
            self.assertEqual("exchange", result["publication_operation"])
            self.assertEqual(str(previous), result["previous_output"])
            self.assertEqual(0o755, output.stat().st_mode & 0o777)
            self.assertEqual("new", (output / "value.txt").read_text())
            self.assertEqual("old", (previous / "value.txt").read_text())
            self.assertEqual(new_identity, (output.stat().st_dev, output.stat().st_ino))
            self.assertEqual(
                old_identity,
                (previous.stat().st_dev, previous.stat().st_ino),
            )

    def test_missing_recovery_path_fails_before_expensive_build_work(self) -> None:
        output = ROOT / ".unused-early-publication-test" / "bundle"
        with (
            mock.patch.object(
                builder,
                "validate_output_target",
                return_value=True,
            ),
            mock.patch.object(builder, "validate_profile_vendor_lock") as expensive,
            self.assertRaisesRegex(ValueError, "requires --previous-output"),
        ):
            builder._build_from_snapshot(
                snapshot_dir=None,
                output_dir=output,
                publication_base=builder.PUBLICATION_BASE,
                replace=True,
                previous_output=None,
                build_input_snapshot=mock.Mock(),
            )
        expensive.assert_not_called()

    def test_actual_no_replace_publishes_only_to_an_absent_target(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix=".test-atomic-live-", dir=ROOT) as live,
            tempfile.TemporaryDirectory(
                prefix=".test-atomic-recovery-", dir=ROOT.parent
            ) as recovery,
        ):
            output = Path(live) / "bundle"
            swap_slot = Path(recovery) / "candidate-slot"
            with self.transaction(output, swap_slot, replace=False) as publication:
                write_generated_fixture(publication.staging_path, "new")
                result = publication.publish()
            self.assertEqual("no-replace", result["publication_operation"])
            self.assertIsNone(result["previous_output"])
            self.assertEqual("new", (output / "value.txt").read_text())
            self.assertFalse(swap_slot.exists())

    def test_exchange_failure_retains_old_and_candidate_directories(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix=".test-atomic-live-", dir=ROOT) as live,
            tempfile.TemporaryDirectory(
                prefix=".test-atomic-recovery-", dir=ROOT.parent
            ) as recovery,
        ):
            output = Path(live) / "bundle"
            previous = Path(recovery) / "previous-bundle"
            write_generated_fixture(output, "old")
            with self.transaction(output, previous) as publication:
                write_generated_fixture(publication.staging_path, "new")
                with (
                    mock.patch.object(
                        builder,
                        "_atomic_directory_rename",
                        side_effect=OSError("injected exchange failure"),
                    ),
                    self.assertRaisesRegex(OSError, "injected exchange failure"),
                ):
                    publication.publish()
            self.assertEqual("old", (output / "value.txt").read_text())
            self.assertEqual("new", (previous / "value.txt").read_text())

    def test_no_replace_closes_the_target_appearance_race(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix=".test-atomic-live-", dir=ROOT) as live,
            tempfile.TemporaryDirectory(
                prefix=".test-atomic-recovery-", dir=ROOT.parent
            ) as recovery,
        ):
            output = Path(live) / "bundle"
            swap_slot = Path(recovery) / "candidate-slot"
            original_rename = builder._atomic_directory_rename
            with self.transaction(output, swap_slot, replace=False) as publication:
                write_generated_fixture(publication.staging_path, "candidate")

                def race_then_rename(*args, **kwargs):
                    write_generated_fixture(output, "racing-output")
                    return original_rename(*args, **kwargs)

                with (
                    mock.patch.object(
                        builder,
                        "_atomic_directory_rename",
                        side_effect=race_then_rename,
                    ),
                    self.assertRaises(FileExistsError),
                ):
                    publication.publish()
            self.assertEqual("racing-output", (output / "value.txt").read_text())
            self.assertEqual("candidate", (swap_slot / "value.txt").read_text())

    def test_target_identity_change_aborts_before_exchange(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix=".test-atomic-live-", dir=ROOT) as live,
            tempfile.TemporaryDirectory(
                prefix=".test-atomic-recovery-", dir=ROOT.parent
            ) as recovery,
        ):
            live_root = Path(live)
            output = live_root / "bundle"
            displaced = live_root / "displaced-bundle"
            previous = Path(recovery) / "previous-bundle"
            write_generated_fixture(output, "old")
            with self.transaction(output, previous) as publication:
                write_generated_fixture(publication.staging_path, "candidate")
                output.rename(displaced)
                write_generated_fixture(output, "replacement")
                with self.assertRaisesRegex(ValueError, "identity changed"):
                    publication.publish()
            self.assertEqual("replacement", (output / "value.txt").read_text())
            self.assertEqual("old", (displaced / "value.txt").read_text())
            self.assertEqual("candidate", (previous / "value.txt").read_text())

    def test_post_exchange_error_still_retains_both_bundles(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix=".test-atomic-live-", dir=ROOT) as live,
            tempfile.TemporaryDirectory(
                prefix=".test-atomic-recovery-", dir=ROOT.parent
            ) as recovery,
        ):
            output = Path(live) / "bundle"
            previous = Path(recovery) / "previous-bundle"
            write_generated_fixture(output, "old")
            with self.transaction(output, previous) as publication:
                write_generated_fixture(publication.staging_path, "new")
                with (
                    mock.patch.object(
                        builder.os,
                        "fsync",
                        side_effect=OSError("injected durability failure"),
                    ),
                    self.assertRaisesRegex(OSError, "durability failure"),
                ):
                    publication.publish()
                self.assertEqual("exchange", publication.published_operation)
            self.assertEqual("new", (output / "value.txt").read_text())
            self.assertEqual("old", (previous / "value.txt").read_text())

    def test_two_rebuilds_use_distinct_recovery_paths_and_identical_bytes(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix=".test-atomic-live-", dir=ROOT) as live,
            tempfile.TemporaryDirectory(
                prefix=".test-atomic-recovery-a-", dir=ROOT.parent
            ) as recovery_a,
            tempfile.TemporaryDirectory(
                prefix=".test-atomic-recovery-b-", dir=ROOT.parent
            ) as recovery_b,
        ):
            output = Path(live) / "bundle"
            previous_a = Path(recovery_a) / "previous-bundle"
            previous_b = Path(recovery_b) / "previous-bundle"
            write_generated_fixture(output, "original")
            with self.transaction(output, previous_a) as first:
                write_generated_fixture(first.staging_path, "candidate")
                first.publish()
            first_bytes = tree_bytes(output)
            with self.transaction(output, previous_b) as second:
                write_generated_fixture(second.staging_path, "candidate")
                second.publish()
            self.assertEqual(first_bytes, tree_bytes(output))
            self.assertEqual("original", (previous_a / "value.txt").read_text())
            self.assertEqual("candidate", (previous_b / "value.txt").read_text())

    def test_recovery_path_contract_rejects_reuse_and_in_repository_paths(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix=".test-atomic-live-", dir=ROOT) as live,
            tempfile.TemporaryDirectory(
                prefix=".test-atomic-recovery-", dir=ROOT.parent
            ) as recovery,
        ):
            output = Path(live) / "bundle"
            write_generated_fixture(output, "old")
            existing = Path(recovery) / "existing"
            existing.mkdir()
            with self.assertRaisesRegex(ValueError, "non-existent"):
                with self.transaction(output, existing):
                    pass
            inside_repository = Path(live) / "candidate-slot"
            with self.assertRaisesRegex(ValueError, "outside the repository"):
                with self.transaction(output, inside_repository):
                    pass
            with self.assertRaisesRegex(ValueError, "requires --previous-output"):
                with self.transaction(output, None):
                    pass

    def test_recovery_parent_and_leaf_validation_fail_closed(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix=".test-atomic-live-", dir=ROOT) as live,
            tempfile.TemporaryDirectory(
                prefix=".test-atomic-recovery-", dir=ROOT.parent
            ) as recovery,
        ):
            output = Path(live) / "bundle"
            write_generated_fixture(output, "old")
            with self.assertRaisesRegex(ValueError, "must be an absolute path"):
                with self.transaction(output, Path("relative-candidate-slot")):
                    pass

            recovery_root = Path(recovery)
            ordinary_file = recovery_root / "ordinary-file"
            ordinary_file.write_text("not a directory", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "without following"):
                with self.transaction(output, ordinary_file / "candidate-slot"):
                    pass

            real_parent = recovery_root / "real-parent"
            real_parent.mkdir()
            linked_parent = recovery_root / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "without following"):
                with self.transaction(output, linked_parent / "candidate-slot"):
                    pass

            with self.assertRaisesRegex(ValueError, "unsafe leaf"):
                with self.transaction(output, recovery_root / "unsafe\nslot"):
                    pass

    def test_cross_device_recovery_is_rejected_by_the_shared_guard(self) -> None:
        output_parent = mock.Mock(st_dev=1)
        recovery_parent = mock.Mock(st_dev=2)
        with (
            mock.patch.object(
                builder.os,
                "fstat",
                side_effect=[recovery_parent, output_parent],
            ),
            self.assertRaisesRegex(ValueError, "output file system"),
        ):
            builder._require_same_filesystem(10, 11)

    def test_atomic_exchange_has_no_observable_absent_target(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix=".test-atomic-live-", dir=ROOT) as live,
            tempfile.TemporaryDirectory(
                prefix=".test-atomic-recovery-", dir=ROOT.parent
            ) as recovery,
        ):
            output = Path(live) / "bundle"
            swap_slot = Path(recovery) / "candidate-slot"
            write_generated_fixture(output, "old")
            write_generated_fixture(swap_slot, "new")
            output_parent = builder._open_directory_no_follow(
                output.parent,
                field="observer output parent",
            )
            recovery_parent = builder._open_directory_no_follow(
                swap_slot.parent,
                field="observer recovery parent",
            )
            stop = threading.Event()
            started = threading.Event()
            missing: list[BaseException] = []
            observations = 0

            def observe() -> None:
                nonlocal observations
                started.set()
                while not stop.is_set():
                    try:
                        output.stat()
                        observations += 1
                    except FileNotFoundError as exc:
                        missing.append(exc)

            observer = threading.Thread(target=observe)
            observer.start()
            started.wait(timeout=1)
            try:
                for _index in range(1_000):
                    builder._atomic_directory_rename(
                        recovery_parent,
                        swap_slot.name,
                        output_parent,
                        output.name,
                        exchange=True,
                    )
            finally:
                stop.set()
                observer.join(timeout=2)
                os.close(recovery_parent)
                os.close(output_parent)
            self.assertGreater(observations, 0)
            self.assertEqual([], missing)

    def test_concrete_recovery_path_cannot_enter_reproduction_bytes(self) -> None:
        invocation = builder.build_reproduction_invocation(
            ".venv/bin/python",
            "source/snapshots/fixture/manifest.json",
            "https://example.test/okf/",
        )
        self.assertEqual(
            "<owner-selected-empty-same-filesystem-path>",
            invocation[-1],
        )
        self.assertNotIn(
            "/private/tmp/operator-selected-recovery/previous-bundle",
            json.dumps(invocation),
        )

    def test_output_parent_lock_rejects_a_second_cooperating_builder(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix=".test-atomic-live-", dir=ROOT) as live,
            tempfile.TemporaryDirectory(
                prefix=".test-atomic-recovery-a-", dir=ROOT.parent
            ) as recovery_a,
            tempfile.TemporaryDirectory(
                prefix=".test-atomic-recovery-b-", dir=ROOT.parent
            ) as recovery_b,
        ):
            output = Path(live) / "bundle"
            write_generated_fixture(output, "old")
            previous_a = Path(recovery_a) / "previous-bundle"
            previous_b = Path(recovery_b) / "previous-bundle"
            with self.transaction(output, previous_a):
                with self.assertRaisesRegex(ValueError, "cooperating.*lock"):
                    with self.transaction(output, previous_b):
                        pass
            self.assertFalse(previous_b.exists())

    def test_target_symlink_and_fifo_are_rejected_without_publication(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".test-atomic-special-", dir=ROOT
        ) as live:
            live_root = Path(live)
            real = live_root / "real-bundle"
            write_generated_fixture(real, "real")
            output = live_root / "bundle"
            output.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "ordinary no-follow directory"):
                builder.validate_output_target(output, replace=True)
            output.unlink()
            os.mkfifo(output)
            with self.assertRaisesRegex(ValueError, "ordinary no-follow directory"):
                builder.validate_output_target(output, replace=True)

    def test_unsupported_platform_has_no_destructive_fallback(self) -> None:
        with mock.patch.object(builder.sys, "platform", "unsupported"):
            with self.assertRaisesRegex(ValueError, "no delete-and-rename fallback"):
                builder._atomic_directory_rename(0, "a", 0, "b", exchange=True)
        source = inspect.getsource(builder.BundlePublicationTransaction.publish)
        for forbidden in ("rmtree", "unlink", "os.replace", "shutil.rmtree"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()

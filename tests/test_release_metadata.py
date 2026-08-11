from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from scripts.check_release_evidence import CandidateIdentity, ReleaseEvidenceError
from scripts import build as builder
import scripts.check_release_evidence as evidence_checker
import scripts.create_release_metadata as release_metadata
from scripts.create_release_metadata import (
    CandidateMetadataReader,
    PROVENANCE_MATERIAL_PATHS,
    action_pins,
    build_invocation,
    checked_candidate,
    create_metadata,
    exact_archive_receipt_path,
    locked_packages,
    observed_python_runtime,
    provenance_materials,
    safe_output_directory,
    validate_archive_receipt_binding,
    write_metadata_outputs,
)
from scripts.package_release import create_candidate_archive, create_release_archive


def coordinate_aligned_build_receipt() -> dict[str, object]:
    receipt = json.loads(
        Path("bundle/build-receipt.json").read_text(encoding="utf-8")
    )
    config = json.loads(
        Path("source/build-config.json").read_text(encoding="utf-8")
    )
    for field in (
        "version",
        "publication_base",
        "publication_state",
        "generated_at",
        "release_at",
    ):
        receipt[field] = config[field]
    snapshot_manifest = receipt["snapshot"]["acquisition_snapshot"][
        "manifest_path"
    ]
    receipt["reproduction_invocation"] = [
        ".venv/bin/python",
        "-I",
        "-B",
        "-X",
        "pycache_prefix=<private-empty-directory>",
        "scripts/build.py",
        "--snapshot-dir",
        str(Path(snapshot_manifest).parent),
        "--publication-base",
        config["publication_base"],
        "--replace",
        "--previous-output",
        "<owner-selected-empty-same-filesystem-path>",
    ]
    return receipt


def call_build_invocation(receipt: dict[str, object]) -> list[str]:
    """Validate one receipt against the exact current governed inputs."""

    config = json.loads(
        Path("source/build-config.json").read_text(encoding="utf-8")
    )
    current_receipt = json.loads(
        Path("bundle/build-receipt.json").read_text(encoding="utf-8")
    )
    manifest_name = current_receipt["snapshot"]["acquisition_snapshot"][
        "manifest_path"
    ]
    return build_invocation(
        receipt,
        build_config=config,
        acquisition_manifest_bytes=Path(manifest_name).read_bytes(),
    )


def read_worktree_candidate(name: str, purpose: str, max_bytes: int) -> bytes:
    """Small unit-test reader for provenance path and digest behaviour."""

    del purpose
    value = Path(name).read_bytes()
    if len(value) > max_bytes:
        raise AssertionError(f"fixture input exceeds test byte cap: {name}")
    return value


def write_concurrent_metadata_directory(
    parent_descriptor: int,
    output_name: str,
    documents: dict[str, bytes],
) -> None:
    """Create one racing writer's directory through the held parent handle."""

    os.mkdir(output_name, mode=0o755, dir_fd=parent_descriptor)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory = os.open(output_name, flags, dir_fd=parent_descriptor)
    try:
        for name, content in documents.items():
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o644,
                dir_fd=directory,
            )
            try:
                os.write(descriptor, content)
            finally:
                os.close(descriptor)
    finally:
        os.close(directory)


class ReleaseMetadataTests(unittest.TestCase):
    def archive_fixture(
        self,
        repository_root: Path,
        *,
        archive_kind: str = "candidate-a",
    ) -> tuple[dict[str, object], CandidateIdentity, dict[str, object], Path]:
        version = "0.3.0"
        generated_at = "2026-07-29T12:00:00Z"
        bundle = repository_root / "bundle"
        bundle.mkdir()
        index = bundle / "index.html"
        index.write_text("fixture candidate\n", encoding="utf-8")
        digest_line = f"{hashlib.sha256(index.read_bytes()).hexdigest()}  index.html"
        release_root = hashlib.sha256(
            f"{digest_line}\n".encode("utf-8")
        ).hexdigest()
        checksums = bundle / "CHECKSUMS.sha256"
        checksums.write_text(
            f"{digest_line}\n# release-root-sha256: {release_root}\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "init", "-q"],
            cwd=repository_root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Metadata Fixture"],
            cwd=repository_root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "fixture@example.test"],
            cwd=repository_root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "add", "bundle"],
            cwd=repository_root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add candidate bundle"],
            cwd=repository_root,
            check=True,
            capture_output=True,
        )
        candidate_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        candidate = CandidateIdentity(
            candidate_commit_sha=candidate_commit,
            release_root_sha256=release_root,
            checksums_sha256=hashlib.sha256(checksums.read_bytes()).hexdigest(),
            profile_pack_root_sha256="d" * 64,
            snapshot_manifest_sha256="e" * 64,
        )
        archive_names = {
            "candidate-a": f"okf-landregistry-{version}-candidate-a.zip",
            "candidate-b": f"okf-landregistry-{version}-candidate-b.zip",
            "release": f"okf-landregistry-{version}.zip",
        }
        archive_path = repository_root / "dist" / archive_names[archive_kind]
        archive_path.parent.mkdir(parents=True)
        if archive_kind == "release":
            with mock.patch.dict(
                os.environ,
                {"OKF_RELEASE_ROOT_SHA256": release_root},
                clear=False,
            ):
                receipt = create_release_archive(
                    bundle=bundle,
                    output=archive_path,
                    version=version,
                    release_at=generated_at,
                )
        else:
            receipt = create_candidate_archive(
                bundle=bundle,
                output=archive_path,
                version=version,
                candidate_at=generated_at,
            )
        receipt["candidate"] = asdict(candidate)
        receipt["path"] = f"dist/{archive_names[archive_kind]}"
        config: dict[str, object] = {
            "version": version,
            "publication_state": "digest-bound-external-evidence",
            "generated_at": generated_at,
            "release_at": generated_at if archive_kind == "release" else None,
        }
        return receipt, candidate, config, archive_path

    def test_dependency_lock_has_expected_bounded_package_set(self) -> None:
        packages = locked_packages(Path("requirements-lock.txt").read_bytes())
        self.assertEqual(
            {
                "attrs",
                "cachetools",
                "frozendict",
                "jsonschema",
                "jsonschema-specifications",
                "lxml",
                "pyld",
                "referencing",
                "rpds-py",
                "ruamel-yaml",
                "typing-extensions",
            },
            {name for name, _version in packages},
        )

    def test_metadata_cli_requires_explicit_version_scoped_paths(self) -> None:
        result = subprocess.run(
            [
                ".venv/bin/python",
                "-B",
                "scripts/create_release_metadata.py",
                "--candidate-commit-sha",
                "0" * 40,
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(2, result.returncode)
        self.assertIn("--archive-receipt", result.stderr)
        self.assertIn("--output-directory", result.stderr)

    def test_metadata_paths_are_the_exact_current_runbook_locations(
        self,
    ) -> None:
        archive = Path(
            "validation/candidate-v0.3.0/evidence/release-candidate-archive-a.json"
        )
        self.assertEqual(
            archive.as_posix(),
            exact_archive_receipt_path(archive, version="0.3.0").as_posix(),
        )
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = Path(
                "validation/candidate-v0.3.0/evidence/release-metadata"
            )
            self.assertEqual(
                root.resolve() / output,
                safe_output_directory(
                    output,
                    version="0.3.0",
                    repository_root=root,
                ),
            )

        rejected_archives = (
            Path(
                "validation/candidate-v0.3.0/evidence/release-candidate-archive-b.json"
            ),
            Path(
                "validation/candidate-v0.3.0/final-g9/"
                "release-candidate-archive-a.json"
            ),
            Path("validation/evidence/release-archive.json"),
            Path("validation/release-archive.json"),
            Path("validation/candidate-v0.2.0/evidence/release-archive.json"),
            Path("validation/v0.2.0-pre-g9/release-archive.json"),
            Path("../validation/candidate-v0.3.0/evidence/archive.json"),
            Path("/validation/candidate-v0.3.0/evidence/archive.json"),
        )
        for path in rejected_archives:
            with self.subTest(path=path):
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "must be exactly|must be a safe repository-relative path",
                ):
                    exact_archive_receipt_path(path, version="0.3.0")

        rejected_outputs = (
            Path("validation/candidate-v0.3.0/evidence"),
            Path("validation/candidate-v0.3.0/evidence/other-metadata"),
            Path("validation/candidate-v0.3.0/final-g9/release-metadata"),
            Path("validation/candidate-v0.2.0/evidence/release-metadata"),
        )
        for path in rejected_outputs:
            with self.subTest(path=path):
                with self.assertRaisesRegex(ReleaseEvidenceError, "must be exactly"):
                    safe_output_directory(path, version="0.3.0")

        malformed_versions = (
            "0.3.0/other",
            "0.3.0 extra",
            "01.2.3",
            "1.2",
            "1.2.3-beta",
            "",
        )
        for version in malformed_versions:
            with self.subTest(version=version, helper="archive"):
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "canonical major.minor.patch version",
                ):
                    exact_archive_receipt_path(archive, version=version)
            with self.subTest(version=version, helper="output"):
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "canonical major.minor.patch version",
                ):
                    safe_output_directory(
                        Path(
                            "validation/candidate-v0.3.0/evidence/"
                            "release-metadata"
                        ),
                        version=version,
                    )

    def test_metadata_writer_preserves_identical_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = (
                root
                / "validation"
                / "candidate-v0.3.0"
                / "evidence"
                / "release-metadata"
            )
            output.mkdir(parents=True)
            sbom = output / "sbom.spdx.json"
            provenance = output / "provenance.json"
            sbom.write_bytes(b'{"same":"sbom"}\n')
            provenance.write_bytes(b'{"same":"provenance"}\n')
            before = {
                path: (path.stat().st_ino, path.stat().st_mtime_ns)
                for path in (sbom, provenance)
            }

            write_metadata_outputs(
                root,
                output,
                {
                    sbom: b'{"same":"sbom"}\n',
                    provenance: b'{"same":"provenance"}\n',
                }
            )

            self.assertEqual(
                before,
                {
                    path: (path.stat().st_ino, path.stat().st_mtime_ns)
                    for path in (sbom, provenance)
                },
            )

    def test_metadata_writer_rejects_a_difference_before_writing_any_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = (
                root
                / "validation"
                / "candidate-v0.3.0"
                / "evidence"
                / "release-metadata"
            )
            output.mkdir(parents=True)
            sbom = output / "sbom.spdx.json"
            provenance = output / "provenance.json"
            provenance.write_bytes(b'{"old":true}\n')

            with self.assertRaisesRegex(
                ReleaseEvidenceError,
                "not the exact complete two-file set",
            ):
                write_metadata_outputs(
                    root,
                    output,
                    {
                        sbom: b'{"new":"sbom"}\n',
                        provenance: b'{"new":"provenance"}\n',
                    }
                )

            self.assertFalse(sbom.exists())
            self.assertEqual(b'{"old":true}\n', provenance.read_bytes())

    def test_metadata_writer_rejects_a_complete_but_different_existing_set(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = (
                root
                / "validation"
                / "candidate-v0.3.0"
                / "evidence"
                / "release-metadata"
            )
            output.mkdir(parents=True)
            (output / "sbom.spdx.json").write_bytes(b"old sbom\n")
            (output / "provenance.json").write_bytes(b"old provenance\n")
            before = {
                path.name: path.read_bytes()
                for path in output.iterdir()
            }

            with self.assertRaisesRegex(
                ReleaseEvidenceError,
                "refusing to accept differing metadata output",
            ):
                write_metadata_outputs(
                    root,
                    output,
                    {
                        output / "sbom.spdx.json": b"new sbom\n",
                        output / "provenance.json": b"new provenance\n",
                    },
                )

            self.assertEqual(
                before,
                {path.name: path.read_bytes() for path in output.iterdir()},
            )

    def test_metadata_writer_accepts_an_identical_racing_complete_directory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = (
                root
                / "validation"
                / "candidate-v0.3.0"
                / "evidence"
                / "release-metadata"
            )
            documents = {
                "sbom.spdx.json": b"sbom\n",
                "provenance.json": b"provenance\n",
            }

            def win_race(parent: int, _source: str, destination: str) -> None:
                write_concurrent_metadata_directory(parent, destination, documents)
                raise FileExistsError(destination)

            with mock.patch.object(
                release_metadata,
                "_rename_directory_no_replace",
                side_effect=win_race,
            ):
                write_metadata_outputs(
                    root,
                    output,
                    {
                        output / filename: content
                        for filename, content in documents.items()
                    },
                )

            self.assertEqual(
                documents,
                {path.name: path.read_bytes() for path in output.iterdir()},
            )
            self.assertEqual(
                [], list(output.parent.glob(".release-metadata-*"))
            )

    def test_metadata_writer_rejects_a_racing_partial_directory_without_publishing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = (
                root
                / "validation"
                / "candidate-v0.3.0"
                / "evidence"
                / "release-metadata"
            )

            def win_partial_race(
                parent: int, _source: str, destination: str
            ) -> None:
                write_concurrent_metadata_directory(
                    parent,
                    destination,
                    {"provenance.json": b"concurrent external output\n"},
                )
                raise FileExistsError(destination)

            with (
                mock.patch.object(
                    release_metadata,
                    "_rename_directory_no_replace",
                    side_effect=win_partial_race,
                ),
                self.assertRaisesRegex(
                    ReleaseEvidenceError, "exact complete two-file set"
                ),
            ):
                write_metadata_outputs(
                    root,
                    output,
                    {
                        output / "sbom.spdx.json": b"sbom\n",
                        output / "provenance.json": b"provenance\n",
                    },
                )

            self.assertEqual(
                {"provenance.json": b"concurrent external output\n"},
                {path.name: path.read_bytes() for path in output.iterdir()},
            )
            self.assertEqual(
                [], list(output.parent.glob(".release-metadata-*")),
            )

    def test_metadata_writer_rename_failure_leaves_the_governed_output_absent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = (
                root
                / "validation"
                / "candidate-v0.3.0"
                / "evidence"
                / "release-metadata"
            )

            with (
                mock.patch.object(
                    release_metadata,
                    "_rename_directory_no_replace",
                    side_effect=OSError("injected atomic rename failure"),
                ),
                self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "atomically publish the complete metadata directory",
                ),
            ):
                write_metadata_outputs(
                    root,
                    output,
                    {
                        output / "sbom.spdx.json": b"sbom\n",
                        output / "provenance.json": b"provenance\n",
                    },
                )

            self.assertFalse(output.exists())
            self.assertEqual(
                [], list((root / "validation").rglob(".release-metadata-*"))
                if (root / "validation").exists()
                else [],
            )

    def test_cleanup_failure_can_leave_only_a_rejected_hidden_complete_stage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = (
                root
                / "validation"
                / "candidate-v0.3.0"
                / "evidence"
                / "release-metadata"
            )
            with (
                mock.patch.object(
                    release_metadata,
                    "_rename_directory_no_replace",
                    side_effect=OSError("injected atomic rename failure"),
                ),
                mock.patch.object(
                    release_metadata.os,
                    "unlink",
                    side_effect=OSError("injected cleanup failure"),
                ),
                self.assertRaises(ReleaseEvidenceError),
            ):
                write_metadata_outputs(
                    root,
                    output,
                    {
                        output / "sbom.spdx.json": b"sbom\n",
                        output / "provenance.json": b"provenance\n",
                    },
                )

            self.assertFalse(output.exists())
            stages = list(output.parent.glob(".release-metadata-*"))
            self.assertEqual(1, len(stages))
            self.assertEqual(
                {"provenance.json", "sbom.spdx.json"},
                {path.name for path in stages[0].iterdir()},
            )
            relative = stages[0].relative_to(root).as_posix().encode("utf-8")
            self.assertFalse(evidence_checker._is_mutable_evidence_path(relative))
            self.assertFalse(evidence_checker._is_evidence_root_path(relative))

    def test_metadata_writer_detects_a_parent_symlink_swap(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            evidence = root / "validation" / "candidate-v0.3.0" / "evidence"
            output = evidence / "release-metadata"
            displaced = evidence.with_name("displaced-evidence")
            outside = root / "outside"
            outside.mkdir()
            real_rename = release_metadata._rename_directory_no_replace
            swapped = False

            def swap_parent_before_rename(
                parent: int,
                source: str,
                destination: str,
            ) -> None:
                nonlocal swapped
                if not swapped:
                    evidence.rename(displaced)
                    evidence.symlink_to(outside, target_is_directory=True)
                    swapped = True
                real_rename(parent, source, destination)

            with (
                mock.patch.object(
                    release_metadata,
                    "_rename_directory_no_replace",
                    side_effect=swap_parent_before_rename,
                ),
                self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "metadata output parent moved|metadata output parent changed",
                ),
            ):
                write_metadata_outputs(
                    root,
                    output,
                    {
                        output / "sbom.spdx.json": b"sbom\n",
                        output / "provenance.json": b"provenance\n",
                    },
                )

            self.assertEqual(
                [], [path for path in outside.rglob("*") if path.is_file()]
            )
            self.assertEqual(
                {"provenance.json", "sbom.spdx.json"},
                {
                    path.name
                    for path in (displaced / "release-metadata").iterdir()
                    if path.is_file()
                },
            )
            self.assertEqual(
                [], list(displaced.glob(".release-metadata-*"))
            )

    def test_pages_workflow_actions_are_sha_pinned(self) -> None:
        pins = action_pins(Path(".github/workflows/pages.yml").read_bytes())
        self.assertGreaterEqual(len(pins), 5)
        self.assertTrue(
            all(
                len(pin["revision"]) == 40
                for pin in pins
                if pin["kind"] == "github-action"
            )
        )
        self.assertTrue(all(pin["scope"].startswith("job:") for pin in pins))

    def test_workflow_action_inventory_is_structural_and_complete(self) -> None:
        commit = "a" * 40
        digest = "b" * 64
        workflow = f"""
jobs:
  reusable:
    "uses": owner/repository/.github/workflows/check.yml@{commit}
  build:
    steps:
      - {{"uses": owner/action@{commit}}}
      - uses: docker://registry.example.test/tool@sha256:{digest}
""".encode("utf-8")

        self.assertEqual(
            [
                {
                    "kind": "github-action",
                    "action": "owner/repository/.github/workflows/check.yml",
                    "revision": commit,
                    "scope": "job:reusable",
                },
                {
                    "kind": "github-action",
                    "action": "owner/action",
                    "revision": commit,
                    "scope": "job:build:step:0",
                },
                {
                    "kind": "container-image",
                    "action": "registry.example.test/tool",
                    "revision": f"sha256:{digest}",
                    "scope": "job:build:step:1",
                },
            ],
            action_pins(workflow),
        )

    def test_workflow_action_inventory_rejects_any_unpinned_or_local_use(self) -> None:
        commit = "a" * 40
        invalid_workflows = (
            f"""
jobs:
  build:
    steps:
      - uses: owner/pinned@{commit}
      - uses: owner/unpinned@v1
""",
            """
jobs:
  build:
    steps:
      - uses: docker://registry.example.test/tool:latest
""",
            """
jobs:
  build:
    steps:
      - uses: ./local-action
""",
        )
        for workflow in invalid_workflows:
            with self.subTest(workflow=workflow):
                with self.assertRaises(ReleaseEvidenceError):
                    action_pins(workflow.encode("utf-8"))

    def test_workflow_action_inventory_rejects_noncanonical_github_paths(
        self,
    ) -> None:
        commit = "a" * 40
        invalid_actions = (
            "owner",
            "../action",
            "owner/../action",
            "owner//action",
            "/owner/action",
            "https://github.com/owner/action",
            "git+https://github.com/owner/action",
            "owner_/action",
            "owner/repository/.",
        )
        for action in invalid_actions:
            with self.subTest(action=action):
                workflow = (
                    "jobs:\n"
                    "  build:\n"
                    "    steps:\n"
                    f"      - uses: {action}@{commit}\n"
                )
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "not pinned by a full 40-character commit",
                ):
                    action_pins(workflow.encode("utf-8"))

    def test_strict_hashed_lock_rejects_ambiguous_or_incomplete_blocks(self) -> None:
        digest = b"0" * 64
        invalid = (
            b"fixture==1.0\n",
            b"fixture==1.0 \\\n    --hash=sha256:" + digest + b" \\\n",
            b"fixture==1.0 \\\n    --hash=sha256:" + digest + b"\ntrailing junk\n",
            b"fixture==1.0;python_version<'3.13' \\\n"
            b"    --hash=sha256:" + digest + b"\n",
            b"fixture==1.0 \\\n    --hash=sha256:" + digest + b"\n"
            b"fixture==2.0 \\\n    --hash=sha256:" + b"1" * 64 + b"\n",
            b"fixture==1.0 \\\n    --hash=sha256:" + digest + b" \\\n"
            b"    --hash=sha256:" + digest + b"\n",
        )
        for lock in invalid:
            with self.subTest(lock=lock):
                with self.assertRaises(ValueError):
                    locked_packages(lock)

    def test_provenance_uses_exact_frozen_snapshot_build_command(self) -> None:
        receipt = coordinate_aligned_build_receipt()
        snapshot_manifest = receipt["snapshot"]["acquisition_snapshot"][
            "manifest_path"
        ]
        self.assertEqual(
            [
                ".venv/bin/python",
                "-I",
                "-B",
                "-X",
                "pycache_prefix=<private-empty-directory>",
                "scripts/build.py",
                "--snapshot-dir",
                str(Path(snapshot_manifest).parent),
                "--publication-base",
                "https://chris-page-gov.github.io/okf-LandRegistry/",
                "--replace",
                "--previous-output",
                "<owner-selected-empty-same-filesystem-path>",
            ],
            call_build_invocation(receipt),
        )

    def test_metadata_runtime_matches_the_observed_build_runtime(self) -> None:
        self.assertIs(
            builder.observe_python_runtime,
            release_metadata.observe_python_runtime,
        )
        lock = Path("requirements-lock.txt").read_bytes()
        sentinel = {"schema": "shared-runtime-fixture"}
        with mock.patch.object(
            release_metadata,
            "observe_python_runtime",
            return_value=sentinel,
        ) as observer:
            self.assertEqual(sentinel, observed_python_runtime(lock))
        observer.assert_called_once_with(Path.cwd(), lock)

    def test_build_invocation_rejects_an_unobserved_interpreter(self) -> None:
        receipt = json.loads(
            Path("bundle/build-receipt.json").read_text(encoding="utf-8")
        )
        tampered = deepcopy(receipt)
        tampered.pop("python_runtime")
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "lacks its observed Python runtime",
        ):
            call_build_invocation(tampered)

    def test_build_invocation_rejects_a_reconstructed_or_changed_command(self) -> None:
        receipt = coordinate_aligned_build_receipt()
        tampered = deepcopy(receipt)
        tampered["reproduction_invocation"][-1] = "--changed"
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "reproduction invocation differs",
        ):
            call_build_invocation(tampered)

    def test_build_invocation_rejects_changed_release_coordinates(self) -> None:
        receipt = json.loads(
            Path("bundle/build-receipt.json").read_text(encoding="utf-8")
        )
        config = json.loads(
            Path("source/build-config.json").read_text(encoding="utf-8")
        )
        snapshot_manifest = receipt["snapshot"]["acquisition_snapshot"][
            "manifest_path"
        ]
        tampered = deepcopy(receipt)
        tampered["version"] = config["version"]
        tampered["publication_base"] = config["publication_base"]
        tampered["python_runtime"] = {
            "executable_contract": ".venv/bin/python"
        }
        tampered["reproduction_invocation"] = [
            ".venv/bin/python",
            "-I",
            "-B",
            "-X",
            "pycache_prefix=<private-empty-directory>",
            "scripts/build.py",
            "--snapshot-dir",
            str(Path(snapshot_manifest).parent),
            "--publication-base",
            config["publication_base"],
            "--replace",
            "--previous-output",
            "<owner-selected-empty-same-filesystem-path>",
        ]
        tampered["publication_base"] = "https://different.example.test/"
        tampered["reproduction_invocation"] = [
            value
            if value != "https://chris-page-gov.github.io/okf-LandRegistry/"
            else tampered["publication_base"]
            for value in tampered["reproduction_invocation"]
        ]
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "publication_base does not match governed",
        ):
            call_build_invocation(tampered)

    def test_build_invocation_rejects_changed_release_state_and_time(
        self,
    ) -> None:
        receipt = coordinate_aligned_build_receipt()
        cases = (
            ("publication_state", "different-state"),
            ("generated_at", "2099-01-01T00:00:00Z"),
            ("release_at", "2099-01-01T00:00:00Z"),
        )
        for field, replacement in cases:
            with self.subTest(field=field):
                tampered = deepcopy(receipt)
                tampered[field] = replacement
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    f"build receipt {field} does not match governed",
                ):
                    call_build_invocation(tampered)

    def test_metadata_entry_parses_the_secure_archive_receipt_buffer(self) -> None:
        candidate = CandidateIdentity(
            candidate_commit_sha="0" * 40,
            release_root_sha256="1" * 64,
            checksums_sha256="2" * 64,
            profile_pack_root_sha256="3" * 64,
            snapshot_manifest_sha256="4" * 64,
        )
        config_bytes = json.dumps(
            {
                "version": "0.3.0",
                "status": "ai-generated-proof-of-concept",
                "ai_generated_proof_of_concept": True,
            }
        ).encode("utf-8")
        receipt = {"schema": "secure-buffer-fixture"}
        receipt_bytes = json.dumps(receipt).encode("utf-8")
        documents = {
            "sbom.spdx.json": b"{}\n",
            "provenance.json": b"{}\n",
        }
        output = Path(
            "validation/candidate-v0.3.0/evidence/release-metadata"
        )
        archive = Path(
            "validation/candidate-v0.3.0/evidence/"
            "release-candidate-archive-a.json"
        )
        reader = mock.Mock()
        reader.read.return_value = config_bytes
        with (
            mock.patch(
                "scripts.create_release_metadata.checked_candidate",
                return_value=candidate,
            ),
            mock.patch(
                "scripts.create_release_metadata.CandidateMetadataReader",
                return_value=reader,
            ),
            mock.patch(
                "scripts.create_release_metadata.read_repository_file_bytes",
                return_value=receipt_bytes,
            ) as secure_read,
            mock.patch(
                "scripts.create_release_metadata.observed_python_runtime",
                return_value={"schema": "fixture-runtime"},
            ),
            mock.patch(
                "scripts.create_release_metadata.expected_release_metadata_documents",
                return_value=("0.3.0", documents),
            ) as derive,
            mock.patch(
                "scripts.create_release_metadata.write_metadata_outputs"
            ) as writer,
        ):
            paths = create_metadata(
                candidate_commit="0" * 40,
                archive_receipt_path=archive,
                output_directory=output,
            )

        secure_read.assert_called_once()
        self.assertEqual(receipt, derive.call_args.kwargs["archive_receipt"])
        writer.assert_called_once()
        self.assertEqual(
            (
                Path.cwd() / output / "sbom.spdx.json",
                Path.cwd() / output / "provenance.json",
            ),
            paths,
        )

    def test_candidate_check_invokes_governed_tree_validation(self) -> None:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        sentinel = CandidateIdentity(
            candidate_commit_sha=commit,
            release_root_sha256="1" * 64,
            checksums_sha256="2" * 64,
            profile_pack_root_sha256="3" * 64,
            snapshot_manifest_sha256="4" * 64,
        )
        with (
            mock.patch(
                "scripts.create_release_metadata.validate_governed_candidate_commit"
            ) as validate,
            mock.patch(
                "scripts.create_release_metadata.validate_committed_candidate_closure",
                return_value=(sentinel, 42),
            ),
        ):
            self.assertIs(sentinel, checked_candidate(commit))
        validate.assert_called_once()

    def test_candidate_check_uses_bounded_git_resolution(self) -> None:
        commit = "a" * 40
        sentinel = CandidateIdentity(
            candidate_commit_sha=commit,
            release_root_sha256="1" * 64,
            checksums_sha256="2" * 64,
            profile_pack_root_sha256="3" * 64,
            snapshot_manifest_sha256="4" * 64,
        )
        completed = subprocess.CompletedProcess(
            ["git", "rev-parse"],
            0,
            f"{commit}\n".encode("ascii"),
            b"",
        )
        with (
            mock.patch(
                "scripts.create_release_metadata._git_command_bytes",
                return_value=completed,
            ) as bounded_git,
            mock.patch(
                "scripts.create_release_metadata.validate_governed_candidate_commit"
            ),
            mock.patch(
                "scripts.create_release_metadata.validate_committed_candidate_closure",
                return_value=(sentinel, 42),
            ),
        ):
            self.assertIs(sentinel, checked_candidate(commit))
        bounded_git.assert_called_once_with(
            Path.cwd(),
            ["rev-parse", "--verify", f"{commit}^{{commit}}"],
            maximum_stdout_bytes=64,
        )

    def test_provenance_materials_include_exact_explorer_lock(self) -> None:
        self.assertIn(
            "contracts/okf-explorer.consumer-lock.json",
            PROVENANCE_MATERIAL_PATHS,
        )
        receipt = json.loads(
            Path("bundle/build-receipt.json").read_text(encoding="utf-8")
        )
        materials = {
            row["path"]: row["sha256"]
            for row in provenance_materials(
                receipt,
                read_candidate=read_worktree_candidate,
            )
        }
        self.assertIn("contracts/okf-explorer.consumer-lock.json", materials)
        self.assertRegex(
            materials["contracts/okf-explorer.consumer-lock.json"],
            r"^[0-9a-f]{64}$",
        )

    def test_build_invocation_rejects_an_ungoverned_snapshot_path(self) -> None:
        receipt = json.loads(
            Path("bundle/build-receipt.json").read_text(encoding="utf-8")
        )
        tampered = deepcopy(receipt)
        tampered["snapshot"]["acquisition_snapshot"]["manifest_path"] = (
            "source/../outside/manifest.json"
        )
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "not governed",
        ):
            call_build_invocation(tampered)

    def test_candidate_metadata_reader_ignores_timed_worktree_replacement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            lock = root / "requirements-lock.txt"
            lock.write_bytes(b"safe-package==1.0\n")
            subprocess.run(
                ["git", "init", "-q"], cwd=root, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Metadata Reader Fixture"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "fixture@example.test"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "add", "requirements-lock.txt"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "Freeze metadata input"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            reader = CandidateMetadataReader(root, commit)

            lock.write_bytes(b"replacement-package==9.9\n")

            self.assertEqual(
                b"safe-package==1.0\n",
                reader.read("requirements-lock.txt", "dependency lock", 100),
            )

    def test_archive_receipt_version_must_equal_governed_version(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            receipt, candidate, config, _archive = self.archive_fixture(root)
            receipt["version"] = "9.9.9"
            with self.assertRaisesRegex(
                ReleaseEvidenceError,
                "version does not match the governed version",
            ):
                validate_archive_receipt_binding(
                    receipt,
                    candidate=candidate,
                    config=config,
                    expected_archive_kind="candidate-a",
                    repository_root=root,
                )

    def test_archive_receipt_bytes_must_equal_actual_archive_size(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            receipt, candidate, config, archive = self.archive_fixture(root)
            receipt["bytes"] = archive.stat().st_size + 1
            with self.assertRaisesRegex(
                ReleaseEvidenceError,
                "byte count does not match the ZIP",
            ):
                validate_archive_receipt_binding(
                    receipt,
                    candidate=candidate,
                    config=config,
                    expected_archive_kind="candidate-a",
                    repository_root=root,
                )

    def test_archive_receipt_rehashes_exact_governed_archive(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            receipt, candidate, config, archive = self.archive_fixture(root)
            schema, validated_archive = validate_archive_receipt_binding(
                receipt,
                candidate=candidate,
                config=config,
                expected_archive_kind="candidate-a",
                repository_root=root,
            )
            self.assertEqual("okf-hmlr-candidate-archive.v1", schema)
            self.assertEqual(archive, validated_archive)

    def test_archive_metadata_helper_rejects_ungoverned_zip_comment(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            receipt, candidate, config, archive = self.archive_fixture(root)
            with zipfile.ZipFile(archive, "a") as packaged:
                packaged.comment = b"ungoverned archive comment"
            receipt["bytes"] = archive.stat().st_size
            receipt["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
            with self.assertRaisesRegex(
                ReleaseEvidenceError,
                "archive comment is not the governed empty value",
            ):
                validate_archive_receipt_binding(
                    receipt,
                    candidate=candidate,
                    config=config,
                    expected_archive_kind="candidate-a",
                    repository_root=root,
                )

    def test_archive_kinds_bind_exact_candidate_a_b_and_final_paths(self) -> None:
        for archive_kind, expected_schema in (
            ("candidate-a", "okf-hmlr-candidate-archive.v1"),
            ("candidate-b", "okf-hmlr-candidate-archive.v1"),
            ("release", "okf-hmlr-release-archive.v1"),
        ):
            with self.subTest(archive_kind=archive_kind), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                receipt, candidate, config, archive = self.archive_fixture(
                    root,
                    archive_kind=archive_kind,
                )
                schema, observed = validate_archive_receipt_binding(
                    receipt,
                    candidate=candidate,
                    config=config,
                    expected_archive_kind=archive_kind,
                    repository_root=root,
                )
                self.assertEqual(expected_schema, schema)
                self.assertEqual(archive, observed)

    def test_archive_kind_cannot_be_substituted_by_another_valid_archive(self) -> None:
        for actual_kind, expected_kind in (
            ("candidate-a", "candidate-b"),
            ("candidate-b", "candidate-a"),
            ("candidate-a", "release"),
            ("release", "candidate-a"),
        ):
            with (
                self.subTest(actual_kind=actual_kind, expected_kind=expected_kind),
                tempfile.TemporaryDirectory() as name,
            ):
                root = Path(name)
                receipt, candidate, config, _archive = self.archive_fixture(
                    root,
                    archive_kind=actual_kind,
                )
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "schema does not match|path does not match",
                ):
                    validate_archive_receipt_binding(
                        receipt,
                        candidate=candidate,
                        config=config,
                        expected_archive_kind=expected_kind,
                        repository_root=root,
                    )


if __name__ == "__main__":
    unittest.main()

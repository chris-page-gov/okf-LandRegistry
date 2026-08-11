from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import zipfile

from scripts import check_release_evidence as release_evidence_checker
from scripts.check_release_evidence import (
    GATE_RECEIPTS,
    MAX_BUNDLE_AGGREGATE_BYTES,
    MAX_BUNDLE_ARTEFACT_BYTES,
    MAX_BUNDLE_CHECKSUM_ENTRIES,
    MAX_EVIDENCE_BYTES,
    MAX_PROFILE_AGGREGATE_BYTES,
    MAX_PROFILE_CHECKSUM_ENTRIES,
    REVIEWED_GATES,
    REQUIRED_CHECKS,
    CandidateTreeBlobReader,
    CandidateIdentity,
    GitTreeEntry,
    ReleaseEvidenceError,
    _git_command_bytes,
    candidate_identity_from_repository,
    current_commit,
    read_repository_file_bytes,
    release_coordinates_from_build_config,
    safe_repository_file,
    sha256_file,
    sha256_repository_file,
    validate_checksum_manifest,
    validate_governed_candidate_commit,
    validate_release_record,
    validate_release_evidence,
    validate_staged_candidate,
)
from scripts.create_release_metadata import expected_release_metadata_documents


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "release-evidence.schema.json"
SCHEMA_ID = (
    "https://chris-page-gov.github.io/okf-LandRegistry/"
    "schemas/release-evidence.schema.json"
)
NOW = "2026-07-29T12:00:00Z"


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def digest_line(path: Path, name: str) -> str:
    return f"{sha256_file(path)}  {name}"


def write_fake_git(directory: Path, body: str) -> Path:
    executable = directory / "git"
    executable.write_text(
        f"#!{sys.executable} -B\n{body}\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def write_zip_member(
    archive: zipfile.ZipFile,
    name: str,
    value: bytes | str,
    *,
    unix_mode: int = 0o100644,
    metadata_overrides: dict[str, object] | None = None,
) -> None:
    info = zipfile.ZipInfo(
        filename=name,
        date_time=(2026, 7, 29, 12, 0, 0),
    )
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (unix_mode & 0xFFFF) << 16
    for field, replacement in (metadata_overrides or {}).items():
        setattr(info, field, replacement)
    archive.writestr(info, value, compresslevel=9)


class ChecksumManifestSizePolicyTests(unittest.TestCase):
    @staticmethod
    def _write_checksum_manifest(
        bundle: Path,
        *,
        artefact_name: str,
        artefact_digest: str,
    ) -> Path:
        digest_line_value = f"{artefact_digest}  {artefact_name}"
        release_root = hashlib.sha256(
            f"{digest_line_value}\n".encode("utf-8")
        ).hexdigest()
        checksums = bundle / "CHECKSUMS.sha256"
        checksums.write_text(
            f"{digest_line_value}\n"
            f"# release-root-sha256: {release_root}\n",
            encoding="utf-8",
        )
        return checksums

    def test_live_bundle_manifest_accepts_the_governed_artefact_sizes(
        self,
    ) -> None:
        for name in ("okf-bundle.jsonld", "okf-bundle.yamlld"):
            with self.subTest(name=name):
                artefact_bytes = (ROOT / "bundle" / name).stat().st_size
                self.assertLessEqual(
                    artefact_bytes,
                    MAX_BUNDLE_ARTEFACT_BYTES,
                )

        with mock.patch(
            "scripts.check_release_evidence.read_repository_file_bytes",
            wraps=read_repository_file_bytes,
        ) as byte_reader:
            release_root, checksums_digest = validate_checksum_manifest(
                ROOT / "bundle" / "CHECKSUMS.sha256",
                "# release-root-sha256: ",
                max_artefact_bytes=MAX_BUNDLE_ARTEFACT_BYTES,
                max_entries=MAX_BUNDLE_CHECKSUM_ENTRIES,
                max_aggregate_bytes=MAX_BUNDLE_AGGREGATE_BYTES,
            )
        self.assertRegex(release_root, r"^[0-9a-f]{64}$")
        self.assertRegex(checksums_digest, r"^[0-9a-f]{64}$")
        self.assertFalse(
            any(
                call.kwargs.get("purpose") == "checksummed artefact"
                for call in byte_reader.call_args_list
            )
        )

    def test_candidate_identity_uses_the_bundle_cap_only_for_bundle_artefacts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle"
            bundle.mkdir()
            artefact = bundle / "okf-bundle.jsonld"
            artefact.write_bytes(b"fixture bundle\n")
            self._write_checksum_manifest(
                bundle,
                artefact_name=artefact.name,
                artefact_digest=sha256_file(artefact),
            )

            profile = root / "domain-profile"
            profile.mkdir()
            profile_artefact = profile / "profile.json"
            profile_artefact.write_bytes(b'{}\n')
            profile_line = (
                f"{sha256_file(profile_artefact)}  {profile_artefact.name}"
            )
            profile_root = hashlib.sha256(
                f"{profile_line}\n".encode("utf-8")
            ).hexdigest()
            (profile / "CHECKSUMS.sha256").write_text(
                f"{profile_line}\n# pack-root-sha256: {profile_root}\n",
                encoding="utf-8",
            )

            snapshot = root / "source" / "snapshots" / "fixture" / "manifest.json"
            snapshot.parent.mkdir(parents=True)
            snapshot.write_bytes(b'{}\n')
            build_receipt = bundle / "build-receipt.json"
            write_json(
                build_receipt,
                {
                    "domain_profile_pack_root_sha256": profile_root,
                    "snapshot": {
                        "manifest_path": snapshot.relative_to(root).as_posix(),
                        "source_manifest_sha256": sha256_file(snapshot),
                    },
                },
            )

            with mock.patch(
                "scripts.check_release_evidence.validate_checksum_manifest",
                wraps=validate_checksum_manifest,
            ) as validator:
                candidate_identity_from_repository(
                    root,
                    checksums_path=Path("bundle/CHECKSUMS.sha256"),
                    profile_checksums_path=Path(
                        "domain-profile/CHECKSUMS.sha256"
                    ),
                    build_receipt_path=Path("bundle/build-receipt.json"),
                    candidate_commit_sha="a" * 40,
                )

            self.assertEqual(2, len(validator.call_args_list))
            policy_by_parent = {
                Path(call.args[0]).parent.name: (
                    call.kwargs["max_artefact_bytes"],
                    call.kwargs["max_entries"],
                    call.kwargs["max_aggregate_bytes"],
                )
                for call in validator.call_args_list
            }
            self.assertEqual(
                {
                    "bundle": (
                        MAX_BUNDLE_ARTEFACT_BYTES,
                        MAX_BUNDLE_CHECKSUM_ENTRIES,
                        MAX_BUNDLE_AGGREGATE_BYTES,
                    ),
                    "domain-profile": (
                        MAX_EVIDENCE_BYTES,
                        MAX_PROFILE_CHECKSUM_ENTRIES,
                        MAX_PROFILE_AGGREGATE_BYTES,
                    ),
                },
                policy_by_parent,
            )

    def test_bundle_artefact_above_the_dedicated_cap_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            bundle.mkdir()
            artefact = bundle / "oversized.jsonld"
            with artefact.open("wb") as handle:
                handle.truncate(MAX_BUNDLE_ARTEFACT_BYTES + 1)
            checksums = self._write_checksum_manifest(
                bundle,
                artefact_name=artefact.name,
                artefact_digest="0" * 64,
            )

            with self.assertRaisesRegex(
                ReleaseEvidenceError,
                f"exceeds the {MAX_BUNDLE_ARTEFACT_BYTES}-byte read limit",
            ):
                validate_checksum_manifest(
                    checksums,
                    "# release-root-sha256: ",
                    max_artefact_bytes=MAX_BUNDLE_ARTEFACT_BYTES,
                    max_entries=MAX_BUNDLE_CHECKSUM_ENTRIES,
                    max_aggregate_bytes=MAX_BUNDLE_AGGREGATE_BYTES,
                )

    def test_checksum_manifest_entry_limit_fails_before_hashing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            bundle.mkdir()
            digest_lines: list[str] = []
            for index in range(3):
                artefact = bundle / f"artefact-{index}.txt"
                artefact.write_bytes(f"fixture {index}\n".encode("utf-8"))
                digest_lines.append(digest_line(artefact, artefact.name))
            release_root = hashlib.sha256(
                ("\n".join(digest_lines) + "\n").encode("utf-8")
            ).hexdigest()
            checksums = bundle / "CHECKSUMS.sha256"
            checksums.write_text(
                "\n".join(
                    [
                        *digest_lines,
                        f"# release-root-sha256: {release_root}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with mock.patch(
                "scripts.check_release_evidence.sha256_repository_file",
                wraps=sha256_repository_file,
            ) as digester:
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "exceeds the 2-entry limit",
                ):
                    validate_checksum_manifest(
                        checksums,
                        "# release-root-sha256: ",
                        max_artefact_bytes=100,
                        max_entries=2,
                        max_aggregate_bytes=1_000,
                    )
            digester.assert_not_called()

    def test_checksum_manifest_aggregate_limit_fails_before_excess_read(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            bundle.mkdir()
            first = bundle / "first.txt"
            second = bundle / "second.txt"
            first.write_bytes(b"123456")
            second.write_bytes(b"abcdef")
            digest_lines = [
                digest_line(first, first.name),
                digest_line(second, second.name),
            ]
            release_root = hashlib.sha256(
                ("\n".join(digest_lines) + "\n").encode("utf-8")
            ).hexdigest()
            checksums = bundle / "CHECKSUMS.sha256"
            checksums.write_text(
                "\n".join(
                    [
                        *digest_lines,
                        f"# release-root-sha256: {release_root}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with mock.patch(
                "scripts.check_release_evidence.hashlib.sha256",
                wraps=hashlib.sha256,
            ) as digest_factory:
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "remaining 4-byte aggregate allowance",
                ):
                    validate_checksum_manifest(
                        checksums,
                        "# release-root-sha256: ",
                        max_artefact_bytes=100,
                        max_entries=2,
                        max_aggregate_bytes=10,
                    )
            # One digest validates the checksum-line root and one hashes the
            # first artefact; the aggregate cap prevents opening the second.
            self.assertEqual(2, digest_factory.call_count)

    def test_checksum_manifest_accepts_exact_entry_and_aggregate_limits(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            bundle.mkdir()
            first = bundle / "first.txt"
            second = bundle / "second.txt"
            first.write_bytes(b"123456")
            second.write_bytes(b"abcd")
            digest_lines = [
                digest_line(first, first.name),
                digest_line(second, second.name),
            ]
            release_root = hashlib.sha256(
                ("\n".join(digest_lines) + "\n").encode("utf-8")
            ).hexdigest()
            checksums = bundle / "CHECKSUMS.sha256"
            checksums.write_text(
                "\n".join(
                    [
                        *digest_lines,
                        f"# release-root-sha256: {release_root}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                release_root,
                validate_checksum_manifest(
                    checksums,
                    "# release-root-sha256: ",
                    max_artefact_bytes=6,
                    max_entries=2,
                    max_aggregate_bytes=10,
                )[0],
            )

    def test_streamed_digest_detects_file_change_during_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artefact = root / "artefact.txt"
            artefact.write_bytes(b"stable fixture\n")
            before = artefact.stat()
            after = mock.Mock(wraps=before)
            after.st_mtime_ns = before.st_mtime_ns + 1

            with mock.patch(
                "scripts.check_release_evidence.os.fstat",
                side_effect=(before, after),
            ):
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "changed while it was being read",
                ):
                    sha256_repository_file(
                        root,
                        artefact.name,
                        purpose="checksummed artefact",
                        max_bytes=100,
                        aggregate_bytes_remaining=100,
                    )

    def test_streamed_digest_has_no_insecure_directory_walk_fallback(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artefact = root / "artefact.txt"
            artefact.write_bytes(b"fixture\n")
            with mock.patch.object(os, "supports_dir_fd", set()):
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "refusing the race-prone fallback",
                ):
                    sha256_repository_file(
                        root,
                        artefact.name,
                        purpose="checksummed artefact",
                        max_bytes=100,
                        aggregate_bytes_remaining=100,
                    )

    def test_streamed_digest_rejects_symbolic_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.txt"
            target.write_bytes(b"fixture\n")
            link = root / "link.txt"
            link.symlink_to(target.name)

            with self.assertRaisesRegex(
                ReleaseEvidenceError,
                "path contains a symbolic link",
            ):
                sha256_repository_file(
                    root,
                    link.name,
                    purpose="checksummed artefact",
                    max_bytes=100,
                    aggregate_bytes_remaining=100,
                )

    def test_streamed_digest_rejects_a_fifo_swapped_before_open(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artefact = root / "artefact.txt"
            artefact.write_bytes(b"fixture\n")

            def replace_with_fifo(
                repository_root: Path,
                relative_name: str,
                *,
                purpose: str,
            ) -> Path:
                candidate = safe_repository_file(
                    repository_root,
                    relative_name,
                    purpose=purpose,
                )
                candidate.unlink()
                os.mkfifo(candidate)
                return candidate

            with mock.patch(
                "scripts.check_release_evidence.safe_repository_file",
                side_effect=replace_with_fifo,
            ):
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "is not a regular file",
                ):
                    sha256_repository_file(
                        root,
                        artefact.name,
                        purpose="checksummed artefact",
                        max_bytes=100,
                        aggregate_bytes_remaining=100,
                    )

    def test_streamed_digest_rejects_path_replacement_after_hashing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artefact = root / "artefact.txt"
            moved = root / "moved.txt"
            artefact.write_bytes(b"fixture\n")
            real_digest = release_evidence_checker._sha256_open_descriptor

            def replace_after_hash(*args: object, **kwargs: object) -> object:
                result = real_digest(*args, **kwargs)  # type: ignore[arg-type]
                artefact.rename(moved)
                artefact.write_bytes(b"replacement\n")
                return result

            with mock.patch(
                "scripts.check_release_evidence._sha256_open_descriptor",
                side_effect=replace_after_hash,
            ):
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "path changed while it was being hashed",
                ):
                    sha256_repository_file(
                        root,
                        artefact.name,
                        purpose="checksummed artefact",
                        max_bytes=100,
                        aggregate_bytes_remaining=100,
                    )


class BoundedGitProcessTests(unittest.TestCase):
    def run_fake_git(
        self,
        body: str,
        *,
        maximum_stdout_bytes: int = 64,
        maximum_stderr_bytes: int = 64,
        timeout: float = 1.0,
    ) -> subprocess.CompletedProcess[bytes]:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            write_fake_git(root, body)
            environment_path = os.pathsep.join(
                [str(root), os.environ.get("PATH", "")]
            )
            with (
                mock.patch.dict(os.environ, {"PATH": environment_path}),
                mock.patch.object(
                    release_evidence_checker,
                    "MAX_GIT_DIAGNOSTIC_BYTES",
                    maximum_stderr_bytes,
                ),
                mock.patch.object(
                    release_evidence_checker,
                    "GIT_COMMAND_TIMEOUT_SECONDS",
                    timeout,
                ),
            ):
                return _git_command_bytes(
                    root,
                    ["fixture"],
                    maximum_stdout_bytes=maximum_stdout_bytes,
                )

    def test_bounded_git_stdout_flood_is_stopped_in_flight(self) -> None:
        with self.assertRaisesRegex(ReleaseEvidenceError, "stdout exceeds"):
            self.run_fake_git(
                "import os; os.write(1, b'x' * 4096)",
                maximum_stdout_bytes=32,
            )

    def test_bounded_git_stderr_flood_is_stopped_in_flight(self) -> None:
        with self.assertRaisesRegex(ReleaseEvidenceError, "stderr exceeds"):
            self.run_fake_git(
                "import os; os.write(2, b'x' * 4096)",
                maximum_stderr_bytes=32,
            )

    def test_bounded_git_hang_is_killed_at_the_deadline(self) -> None:
        started = time.monotonic()
        with self.assertRaisesRegex(ReleaseEvidenceError, "time ceiling"):
            self.run_fake_git("import time; time.sleep(10)", timeout=0.1)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_current_commit_inherits_the_bounded_stdout_contract(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            write_fake_git(root, "import os; os.write(1, b'a' * 4096)")
            environment_path = os.pathsep.join(
                [str(root), os.environ.get("PATH", "")]
            )
            with mock.patch.dict(os.environ, {"PATH": environment_path}):
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "stdout exceeds",
                ):
                    current_commit(root)


class CandidateTreeBlobReaderResourceTests(unittest.TestCase):
    CANDIDATE = "a" * 40
    OBJECT_ID = "b" * 40
    ENTRIES = {"fixture.txt": GitTreeEntry(mode="100644", object_id=OBJECT_ID)}

    def reader_environment(self, root: Path) -> mock._patch_dict:
        environment_path = os.pathsep.join(
            [str(root), os.environ.get("PATH", "")]
        )
        return mock.patch.dict(os.environ, {"PATH": environment_path})

    def test_blob_reader_rejects_stdout_header_flood(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            write_fake_git(
                root,
                "import os,time; os.write(1, b'x' * 4096); time.sleep(10)",
            )
            with self.reader_environment(root):
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "header exceeds",
                ):
                    with CandidateTreeBlobReader(
                        root,
                        candidate_commit_sha=self.CANDIDATE,
                        entries=self.ENTRIES,
                    ) as reader:
                        reader.read("fixture.txt", "fixture", 10)

    def test_blob_reader_rejects_stderr_flood_in_flight(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            write_fake_git(
                root,
                "import os,time; os.write(2, b'x' * 4096); time.sleep(10)",
            )
            with (
                self.reader_environment(root),
                mock.patch.object(
                    release_evidence_checker,
                    "MAX_GIT_DIAGNOSTIC_BYTES",
                    32,
                ),
            ):
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "stderr exceeds",
                ):
                    with CandidateTreeBlobReader(
                        root,
                        candidate_commit_sha=self.CANDIDATE,
                        entries=self.ENTRIES,
                    ) as reader:
                        reader.read("fixture.txt", "fixture", 10)

    def test_blob_reader_rejects_a_hung_process(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            write_fake_git(root, "import time; time.sleep(10)")
            started = time.monotonic()
            with (
                self.reader_environment(root),
                mock.patch.object(
                    release_evidence_checker,
                    "GIT_COMMAND_TIMEOUT_SECONDS",
                    0.1,
                ),
            ):
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "total time ceiling",
                ):
                    with CandidateTreeBlobReader(
                        root,
                        candidate_commit_sha=self.CANDIDATE,
                        entries=self.ENTRIES,
                    ) as reader:
                        reader.read("fixture.txt", "fixture", 10)
            self.assertLess(time.monotonic() - started, 2.0)

    def test_blob_reader_uses_one_deadline_across_all_reads(self) -> None:
        body = (
            "import sys,time\n"
            "for raw in sys.stdin.buffer:\n"
            "    object_id = raw.strip()\n"
            "    time.sleep(0.30)\n"
            "    sys.stdout.buffer.write(object_id + b' blob 1\\nx\\n')\n"
            "    sys.stdout.buffer.flush()"
        )
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            write_fake_git(root, body)
            with (
                self.reader_environment(root),
                mock.patch.object(
                    release_evidence_checker,
                    "GIT_COMMAND_TIMEOUT_SECONDS",
                    0.5,
                ),
            ):
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "total time ceiling",
                ):
                    with CandidateTreeBlobReader(
                        root,
                        candidate_commit_sha=self.CANDIDATE,
                        entries=self.ENTRIES,
                    ) as reader:
                        self.assertEqual(
                            b"x",
                            reader.read("fixture.txt", "fixture", 10),
                        )
                        reader.read("fixture.txt", "fixture", 10)


class HistoricalReleaseEvidenceTests(unittest.TestCase):
    def test_v02_release_evidence_remains_byte_frozen(self) -> None:
        manifest_path = ROOT / "validation" / "release-evidence.json"
        release_record_path = ROOT / "validation" / "release-record.json"
        self.assertEqual(
            "facafcc21bf0b69a8b97a47df0cfd334b0a45f30680c6dc69c9c623f5423be9f",
            sha256_file(manifest_path),
        )
        self.assertEqual(
            "46aadf4563f878285de3155124870be7144ec91f9f00e825c0e297b5618e2c11",
            sha256_file(release_record_path),
        )

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [*GATE_RECEIPTS, "G9"],
            [reference["gate"] for reference in manifest["receipts"]],
        )
        for reference in manifest["receipts"]:
            with self.subTest(gate=reference["gate"]):
                self.assertEqual(
                    reference["sha256"],
                    sha256_file(ROOT / reference["path"]),
                )

    def test_checker_cli_requires_an_explicit_manifest(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / "scripts" / "check_release_evidence.py"),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("--manifest", result.stderr)


class CandidateModeFixture:
    """Small real Git repository for the staged and candidate-only CLIs."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.git("init", "-q")
        self.git("config", "user.name", "Candidate Mode Fixture")
        self.git("config", "user.email", "candidate-fixture@example.test")
        (self.root / ".gitignore").write_text(
            "ignored-fixture/\n__pycache__/\n*.py[cod]\n"
            "evaluation/latest-report.json\n",
            encoding="utf-8",
        )
        (self.root / "README.md").write_text(
            "# Candidate mode fixture\n",
            encoding="utf-8",
        )
        inherited_review = self.root / "review" / "v0.2" / "frozen.json"
        write_json(
            inherited_review,
            {"schema": "okf-inherited-review-evidence.v1"},
        )
        inherited_validation = self.root / "validation" / "release-record.json"
        write_json(
            inherited_validation,
            {"schema": "okf-inherited-release-record.v1"},
        )
        inherited_archive = self.root / "dist" / "okf-landregistry-0.2.0.zip"
        inherited_archive.parent.mkdir(parents=True, exist_ok=True)
        inherited_archive.write_bytes(b"frozen v0.2 archive\n")
        self.git(
            "add",
            ".gitignore",
            "README.md",
            "review",
            "validation",
            "dist",
        )
        self.git("commit", "-m", "Add fixture base")

        checker = self.root / "scripts" / "check_release_evidence.py"
        checker.parent.mkdir(parents=True)
        for script_name in (
            "build.py",
            "change_impact.py",
            "check_release_evidence.py",
            "python_runtime_contract.py",
        ):
            (checker.parent / script_name).write_bytes(
                (ROOT / "scripts" / script_name).read_bytes()
            )

        graph_schema_path = (
            self.root / "schemas" / "artifact-dependency-graph.schema.json"
        )
        graph_schema_path.parent.mkdir(parents=True)
        graph_schema_path.write_bytes(
            (
                ROOT / "schemas" / "artifact-dependency-graph.schema.json"
            ).read_bytes()
        )

        requirements_path = self.root / "governance" / "requirements.json"
        write_json(
            requirements_path,
            {
                "schema": "okf-candidate-mode-requirements.v1",
                "requirements": [{"id": "REQ-001"}],
            },
        )
        risk_register_path = self.root / "governance" / "risk-register.json"
        write_json(
            risk_register_path,
            {
                "schema": "okf-candidate-mode-risks.v1",
                "risks": [{"id": "RISK-001"}],
            },
        )
        graph_path = self.root / "governance" / "artifact-dependency-graph.json"
        write_json(
            graph_path,
            {
                "schema": "okf-artifact-dependency-graph.v1",
                "version": "0.3.0",
                "unknown_change_policy": "all-gates-and-manual-review",
                "all_release_gates": ["G1"],
                "build_inputs": [
                    "domain-profile/**",
                    "governance/artifact-dependency-graph.json",
                    "governance/requirements.json",
                    "governance/risk-register.json",
                    "schemas/artifact-dependency-graph.schema.json",
                    "source/**",
                ],
                "generated_roots": [
                    "bundle/**",
                    "dist/**",
                    "validation/**",
                ],
                "validation_gate_map": {
                    "VAL-FIXTURE": ["G1"],
                    "VAL-REPRODUCIBILITY": ["G1"],
                },
                "tests": [
                    {
                        "id": "fixture",
                        "command": ["python", "-m", "unittest"],
                        "repository_paths": [
                            "scripts/check_release_evidence.py"
                        ],
                    },
                    {
                        "id": "build-semantics",
                        "command": ["python", "-m", "unittest"],
                        "repository_paths": ["scripts/build.py"],
                    },
                    {
                        "id": "bundle",
                        "command": ["python", "-m", "unittest"],
                        "repository_paths": ["scripts/build.py"],
                    },
                ],
                "stages": [
                    {
                        "id": "fixture",
                        "inputs": [
                            ".gitignore",
                            "README.md",
                            "domain-profile/**",
                            "governance/artifact-dependency-graph.json",
                            "governance/requirements.json",
                            "governance/risk-register.json",
                            "schemas/artifact-dependency-graph.schema.json",
                            "source/**",
                        ],
                        "validation_inputs": ["scripts/**"],
                        "outputs": ["bundle/**"],
                        "test_ids": ["fixture", "build-semantics", "bundle"],
                        "requirement_ids": ["REQ-001"],
                        "risk_ids": ["RISK-001"],
                        "validation_refs": [
                            "VAL-FIXTURE",
                            "VAL-REPRODUCIBILITY",
                        ],
                        "release_gates": ["G1"],
                        "stage1_review": False,
                    }
                ],
            },
        )

        profile_path = self.root / "domain-profile" / "profile.json"
        write_json(
            profile_path,
            {"schema": "okf-candidate-mode-profile.v1"},
        )
        profile_line = digest_line(profile_path, profile_path.name)
        self.profile_root = hashlib.sha256(
            f"{profile_line}\n".encode("utf-8")
        ).hexdigest()
        (profile_path.parent / "CHECKSUMS.sha256").write_text(
            f"{profile_line}\n# pack-root-sha256: {self.profile_root}\n",
            encoding="utf-8",
        )
        self.snapshot_path = (
            self.root / "source" / "snapshots" / "fixture" / "manifest.json"
        )
        write_json(
            self.snapshot_path,
            {"schema": "okf-candidate-mode-snapshot.v1"},
        )
        self.build_config_path = self.root / "source" / "build-config.json"
        write_json(
            self.build_config_path,
            {"schema": "okf-candidate-mode-build-config.v1"},
        )
        self.build_receipt_path = self.root / "bundle" / "build-receipt.json"
        self.write_current_receipt()
        self.bundle_artefact_path = self.root / "bundle" / "artefact.txt"
        self.bundle_artefact_path.write_text(
            "candidate bundle artefact\n",
            encoding="utf-8",
        )
        self.write_bundle_checksums()
        self.git("add", "--all")

        self.diagnostic_path = (
            self.root
            / "validation"
            / "candidate-v0.3.0"
            / "evidence"
            / "evaluation-diagnostic.json"
        )
        write_json(
            self.diagnostic_path,
            {"schema": "okf-candidate-mode-diagnostic.v1"},
        )

    def git(
        self, *arguments: str, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
            input=input_text,
        )

    def governed_paths(self) -> list[Path]:
        return sorted(
            [
                *(
                    path
                    for root_name in (
                        "domain-profile",
                        "governance",
                        "schemas",
                        "source",
                    )
                    for path in (self.root / root_name).rglob("*")
                    if path.is_file() or path.is_symlink()
                ),
            ],
            key=lambda path: path.relative_to(self.root).as_posix(),
        )

    def write_current_receipt(self) -> None:
        governed_inputs: list[dict[str, object]] = []
        for path in self.governed_paths():
            if path.is_symlink():
                payload = os.readlink(path).encode("utf-8")
            else:
                payload = path.read_bytes()
            governed_inputs.append(
                {
                    "path": path.relative_to(self.root).as_posix(),
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
        self.governed_count = len(governed_inputs)
        write_json(
            self.build_receipt_path,
            {
                "schema": "okf-candidate-mode-build-receipt.v1",
                "domain_profile_pack_root_sha256": self.profile_root,
                "snapshot": {
                    "manifest_path": self.snapshot_path.relative_to(
                        self.root
                    ).as_posix(),
                    "source_manifest_sha256": sha256_file(self.snapshot_path),
                },
                "governed_inputs": governed_inputs,
            },
        )

    def write_bundle_checksums(self) -> None:
        lines = [
            digest_line(self.bundle_artefact_path, self.bundle_artefact_path.name),
            digest_line(self.build_receipt_path, self.build_receipt_path.name),
        ]
        release_root = hashlib.sha256(
            ("\n".join(lines) + "\n").encode("utf-8")
        ).hexdigest()
        (self.root / "bundle" / "CHECKSUMS.sha256").write_text(
            "\n".join(
                [*lines, f"# release-root-sha256: {release_root}", ""]
            ),
            encoding="utf-8",
        )
        self.release_root = release_root

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(self.root / "scripts" / "check_release_evidence.py"),
                *arguments,
            ],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )

    def commit_candidate(self) -> str:
        self.git("commit", "-m", "Freeze fixture candidate")
        return self.git("rev-parse", "HEAD").stdout.strip()


class CandidateModeCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = CandidateModeFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_staged_candidate_and_committed_candidate_only_cli_pass(self) -> None:
        staged = self.fixture.run_cli("--staged-candidate")
        self.assertEqual(0, staged.returncode, staged.stderr)
        self.assertIn("staged candidate validated before freeze", staged.stdout)
        self.assertIn(
            f"{self.fixture.governed_count} governed regular blobs",
            staged.stdout,
        )

        candidate = self.fixture.commit_candidate()
        committed = self.fixture.run_cli(
            "--candidate-only",
            "--candidate-commit-sha",
            candidate,
        )
        self.assertEqual(0, committed.returncode, committed.stderr)
        self.assertIn("committed candidate topology validated", committed.stdout)
        self.assertIn(self.fixture.release_root, committed.stdout)

    def test_candidate_modes_are_mutually_exclusive_and_sha_is_required(
        self,
    ) -> None:
        mutually_exclusive = self.fixture.run_cli(
            "--staged-candidate",
            "--candidate-only",
        )
        self.assertEqual(2, mutually_exclusive.returncode)
        self.assertIn("not allowed with argument", mutually_exclusive.stderr)

        missing_sha = self.fixture.run_cli("--candidate-only")
        self.assertEqual(2, missing_sha.returncode)
        self.assertIn(
            "--candidate-commit-sha is required with --candidate-only",
            missing_sha.stderr,
        )

    def test_staged_candidate_rejects_incomplete_governed_inventory(self) -> None:
        receipt = json.loads(self.fixture.build_receipt_path.read_text())
        receipt["governed_inputs"] = receipt["governed_inputs"][:-1]
        write_json(self.fixture.build_receipt_path, receipt)
        self.fixture.write_bundle_checksums()
        self.fixture.git(
            "add", "bundle/build-receipt.json", "bundle/CHECKSUMS.sha256"
        )

        result = self.fixture.run_cli("--staged-candidate")
        self.assertEqual(1, result.returncode)
        self.assertIn("inventory differs from the candidate dependency graph", result.stderr)

    def test_staged_candidate_rejects_unchecksummed_domain_profile_file(
        self,
    ) -> None:
        extra = self.fixture.root / "domain-profile" / "omitted-profile.json"
        write_json(extra, {"schema": "unchecksummed-profile-fixture.v1"})
        self.fixture.write_current_receipt()
        self.fixture.write_bundle_checksums()
        self.fixture.git(
            "add",
            "domain-profile/omitted-profile.json",
            "bundle/build-receipt.json",
            "bundle/CHECKSUMS.sha256",
        )

        result = self.fixture.run_cli("--staged-candidate")

        self.assertEqual(1, result.returncode)
        self.assertIn("domain-profile Git inventory differs", result.stderr)

    def test_staged_candidate_rejects_non_regular_governed_blob(self) -> None:
        link_path = self.fixture.root / "source" / "linked-config.json"
        try:
            link_path.symlink_to("build-config.json")
        except OSError as exc:  # pragma: no cover - platform capability
            self.skipTest(f"symbolic links are unavailable: {exc}")
        self.fixture.write_current_receipt()
        self.fixture.git("add", "source/linked-config.json", "bundle/build-receipt.json")

        result = self.fixture.run_cli("--staged-candidate")
        self.assertEqual(1, result.returncode)
        self.assertIn("symbolic link", result.stderr)

    def test_staged_candidate_rejects_governed_digest_mismatch(self) -> None:
        receipt = json.loads(self.fixture.build_receipt_path.read_text())
        receipt["governed_inputs"][0]["sha256"] = "0" * 64
        write_json(self.fixture.build_receipt_path, receipt)
        self.fixture.write_bundle_checksums()
        self.fixture.git(
            "add", "bundle/build-receipt.json", "bundle/CHECKSUMS.sha256"
        )

        result = self.fixture.run_cli("--staged-candidate")
        self.assertEqual(1, result.returncode)
        self.assertIn("governed build input digest mismatch", result.stderr)

    def test_staged_candidate_rejects_unstaged_protected_change(self) -> None:
        self.fixture.build_config_path.write_text(
            self.fixture.build_config_path.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )

        result = self.fixture.run_cli("--staged-candidate")
        self.assertEqual(1, result.returncode)
        self.assertIn("has unstaged or non-ignored untracked changes", result.stderr)
        self.assertIn("source/build-config.json", result.stderr)

    def test_staged_candidate_rejects_staged_evidence_but_allows_untracked(
        self,
    ) -> None:
        self.assertEqual(
            self.fixture.governed_count,
            validate_staged_candidate(
                self.fixture.root,
                build_receipt_path=Path("bundle/build-receipt.json"),
            ),
        )
        self.fixture.git(
            "add",
            "validation/candidate-v0.3.0/evidence/evaluation-diagnostic.json",
        )

        result = self.fixture.run_cli("--staged-candidate")
        self.assertEqual(1, result.returncode)
        self.assertIn("contains evidence changes under validation/**", result.stderr)

    def test_candidate_modes_reject_historical_evidence_mutation(self) -> None:
        historical = self.fixture.root / "validation" / "release-record.json"
        write_json(
            historical,
            {"schema": "okf-mutated-historical-release-record.v1"},
        )
        self.fixture.git("add", "validation/release-record.json")

        staged = self.fixture.run_cli("--staged-candidate")
        self.assertEqual(1, staged.returncode)
        self.assertIn("contains evidence changes under validation/**", staged.stderr)

        candidate = self.fixture.commit_candidate()
        committed = self.fixture.run_cli(
            "--candidate-only",
            "--candidate-commit-sha",
            candidate,
        )
        self.assertEqual(1, committed.returncode)
        self.assertIn("must remain outside C", committed.stderr)
        self.assertIn("validation/release-record.json", committed.stderr)

    def test_staged_candidate_enforces_git_diff_check(self) -> None:
        whitespace_path = self.fixture.root / "docs" / "whitespace.md"
        whitespace_path.parent.mkdir()
        whitespace_path.write_text("trailing whitespace   \n", encoding="utf-8")
        self.fixture.git("add", "docs/whitespace.md")

        result = self.fixture.run_cli("--staged-candidate")
        self.assertEqual(1, result.returncode)
        self.assertIn("fails git diff --cached --check", result.stderr)

    def test_staged_candidate_uses_full_relational_graph_validation(self) -> None:
        graph_path = (
            self.fixture.root
            / "governance"
            / "artifact-dependency-graph.json"
        )
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        graph["stages"][0]["outputs"].append("source/build-config.json")
        write_json(graph_path, graph)
        self.fixture.git("add", "governance/artifact-dependency-graph.json")

        result = self.fixture.run_cli("--staged-candidate")
        self.assertEqual(1, result.returncode)
        self.assertIn("output is outside generated_roots", result.stderr)

    def test_staged_candidate_uses_canonical_pattern_grammar(self) -> None:
        graph_path = (
            self.fixture.root
            / "governance"
            / "artifact-dependency-graph.json"
        )
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        graph["stages"][0]["inputs"][-1] = "source/*.json"
        write_json(graph_path, graph)
        self.fixture.git("add", "governance/artifact-dependency-graph.json")

        result = self.fixture.run_cli("--staged-candidate")
        self.assertEqual(1, result.returncode)
        self.assertIn("schema-invalid", result.stderr)

    def test_staged_candidate_rejects_unresolved_index_conflict(self) -> None:
        object_ids = [
            self.fixture.git(
                "hash-object",
                "-w",
                "--stdin",
                input_text=f"conflict stage {stage}\n",
            ).stdout.strip()
            for stage in (1, 2, 3)
        ]
        path = "source/build-config.json"
        index_info = [f"0 {'0' * 40} 0\t{path}"]
        index_info.extend(
            f"100644 {object_id} {stage}\t{path}"
            for stage, object_id in enumerate(object_ids, start=1)
        )
        self.fixture.git(
            "update-index",
            "--index-info",
            input_text="\n".join(index_info) + "\n",
        )

        result = self.fixture.run_cli("--staged-candidate")
        self.assertEqual(1, result.returncode)
        self.assertIn("contains unresolved conflicts", result.stderr)

    def test_candidate_only_allows_linear_evidence_and_rejects_protected_history(
        self,
    ) -> None:
        candidate = self.fixture.commit_candidate()
        self.fixture.git("add", "validation")
        self.fixture.git("commit", "-m", "Add candidate diagnostic")
        allowed = self.fixture.run_cli(
            "--candidate-only",
            "--candidate-commit-sha",
            candidate,
        )
        self.assertEqual(0, allowed.returncode, allowed.stderr)

        (self.fixture.root / "README.md").write_text(
            "# Protected post-candidate change\n",
            encoding="utf-8",
        )
        self.fixture.git("add", "README.md")
        self.fixture.git("commit", "-m", "Attempt protected change")
        rejected = self.fixture.run_cli(
            "--candidate-only",
            "--candidate-commit-sha",
            candidate,
        )
        self.assertEqual(1, rejected.returncode)
        self.assertIn(
            "changed in commit history outside validation/candidate-v0.3.0/**",
            rejected.stderr,
        )

    def test_candidate_only_rehashes_checksums_before_topology(self) -> None:
        candidate = self.fixture.commit_candidate()
        self.fixture.bundle_artefact_path.write_text(
            "tampered candidate bundle artefact\n",
            encoding="utf-8",
        )

        result = self.fixture.run_cli(
            "--candidate-only",
            "--candidate-commit-sha",
            candidate,
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("governed candidate tree has", result.stderr)

    def test_staged_and_committed_modes_reject_forced_staged_ignored_file(
        self,
    ) -> None:
        ignored_path = self.fixture.root / "evaluation" / "latest-report.json"
        write_json(ignored_path, {"schema": "okf-local-diagnostic.v1"})
        self.fixture.git("add", "-f", "evaluation/latest-report.json")

        staged = self.fixture.run_cli("--staged-candidate")
        self.assertEqual(1, staged.returncode)
        self.assertIn("excluded by its ignore policy", staged.stderr)
        self.assertIn("evaluation/latest-report.json", staged.stderr)

        candidate = self.fixture.commit_candidate()
        committed = self.fixture.run_cli(
            "--candidate-only",
            "--candidate-commit-sha",
            candidate,
        )
        self.assertEqual(1, committed.returncode)
        self.assertIn("excluded by its ignore policy", committed.stderr)

    def test_staged_and_committed_modes_reject_unmanifested_bundle_file(
        self,
    ) -> None:
        extra = self.fixture.root / "bundle" / "unmanifested.txt"
        extra.write_text("not in the checksum manifest\n", encoding="utf-8")
        self.fixture.git("add", "bundle/unmanifested.txt")

        staged = self.fixture.run_cli("--staged-candidate")
        self.assertEqual(1, staged.returncode)
        self.assertIn("bundle Git inventory differs", staged.stderr)
        self.assertIn("bundle/unmanifested.txt", staged.stderr)

        candidate = self.fixture.commit_candidate()
        committed = self.fixture.run_cli(
            "--candidate-only",
            "--candidate-commit-sha",
            candidate,
        )
        self.assertEqual(1, committed.returncode)
        self.assertIn("bundle Git inventory differs", committed.stderr)

    def test_candidate_modes_allow_unchanged_inherited_review_but_reject_new(
        self,
    ) -> None:
        unchanged = self.fixture.run_cli("--staged-candidate")
        self.assertEqual(0, unchanged.returncode, unchanged.stderr)

        new_review = self.fixture.root / "review" / "v0.3" / "decoy.json"
        write_json(new_review, {"schema": "okf-review-decoy.v1"})
        self.fixture.git("add", "review/v0.3/decoy.json")
        rejected = self.fixture.run_cli("--staged-candidate")
        self.assertEqual(1, rejected.returncode)
        self.assertIn("outside the canonical governed inputs", rejected.stderr)
        self.assertIn("review/v0.3/decoy.json", rejected.stderr)

    def test_candidate_only_rechecks_canonical_graph_and_receipt_closure(
        self,
    ) -> None:
        receipt = json.loads(self.fixture.build_receipt_path.read_text())
        receipt["governed_inputs"] = receipt["governed_inputs"][:-1]
        write_json(self.fixture.build_receipt_path, receipt)
        self.fixture.write_bundle_checksums()
        self.fixture.git("add", "bundle")
        candidate = self.fixture.commit_candidate()

        result = self.fixture.run_cli(
            "--candidate-only",
            "--candidate-commit-sha",
            candidate,
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("inventory differs from the candidate dependency graph", result.stderr)

    def test_staged_candidate_bounds_governed_input_count_and_aggregate(
        self,
    ) -> None:
        original = json.loads(self.fixture.build_receipt_path.read_text())
        over_count = json.loads(json.dumps(original))
        over_count["governed_inputs"] = [
            over_count["governed_inputs"][0]
            for _index in range(release_evidence_checker.MAX_GOVERNED_INPUTS + 1)
        ]
        write_json(self.fixture.build_receipt_path, over_count)
        self.fixture.write_bundle_checksums()
        self.fixture.git("add", "bundle")
        count_result = self.fixture.run_cli("--staged-candidate")
        self.assertEqual(1, count_result.returncode)
        self.assertIn("governed-input limit", count_result.stderr)

        over_aggregate = json.loads(json.dumps(original))
        over_aggregate["governed_inputs"][0]["bytes"] = (
            release_evidence_checker.MAX_GOVERNED_INPUT_AGGREGATE_BYTES + 1
        )
        write_json(self.fixture.build_receipt_path, over_aggregate)
        self.fixture.write_bundle_checksums()
        self.fixture.git("add", "bundle")
        aggregate_result = self.fixture.run_cli("--staged-candidate")
        self.assertEqual(1, aggregate_result.returncode)
        self.assertIn("aggregate limit", aggregate_result.stderr)

    def test_candidate_only_rechecks_relational_graph_validation(self) -> None:
        graph_path = (
            self.fixture.root
            / "governance"
            / "artifact-dependency-graph.json"
        )
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        graph["stages"][0]["outputs"].append("source/build-config.json")
        write_json(graph_path, graph)
        self.fixture.git("add", "governance/artifact-dependency-graph.json")
        candidate = self.fixture.commit_candidate()

        result = self.fixture.run_cli(
            "--candidate-only",
            "--candidate-commit-sha",
            candidate,
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("output is outside generated_roots", result.stderr)

    def test_checksum_manifest_rejects_non_canonical_alias_in_actual_cli(
        self,
    ) -> None:
        artefact_digest = sha256_file(self.fixture.bundle_artefact_path)
        receipt_digest = sha256_file(self.fixture.build_receipt_path)
        lines = [
            f"{artefact_digest}  ./artefact.txt",
            f"{receipt_digest}  build-receipt.json",
        ]
        release_root = hashlib.sha256(
            ("\n".join(lines) + "\n").encode("utf-8")
        ).hexdigest()
        (self.fixture.root / "bundle" / "CHECKSUMS.sha256").write_text(
            "\n".join([*lines, f"# release-root-sha256: {release_root}", ""]),
            encoding="utf-8",
        )
        self.fixture.git("add", "bundle/CHECKSUMS.sha256")

        result = self.fixture.run_cli("--staged-candidate")
        self.assertEqual(1, result.returncode)
        self.assertIn("unsafe", result.stderr)
        self.assertIn("./artefact.txt", result.stderr)

    def test_candidate_only_rejects_non_linear_evidence_merge(self) -> None:
        candidate = self.fixture.commit_candidate()
        self.fixture.git("checkout", "-q", "-b", "evidence-a")
        first = (
            self.fixture.root / "validation" / "candidate-v0.3.0" / "a.json"
        )
        write_json(first, {"schema": "okf-evidence-a.v1"})
        self.fixture.git("add", "validation/candidate-v0.3.0/a.json")
        self.fixture.git("commit", "-m", "Add evidence A")

        self.fixture.git("checkout", "-q", "-b", "evidence-b", candidate)
        second = (
            self.fixture.root / "validation" / "candidate-v0.3.0" / "b.json"
        )
        write_json(second, {"schema": "okf-evidence-b.v1"})
        self.fixture.git("add", "validation/candidate-v0.3.0/b.json")
        self.fixture.git("commit", "-m", "Add evidence B")
        self.fixture.git("merge", "--no-ff", "-m", "Merge evidence", "evidence-a")

        result = self.fixture.run_cli(
            "--candidate-only",
            "--candidate-commit-sha",
            candidate,
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("single-parent linear chain", result.stderr)


class EvidenceFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.receipt_paths: dict[str, Path] = {}
        self._write_candidate_materials()
        self._initialise_repository()
        self.candidate = CandidateIdentity(
            candidate_commit_sha=self.candidate_commit_sha,
            release_root_sha256=self.release_root,
            checksums_sha256=sha256_file(
                self.root / "bundle" / "CHECKSUMS.sha256"
            ),
            profile_pack_root_sha256=self.profile_root,
            snapshot_manifest_sha256=self.snapshot_digest,
        )
        self._write_evidence()
        self.bind_v0_3_owner_approval()
        self.git("add", "validation", "dist")
        self.git("commit", "-m", "Add fixture release evidence")
        self.evidence_commit_sha = self.git("rev-parse", "HEAD").stdout.strip()

    @property
    def validation_root(self) -> Path:
        return self.root / "validation" / "candidate-v0.3.0"

    @property
    def manifest_path(self) -> Path:
        return self.validation_root / "final-g9" / "release-evidence.json"

    def git(
        self, *arguments: str, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
            input=input_text,
        )

    def _initialise_repository(self) -> None:
        self.git("init")
        self.git("config", "user.name", "Release Evidence Fixture")
        self.git("config", "user.email", "fixture@example.test")
        self.git("add", "--all")
        self.git("commit", "-m", "Add governed candidate")
        self.candidate_commit_sha = self.git("rev-parse", "HEAD").stdout.strip()

    def _write_candidate_materials(self) -> None:
        schema_path = self.root / "schemas" / "release-evidence.schema.json"
        schema_path.parent.mkdir(parents=True)
        schema_path.write_bytes(SCHEMA.read_bytes())

        profile_file = self.root / "domain-profile" / "profile.json"
        profile_file.parent.mkdir(parents=True)
        profile_file.write_text('{"schema":"fixture"}\n', encoding="utf-8")
        profile_line = digest_line(profile_file, "profile.json")
        profile_root = hashlib.sha256(
            f"{profile_line}\n".encode("utf-8")
        ).hexdigest()
        self.profile_root = profile_root
        profile_checksums = profile_file.parent / "CHECKSUMS.sha256"
        profile_checksums.write_text(
            f"{profile_line}\n# pack-root-sha256: {profile_root}\n",
            encoding="utf-8",
        )

        snapshot_manifest = (
            self.root / "source" / "snapshots" / "fixture" / "manifest.json"
        )
        snapshot_manifest.parent.mkdir(parents=True)
        snapshot_manifest.write_text(
            '{"schema":"okf-test-snapshot.v1"}\n', encoding="utf-8"
        )
        snapshot_digest = sha256_file(snapshot_manifest)
        self.snapshot_digest = snapshot_digest

        risk_register = self.root / "governance" / "risk-register.json"
        write_json(
            risk_register,
            {
                "schema": "okf-risk-register.v1",
                "risks": [
                    {
                        "id": "RISK-FIXTURE",
                        "residual": {"likelihood": 1, "impact": 1},
                        "release_disposition": "accept for fixture",
                    },
                    {
                        "id": "RISK-HUMAN-AUDIT",
                        "residual": {"likelihood": 2, "impact": 2},
                        "release_disposition": "retain and disclose",
                    },
                ],
            },
        )

        build_config = self.root / "source" / "build-config.json"
        write_json(
            build_config,
            {
                "schema": "okf-hmlr-build-config.v1",
                "version": "0.3.0",
                "status": "ai-generated-proof-of-concept",
                "ai_generated_proof_of_concept": True,
                "publication_base": "https://example.test/okf/",
                "publication_state": "digest-bound-external-evidence",
                "generated_at": NOW,
                "release_at": None,
            },
        )

        freeze_surface = {
            ".github/workflows/pages.yml": (
                "name: Fixture Pages\n"
                "jobs:\n"
                "  fixture:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                "      - uses: actions/checkout@"
                + "a" * 40
                + " # fixture pin\n"
            ),
            "docs/release-assurance.md": "# Fixture release assurance\n",
            "evaluation/acceptance-review-v0.2.0.json": (
                '{"schema":"okf-fixture-acceptance-review.v1"}\n'
            ),
            "scripts/assemble_release_evidence.py": "# fixture assembler\n",
            "scripts/build.py": "# exact fixture builder\n",
            "scripts/check_release_evidence.py": "# fixture checker\n",
            "scripts/run_authored_site_browser_quality.mjs": (
                "// fixture G6 observer\n"
            ),
            "tests/test_release_evidence.py": "# fixture checker tests\n",
            "requirements-lock.txt": (
                "fixture-package==1.0 \\\n"
                "    --hash=sha256:" + "0" * 64 + "\n"
            ),
            "contracts/okf-explorer.consumer-lock.json": "{}\n",
            "pages/search-contract.json": "{}\n",
            "evaluation/questions.json": "{}\n",
        }
        for relative_name, content in freeze_surface.items():
            path = self.root / relative_name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        historical_receipt = self.root / "validation" / "release-record.json"
        write_json(
            historical_receipt,
            {"schema": "okf-frozen-historical-release-record.v1"},
        )
        historical_archive = self.root / "dist" / "okf-landregistry-0.2.0.zip"
        historical_archive.parent.mkdir(parents=True, exist_ok=True)
        historical_archive.write_bytes(b"frozen historical archive\n")
        (self.root / ".gitignore").write_text(
            "ignored-fixture/\n",
            encoding="utf-8",
        )

        bundle = self.root / "bundle"
        bundle.mkdir()
        artefact = bundle / "artefact.txt"
        artefact.write_text("governed candidate artefact\n", encoding="utf-8")
        build_receipt = bundle / "build-receipt.json"
        write_json(
            build_receipt,
            {
                "schema": "okf-test-build-receipt.v1",
                "version": "0.3.0",
                "publication_base": "https://example.test/okf/",
                "publication_state": "digest-bound-external-evidence",
                "generated_at": NOW,
                "release_at": None,
                "python_runtime": {
                    "schema": "okf-python-runtime.v1",
                    "executable_contract": ".venv/bin/python",
                    "virtual_environment_contract": ".venv",
                    "implementation": "CPython",
                    "version": "3.12.11",
                },
                "reproduction_invocation": [
                    ".venv/bin/python",
                    "-I",
                    "-B",
                    "-X",
                    "pycache_prefix=<private-empty-directory>",
                    "scripts/build.py",
                    "--snapshot-dir",
                    "source/snapshots/fixture",
                    "--publication-base",
                    "https://example.test/okf/",
                    "--replace",
                    "--previous-output",
                    "<owner-selected-empty-same-filesystem-path>",
                ],
                "domain_profile_pack_root_sha256": profile_root,
                "snapshot": {
                    "manifest_path": (
                        "source/snapshots/fixture/manifest.json"
                    ),
                    "source_manifest_sha256": snapshot_digest,
                    "acquisition_snapshot": {
                        "manifest_path": (
                            "source/snapshots/fixture/manifest.json"
                        ),
                        "source_manifest_sha256": snapshot_digest,
                    },
                },
                "governed_inputs": [
                    {
                        "path": "domain-profile/profile.json",
                        "bytes": len(profile_file.read_bytes()),
                        "sha256": sha256_file(profile_file),
                    },
                    {
                        "path": "source/snapshots/fixture/manifest.json",
                        "bytes": len(snapshot_manifest.read_bytes()),
                        "sha256": snapshot_digest,
                    },
                    {
                        "path": "governance/risk-register.json",
                        "bytes": len(risk_register.read_bytes()),
                        "sha256": sha256_file(risk_register),
                    },
                    {
                        "path": "source/build-config.json",
                        "bytes": len(build_config.read_bytes()),
                        "sha256": sha256_file(build_config),
                    },
                ],
            },
        )
        bundle_lines = [
            digest_line(artefact, "artefact.txt"),
            digest_line(build_receipt, "build-receipt.json"),
        ]
        release_root = hashlib.sha256(
            ("\n".join(bundle_lines) + "\n").encode("utf-8")
        ).hexdigest()
        self.release_root = release_root
        bundle_checksums = bundle / "CHECKSUMS.sha256"
        bundle_checksums.write_text(
            "\n".join(
                [*bundle_lines, f"# release-root-sha256: {release_root}", ""]
            ),
            encoding="utf-8",
        )
    def _gate_receipt(self, gate: str, evidence_path: Path) -> dict[str, object]:
        reviewer_identity = f"independent-reviewer-{gate}"
        reviewed = gate in REVIEWED_GATES
        reviewers: list[dict[str, object]] = []
        reviewed_checks: list[dict[str, object]] = []
        if reviewed:
            reviewers.append(
                {
                    "identity": reviewer_identity,
                    "kind": "ai-agent",
                    "role": f"{gate.lower()}-reviewer",
                    "reviewed_at": NOW,
                    "independent": True,
                }
            )
            reviewed_checks.append(
                {
                    "id": f"{gate.lower()}-agent-review",
                    "status": "pass",
                    "reviewer_identity": reviewer_identity,
                    "completed_at": NOW,
                    "execution_mode": "automated-agent",
                }
            )
        return {
            "$schema": SCHEMA_ID,
            "schema": "okf-gate-receipt.v1",
            "gate": gate,
            "status": "pass",
            "candidate": asdict(self.candidate),
            "executed_at": NOW,
            "validator": {
                "name": f"fixture-{gate.lower()}-validator",
                "version": "1.0.0",
                "sha256": "b" * 64,
                "command": ["fixture-validator", gate],
            },
            "checks": [
                {
                    "id": check_id,
                    "status": "pass",
                    "summary": f"{check_id} passed for the fixture",
                }
                for check_id in sorted(REQUIRED_CHECKS[gate])
            ],
            "evidence": [
                {
                    "path": evidence_path.relative_to(self.root).as_posix(),
                    "sha256": sha256_file(evidence_path),
                }
            ],
            "failures": [],
            "waivers": [],
            "review": {
                "mode": "automated-agent-review" if reviewed else "automated",
                "reviewers": reviewers,
            },
            "reviewed_checks": reviewed_checks,
        }

    def _write_evidence(self) -> None:
        entries: list[dict[str, str]] = []
        gate_hashes: dict[str, str] = {}
        archive_path = (
            self.root
            / "dist"
            / "okf-landregistry-0.3.0-candidate-a.zip"
        )
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path, "w") as archive:
            for path in sorted((self.root / "bundle").rglob("*")):
                if path.is_file():
                    write_zip_member(
                        archive,
                        "okf-landregistry-0.3.0/"
                        + path.relative_to(self.root / "bundle").as_posix(),
                        path.read_bytes(),
                    )
        archive_receipt_path = (
            self.validation_root
            / "evidence"
            / "release-candidate-archive-a.json"
        )
        write_json(
            archive_receipt_path,
            {
                "schema": "okf-hmlr-candidate-archive.v1",
                "version": "0.3.0",
                "candidate_at": NOW,
                "publication_state": "unreleased-candidate",
                "release_root_sha256": self.candidate.release_root_sha256,
                "candidate": asdict(self.candidate),
                "path": archive_path.relative_to(self.root).as_posix(),
                "bytes": archive_path.stat().st_size,
                "sha256": sha256_file(archive_path),
            },
        )
        self.archive_path = archive_path
        self.archive_receipt_path = archive_receipt_path
        archive_receipt = self.read_json(archive_receipt_path)
        _version, metadata_documents = expected_release_metadata_documents(
            self.root,
            candidate=self.candidate,
            archive_receipt=archive_receipt,
        )
        metadata_directory = (
            self.validation_root / "evidence" / "release-metadata"
        )
        metadata_directory.mkdir(parents=True, exist_ok=True)
        self.metadata_paths: dict[str, Path] = {}
        for filename, content in metadata_documents.items():
            metadata_path = metadata_directory / filename
            metadata_path.write_bytes(content)
            self.metadata_paths[filename] = metadata_path
        for number in range(1, 9):
            gate = f"G{number}"
            evidence_path = (
                self.validation_root / "evidence" / f"{gate.lower()}.txt"
            )
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text(
                f"digest-bound supporting evidence for {gate}\n",
                encoding="utf-8",
            )
            receipt_path = (
                self.validation_root
                / "final-g9"
                / "receipts"
                / f"{gate.lower()}.json"
            )
            receipt = self._gate_receipt(gate, evidence_path)
            if gate == "G8":
                receipt["evidence"].extend(  # type: ignore[union-attr]
                    [
                        {
                            "path": archive_receipt_path.relative_to(
                                self.root
                            ).as_posix(),
                            "sha256": sha256_file(archive_receipt_path),
                        },
                        {
                            "path": archive_path.relative_to(
                                self.root
                            ).as_posix(),
                            "sha256": sha256_file(archive_path),
                        },
                        *[
                            {
                                "path": path.relative_to(self.root).as_posix(),
                                "sha256": sha256_file(path),
                            }
                            for path in self.metadata_paths.values()
                        ],
                    ]
                )
            write_json(receipt_path, receipt)
            receipt_hash = sha256_file(receipt_path)
            self.receipt_paths[gate] = receipt_path
            gate_hashes[gate] = receipt_hash
            entries.append(
                {
                    "gate": gate,
                    "path": receipt_path.relative_to(self.root).as_posix(),
                    "sha256": receipt_hash,
                }
            )

        release_record: dict[str, object] = {
            "$schema": SCHEMA_ID,
            "schema": "okf-release-record.v1",
            "gate": "G9",
            "status": "approved",
            "candidate": asdict(self.candidate),
            "version": "0.2.0",
            "canonical_url": "https://example.test/okf/",
            "claims_reviewed": True,
            "residual_risks_reviewed": True,
            "residual_risk_ids": [
                "RISK-FIXTURE",
                "RISK-HUMAN-AUDIT",
            ],
            "human_audit": {
                "status": "not_completed",
                "residual_risk_id": "RISK-HUMAN-AUDIT",
                "notes": (
                    "This fixture models independent AI-agent review; a human "
                    "audit remains an explicitly accepted residual risk."
                ),
            },
            "owner_approval": {
                "identity": "fixture-owner",
                "kind": "human",
                "role": "project-owner",
                "approved_at": NOW,
                "approved": True,
            },
            "independent_review": {
                "identity": "fixture-release-reviewer",
                "kind": "ai-agent",
                "role": "release-reviewer",
                "reviewed_at": NOW,
                "independent": True,
                "outcome": "recommend_approval",
            },
            "approved_receipts": [
                {"gate": gate, "sha256": gate_hashes[gate]}
                for gate in sorted(gate_hashes)
            ],
        }
        release_path = self.validation_root / "final-g9" / "release-record.json"
        write_json(release_path, release_record)
        self.receipt_paths["G9"] = release_path
        entries.append(
            {
                "gate": "G9",
                "path": release_path.relative_to(self.root).as_posix(),
                "sha256": sha256_file(release_path),
            }
        )
        write_json(
            self.manifest_path,
            {
                "$schema": SCHEMA_ID,
                "schema": "okf-release-evidence-manifest.v1",
                "status": "complete",
                "generated_at": NOW,
                "candidate": asdict(self.candidate),
                "receipts": entries,
            },
        )

    def read_json(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def rewrite_receipt(self, gate: str, value: dict[str, object]) -> None:
        path = self.receipt_paths[gate]
        write_json(path, value)
        manifest = self.read_json(self.manifest_path)
        for reference in manifest["receipts"]:  # type: ignore[index]
            if reference["gate"] == gate:  # type: ignore[index]
                reference["sha256"] = sha256_file(path)  # type: ignore[index]
        write_json(self.manifest_path, manifest)

    def rewrite_g8_archive_receipt(self, value: dict[str, object]) -> None:
        write_json(self.archive_receipt_path, value)
        g8 = self.read_json(self.receipt_paths["G8"])
        archive_receipt_name = self.archive_receipt_path.relative_to(
            self.root
        ).as_posix()
        for reference in g8["evidence"]:  # type: ignore[index]
            if reference["path"] == archive_receipt_name:  # type: ignore[index]
                reference["sha256"] = sha256_file(  # type: ignore[index]
                    self.archive_receipt_path
                )
        self.rewrite_receipt("G8", g8)
        self.bind_v0_3_owner_approval()

    def rebind_changed_g8_archive(self) -> None:
        archive_receipt = self.read_json(self.archive_receipt_path)
        archive_receipt["bytes"] = self.archive_path.stat().st_size
        archive_receipt["sha256"] = sha256_file(self.archive_path)
        write_json(self.archive_receipt_path, archive_receipt)
        g8 = self.read_json(self.receipt_paths["G8"])
        archive_name = self.archive_path.relative_to(self.root).as_posix()
        receipt_name = self.archive_receipt_path.relative_to(
            self.root
        ).as_posix()
        for reference in g8["evidence"]:  # type: ignore[index]
            if reference["path"] == archive_name:  # type: ignore[index]
                reference["sha256"] = sha256_file(  # type: ignore[index]
                    self.archive_path
                )
            elif reference["path"] == receipt_name:  # type: ignore[index]
                reference["sha256"] = sha256_file(  # type: ignore[index]
                    self.archive_receipt_path
                )
        self.rewrite_receipt("G8", g8)
        self.bind_v0_3_owner_approval()

    def rewrite_archive_metadata(
        self,
        *,
        archive_comment: bytes = b"",
        member_metadata: dict[str, object] | None = None,
    ) -> None:
        bundle = self.root / "bundle"
        with zipfile.ZipFile(self.archive_path, "w") as archive:
            for path in sorted(bundle.rglob("*")):
                if path.is_file():
                    write_zip_member(
                        archive,
                        "okf-landregistry-0.3.0/"
                        + path.relative_to(bundle).as_posix(),
                        path.read_bytes(),
                        metadata_overrides=member_metadata,
                    )
            archive.comment = archive_comment
        self.rebind_changed_g8_archive()

    def rewrite_g8_evidence(self, value: dict[str, object]) -> None:
        self.rewrite_receipt("G8", value)
        self.bind_v0_3_owner_approval()

    def bind_v0_3_owner_approval(self) -> None:
        pre_g9_receipts: list[dict[str, str]] = []
        approved_receipts: list[dict[str, str]] = []
        for gate in GATE_RECEIPTS:
            source = self.receipt_paths[gate]
            target = (
                self.validation_root
                / "pre-g9"
                / "receipts"
                / f"{gate.lower()}.json"
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            digest = sha256_file(target)
            pre_g9_receipts.append(
                {
                    "gate": gate,
                    "path": target.relative_to(self.root).as_posix(),
                    "sha256": digest,
                }
            )
            approved_receipts.append({"gate": gate, "sha256": digest})

        pre_g9_manifest_path = (
            self.validation_root / "pre-g9" / "pre-g9-evidence.json"
        )
        write_json(
            pre_g9_manifest_path,
            {
                "schema": "okf-pre-g9-evidence-manifest.v1",
                "status": "ready_for_owner_review",
                "generated_at": NOW,
                "candidate": asdict(self.candidate),
                "receipts": pre_g9_receipts,
                "limitations": [
                    "This is G1-G8 evidence only and is not owner approval."
                ],
            },
        )
        claims = [
            "Version 0.3.0 is an AI-generated proof of concept.",
            "No completed human audit or WCAG conformance is claimed.",
        ]
        human_audit = {
            "status": "not_completed",
            "residual_risk_id": "RISK-HUMAN-AUDIT",
            "notes": (
                "This fixture models independent AI-agent review; a human "
                "audit remains an explicitly accepted residual risk."
            ),
        }
        risk_register = self.root / "governance" / "risk-register.json"
        release_record = self.read_json(self.receipt_paths["G9"])
        release_record["version"] = "0.3.0"
        release_record["approved_claims"] = claims
        release_record["human_audit"] = human_audit
        release_record["approved_receipts"] = approved_receipts
        independent_review = release_record["independent_review"]
        independent_review_path = (
            self.validation_root
            / "independent-release-review-evidence.json"
        )
        write_json(
            independent_review_path,
            {
                "$schema": SCHEMA_ID,
                "schema": "okf-independent-release-review-evidence.v1",
                "candidate": asdict(self.candidate),
                "independent_review": independent_review,
                "pre_g9_manifest_sha256": sha256_file(
                    pre_g9_manifest_path
                ),
                "approved_claims": claims,
                "residual_risk_ids": [
                    "RISK-FIXTURE",
                    "RISK-HUMAN-AUDIT",
                ],
            },
        )
        release_record["owner_approval"]["binding"] = {  # type: ignore[index]
            "version": "0.3.0",
            "canonical_url": release_record["canonical_url"],
            "candidate": asdict(self.candidate),
            "pre_g9_manifest": {
                "path": pre_g9_manifest_path.relative_to(
                    self.root
                ).as_posix(),
                "sha256": sha256_file(pre_g9_manifest_path),
            },
            "approved_receipts": approved_receipts,
            "approved_claims": claims,
            "residual_risks": {
                "register": {
                    "path": "governance/risk-register.json",
                    "sha256": sha256_file(risk_register),
                },
                "ids": ["RISK-FIXTURE", "RISK-HUMAN-AUDIT"],
            },
            "human_audit": human_audit,
            "independent_review": independent_review,
            "independent_review_evidence": {
                "path": independent_review_path.relative_to(
                    self.root
                ).as_posix(),
                "sha256": sha256_file(independent_review_path),
            },
        }
        self.rewrite_receipt("G9", release_record)

    def validate(self) -> CandidateIdentity:
        derived = candidate_identity_from_repository(
            self.root,
            checksums_path=Path("bundle/CHECKSUMS.sha256"),
            profile_checksums_path=Path("domain-profile/CHECKSUMS.sha256"),
            build_receipt_path=Path("bundle/build-receipt.json"),
            candidate_commit_sha=self.candidate_commit_sha,
        )
        validate_governed_candidate_commit(
            self.root,
            candidate_commit_sha=self.candidate_commit_sha,
            build_receipt_path=Path("bundle/build-receipt.json"),
        )
        return validate_release_evidence(
            self.root,
            manifest_path=self.manifest_path.relative_to(self.root),
            schema_path=Path("schemas/release-evidence.schema.json"),
            expected_candidate=derived,
        )


class ReleaseEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = EvidenceFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_complete_exact_candidate_evidence_passes(self) -> None:
        current = self.fixture.read_json(self.fixture.receipt_paths["G9"])
        self.assertEqual("0.3.0", current["version"])
        self.assertIn("binding", current["owner_approval"])
        self.assertEqual(self.fixture.candidate, self.fixture.validate())

    def test_committed_evidence_closure_allows_unreferenced_diagnostic(
        self,
    ) -> None:
        diagnostic = (
            self.fixture.validation_root / "evidence" / "external-diagnostic.json"
        )
        write_json(diagnostic, {"schema": "okf-external-diagnostic.v1"})
        evidence_commit = self.fixture.git("rev-parse", "HEAD").stdout.strip()

        declared = (
            release_evidence_checker.validate_committed_release_evidence_closure(
                self.fixture.root,
                manifest_path=self.fixture.manifest_path.relative_to(
                    self.fixture.root
                ),
                schema_path=Path("schemas/release-evidence.schema.json"),
                evidence_commit_sha=evidence_commit,
            )
        )
        self.assertEqual(self.fixture.candidate_commit_sha, declared)

    def test_committed_evidence_closure_rejects_alternate_schema(self) -> None:
        alternate = self.fixture.validation_root / "relaxed-schema.json"
        write_json(
            alternate,
            {"$schema": "https://json-schema.org/draft/2020-12/schema"},
        )
        evidence_commit = self.fixture.git("rev-parse", "HEAD").stdout.strip()

        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "must use the canonical committed schema",
        ):
            release_evidence_checker.validate_committed_release_evidence_closure(
                self.fixture.root,
                manifest_path=self.fixture.manifest_path.relative_to(
                    self.fixture.root
                ),
                schema_path=alternate.relative_to(self.fixture.root),
                evidence_commit_sha=evidence_commit,
            )

    def test_actual_cli_rejects_wholly_untracked_final_evidence(self) -> None:
        self.fixture.git("reset", "--mixed", self.fixture.candidate_commit_sha)
        checker_path = self.fixture.root / "scripts" / "check_release_evidence.py"
        checker_path.write_bytes(
            (ROOT / "scripts" / "check_release_evidence.py").read_bytes()
        )

        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(checker_path),
                "--manifest",
                self.fixture.manifest_path.relative_to(
                    self.fixture.root
                ).as_posix(),
            ],
            cwd=self.fixture.root,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("committed release evidence manifest", result.stderr)

    def test_gate_evidence_reference_count_and_aggregate_are_bounded(self) -> None:
        g1 = self.fixture.read_json(self.fixture.receipt_paths["G1"])
        g1["evidence"] = [
            g1["evidence"][0]
            for _index in range(release_evidence_checker.MAX_EVIDENCE_REFERENCES + 1)
        ]
        self.fixture.rewrite_receipt("G1", g1)
        with self.assertRaisesRegex(ReleaseEvidenceError, "not schema-valid"):
            self.fixture.validate()

        original = self.fixture._gate_receipt(
            "G1",
            self.fixture.validation_root / "evidence" / "g1.txt",
        )
        with mock.patch.object(
            release_evidence_checker,
            "MAX_EVIDENCE_REFERENCE_AGGREGATE_BYTES",
            1,
        ):
            with self.assertRaisesRegex(ReleaseEvidenceError, "1-byte"):
                release_evidence_checker.validate_evidence_references(
                    self.fixture.root,
                    original,
                    gate="G1",
                )

    def test_exact_historical_v0_2_unbound_record_remains_readable(
        self,
    ) -> None:
        historical_path = ROOT / "validation" / "release-record.json"
        historical = json.loads(historical_path.read_text(encoding="utf-8"))
        candidate = CandidateIdentity(**historical["candidate"])
        receipt_hashes = {
            reference["gate"]: reference["sha256"]
            for reference in historical["approved_receipts"]
        }
        self.assertNotIn("binding", historical["owner_approval"])
        validate_release_record(
            ROOT,
            historical,
            expected_candidate=candidate,
            receipt_hashes=receipt_hashes,
            release_record_bytes=historical_path.read_bytes(),
            manifest_bytes=(
                ROOT / "validation" / "release-evidence.json"
            ).read_bytes(),
        )

    def test_exact_historical_v0_1_unbound_record_remains_readable(
        self,
    ) -> None:
        historical_bytes = subprocess.run(
            [
                "git",
                "show",
                "v0.1.0:validation/release-record.json",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        historical = json.loads(historical_bytes)
        manifest_bytes = subprocess.run(
            [
                "git",
                "show",
                "v0.1.0:validation/release-evidence.json",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        candidate = CandidateIdentity(**historical["candidate"])
        receipt_hashes = {
            reference["gate"]: reference["sha256"]
            for reference in historical["approved_receipts"]
        }
        self.assertNotIn("binding", historical["owner_approval"])
        validate_release_record(
            ROOT,
            historical,
            expected_candidate=candidate,
            receipt_hashes=receipt_hashes,
            release_record_bytes=historical_bytes,
            manifest_bytes=manifest_bytes,
        )

    def test_legacy_exception_uses_actual_g9_and_manifest_bytes(
        self,
    ) -> None:
        historical_path = ROOT / "validation" / "release-record.json"
        historical = json.loads(historical_path.read_text(encoding="utf-8"))
        candidate = CandidateIdentity(**historical["candidate"])
        receipt_hashes = {
            reference["gate"]: reference["sha256"]
            for reference in historical["approved_receipts"]
        }
        historical["canonical_url"] = "https://attacker.example/"
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "differs from its supplied byte buffer",
        ):
            validate_release_record(
                ROOT,
                historical,
                expected_candidate=candidate,
                receipt_hashes=receipt_hashes,
                release_record_bytes=historical_path.read_bytes(),
                manifest_bytes=(
                    ROOT / "validation" / "release-evidence.json"
                ).read_bytes(),
            )

    def test_complete_v0_3_exact_owner_binding_passes(self) -> None:
        self.fixture.bind_v0_3_owner_approval()
        self.assertEqual(self.fixture.candidate, self.fixture.validate())

    def test_v0_3_hand_authored_generic_owner_approval_fails_closed(
        self,
    ) -> None:
        self.fixture.bind_v0_3_owner_approval()
        release_record = self.fixture.read_json(
            self.fixture.receipt_paths["G9"]
        )
        release_record["owner_approval"].pop("binding")  # type: ignore[union-attr]
        self.fixture.rewrite_receipt("G9", release_record)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "G9 receipt is not schema-valid"
        ):
            self.fixture.validate()

    def test_v0_3_candidate_cannot_masquerade_as_legacy_v0_2(self) -> None:
        release_record = self.fixture.read_json(
            self.fixture.receipt_paths["G9"]
        )
        release_record["version"] = "0.2.0"
        release_record["owner_approval"].pop("binding")  # type: ignore[union-attr]
        release_record.pop("approved_claims")
        self.fixture.rewrite_receipt("G9", release_record)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "G9 owner approval binding must be an object"
        ):
            self.fixture.validate()

    def test_v0_3_owner_binding_identity_bypasses_fail_closed(self) -> None:
        self.fixture.bind_v0_3_owner_approval()
        original = self.fixture.read_json(self.fixture.receipt_paths["G9"])
        cases = (
            (
                "candidate",
                "release_root_sha256",
                "0" * 64,
                "owner binding candidate differs",
            ),
            (
                "version",
                None,
                "9.9.9",
                "must equal the governed.*version",
            ),
            (
                "canonical_url",
                None,
                "https://different.example.test/",
                "must equal the governed.*publication_base",
            ),
        )
        for field, child_field, replacement, expected_message in cases:
            with self.subTest(field=field):
                release_record = json.loads(json.dumps(original))
                binding = release_record["owner_approval"]["binding"]
                if child_field is None:
                    binding[field] = replacement
                else:
                    binding[field][child_field] = replacement
                self.fixture.rewrite_receipt("G9", release_record)
                with self.assertRaisesRegex(
                    ReleaseEvidenceError, expected_message
                ):
                    self.fixture.validate()

    def test_v0_3_owner_binding_scope_bypasses_fail_closed(self) -> None:
        self.fixture.bind_v0_3_owner_approval()
        original = self.fixture.read_json(self.fixture.receipt_paths["G9"])

        release_record = json.loads(json.dumps(original))
        binding = release_record["owner_approval"]["binding"]
        binding["approved_receipts"][0]["sha256"] = "0" * 64
        self.fixture.rewrite_receipt("G9", release_record)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "owner binding receipt hashes do not match"
        ):
            self.fixture.validate()

        release_record = json.loads(json.dumps(original))
        binding = release_record["owner_approval"]["binding"]
        binding["approved_claims"].pop()
        self.fixture.rewrite_receipt("G9", release_record)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "owner binding approved claims do not match"
        ):
            self.fixture.validate()

        release_record = json.loads(json.dumps(original))
        binding = release_record["owner_approval"]["binding"]
        binding["human_audit"]["notes"] = "Different audit scope."
        self.fixture.rewrite_receipt("G9", release_record)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "owner binding human audit does not match"
        ):
            self.fixture.validate()

    def test_v0_3_owner_binding_external_bytes_fail_closed(self) -> None:
        self.fixture.bind_v0_3_owner_approval()
        original = self.fixture.read_json(self.fixture.receipt_paths["G9"])

        release_record = json.loads(json.dumps(original))
        binding = release_record["owner_approval"]["binding"]
        binding["pre_g9_manifest"]["sha256"] = "0" * 64
        self.fixture.rewrite_receipt("G9", release_record)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "pre_g9_manifest digest mismatch"
        ):
            self.fixture.validate()

        release_record = json.loads(json.dumps(original))
        binding = release_record["owner_approval"]["binding"]
        binding["independent_review_evidence"]["sha256"] = "0" * 64
        self.fixture.rewrite_receipt("G9", release_record)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "independent_review_evidence digest mismatch",
        ):
            self.fixture.validate()

        release_record = json.loads(json.dumps(original))
        binding = release_record["owner_approval"]["binding"]
        binding["residual_risks"]["register"]["sha256"] = "0" * 64
        self.fixture.rewrite_receipt("G9", release_record)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "residual_risks.register digest mismatch"
        ):
            self.fixture.validate()

        release_record = json.loads(json.dumps(original))
        binding = release_record["owner_approval"]["binding"]
        binding["residual_risks"]["ids"].pop()
        self.fixture.rewrite_receipt("G9", release_record)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "owner-bound residual-risk IDs do not equal the governed set",
        ):
            self.fixture.validate()

    def test_v0_3_pre_g9_receipt_bytes_are_rehashed(self) -> None:
        self.fixture.bind_v0_3_owner_approval()
        pre_g9_receipt = (
            self.fixture.validation_root
            / "pre-g9"
            / "receipts"
            / "g4.json"
        )
        pre_g9_receipt.write_text(
            pre_g9_receipt.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "pre-G9 receipts.*digest mismatch"
        ):
            self.fixture.validate()

    def test_v0_3_owner_binding_includes_exact_independent_review(
        self,
    ) -> None:
        release_record = self.fixture.read_json(
            self.fixture.receipt_paths["G9"]
        )
        release_record["owner_approval"]["binding"][  # type: ignore[index]
            "independent_review"
        ]["role"] = "different-review-role"
        self.fixture.rewrite_receipt("G9", release_record)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "owner binding independent review does not match",
        ):
            self.fixture.validate()

    def test_v0_3_release_coordinates_are_governed(self) -> None:
        original = self.fixture.read_json(self.fixture.receipt_paths["G9"])
        cases = (
            ("version", "9.9.9", "build-config.json version"),
            (
                "canonical_url",
                "https://different.example.test/",
                "build-config.json publication_base",
            ),
        )
        for field, replacement, message in cases:
            with self.subTest(field=field):
                release_record = json.loads(json.dumps(original))
                release_record[field] = replacement
                release_record["owner_approval"]["binding"][field] = replacement
                self.fixture.rewrite_receipt("G9", release_record)
                with self.assertRaisesRegex(ReleaseEvidenceError, message):
                    self.fixture.validate()

    def test_release_coordinate_receipt_must_repeat_governed_values(self) -> None:
        receipt_path = self.fixture.root / "bundle" / "build-receipt.json"
        receipt = self.fixture.read_json(receipt_path)
        receipt["publication_base"] = "https://different.example.test/"
        write_json(receipt_path, receipt)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "build receipt publication_base does not match",
        ):
            release_coordinates_from_build_config(
                self.fixture.root,
                build_receipt_path=Path("bundle/build-receipt.json"),
            )

    def test_release_coordinate_receipt_must_repeat_governed_state_and_time(
        self,
    ) -> None:
        receipt_path = self.fixture.root / "bundle" / "build-receipt.json"
        original = self.fixture.read_json(receipt_path)
        cases = (
            ("publication_state", "different-state"),
            ("generated_at", "2099-01-01T00:00:00Z"),
            ("release_at", NOW),
        )
        for field, replacement in cases:
            with self.subTest(field=field):
                receipt = json.loads(json.dumps(original))
                receipt[field] = replacement
                write_json(receipt_path, receipt)
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    f"build receipt {field} does not match governed",
                ):
                    release_coordinates_from_build_config(
                        self.fixture.root,
                        build_receipt_path=Path("bundle/build-receipt.json"),
                    )
        write_json(receipt_path, original)

    def test_identity_text_is_canonical_and_owner_is_not_a_reviewer(
        self,
    ) -> None:
        release_record = self.fixture.read_json(self.fixture.receipt_paths["G9"])
        release_record["owner_approval"]["identity"] = " "  # type: ignore[index]
        self.fixture.rewrite_receipt("G9", release_record)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "G9 receipt is not schema-valid"
        ):
            self.fixture.validate()

        release_record = self.fixture.read_json(self.fixture.receipt_paths["G9"])
        release_record["owner_approval"]["identity"] = (  # type: ignore[index]
            "fixture-owner"
        )
        self.fixture.rewrite_receipt("G9", release_record)
        g3 = self.fixture.read_json(self.fixture.receipt_paths["G3"])
        g3["review"]["reviewers"][0]["identity"] = (  # type: ignore[index]
            "fixture-owner"
        )
        g3["reviewed_checks"][0]["reviewer_identity"] = (  # type: ignore[index]
            "fixture-owner"
        )
        self.fixture.rewrite_receipt("G3", g3)
        self.fixture.bind_v0_3_owner_approval()
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "project owner is declared as an independent gate reviewer",
        ):
            self.fixture.validate()

    def test_gate_reviews_must_precede_the_gate_receipt(self) -> None:
        g3 = self.fixture.read_json(self.fixture.receipt_paths["G3"])
        g3["review"]["reviewers"][0]["reviewed_at"] = (  # type: ignore[index]
            "2099-01-01T00:00:00Z"
        )
        g3["reviewed_checks"][0]["completed_at"] = (  # type: ignore[index]
            "2099-01-01T00:00:00Z"
        )
        self.fixture.rewrite_receipt("G3", g3)
        self.fixture.bind_v0_3_owner_approval()
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "reviewed after the gate executed_at"
        ):
            self.fixture.validate()

    def test_final_manifest_is_strict_utc_and_after_owner_approval(self) -> None:
        manifest = self.fixture.read_json(self.fixture.manifest_path)
        manifest["generated_at"] = "2000-01-01T00:00:00Z"
        write_json(self.fixture.manifest_path, manifest)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "is after manifest.generated_at"
        ):
            self.fixture.validate()

        manifest["generated_at"] = "2026-07-29T13:00:00+01:00"
        write_json(self.fixture.manifest_path, manifest)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "manifest is not schema-valid"
        ):
            self.fixture.validate()

    def test_checker_requires_exact_pre_g9_manifest_shape(self) -> None:
        pre_g9_path = (
            self.fixture.validation_root
            / "pre-g9"
            / "pre-g9-evidence.json"
        )
        pre_g9 = self.fixture.read_json(pre_g9_path)
        pre_g9.pop("limitations")
        pre_g9["owner_approval"] = True
        write_json(pre_g9_path, pre_g9)

        review_path = (
            self.fixture.validation_root
            / "independent-release-review-evidence.json"
        )
        review_evidence = self.fixture.read_json(review_path)
        review_evidence["pre_g9_manifest_sha256"] = sha256_file(pre_g9_path)
        write_json(review_path, review_evidence)
        release_record = self.fixture.read_json(self.fixture.receipt_paths["G9"])
        binding = release_record["owner_approval"]["binding"]  # type: ignore[index]
        binding["pre_g9_manifest"]["sha256"] = sha256_file(pre_g9_path)
        binding["independent_review_evidence"]["sha256"] = sha256_file(
            review_path
        )
        self.fixture.rewrite_receipt("G9", release_record)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "pre-G9 manifest has an invalid field set",
        ):
            self.fixture.validate()

    def test_secure_repository_read_has_no_insecure_fallback(self) -> None:
        with mock.patch.object(os, "supports_dir_fd", set()):
            with self.assertRaisesRegex(
                ReleaseEvidenceError,
                "refusing the race-prone fallback",
            ):
                read_repository_file_bytes(
                    self.fixture.root,
                    "source/build-config.json",
                    purpose="fixture governed input",
                )

    def test_v0_3_completed_human_audit_needs_separate_workflow(
        self,
    ) -> None:
        release_record = self.fixture.read_json(
            self.fixture.receipt_paths["G9"]
        )
        completed_audit = {
            "status": "completed",
            "residual_risk_id": "RISK-HUMAN-AUDIT",
            "notes": "A claimed completed audit must be separately bound.",
            "reviewer": {
                "identity": "fixture-human-auditor",
                "kind": "human",
                "role": "human-auditor",
                "reviewed_at": NOW,
                "independent": True,
            },
        }
        release_record["human_audit"] = completed_audit
        release_record["owner_approval"]["binding"][  # type: ignore[index]
            "human_audit"
        ] = completed_audit
        self.fixture.rewrite_receipt("G9", release_record)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "separately digest-bound human-audit workflow",
        ):
            self.fixture.validate()

    def test_v0_3_release_chronology_is_utc_and_ordered(self) -> None:
        original = self.fixture.read_json(self.fixture.receipt_paths["G9"])

        release_record = json.loads(json.dumps(original))
        release_record["owner_approval"]["approved_at"] = (  # type: ignore[index]
            "2026-07-29T11:59:59Z"
        )
        self.fixture.rewrite_receipt("G9", release_record)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "owner approval predates the independent review",
        ):
            self.fixture.validate()

        release_record = json.loads(json.dumps(original))
        release_record["owner_approval"]["approved_at"] = (  # type: ignore[index]
            "2026-07-29T13:00:00+01:00"
        )
        self.fixture.rewrite_receipt("G9", release_record)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "G9 receipt is not schema-valid",
        ):
            self.fixture.validate()

    def test_missing_gate_fails_closed(self) -> None:
        manifest = self.fixture.read_json(self.fixture.manifest_path)
        manifest["receipts"] = [  # type: ignore[index]
            reference
            for reference in manifest["receipts"]  # type: ignore[index]
            if reference["gate"] != "G4"
        ]
        write_json(self.fixture.manifest_path, manifest)
        with self.assertRaises(ReleaseEvidenceError):
            self.fixture.validate()

    def test_incomplete_manifest_states_fail_closed(self) -> None:
        for state in ("not_run", "candidate"):
            with self.subTest(state=state):
                manifest = self.fixture.read_json(self.fixture.manifest_path)
                manifest["status"] = state
                write_json(self.fixture.manifest_path, manifest)
                with self.assertRaisesRegex(
                    ReleaseEvidenceError, "manifest is not complete"
                ):
                    self.fixture.validate()
                manifest["status"] = "complete"
                write_json(self.fixture.manifest_path, manifest)

    def test_non_pass_gate_states_fail_closed(self) -> None:
        for state in ("not_run", "candidate", "fail"):
            with self.subTest(state=state):
                receipt = self.fixture.read_json(self.fixture.receipt_paths["G2"])
                receipt["status"] = state
                self.fixture.rewrite_receipt("G2", receipt)
                with self.assertRaisesRegex(ReleaseEvidenceError, "G2 is not passed"):
                    self.fixture.validate()
                receipt["status"] = "pass"
                self.fixture.rewrite_receipt("G2", receipt)

    def test_candidate_identity_mismatch_fails_closed(self) -> None:
        receipt = self.fixture.read_json(self.fixture.receipt_paths["G4"])
        receipt["candidate"]["candidate_commit_sha"] = "c" * 40  # type: ignore[index]
        self.fixture.rewrite_receipt("G4", receipt)
        with self.assertRaisesRegex(ReleaseEvidenceError, "candidate identity differs"):
            self.fixture.validate()

    def test_unsafe_receipt_path_fails_closed(self) -> None:
        manifest = self.fixture.read_json(self.fixture.manifest_path)
        manifest["receipts"][1]["path"] = "../g2.json"  # type: ignore[index]
        write_json(self.fixture.manifest_path, manifest)
        with self.assertRaisesRegex(ReleaseEvidenceError, "unsafe G2 receipt path"):
            self.fixture.validate()

    def test_receipt_hash_mismatch_fails_closed(self) -> None:
        path = self.fixture.receipt_paths["G7"]
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ReleaseEvidenceError, "G7 receipt digest mismatch"):
            self.fixture.validate()

    def test_receipt_loop_rejects_symbolic_links(self) -> None:
        path = self.fixture.receipt_paths["G2"]
        target = path.with_name("g2-target.json")
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target.name)
        with self.assertRaisesRegex(ReleaseEvidenceError, "symbolic link"):
            self.fixture.validate()

    def test_reviewed_gate_requires_independent_reviewer_and_checks(self) -> None:
        original = self.fixture.read_json(self.fixture.receipt_paths["G5"])
        cases = [
            (
                {
                    "review": {"mode": "automated", "reviewers": []},
                    "reviewed_checks": [],
                },
                "requires an explicit reviewer mode",
            ),
            (
                {
                    "review": {
                        "mode": "mixed",
                        "reviewers": [
                            {
                                **original["review"]["reviewers"][0],  # type: ignore[index]
                                "independent": False,
                            }
                        ],
                    }
                },
                "requires a named independent",
            ),
            ({"reviewed_checks": []}, "requires passed reviewed checks"),
        ]
        for changes, message in cases:
            with self.subTest(message=message):
                receipt = json.loads(json.dumps(original))
                receipt.update(changes)
                self.fixture.rewrite_receipt("G5", receipt)
                with self.assertRaisesRegex(ReleaseEvidenceError, message):
                    self.fixture.validate()
        self.fixture.rewrite_receipt("G5", original)

    def test_g9_must_be_approved(self) -> None:
        release_record = self.fixture.read_json(self.fixture.receipt_paths["G9"])
        release_record["status"] = "candidate"
        self.fixture.rewrite_receipt("G9", release_record)
        with self.assertRaisesRegex(ReleaseEvidenceError, "G9 is not approved"):
            self.fixture.validate()

    def test_g9_must_bind_exact_g1_g8_receipt_hashes(self) -> None:
        release_record = self.fixture.read_json(self.fixture.receipt_paths["G9"])
        release_record["approved_receipts"][0]["sha256"] = "0" * 64  # type: ignore[index]
        self.fixture.rewrite_receipt("G9", release_record)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "approved receipt hashes do not match"
        ):
            self.fixture.validate()

    def test_incomplete_human_audit_must_remain_a_residual_risk(self) -> None:
        release_record = self.fixture.read_json(self.fixture.receipt_paths["G9"])
        release_record["residual_risk_ids"].remove("RISK-HUMAN-AUDIT")  # type: ignore[index]
        self.fixture.rewrite_receipt("G9", release_record)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "human audit must be declared"
        ):
            self.fixture.validate()

    def test_candidate_commit_must_be_ancestor_of_evidence_commit(self) -> None:
        tree = self.fixture.git(
            "rev-parse", f"{self.fixture.candidate_commit_sha}^{{tree}}"
        ).stdout.strip()
        unrelated = self.fixture.git(
            "commit-tree", tree, input_text="Unrelated candidate\n"
        ).stdout.strip()
        with self.assertRaisesRegex(ReleaseEvidenceError, "is not an ancestor"):
            validate_governed_candidate_commit(
                self.fixture.root,
                candidate_commit_sha=unrelated,
                build_receipt_path=Path("bundle/build-receipt.json"),
            )

    def test_candidate_tree_must_not_change_in_evidence_commit(self) -> None:
        added = self.fixture.root / "domain-profile" / "post-candidate.txt"
        added.write_text("candidate drift\n", encoding="utf-8")
        self.fixture.git("add", "domain-profile/post-candidate.txt")
        self.fixture.git("commit", "-m", "Mutate governed candidate tree")
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "governed candidate tree changed"
        ):
            validate_governed_candidate_commit(
                self.fixture.root,
                candidate_commit_sha=self.fixture.candidate_commit_sha,
                build_receipt_path=Path("bundle/build-receipt.json"),
            )

    def test_evidence_commit_rejects_every_protected_path_class(self) -> None:
        modified_paths = (
            ".github/workflows/pages.yml",
            "scripts/check_release_evidence.py",
            "scripts/assemble_release_evidence.py",
            "schemas/release-evidence.schema.json",
            "scripts/run_authored_site_browser_quality.mjs",
            "evaluation/acceptance-review-v0.2.0.json",
        )
        for relative_name in modified_paths:
            path = self.fixture.root / relative_name
            path.write_bytes(path.read_bytes() + b"post-candidate mutation\n")

        deleted_path = "docs/release-assurance.md"
        (self.fixture.root / deleted_path).unlink()
        renamed_source = "tests/test_release_evidence.py"
        renamed_target = "tests/test_release_evidence-renamed.py"
        self.fixture.git("mv", renamed_source, renamed_target)
        added_path = "scripts/post-candidate-added.py"
        (self.fixture.root / added_path).write_text(
            "# post-candidate addition\n",
            encoding="utf-8",
        )
        newline_path = "scripts/post-candidate\nnewline.py"
        (self.fixture.root / newline_path).write_text(
            "# newline path\n",
            encoding="utf-8",
        )
        self.fixture.git("add", "--all")
        self.fixture.git("commit", "-m", "Attempt protected evidence drift")

        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            r"outside validation/candidate-v0\.3\.0/\*\*",
        ) as raised:
            validate_governed_candidate_commit(
                self.fixture.root,
                candidate_commit_sha=self.fixture.candidate_commit_sha,
                build_receipt_path=Path("bundle/build-receipt.json"),
            )
        diagnostic = str(raised.exception)
        for relative_name in (
            *modified_paths,
            deleted_path,
            renamed_source,
            renamed_target,
            added_path,
            "scripts/post-candidate\\nnewline.py",
        ):
            with self.subTest(relative_name=relative_name):
                self.assertIn(relative_name, diagnostic)

    def test_mutate_then_revert_commit_history_fails_closed(self) -> None:
        workflow = self.fixture.root / ".github" / "workflows" / "pages.yml"
        original = workflow.read_bytes()
        workflow.write_bytes(original + b"# transient protected mutation\n")
        self.fixture.git("add", ".github/workflows/pages.yml")
        self.fixture.git("commit", "-m", "Mutate protected workflow")
        workflow.write_bytes(original)
        self.fixture.git("add", ".github/workflows/pages.yml")
        self.fixture.git("commit", "-m", "Revert protected workflow")

        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "tree changed in commit history.*pages.yml",
        ):
            validate_governed_candidate_commit(
                self.fixture.root,
                candidate_commit_sha=self.fixture.candidate_commit_sha,
                build_receipt_path=Path("bundle/build-receipt.json"),
            )

    def test_protected_side_branch_merge_fails_the_linear_history_rule(self) -> None:
        main_branch = self.fixture.git(
            "branch", "--show-current"
        ).stdout.strip()
        workflow = self.fixture.root / ".github" / "workflows" / "pages.yml"
        original = workflow.read_bytes()
        self.fixture.git("switch", "-c", "protected-side")
        workflow.write_bytes(original + b"# side-branch mutation\n")
        self.fixture.git("add", ".github/workflows/pages.yml")
        self.fixture.git("commit", "-m", "Mutate workflow on side branch")
        workflow.write_bytes(original)
        self.fixture.git("add", ".github/workflows/pages.yml")
        self.fixture.git("commit", "-m", "Restore workflow on side branch")

        self.fixture.git("switch", main_branch)
        merge_evidence = (
            self.fixture.validation_root / "evidence" / "merge-base.json"
        )
        write_json(merge_evidence, {"status": "pass"})
        self.fixture.git("add", merge_evidence.relative_to(self.fixture.root).as_posix())
        self.fixture.git("commit", "-m", "Add independent evidence")
        self.fixture.git(
            "merge",
            "--no-ff",
            "protected-side",
            "-m",
            "Merge protected side history",
        )

        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "single-parent linear chain",
        ):
            validate_governed_candidate_commit(
                self.fixture.root,
                candidate_commit_sha=self.fixture.candidate_commit_sha,
                build_receipt_path=Path("bundle/build-receipt.json"),
            )

    def test_governed_input_digest_uses_candidate_blob_then_checks_worktree(
        self,
    ) -> None:
        profile = self.fixture.root / "domain-profile" / "profile.json"
        profile.write_text('{"schema":"worktree-tamper"}\n', encoding="utf-8")
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "staged, unstaged.*outside validation/.*domain-profile/profile.json",
        ):
            validate_governed_candidate_commit(
                self.fixture.root,
                candidate_commit_sha=self.fixture.candidate_commit_sha,
                build_receipt_path=Path("bundle/build-receipt.json"),
            )

    def test_governed_input_row_requires_exact_fields_and_candidate_size(
        self,
    ) -> None:
        receipt_path = self.fixture.root / "bundle" / "build-receipt.json"
        original = self.fixture.read_json(receipt_path)
        cases = (
            ("missing-bytes", None, "must contain exactly path, bytes and sha256"),
            ("boolean-bytes", True, "bytes must be a non-negative integer"),
            ("negative-bytes", -1, "bytes must be a non-negative integer"),
            ("mismatched-bytes", 0, "byte count mismatch"),
            ("extra-field", "extra", "must contain exactly path, bytes and sha256"),
        )
        for label, replacement, message in cases:
            with self.subTest(case=label):
                receipt = json.loads(json.dumps(original))
                row = receipt["governed_inputs"][0]  # type: ignore[index]
                if label == "missing-bytes":
                    row.pop("bytes")
                elif label == "extra-field":
                    row["description"] = replacement
                else:
                    row["bytes"] = replacement
                write_json(receipt_path, receipt)
                self.fixture.git("add", "bundle/build-receipt.json")
                self.fixture.git("commit", "-m", f"Exercise {label}")
                candidate = self.fixture.git("rev-parse", "HEAD").stdout.strip()
                with self.assertRaisesRegex(ReleaseEvidenceError, message):
                    validate_governed_candidate_commit(
                        self.fixture.root,
                        candidate_commit_sha=candidate,
                        build_receipt_path=Path("bundle/build-receipt.json"),
                    )

    def test_governed_input_must_be_a_regular_candidate_blob(self) -> None:
        target = self.fixture.root / "source" / "regular-target.txt"
        target.write_text("target bytes\n", encoding="utf-8")
        link = self.fixture.root / "source" / "symlink-input.txt"
        link.symlink_to("regular-target.txt")
        receipt_path = self.fixture.root / "bundle" / "build-receipt.json"
        receipt = self.fixture.read_json(receipt_path)
        receipt["governed_inputs"].append(  # type: ignore[union-attr]
            {
                "path": "source/symlink-input.txt",
                "bytes": len(b"regular-target.txt"),
                "sha256": hashlib.sha256(b"regular-target.txt").hexdigest(),
            }
        )
        write_json(receipt_path, receipt)
        self.fixture.git(
            "add",
            "bundle/build-receipt.json",
            "source/regular-target.txt",
            "source/symlink-input.txt",
        )
        self.fixture.git("commit", "-m", "Create symlink candidate input")
        candidate = self.fixture.git("rev-parse", "HEAD").stdout.strip()

        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "must resolve to exactly one regular blob",
        ):
            validate_governed_candidate_commit(
                self.fixture.root,
                candidate_commit_sha=candidate,
                build_receipt_path=Path("bundle/build-receipt.json"),
            )

    def test_ignored_untracked_build_receipt_cannot_replace_candidate_blob(
        self,
    ) -> None:
        ignored_receipt = (
            self.fixture.root / "ignored-fixture" / "build-receipt.json"
        )
        ignored_receipt.parent.mkdir(parents=True)
        ignored_receipt.write_bytes(
            (self.fixture.root / "bundle" / "build-receipt.json").read_bytes()
        )
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "candidate build receipt must resolve to exactly one regular blob",
        ):
            validate_governed_candidate_commit(
                self.fixture.root,
                candidate_commit_sha=self.fixture.candidate_commit_sha,
                build_receipt_path=Path("ignored-fixture/build-receipt.json"),
            )

    def test_ignored_untracked_governed_input_cannot_replace_candidate_blob(
        self,
    ) -> None:
        malicious_bytes = b'{"schema":"ignored-worktree-input"}\n'
        receipt_path = self.fixture.root / "bundle" / "build-receipt.json"
        receipt = self.fixture.read_json(receipt_path)
        receipt["governed_inputs"].append(  # type: ignore[union-attr]
            {
                "path": "ignored-fixture/governed-input.json",
                "bytes": len(malicious_bytes),
                "sha256": hashlib.sha256(malicious_bytes).hexdigest(),
            }
        )
        write_json(receipt_path, receipt)
        self.fixture.git("add", "bundle/build-receipt.json")
        self.fixture.git("commit", "-m", "Declare absent candidate input")
        candidate = self.fixture.git("rev-parse", "HEAD").stdout.strip()
        malicious_input = (
            self.fixture.root / "ignored-fixture" / "governed-input.json"
        )
        malicious_input.parent.mkdir(parents=True, exist_ok=True)
        malicious_input.write_bytes(malicious_bytes)

        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "candidate governed build input must resolve to exactly one regular blob",
        ):
            validate_governed_candidate_commit(
                self.fixture.root,
                candidate_commit_sha=candidate,
                build_receipt_path=Path("bundle/build-receipt.json"),
            )

    def test_worktree_rejects_staged_unstaged_and_untracked_protected_paths(
        self,
    ) -> None:
        staged_path = "scripts/staged-protected.py"
        (self.fixture.root / staged_path).write_text(
            "# staged protected path\n",
            encoding="utf-8",
        )
        self.fixture.git("add", staged_path)

        unstaged_path = ".github/workflows/pages.yml"
        workflow = self.fixture.root / unstaged_path
        workflow.write_bytes(workflow.read_bytes() + b"# unstaged drift\n")

        untracked_path = "tests/untracked-protected.py"
        (self.fixture.root / untracked_path).write_text(
            "# untracked protected path\n",
            encoding="utf-8",
        )
        lookalike_root_path = "validation-copy/not-evidence.json"
        lookalike = self.fixture.root / lookalike_root_path
        lookalike.parent.mkdir()
        write_json(lookalike, {"status": "not-evidence"})

        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "staged, unstaged or non-ignored untracked changes",
        ) as raised:
            validate_governed_candidate_commit(
                self.fixture.root,
                candidate_commit_sha=self.fixture.candidate_commit_sha,
                build_receipt_path=Path("bundle/build-receipt.json"),
            )
        diagnostic = str(raised.exception)
        for relative_name in (
            staged_path,
            unstaged_path,
            untracked_path,
            lookalike_root_path,
        ):
            with self.subTest(relative_name=relative_name):
                self.assertIn(relative_name, diagnostic)

    def test_only_exact_v0_3_evidence_paths_are_mutable_after_the_candidate(
        self,
    ) -> None:
        committed_acceptance = (
            self.fixture.root
            / "validation"
            / "candidate-v0.3.0"
            / "evidence"
            / "acceptance-review.json"
        )
        write_json(
            committed_acceptance,
            {"schema": "okf-hmlr-acceptance-review.v1"},
        )
        committed_archive = (
            self.fixture.root
            / "dist"
            / "okf-landregistry-0.3.0-candidate-b.zip"
        )
        committed_archive.parent.mkdir(parents=True, exist_ok=True)
        committed_archive.write_bytes(b"committed candidate archive\n")
        self.fixture.git("add", "validation", "dist")
        self.fixture.git("commit", "-m", "Add exact candidate evidence")

        staged_validation = (
            self.fixture.validation_root / "evidence" / "staged.json"
        )
        write_json(staged_validation, {"status": "pass"})
        self.fixture.git(
            "add", staged_validation.relative_to(self.fixture.root).as_posix()
        )
        committed_archive.write_bytes(b"modified comparison archive\n")
        ignored = self.fixture.root / "ignored-fixture" / "diagnostic.txt"
        ignored.parent.mkdir(parents=True)
        ignored.write_text("ignored diagnostic\n", encoding="utf-8")

        expected_head = self.fixture.git("rev-parse", "HEAD").stdout.strip()
        self.assertEqual(
            expected_head,
            validate_governed_candidate_commit(
                self.fixture.root,
                candidate_commit_sha=self.fixture.candidate_commit_sha,
                build_receipt_path=Path("bundle/build-receipt.json"),
            ),
        )

    def test_historical_validation_and_archives_are_immutable_after_candidate(
        self,
    ) -> None:
        historical_receipt = self.fixture.root / "validation" / "release-record.json"
        historical_archive = (
            self.fixture.root / "dist" / "okf-landregistry-0.2.0.zip"
        )
        historical_receipt.write_bytes(
            historical_receipt.read_bytes() + b"historical mutation\n"
        )
        historical_archive.write_bytes(b"historical archive mutation\n")

        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "exact v0.3 candidate archives",
        ) as raised:
            validate_governed_candidate_commit(
                self.fixture.root,
                candidate_commit_sha=self.fixture.candidate_commit_sha,
                build_receipt_path=Path("bundle/build-receipt.json"),
            )
        diagnostic = str(raised.exception)
        self.assertIn("validation/release-record.json", diagnostic)
        self.assertIn("dist/okf-landregistry-0.2.0.zip", diagnostic)

    def test_g8_dist_archive_is_rehashed_by_the_final_checker(self) -> None:
        self.assertEqual(self.fixture.candidate, self.fixture.validate())

        self.fixture.archive_path.write_bytes(
            self.fixture.archive_path.read_bytes() + b"tampered"
        )
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "G8 evidence digest mismatch.*dist/okf-landregistry",
        ):
            self.fixture.validate()

    def test_g8_missing_actual_zip_fails_closed(self) -> None:
        self.fixture.archive_path.unlink()
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "G8 evidence.*missing or unreadable",
        ):
            self.fixture.validate()

    def test_g8_missing_zip_reference_fails_closed(self) -> None:
        g8 = self.fixture.read_json(self.fixture.receipt_paths["G8"])
        g8["evidence"] = [  # type: ignore[index]
            reference
            for reference in g8["evidence"]  # type: ignore[index]
            if not str(reference["path"]).startswith("dist/")
        ]
        self.fixture.rewrite_g8_evidence(g8)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            r"exactly the designated dist/\*\.zip archive",
        ):
            self.fixture.validate()

    def test_g8_missing_archive_receipt_fails_closed(self) -> None:
        g8 = self.fixture.read_json(self.fixture.receipt_paths["G8"])
        archive_receipt_name = self.fixture.archive_receipt_path.relative_to(
            self.fixture.root
        ).as_posix()
        g8["evidence"] = [  # type: ignore[index]
            reference
            for reference in g8["evidence"]  # type: ignore[index]
            if reference["path"] != archive_receipt_name
        ]
        self.fixture.rewrite_g8_evidence(g8)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "missing exact version-scoped archive or release metadata evidence",
        ):
            self.fixture.validate()

    def test_g8_generic_decoy_is_not_an_archive_receipt(self) -> None:
        g8 = self.fixture.read_json(self.fixture.receipt_paths["G8"])
        archive_receipt_name = self.fixture.archive_receipt_path.relative_to(
            self.fixture.root
        ).as_posix()
        g8["evidence"] = [  # type: ignore[index]
            reference
            for reference in g8["evidence"]  # type: ignore[index]
            if reference["path"] != archive_receipt_name
        ]
        decoy_path = (
            self.fixture.validation_root
            / "evidence"
            / "generic-g8-decoy.json"
        )
        write_json(decoy_path, {"schema": "okf-generic-g8-evidence.v1"})
        g8["evidence"].append(  # type: ignore[union-attr]
            {
                "path": decoy_path.relative_to(self.fixture.root).as_posix(),
                "sha256": sha256_file(decoy_path),
            }
        )
        self.fixture.rewrite_g8_evidence(g8)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "missing exact version-scoped archive or release metadata evidence",
        ):
            self.fixture.validate()

    def test_g8_rejects_arbitrary_self_consistent_release_metadata(self) -> None:
        provenance = self.fixture.metadata_paths["provenance.json"]
        write_json(
            provenance,
            {
                "schema": "arbitrary-but-self-consistent-provenance.v1",
                "claim": "false metadata must not become valid by rehashing it",
            },
        )
        g8 = self.fixture.read_json(self.fixture.receipt_paths["G8"])
        provenance_name = provenance.relative_to(self.fixture.root).as_posix()
        for reference in g8["evidence"]:  # type: ignore[index]
            if reference["path"] == provenance_name:  # type: ignore[index]
                reference["sha256"] = sha256_file(provenance)  # type: ignore[index]
        self.fixture.rewrite_g8_evidence(g8)

        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "provenance.json does not match exact candidate metadata",
        ):
            self.fixture.validate()

    def test_g8_rejects_release_metadata_alias_paths(self) -> None:
        alias = (
            self.fixture.validation_root
            / "evidence"
            / "alternate"
            / "provenance.json"
        )
        write_json(alias, {"schema": "release-metadata-alias-fixture.v1"})
        g8 = self.fixture.read_json(self.fixture.receipt_paths["G8"])
        g8["evidence"].append(  # type: ignore[union-attr]
            {
                "path": alias.relative_to(self.fixture.root).as_posix(),
                "sha256": sha256_file(alias),
            }
        )
        self.fixture.rewrite_g8_evidence(g8)

        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "G8 release metadata uses an ungoverned path",
        ):
            self.fixture.validate()

    def test_g8_receipt_and_zip_paths_must_match(self) -> None:
        other_archive = (
            self.fixture.root
            / "dist"
            / "okf-landregistry-0.3.0-candidate-b.zip"
        )
        other_archive.write_bytes(self.fixture.archive_path.read_bytes())
        archive_receipt = self.fixture.read_json(
            self.fixture.archive_receipt_path
        )
        archive_receipt["path"] = other_archive.relative_to(
            self.fixture.root
        ).as_posix()
        archive_receipt["bytes"] = other_archive.stat().st_size
        archive_receipt["sha256"] = sha256_file(other_archive)
        self.fixture.rewrite_g8_archive_receipt(archive_receipt)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "path does not match its exact governed archive kind",
        ):
            self.fixture.validate()

    def test_g8_archive_receipt_candidate_must_match(self) -> None:
        archive_receipt = self.fixture.read_json(
            self.fixture.archive_receipt_path
        )
        archive_receipt["candidate"]["release_root_sha256"] = (  # type: ignore[index]
            "0" * 64
        )
        self.fixture.rewrite_g8_archive_receipt(archive_receipt)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "archive receipt does not bind the exact candidate",
        ):
            self.fixture.validate()

    def test_g8_archive_receipt_release_root_must_match(self) -> None:
        archive_receipt = self.fixture.read_json(
            self.fixture.archive_receipt_path
        )
        archive_receipt["release_root_sha256"] = "0" * 64
        self.fixture.rewrite_g8_archive_receipt(archive_receipt)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "archive receipt does not bind the exact release root",
        ):
            self.fixture.validate()

    def test_g8_archive_receipt_version_must_match(self) -> None:
        archive_receipt = self.fixture.read_json(
            self.fixture.archive_receipt_path
        )
        archive_receipt["version"] = "9.9.9"
        self.fixture.rewrite_g8_archive_receipt(archive_receipt)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "archive receipt version does not match the governed version",
        ):
            self.fixture.validate()

    def test_g8_archive_receipt_bytes_must_match(self) -> None:
        archive_receipt = self.fixture.read_json(
            self.fixture.archive_receipt_path
        )
        archive_receipt["bytes"] = self.fixture.archive_path.stat().st_size + 1
        self.fixture.rewrite_g8_archive_receipt(archive_receipt)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "archive receipt byte count does not match the ZIP",
        ):
            self.fixture.validate()

    def test_g8_archive_receipt_hash_must_match(self) -> None:
        archive_receipt = self.fixture.read_json(
            self.fixture.archive_receipt_path
        )
        archive_receipt["sha256"] = "0" * 64
        self.fixture.rewrite_g8_archive_receipt(archive_receipt)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "archive receipt SHA-256 does not match the ZIP",
        ):
            self.fixture.validate()

    def test_g8_archive_receipt_rejects_undeclared_top_level_metadata(
        self,
    ) -> None:
        archive_receipt = self.fixture.read_json(
            self.fixture.archive_receipt_path
        )
        archive_receipt["note"] = "undeclared receipt metadata"
        self.fixture.rewrite_g8_archive_receipt(archive_receipt)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "archive receipt fields differ from the exact governed contract",
        ):
            self.fixture.validate()

    def test_g8_candidate_archive_publication_state_must_match(self) -> None:
        archive_receipt = self.fixture.read_json(
            self.fixture.archive_receipt_path
        )
        archive_receipt["publication_state"] = "released"
        self.fixture.rewrite_g8_archive_receipt(archive_receipt)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "candidate archive receipt does not match the governed publication state",
        ):
            self.fixture.validate()

    def test_g8_candidate_archive_timestamp_must_match(self) -> None:
        archive_receipt = self.fixture.read_json(
            self.fixture.archive_receipt_path
        )
        archive_receipt["candidate_at"] = "2099-01-01T00:00:00Z"
        self.fixture.rewrite_g8_archive_receipt(archive_receipt)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "candidate archive receipt does not match the governed publication state",
        ):
            self.fixture.validate()

    def test_g8_release_archive_requires_governed_release_timestamp(self) -> None:
        archive_receipt = self.fixture.read_json(
            self.fixture.archive_receipt_path
        )
        archive_receipt["schema"] = "okf-hmlr-release-archive.v1"
        archive_receipt.pop("candidate_at")
        archive_receipt.pop("publication_state")
        archive_receipt["release_at"] = NOW
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "release archive receipt does not match the governed release timestamp",
        ):
            release_evidence_checker.validate_archive_receipt_document(
                self.fixture.root,
                archive_receipt,
                expected_candidate=self.fixture.candidate,
                expected_version="0.3.0",
                expected_publication_state="digest-bound-external-evidence",
                expected_generated_at=NOW,
                expected_release_at=None,
                expected_archive_kind="release",
            )

    def test_g8_arbitrary_self_consistent_zip_is_not_candidate_bundle(self) -> None:
        with zipfile.ZipFile(self.fixture.archive_path, "w") as archive:
            write_zip_member(
                archive,
                "okf-landregistry-0.3.0/arbitrary.txt",
                "self-consistent but not the bundle\n",
            )
        self.fixture.rebind_changed_g8_archive()
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "archive member inventory differs from the candidate bundle",
        ):
            self.fixture.validate()

    def test_g8_archive_member_count_and_aggregate_are_bounded(self) -> None:
        with mock.patch.object(
            release_evidence_checker,
            "MAX_ARCHIVE_MEMBERS",
            1,
        ):
            with self.assertRaisesRegex(ReleaseEvidenceError, "member limit"):
                self.fixture.validate()

        with mock.patch.object(
            release_evidence_checker,
            "MAX_ARCHIVE_UNCOMPRESSED_BYTES",
            1,
        ):
            with self.assertRaisesRegex(
                ReleaseEvidenceError,
                "uncompressed-byte limit",
            ):
                self.fixture.validate()

    def test_g8_declared_member_size_is_checked_before_decompression(self) -> None:
        payload = bytearray(self.fixture.archive_path.read_bytes())
        central_offset = payload.index(b"PK\x01\x02")
        declared_size = struct.unpack_from("<L", payload, central_offset + 24)[0]
        struct.pack_into("<L", payload, central_offset + 24, declared_size + 1)
        self.fixture.archive_path.write_bytes(payload)
        self.fixture.rebind_changed_g8_archive()

        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "declared member size differs",
        ):
            self.fixture.validate()

    def test_g8_archive_symlink_member_metadata_fails_closed(self) -> None:
        bundle = self.fixture.root / "bundle"
        with zipfile.ZipFile(self.fixture.archive_path, "w") as archive:
            for path in sorted(bundle.rglob("*")):
                if not path.is_file():
                    continue
                relative_name = path.relative_to(bundle).as_posix()
                write_zip_member(
                    archive,
                    f"okf-landregistry-0.3.0/{relative_name}",
                    path.read_bytes(),
                    unix_mode=(
                        0o120777 if relative_name == "artefact.txt" else 0o100644
                    ),
                )
        self.fixture.rebind_changed_g8_archive()
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "member metadata is not the governed regular-file form",
        ):
            self.fixture.validate()

    def test_g8_archive_rejects_ungoverned_zip_metadata(self) -> None:
        cases = (
            ("archive-comment", b"ungoverned archive comment", None),
            ("member-comment", b"", {"comment": b"ungoverned member comment"}),
            (
                "member-extra",
                b"",
                {"extra": b"\x01\x00\x00\x00"},
            ),
            (
                "external-attribute-low-bit",
                b"",
                {"external_attr": (0o100644 << 16) | 1},
            ),
            ("create-version", b"", {"create_version": 63}),
            ("extract-version", b"", {"extract_version": 63}),
            ("reserved-byte", b"", {"reserved": 1}),
            ("internal-attribute", b"", {"internal_attr": 1}),
        )
        for label, archive_comment, member_metadata in cases:
            with self.subTest(metadata=label):
                self.fixture.rewrite_archive_metadata(
                    archive_comment=archive_comment,
                    member_metadata=member_metadata,
                )
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "archive comment|member metadata",
                ):
                    self.fixture.validate()

    def test_g8_archive_rejects_central_and_local_header_only_metadata(
        self,
    ) -> None:
        cases = (
            ("central-flag", "central", 8, "<H", 0x800),
            ("central-volume", "central", 34, "<H", 1),
            ("local-extract-version", "local", 4, "<B", 21),
            ("local-reserved", "local", 5, "<B", 1),
            ("local-flag", "local", 6, "<H", 0x800),
        )
        for label, header, relative_offset, value_format, replacement in cases:
            with self.subTest(metadata=label):
                self.fixture.rewrite_archive_metadata()
                with zipfile.ZipFile(self.fixture.archive_path) as archive:
                    offset = (
                        archive.start_dir
                        if header == "central"
                        else archive.infolist()[0].header_offset
                    )
                archive_bytes = bytearray(self.fixture.archive_path.read_bytes())
                struct.pack_into(
                    value_format,
                    archive_bytes,
                    offset + relative_offset,
                    replacement,
                )
                self.fixture.archive_path.write_bytes(archive_bytes)
                self.fixture.rebind_changed_g8_archive()
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "member metadata|local header metadata",
                ):
                    self.fixture.validate()

    def test_snapshot_drift_fails_candidate_derivation(self) -> None:
        snapshot = (
            self.fixture.root
            / "source"
            / "snapshots"
            / "fixture"
            / "manifest.json"
        )
        snapshot.write_text('{"schema":"changed"}\n', encoding="utf-8")
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "snapshot digest does not match"
        ):
            self.fixture.validate()


if __name__ == "__main__":
    unittest.main()

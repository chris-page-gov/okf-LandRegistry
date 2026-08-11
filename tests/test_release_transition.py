from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import unicodedata

import scripts.check_release_evidence as release_evidence
import scripts.check_release_transition as transition
from scripts.check_release_transition import (
    ReleaseTransitionError,
    validate_bundle_inventory,
    validate_deployment_identity,
    validate_pr_state,
    validate_remote_binding,
    validate_required_checks,
    validate_staged_candidate,
    validate_staged_evidence,
)


def write_bundle(root: Path, artefacts: dict[str, bytes]) -> str:
    root.mkdir(parents=True)
    lines: list[str] = []
    for name, content in sorted(artefacts.items()):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        lines.append(f"{hashlib.sha256(content).hexdigest()}  {name}")
    manifest = ("\n".join(lines) + "\n").encode("utf-8")
    release_root = hashlib.sha256(manifest).hexdigest()
    (root / "CHECKSUMS.sha256").write_bytes(
        manifest + f"# release-root-sha256: {release_root}\n".encode("ascii")
    )
    return release_root


def git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
    )


def initialise_repository(root: Path, initial: dict[str, bytes] | None = None) -> None:
    git(root, "init", "-q")
    git(root, "config", "user.name", "Release transition test")
    git(root, "config", "user.email", "release-transition@example.invalid")
    files = initial or {"README.md": b"fixture\n"}
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    git(root, "add", "--", ".")
    git(root, "commit", "-qm", "Create fixture")


class BundleInventoryTests(unittest.TestCase):
    def test_live_bundle_fits_the_shared_release_size_policy(self) -> None:
        live_bundle = Path(__file__).resolve().parents[1] / "bundle"
        for name in ("okf-bundle.jsonld", "okf-bundle.yamlld"):
            with self.subTest(name=name):
                artefact_bytes = (live_bundle / name).stat().st_size
                self.assertLessEqual(
                    artefact_bytes,
                    transition.MAX_ARTEFACT_BYTES,
                )
        self.assertEqual(
            release_evidence.MAX_BUNDLE_ARTEFACT_BYTES,
            transition.MAX_ARTEFACT_BYTES,
        )
        self.assertEqual(
            release_evidence.MAX_BUNDLE_CHECKSUM_ENTRIES,
            transition.MAX_MANIFEST_ENTRIES,
        )
        self.assertEqual(
            release_evidence.MAX_BUNDLE_AGGREGATE_BYTES,
            transition.MAX_AGGREGATE_BYTES,
        )
        self.assertRegex(
            validate_bundle_inventory(live_bundle), r"^[0-9a-f]{64}$"
        )

    def test_per_member_ceiling_remains_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            content = b"bounded fixture\n"
            write_bundle(bundle, {"data/example.json": content})
            with (
                mock.patch.object(
                    transition, "MAX_ARTEFACT_BYTES", len(content) - 1
                ),
                self.assertRaisesRegex(
                    ReleaseTransitionError, "artefact exceeds"
                ),
            ):
                validate_bundle_inventory(bundle)

    def test_exact_inventory_returns_release_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            expected = write_bundle(
                bundle,
                {
                    ".nojekyll": b"",
                    ".okf-generated": b"generated\n",
                    "index.html": b"hello\n",
                    "data/example.json": b"{}\n",
                },
            )
            self.assertEqual(expected, validate_bundle_inventory(bundle))
            self.assertEqual(
                expected,
                validate_bundle_inventory(bundle, expected_root=expected),
            )

    def test_ignored_style_extra_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            write_bundle(bundle, {"index.html": b"hello\n"})
            (bundle / "data").mkdir()
            (bundle / "data" / "unreviewed.pyc").write_bytes(b"ignored locally")
            with self.assertRaisesRegex(
                ReleaseTransitionError, "prohibited local artefact|cache or backup"
            ):
                validate_bundle_inventory(bundle)

    def test_checksummed_hidden_cache_and_backup_artefacts_are_rejected(self) -> None:
        for name in (".DS_Store", "pkg/__pycache__/probe.pyc", "backup.bak"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                bundle = Path(temporary) / "bundle"
                write_bundle(bundle, {"index.html": b"hello\n", name: b"probe\n"})
                (bundle / name).chmod(0o755)
                with self.assertRaisesRegex(
                    ReleaseTransitionError,
                    "prohibited local artefact|cache or backup|hidden bundle",
                ):
                    validate_bundle_inventory(bundle)

    def test_missing_and_changed_artefacts_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            write_bundle(bundle, {"index.html": b"hello\n"})
            (bundle / "index.html").write_bytes(b"changed\n")
            with self.assertRaisesRegex(ReleaseTransitionError, "digest mismatch"):
                validate_bundle_inventory(bundle)
            (bundle / "index.html").unlink()
            with self.assertRaisesRegex(ReleaseTransitionError, "missing 'index.html'"):
                validate_bundle_inventory(bundle)

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links unavailable")
    def test_symbolic_link_is_rejected_even_when_checksums_name_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            write_bundle(bundle, {"target.txt": b"target\n", "linked.txt": b"target\n"})
            (bundle / "linked.txt").unlink()
            (bundle / "linked.txt").symlink_to("target.txt")
            with self.assertRaisesRegex(ReleaseTransitionError, "symbolic link"):
                validate_bundle_inventory(bundle)

    def test_inventory_change_during_validation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            write_bundle(bundle, {"index.html": b"hello\n"})
            original_parser = transition._parse_manifest

            def add_late_ignored_file(value: bytes):
                (bundle / "late.txt").write_bytes(b"late local output")
                return original_parser(value)

            with mock.patch.object(
                transition,
                "_parse_manifest",
                side_effect=add_late_ignored_file,
            ):
                with self.assertRaisesRegex(
                    ReleaseTransitionError, "changed during validation"
                ):
                    validate_bundle_inventory(bundle)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs unavailable")
    def test_regular_file_to_fifo_race_fails_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            write_bundle(bundle, {"index.html": b"hello\n"})
            original_parser = transition._parse_manifest

            def replace_artefact_with_fifo(value: bytes):
                (bundle / "index.html").unlink()
                os.mkfifo(bundle / "index.html")
                return original_parser(value)

            with mock.patch.object(
                transition,
                "_parse_manifest",
                side_effect=replace_artefact_with_fifo,
            ):
                with self.assertRaisesRegex(
                    ReleaseTransitionError, "not regular|changed during validation"
                ):
                    validate_bundle_inventory(bundle)

    def test_secure_open_support_is_mandatory(self) -> None:
        for attribute in ("O_NOFOLLOW", "O_NONBLOCK"):
            with (
                self.subTest(attribute=attribute),
                tempfile.TemporaryDirectory() as temporary,
            ):
                bundle = Path(temporary) / "bundle"
                write_bundle(bundle, {"index.html": b"hello\n"})
                with mock.patch.object(transition.os, attribute, None):
                    with self.assertRaisesRegex(ReleaseTransitionError, attribute):
                        validate_bundle_inventory(bundle)


class StagedEvidenceTests(unittest.TestCase):
    def test_exact_v03_additions_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            initialise_repository(repository)
            evidence = (
                repository
                / "validation"
                / "candidate-v0.3.0"
                / "evidence"
                / "result.json"
            )
            evidence.parent.mkdir(parents=True)
            evidence.write_text("{}\n", encoding="utf-8")
            archive = repository / "dist" / "okf-landregistry-0.3.0-candidate-a.zip"
            archive.parent.mkdir()
            archive.write_bytes(b"archive")
            git(repository, "add", "--", str(evidence), str(archive))

            paths = validate_staged_evidence(repository)

            self.assertEqual(
                {
                    b"validation/candidate-v0.3.0/evidence/result.json",
                    b"dist/okf-landregistry-0.3.0-candidate-a.zip",
                },
                set(paths),
            )

    def test_arbitrary_dist_and_historical_validation_are_rejected(self) -> None:
        for name in (
            "dist/okf-landregistry-0.3.0-candidate.zip",
            "validation/receipts/new.json",
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                repository = Path(temporary)
                initialise_repository(repository)
                path = repository / name
                path.parent.mkdir(parents=True)
                path.write_text("{}\n", encoding="utf-8")
                git(repository, "add", "--", name)
                with self.assertRaisesRegex(
                    ReleaseTransitionError, "outside the exact v0.3 evidence surface"
                ):
                    validate_staged_evidence(repository)

    def test_modification_and_deletion_are_rejected(self) -> None:
        evidence_name = "validation/candidate-v0.3.0/evidence/result.json"
        for operation in ("modify", "delete"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as temporary:
                repository = Path(temporary)
                initialise_repository(repository, {evidence_name: b"initial\n"})
                evidence = repository / evidence_name
                if operation == "modify":
                    evidence.write_bytes(b"changed\n")
                    git(repository, "add", "--", evidence_name)
                else:
                    evidence.unlink()
                    git(repository, "add", "-u", "--", evidence_name)
                with self.assertRaisesRegex(ReleaseTransitionError, "add new files only"):
                    validate_staged_evidence(repository)

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links unavailable")
    def test_staged_symbolic_link_mode_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            initialise_repository(repository)
            target = repository / "target.json"
            target.write_text("{}\n", encoding="utf-8")
            link = repository / "validation" / "candidate-v0.3.0" / "link.json"
            link.parent.mkdir(parents=True)
            link.symlink_to(target)
            git(repository, "add", "--", str(link))
            with self.assertRaisesRegex(ReleaseTransitionError, "regular stage-zero"):
                validate_staged_evidence(repository)

    def test_backslash_and_non_utf8_paths_are_rejected_canonically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            initialise_repository(repository)
            backslash_name = (
                "validation/candidate-v0.3.0/evidence\\not-a-directory.json"
            )
            backslash = repository / backslash_name
            backslash.parent.mkdir(parents=True)
            backslash.write_text("{}\n", encoding="utf-8")
            git(repository, "add", "--", backslash_name)
            with self.assertRaisesRegex(ReleaseTransitionError, "unsafe staged"):
                validate_staged_evidence(repository)

        with self.assertRaisesRegex(ReleaseTransitionError, "not valid UTF-8"):
            transition._canonical_staged_path(
                b"validation/candidate-v0.3.0/evidence/non-utf8-\xff.json"
            )
        with self.assertRaisesRegex(ReleaseTransitionError, "unsafe staged"):
            transition._canonical_staged_path(
                b"validation/candidate-v0.3.0/./evidence.json"
            )

    def test_non_printable_non_nfc_hidden_and_temporary_paths_are_rejected(
        self,
    ) -> None:
        decomposed = unicodedata.normalize("NFD", "café")
        invalid = (
            "validation/candidate-v0.3.0/evidence/bad\nname.json",
            f"validation/candidate-v0.3.0/evidence/{decomposed}.json",
            "validation/candidate-v0.3.0/.release-metadata-stage/result.json",
            "validation/candidate-v0.3.0/evidence/result.json.tmp",
            "validation/candidate-v0.3.0/evidence/~result.json",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ReleaseTransitionError):
                    transition._canonical_staged_path(value.encode("utf-8"))

    def test_actual_staged_cache_and_backup_additions_are_rejected(self) -> None:
        for name in (
            "validation/candidate-v0.3.0/evidence/probe.pyc",
            "validation/candidate-v0.3.0/evidence/probe.json.bak",
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                repository = Path(temporary)
                initialise_repository(repository)
                path = repository / name
                path.parent.mkdir(parents=True)
                path.write_bytes(b"probe\n")
                path.chmod(0o755)
                git(repository, "add", "-f", "--", name)
                with self.assertRaisesRegex(
                    ReleaseTransitionError,
                    "prohibited local artefact|cache or backup",
                ):
                    validate_staged_evidence(repository)

    def test_staged_blob_and_archive_size_limits_fail_closed(self) -> None:
        fixtures = (
            (
                b"validation/candidate-v0.3.0/evidence/oversize.json",
                transition.MAX_STAGED_BLOB_BYTES + 1,
            ),
            (
                b"dist/okf-landregistry-0.3.0-candidate-a.zip",
                transition.MAX_STAGED_ARCHIVE_BYTES + 1,
            ),
        )
        for path, size in fixtures:
            with self.subTest(path=path), mock.patch.object(
                transition,
                "_git_output",
                return_value=(
                    b":000000 100644 "
                    + b"0" * 40
                    + b" "
                    + b"a" * 40
                    + b" A\0"
                    + path
                    + b"\0"
                ),
            ), mock.patch.object(
                transition,
                "_staged_blob_sizes",
                return_value=[size],
            ), mock.patch.object(
                transition,
                "_git",
                return_value=subprocess.CompletedProcess([], 0, b"", b""),
            ):
                with self.assertRaisesRegex(ReleaseTransitionError, "blob limit"):
                    validate_staged_evidence(Path("."))

    def test_staged_aggregate_limit_fails_closed(self) -> None:
        first = b"validation/candidate-v0.3.0/evidence/a.json"
        second = b"validation/candidate-v0.3.0/evidence/b.json"
        def record(oid: bytes, path: bytes) -> bytes:
            return (
                b":000000 100644 "
                + b"0" * 40
                + b" "
                + oid
                + b" A\0"
                + path
                + b"\0"
            )
        with mock.patch.object(
            transition,
            "MAX_STAGED_AGGREGATE_BYTES",
            15,
        ), mock.patch.object(
            transition,
            "_git_output",
            return_value=record(b"a" * 40, first) + record(b"b" * 40, second),
        ), mock.patch.object(
            transition,
            "_staged_blob_sizes",
            return_value=[8, 8],
        ), mock.patch.object(
            transition,
            "_git",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ):
            with self.assertRaisesRegex(ReleaseTransitionError, "aggregate limit"):
                validate_staged_evidence(Path("."))


class StagedCandidateTests(unittest.TestCase):
    def test_governed_hidden_paths_and_regular_executables_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            initialise_repository(
                repository,
                {
                    ".github/workflows/pages.yml": b"name: fixture\n",
                    ".gitignore": b"*.pyc\n",
                    "scripts/check.py": b"#!/usr/bin/env python3\n",
                },
            )
            (repository / "scripts" / "check.py").chmod(0o755)
            git(repository, "add", "--chmod=+x", "--", "scripts/check.py")
            paths = validate_staged_candidate(repository)
            self.assertIn(b".github/workflows/pages.yml", paths)
            self.assertIn(b".gitignore", paths)
            self.assertIn(b"scripts/check.py", paths)

    def test_forced_hidden_cache_and_backup_paths_fail_even_when_executable(self) -> None:
        for name in (".DS_Store", "pkg/__pycache__/probe.pyc", "backup.bak"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                repository = Path(temporary)
                initialise_repository(repository)
                path = repository / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"probe\n")
                path.chmod(0o755)
                git(repository, "add", "-f", "--chmod=+x", "--", name)
                with self.assertRaisesRegex(
                    ReleaseTransitionError,
                    "prohibited local artefact|cache or backup",
                ):
                    validate_staged_candidate(repository)


class BoundedGitTests(unittest.TestCase):
    def test_git_stdout_and_stderr_floods_are_stopped_in_flight(self) -> None:
        real_popen = subprocess.Popen
        for stream_number, limit_name, expected_message in (
            (1, "MAX_GIT_STDOUT_BYTES", "Git output exceeds 8 bytes"),
            (2, "MAX_GIT_STDERR_BYTES", "Git diagnostic output exceeds 8 bytes"),
        ):
            with self.subTest(stream_number=stream_number):
                spawned: list[subprocess.Popen[bytes]] = []

                def flood(_command, **kwargs):
                    process = real_popen(
                        [
                            sys.executable,
                            "-B",
                            "-c",
                            (
                                "import os,time;"
                                f"os.write({stream_number}, b'x' * 65536);"
                                "time.sleep(10)"
                            ),
                        ],
                        **kwargs,
                    )
                    spawned.append(process)
                    return process

                started = time.monotonic()
                with (
                    mock.patch.object(transition, limit_name, 8),
                    mock.patch.object(transition.subprocess, "Popen", flood),
                ):
                    with self.assertRaisesRegex(
                        ReleaseTransitionError,
                        expected_message,
                    ):
                        transition._git(Path("."), ["status"])
                self.assertLess(time.monotonic() - started, 2)
                self.assertEqual(1, len(spawned))
                self.assertIsNotNone(spawned[0].poll())

    def test_git_timeout_fails_closed(self) -> None:
        real_popen = subprocess.Popen
        spawned: list[subprocess.Popen[bytes]] = []

        def hang(_command, **kwargs):
            process = real_popen(
                [sys.executable, "-B", "-c", "import time; time.sleep(10)"],
                **kwargs,
            )
            spawned.append(process)
            return process

        started = time.monotonic()
        with (
            mock.patch.object(transition, "GIT_TIMEOUT_SECONDS", 0.05),
            mock.patch.object(transition.subprocess, "Popen", hang),
        ):
            with self.assertRaisesRegex(ReleaseTransitionError, "time limit"):
                transition._git(Path("."), ["status"])
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(1, len(spawned))
        self.assertIsNotNone(spawned[0].poll())


class PullRequestStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence_sha = "a" * 40
        self.document = {
            "state": "OPEN",
            "isCrossRepository": False,
            "headRepository": {
                "nameWithOwner": transition.EXPECTED_GITHUB_REPOSITORY
            },
            "baseRefName": "main",
            "headRefName": "candidate/v0.3.0",
            "headRefOid": self.evidence_sha,
            "reviewDecision": "APPROVED",
        }

    def validate(self, document: dict[str, object]) -> None:
        validate_pr_state(
            document,
            expected_head_oid=self.evidence_sha,
            required_review_decision="APPROVED",
        )

    def test_exact_open_approved_pull_request_passes(self) -> None:
        self.validate(self.document)

    def test_each_identity_or_state_mismatch_is_rejected(self) -> None:
        mutations = {
            "state": "MERGED",
            "isCrossRepository": True,
            "headRepository": {"nameWithOwner": "someone/okf-LandRegistry"},
            "baseRefName": "release",
            "headRefName": "candidate/other",
            "headRefOid": "b" * 40,
            "reviewDecision": "REVIEW_REQUIRED",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                document = json.loads(json.dumps(self.document))
                document[field] = value
                with self.assertRaises(ReleaseTransitionError):
                    self.validate(document)

    def test_non_github_length_object_ids_are_rejected(self) -> None:
        for length in (39, 41, 64):
            with self.subTest(length=length):
                oid = "a" * length
                document = dict(self.document, headRefOid=oid)
                with self.assertRaisesRegex(ReleaseTransitionError, "40-character"):
                    validate_pr_state(
                        document,
                        expected_head_oid=oid,
                        required_review_decision="APPROVED",
                    )


class RequiredChecksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence_sha = "a" * 40
        self.workflow_id = 101
        self.run_id = 202
        self.job_id = 303
        self.expected = {
            "name": transition.EXPECTED_REQUIRED_CHECK_NAME,
            "workflow": transition.EXPECTED_REQUIRED_CHECK_WORKFLOW,
            "event": transition.EXPECTED_REQUIRED_CHECK_EVENT,
            "state": "SUCCESS",
            "bucket": "pass",
            "link": (
                "https://github.com/chris-page-gov/okf-LandRegistry/actions/"
                f"runs/{self.run_id}/job/{self.job_id}?pr=77"
            ),
        }
        self.workflow = {
            "id": self.workflow_id,
            "name": transition.EXPECTED_REQUIRED_CHECK_WORKFLOW,
            "path": transition.EXPECTED_WORKFLOW_PATH,
            "state": "active",
            "url": (
                "https://api.github.com/repos/chris-page-gov/okf-LandRegistry/"
                f"actions/workflows/{self.workflow_id}"
            ),
        }
        self.workflow_run = {
            "id": self.run_id,
            "workflow_id": self.workflow_id,
            "path": transition.EXPECTED_WORKFLOW_PATH,
            "repository": {"full_name": transition.EXPECTED_GITHUB_REPOSITORY},
            "event": "pull_request",
            "head_sha": self.evidence_sha,
            "status": "completed",
            "conclusion": "success",
            "html_url": (
                "https://github.com/chris-page-gov/okf-LandRegistry/actions/"
                f"runs/{self.run_id}"
            ),
        }
        self.workflow_jobs = {
            "total_count": 2,
            "jobs": [
                {
                    "id": self.job_id,
                    "run_id": self.run_id,
                    "name": "verify",
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": (
                        "https://github.com/chris-page-gov/okf-LandRegistry/"
                        f"actions/runs/{self.run_id}/job/{self.job_id}"
                    ),
                },
                {
                    "id": 304,
                    "run_id": self.run_id,
                    "name": "deploy",
                    "status": "completed",
                    "conclusion": "skipped",
                    "html_url": (
                        "https://github.com/chris-page-gov/okf-LandRegistry/"
                        f"actions/runs/{self.run_id}/job/304"
                    ),
                },
            ],
        }

    def validate(
        self,
        checks: list[dict[str, object]],
        *,
        workflow: dict[str, object] | None = None,
        workflow_run: dict[str, object] | None = None,
        workflow_jobs: dict[str, object] | None = None,
    ) -> None:
        validate_required_checks(
            checks,
            workflow=workflow or self.workflow,
            workflow_run=workflow_run or self.workflow_run,
            workflow_jobs=workflow_jobs or self.workflow_jobs,
            expected_head_oid=self.evidence_sha,
        )

    def test_exact_required_release_check_once_passes(self) -> None:
        self.validate(
            [
                self.expected,
                {
                    "name": "other",
                    "workflow": "Other workflow",
                    "event": "pull_request",
                    "state": "SUCCESS",
                    "bucket": "pass",
                },
            ]
        )

    def test_missing_duplicate_spoofed_or_nonpassing_check_is_rejected(self) -> None:
        fixtures = (
            [],
            [dict(self.expected, workflow="Spoofed workflow")],
            [self.expected, dict(self.expected)],
            [dict(self.expected, bucket="fail", state="FAILURE")],
            [self.expected, {"name": "other", "state": "FAILURE", "bucket": "fail"}],
        )
        for value in fixtures:
            with self.subTest(value=value):
                with self.assertRaises(ReleaseTransitionError):
                    self.validate(value)

    def test_same_display_name_from_different_workflow_path_is_rejected(self) -> None:
        workflow = dict(self.workflow, path=".github/workflows/spoof.yml")
        with self.assertRaisesRegex(ReleaseTransitionError, "canonical path"):
            self.validate([self.expected], workflow=workflow)

    def test_workflow_run_for_wrong_head_is_rejected(self) -> None:
        run = dict(self.workflow_run, head_sha="b" * 40)
        with self.assertRaisesRegex(ReleaseTransitionError, "head SHA"):
            self.validate([self.expected], workflow_run=run)

    def test_link_job_and_authoritative_job_must_match_exactly_once(self) -> None:
        mismatched = dict(
            self.expected,
            link=(
                "https://github.com/chris-page-gov/okf-LandRegistry/actions/"
                f"runs/{self.run_id}/job/999"
            ),
        )
        with self.assertRaisesRegex(ReleaseTransitionError, "job ID differ"):
            self.validate([mismatched])

        jobs = json.loads(json.dumps(self.workflow_jobs))
        jobs["jobs"].append(dict(jobs["jobs"][0], id=305))
        jobs["total_count"] = 3
        with self.assertRaisesRegex(ReleaseTransitionError, "exactly once"):
            self.validate([self.expected], workflow_jobs=jobs)


class RemoteBindingTests(unittest.TestCase):
    def test_exact_credential_free_fetch_and_push_urls_pass(self) -> None:
        for url in (
            "https://github.com/chris-page-gov/okf-LandRegistry.git",
            "git@github.com:chris-page-gov/okf-LandRegistry.git",
            "ssh://git@github.com/chris-page-gov/okf-LandRegistry.git",
        ):
            with self.subTest(url=url), tempfile.TemporaryDirectory() as temporary:
                repository = Path(temporary)
                initialise_repository(repository)
                git(repository, "remote", "add", "release", url)
                git(repository, "remote", "set-url", "--push", "release", url)
                validate_remote_binding(repository, "release")

    def test_local_credentialled_mismatched_and_multiple_destinations_fail(
        self,
    ) -> None:
        fixtures = (
            "/tmp/okf-LandRegistry",
            "https://token@github.com/chris-page-gov/okf-LandRegistry.git",
            "https://github.com/chris-page-gov/another-repository.git",
        )
        for url in fixtures:
            with self.subTest(url=url), tempfile.TemporaryDirectory() as temporary:
                repository = Path(temporary)
                initialise_repository(repository)
                git(repository, "remote", "add", "release", url)
                with self.assertRaisesRegex(
                    ReleaseTransitionError, "canonical"
                ) as caught:
                    validate_remote_binding(repository, "release")
                self.assertNotIn(url, str(caught.exception))

        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            initialise_repository(repository)
            canonical = "https://github.com/chris-page-gov/okf-LandRegistry.git"
            git(repository, "remote", "add", "release", canonical)
            git(repository, "config", "--add", "remote.release.pushurl", canonical)
            git(
                repository,
                "config",
                "--add",
                "remote.release.pushurl",
                "git@github.com:chris-page-gov/okf-LandRegistry.git",
            )
            with self.assertRaisesRegex(ReleaseTransitionError, "exactly one"):
                validate_remote_binding(repository, "release")


class DeploymentIdentityTests(unittest.TestCase):
    def test_exact_commit_and_release_root_pass(self) -> None:
        validate_deployment_identity(
            github_sha="a" * 40,
            expected_commit_sha="a" * 40,
            remote_default_sha="a" * 40,
            input_root="b" * 64,
            approved_root="b" * 64,
        )

    def test_commit_or_root_mismatch_fails(self) -> None:
        fixtures = (
            ("a" * 40, "c" * 40, "a" * 40, "b" * 64, "b" * 64),
            ("a" * 40, "a" * 40, "c" * 40, "b" * 64, "b" * 64),
            ("a" * 40, "a" * 40, "a" * 40, "b" * 64, "c" * 64),
        )
        for github_sha, expected_sha, remote_sha, input_root, approved_root in fixtures:
            with self.subTest(expected_sha=expected_sha, approved_root=approved_root):
                with self.assertRaises(ReleaseTransitionError):
                    validate_deployment_identity(
                        github_sha=github_sha,
                        expected_commit_sha=expected_sha,
                        remote_default_sha=remote_sha,
                        input_root=input_root,
                        approved_root=approved_root,
                    )


if __name__ == "__main__":
    unittest.main()

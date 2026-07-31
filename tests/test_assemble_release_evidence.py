from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts.assemble_release_evidence import (
    assemble_pre_g9_evidence,
    assemble_release_evidence,
)
from scripts.check_release_evidence import (
    GATE_RECEIPTS,
    REVIEWED_GATES,
    REQUIRED_CHECKS,
    ReleaseEvidenceError,
    candidate_identity_from_repository,
    sha256_file,
    validate_release_evidence,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-07-29T12:00:00Z"


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def digest_line(path: Path, name: str) -> str:
    return f"{sha256_file(path)}  {name}"


class AssemblyFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.input_path = self.root / "release-input.json"
        self._write_candidate()
        self._initialise_repository()
        self._write_attestations()

    def git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )

    def _write_candidate(self) -> None:
        profile = self.root / "domain-profile" / "profile.json"
        profile.parent.mkdir(parents=True)
        profile.write_text('{"schema":"fixture"}\n', encoding="utf-8")
        profile_line = digest_line(profile, "profile.json")
        profile_root = hashlib.sha256(
            f"{profile_line}\n".encode("utf-8")
        ).hexdigest()
        (profile.parent / "CHECKSUMS.sha256").write_text(
            f"{profile_line}\n# pack-root-sha256: {profile_root}\n",
            encoding="utf-8",
        )

        snapshot = (
            self.root / "source" / "snapshots" / "fixture" / "manifest.json"
        )
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text(
            '{"schema":"fixture-snapshot"}\n', encoding="utf-8"
        )

        bundle = self.root / "bundle"
        bundle.mkdir()
        artifact = bundle / "artifact.txt"
        artifact.write_text("candidate artifact\n", encoding="utf-8")
        build_receipt = bundle / "build-receipt.json"
        write_json(
            build_receipt,
            {
                "schema": "fixture-build-receipt",
                "domain_profile_pack_root_sha256": profile_root,
                "snapshot": {
                    "manifest_path": (
                        "source/snapshots/fixture/manifest.json"
                    ),
                    "source_manifest_sha256": sha256_file(snapshot),
                },
                "governed_inputs": [
                    {
                        "path": "domain-profile/profile.json",
                        "sha256": sha256_file(profile),
                    },
                    {
                        "path": "source/snapshots/fixture/manifest.json",
                        "sha256": sha256_file(snapshot),
                    },
                ],
            },
        )
        bundle_lines = [
            digest_line(artifact, "artifact.txt"),
            digest_line(build_receipt, "build-receipt.json"),
        ]
        release_root = hashlib.sha256(
            ("\n".join(bundle_lines) + "\n").encode("utf-8")
        ).hexdigest()
        (bundle / "CHECKSUMS.sha256").write_text(
            "\n".join(
                [
                    *bundle_lines,
                    f"# release-root-sha256: {release_root}",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        schema = self.root / "schemas" / "release-evidence.schema.json"
        schema.parent.mkdir()
        shutil.copyfile(
            ROOT / "schemas" / "release-evidence.schema.json", schema
        )
        validator = self.root / "scripts" / "fixture-validator.txt"
        validator.parent.mkdir()
        validator.write_text("fixture validator 1.0.0\n", encoding="utf-8")

    def _initialise_repository(self) -> None:
        self.git("init")
        self.git("config", "user.name", "Assembly Fixture")
        self.git("config", "user.email", "fixture@example.test")
        self.git("add", ".")
        self.git("commit", "-m", "Add governed candidate")
        self.candidate_commit_sha = self.git(
            "rev-parse", "HEAD"
        ).stdout.strip()

    def _gate_input(self, gate: str) -> dict[str, object]:
        reviewers: list[dict[str, object]] = []
        reviewed_checks: dict[str, object] = {}
        mode = "automated"
        if gate in REVIEWED_GATES:
            identity = f"independent-{gate.lower()}"
            reviewers.append(
                {
                    "identity": identity,
                    "kind": "ai-agent",
                    "role": f"{gate.lower()}-reviewer",
                    "reviewed_at": NOW,
                    "independent": True,
                }
            )
            reviewed_checks[f"{gate.lower()}-review"] = {
                "status": "pass",
                "reviewer_identity": identity,
                "completed_at": NOW,
                "execution_mode": "automated-agent",
            }
            mode = "automated-agent-review"
        return {
            "status": "pass",
            "executed_at": NOW,
            "validator": {
                "name": "fixture-validator",
                "version": "1.0.0",
                "artifact_path": "scripts/fixture-validator.txt",
                "command": ["fixture-validator", gate],
            },
            "checks": {
                check_id: {
                    "status": "pass",
                    "summary": f"{check_id} passed in fixture evidence",
                }
                for check_id in sorted(REQUIRED_CHECKS[gate])
            },
            "evidence": [f"evidence/{gate.lower()}.txt"],
            "failures": [],
            "waivers": [],
            "review": {"mode": mode, "reviewers": reviewers},
            "reviewed_checks": reviewed_checks,
        }

    def _write_attestations(self) -> None:
        for gate in GATE_RECEIPTS:
            evidence = self.root / "evidence" / f"{gate.lower()}.txt"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text(
                f"pre-existing passed check evidence for {gate}\n",
                encoding="utf-8",
            )
        write_json(
            self.input_path,
            {
                "schema": "okf-release-assembly-input.v1",
                "generated_at": NOW,
                "gates": {
                    gate: self._gate_input(gate)
                    for gate in GATE_RECEIPTS
                },
                "release": {
                    "status": "approved",
                    "version": "0.1.0",
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
                            "Independent AI-agent review is recorded; a "
                            "human audit was not completed."
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
                        "identity": "fixture-release-agent",
                        "kind": "ai-agent",
                        "role": "release-reviewer",
                        "reviewed_at": NOW,
                        "independent": True,
                        "outcome": "recommend_approval",
                    },
                },
            },
        )

    def read_input(self) -> dict[str, object]:
        return json.loads(self.input_path.read_text(encoding="utf-8"))

    def rewrite_input(self, value: dict[str, object]) -> None:
        write_json(self.input_path, value)

    def assemble(self, *, replace: bool = False) -> None:
        assemble_release_evidence(
            self.root,
            input_path=Path("release-input.json"),
            candidate_commit_sha=self.candidate_commit_sha,
            replace=replace,
        )


class AssembleReleaseEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = AssemblyFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_complete_explicit_input_produces_valid_exact_evidence(self) -> None:
        self.fixture.assemble()
        candidate = candidate_identity_from_repository(
            self.fixture.root,
            checksums_path=Path("bundle/CHECKSUMS.sha256"),
            profile_checksums_path=Path(
                "domain-profile/CHECKSUMS.sha256"
            ),
            build_receipt_path=Path("bundle/build-receipt.json"),
            candidate_commit_sha=self.fixture.candidate_commit_sha,
        )
        validated = validate_release_evidence(
            self.fixture.root,
            manifest_path=Path("validation/release-evidence.json"),
            schema_path=(
                self.fixture.root
                / "schemas"
                / "release-evidence.schema.json"
            ),
            expected_candidate=candidate,
        )
        self.assertEqual(candidate, validated)

        g1 = json.loads(
            (
                self.fixture.root / "validation" / "receipts" / "g1.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            sha256_file(
                self.fixture.root / "scripts" / "fixture-validator.txt"
            ),
            g1["validator"]["sha256"],
        )
        release = json.loads(
            (
                self.fixture.root / "validation" / "release-record.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual("ai-agent", release["independent_review"]["kind"])
        self.assertEqual("not_completed", release["human_audit"]["status"])
        self.assertIn(
            release["human_audit"]["residual_risk_id"],
            release["residual_risk_ids"],
        )
        self.assertEqual(
            asdict(candidate),
            release["candidate"],
        )

    def test_pre_g9_assembly_produces_only_exact_g1_g8_receipts(self) -> None:
        document = self.fixture.read_input()
        document.pop("release")
        document["schema"] = "okf-pre-g9-assembly-input.v1"
        self.fixture.rewrite_input(document)

        assemble_pre_g9_evidence(
            self.fixture.root,
            input_path=Path("release-input.json"),
            candidate_commit_sha=self.fixture.candidate_commit_sha,
        )

        output = self.fixture.root / "validation" / "pre-g9"
        manifest = json.loads(
            (output / "pre-g9-evidence.json").read_text(encoding="utf-8")
        )
        self.assertEqual("ready_for_owner_review", manifest["status"])
        self.assertEqual(
            list(GATE_RECEIPTS),
            [receipt["gate"] for receipt in manifest["receipts"]],
        )
        self.assertFalse((output / "release-record.json").exists())
        self.assertFalse((output / "release-evidence.json").exists())
        for reference in manifest["receipts"]:
            receipt = self.fixture.root / reference["path"]
            self.assertEqual(reference["sha256"], sha256_file(receipt))

    def test_pre_g9_receipts_equal_later_release_receipts(self) -> None:
        full_input = self.fixture.read_input()
        pre_g9_input = dict(full_input)
        pre_g9_input.pop("release")
        pre_g9_input["schema"] = "okf-pre-g9-assembly-input.v1"
        self.fixture.rewrite_input(pre_g9_input)
        assemble_pre_g9_evidence(
            self.fixture.root,
            input_path=Path("release-input.json"),
            candidate_commit_sha=self.fixture.candidate_commit_sha,
        )
        pre_g9_receipts = {
            gate: (
                self.fixture.root
                / "validation"
                / "pre-g9"
                / "receipts"
                / f"{gate.lower()}.json"
            ).read_bytes()
            for gate in GATE_RECEIPTS
        }

        self.fixture.rewrite_input(full_input)
        assemble_release_evidence(
            self.fixture.root,
            input_path=Path("release-input.json"),
            candidate_commit_sha=self.fixture.candidate_commit_sha,
            output_directory=Path("validation/final"),
        )
        final_receipts = {
            gate: (
                self.fixture.root
                / "validation"
                / "final"
                / "receipts"
                / f"{gate.lower()}.json"
            ).read_bytes()
            for gate in GATE_RECEIPTS
        }
        self.assertEqual(pre_g9_receipts, final_receipts)

    def test_non_pass_check_fails_before_writing_receipts(self) -> None:
        document = self.fixture.read_input()
        document["gates"]["G4"]["checks"]["routes-valid"]["status"] = "fail"
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "input check 'routes-valid' is not pass"
        ):
            self.fixture.assemble()
        self.assertFalse(
            (
                self.fixture.root
                / "validation"
                / "release-evidence.json"
            ).exists()
        )

    def test_missing_gate_fails_closed(self) -> None:
        document = self.fixture.read_input()
        del document["gates"]["G8"]
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "must map exactly G1-G8"
        ):
            self.fixture.assemble()

    def test_duplicate_gate_json_key_fails_closed(self) -> None:
        text = self.input_text()
        marker = '"gates": {'
        replacement = '"gates": {"G1": {},'
        self.fixture.input_path.write_text(
            text.replace(marker, replacement, 1),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "duplicate JSON key: 'G1'"
        ):
            self.fixture.assemble()

    def input_text(self) -> str:
        return self.fixture.input_path.read_text(encoding="utf-8")

    def test_unsafe_evidence_path_fails_closed(self) -> None:
        document = self.fixture.read_input()
        document["gates"]["G2"]["evidence"] = ["../outside.json"]
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "unsafe G2 evidence path"
        ):
            self.fixture.assemble()

    def test_reviewed_check_reviewer_must_be_declared(self) -> None:
        document = self.fixture.read_input()
        reviewed = document["gates"]["G5"]["reviewed_checks"]["g5-review"]
        reviewed["reviewer_identity"] = "some-other-agent"
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "names undeclared reviewer"
        ):
            self.fixture.assemble()

    def test_owner_cannot_be_independent_release_reviewer(self) -> None:
        document = self.fixture.read_input()
        document["release"]["independent_review"]["identity"] = (
            "fixture-owner"
        )
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "project owner identities match"
        ):
            self.fixture.assemble()

    def test_human_audit_cannot_be_silently_marked_complete(self) -> None:
        document = self.fixture.read_input()
        document["release"]["human_audit"]["status"] = "completed"
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "requires human audit status 'not_completed'"
        ):
            self.fixture.assemble()

    def test_not_completed_human_audit_must_remain_a_risk(self) -> None:
        document = self.fixture.read_input()
        document["release"]["residual_risk_ids"].remove(
            "RISK-HUMAN-AUDIT"
        )
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "not retained"
        ):
            self.fixture.assemble()

    def test_explicit_replace_is_deterministic(self) -> None:
        self.fixture.assemble()
        paths = sorted(
            (
                self.fixture.root / "validation"
            ).glob("**/*.json")
        )
        first = {
            path.relative_to(self.fixture.root).as_posix(): path.read_bytes()
            for path in paths
        }
        self.fixture.assemble(replace=True)
        second = {
            path.relative_to(self.fixture.root).as_posix(): path.read_bytes()
            for path in paths
        }
        self.assertEqual(first, second)

    def test_output_directory_may_not_escape_repository(self) -> None:
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "unsafe output directory"
        ):
            assemble_release_evidence(
                self.fixture.root,
                input_path=Path("release-input.json"),
                candidate_commit_sha=self.fixture.candidate_commit_sha,
                output_directory=Path("../validation"),
            )

    def test_symbolic_link_receipt_directory_fails_closed(self) -> None:
        outside = self.fixture.root / "outside"
        outside.mkdir()
        validation = self.fixture.root / "validation"
        validation.mkdir()
        (validation / "receipts").symlink_to(
            outside, target_is_directory=True
        )
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "symbolic-link|escapes repository"
        ):
            self.fixture.assemble()


if __name__ == "__main__":
    unittest.main()

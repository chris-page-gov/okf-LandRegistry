from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.check_release_evidence import (
    REVIEWED_GATES,
    REQUIRED_CHECKS,
    CandidateIdentity,
    ReleaseEvidenceError,
    candidate_identity_from_repository,
    sha256_file,
    validate_governed_candidate_commit,
    validate_release_evidence,
)


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
        self.git("add", "validation")
        self.git("commit", "-m", "Add fixture release evidence")
        self.evidence_commit_sha = self.git("rev-parse", "HEAD").stdout.strip()

    @property
    def manifest_path(self) -> Path:
        return self.root / "validation" / "release-evidence.json"

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
        self.git("add", "bundle", "domain-profile", "source")
        self.git("commit", "-m", "Add governed candidate")
        self.candidate_commit_sha = self.git("rev-parse", "HEAD").stdout.strip()

    def _write_candidate_materials(self) -> None:
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

        bundle = self.root / "bundle"
        bundle.mkdir()
        artifact = bundle / "artifact.txt"
        artifact.write_text("governed candidate\n", encoding="utf-8")
        build_receipt = bundle / "build-receipt.json"
        write_json(
            build_receipt,
            {
                "schema": "okf-test-build-receipt.v1",
                "domain_profile_pack_root_sha256": profile_root,
                "snapshot": {
                    "manifest_path": (
                        "source/snapshots/fixture/manifest.json"
                    ),
                    "source_manifest_sha256": snapshot_digest,
                },
                "governed_inputs": [
                    {
                        "path": "domain-profile/profile.json",
                        "sha256": sha256_file(profile_file),
                    },
                    {
                        "path": "source/snapshots/fixture/manifest.json",
                        "sha256": snapshot_digest,
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
        for number in range(1, 9):
            gate = f"G{number}"
            evidence_path = (
                self.root / "validation" / "evidence" / f"{gate.lower()}.txt"
            )
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text(
                f"digest-bound supporting evidence for {gate}\n",
                encoding="utf-8",
            )
            receipt_path = (
                self.root / "validation" / "receipts" / f"{gate.lower()}.json"
            )
            write_json(receipt_path, self._gate_receipt(gate, evidence_path))
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
            "version": "1.0.0",
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
        release_path = self.root / "validation" / "release-record.json"
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
            manifest_path=Path("validation/release-evidence.json"),
            schema_path=SCHEMA,
            expected_candidate=derived,
        )


class ReleaseEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = EvidenceFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_complete_exact_candidate_evidence_passes(self) -> None:
        self.assertEqual(self.fixture.candidate, self.fixture.validate())

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

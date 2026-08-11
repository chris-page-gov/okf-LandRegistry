from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

from scripts.assemble_release_evidence import (
    assemble_pre_g9_evidence,
    assemble_release_evidence,
)
from scripts.check_release_evidence import (
    GATE_RECEIPTS,
    REVIEWED_GATES,
    REQUIRED_CHECKS,
    SCHEMA_ID,
    ReleaseEvidenceError,
    candidate_identity_from_repository,
    sha256_file,
    validate_release_evidence,
)
from scripts.create_release_metadata import expected_release_metadata_documents


ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-07-29T12:00:00Z"
PRE_G9_OUTPUT = Path("validation/candidate-v0.3.0/pre-g9")
FINAL_G9_OUTPUT = Path("validation/candidate-v0.3.0/final-g9")


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def digest_line(path: Path, name: str) -> str:
    return f"{sha256_file(path)}  {name}"


def write_zip_member(
    archive: zipfile.ZipFile, name: str, value: bytes
) -> None:
    info = zipfile.ZipInfo(name, date_time=(2026, 7, 29, 12, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (0o100644 & 0xFFFF) << 16
    archive.writestr(info, value, compresslevel=9)


class AssemblyFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.input_path = (
            self.root
            / "validation"
            / "candidate-v0.3.0"
            / "evidence"
            / "assembly-inputs"
            / "release-input.json"
        )
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

        metadata_inputs = {
            "requirements-lock.txt": (
                "fixture-package==1.0 \\\n"
                "    --hash=sha256:" + "0" * 64 + "\n"
            ),
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
            "scripts/build.py": "# exact fixture builder\n",
            "contracts/okf-explorer.consumer-lock.json": "{}\n",
            "pages/search-contract.json": "{}\n",
            "evaluation/questions.json": "{}\n",
        }
        for relative_name, content in metadata_inputs.items():
            path = self.root / relative_name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        bundle = self.root / "bundle"
        bundle.mkdir()
        artefact = bundle / "artefact.txt"
        artefact.write_text("candidate artefact\n", encoding="utf-8")
        build_receipt = bundle / "build-receipt.json"
        write_json(
            build_receipt,
            {
                "schema": "fixture-build-receipt",
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
                    "source_manifest_sha256": sha256_file(snapshot),
                    "acquisition_snapshot": {
                        "manifest_path": (
                            "source/snapshots/fixture/manifest.json"
                        ),
                        "source_manifest_sha256": sha256_file(snapshot),
                    },
                },
                "governed_inputs": [
                    {
                        "path": "domain-profile/profile.json",
                        "bytes": len(profile.read_bytes()),
                        "sha256": sha256_file(profile),
                    },
                    {
                        "path": "source/snapshots/fixture/manifest.json",
                        "bytes": len(snapshot.read_bytes()),
                        "sha256": sha256_file(snapshot),
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
        validator.parent.mkdir(exist_ok=True)
        validator.write_text("fixture validator 1.0.0\n", encoding="utf-8")

        historical_paths = [
            self.root / "validation" / "release-evidence.json",
            self.root / "validation" / "release-record.json",
            *[
                self.root
                / "validation"
                / "receipts"
                / f"{gate.lower()}.json"
                for gate in GATE_RECEIPTS
            ],
        ]
        for path in historical_paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(
                f"frozen historical evidence: {path.name}\n".encode("utf-8")
            )

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
            "evidence": [
                "validation/candidate-v0.3.0/evidence/source/"
                f"{gate.lower()}.txt"
            ],
            "failures": [],
            "waivers": [],
            "review": {"mode": mode, "reviewers": reviewers},
            "reviewed_checks": reviewed_checks,
        }

    def _write_attestations(self) -> None:
        for gate in GATE_RECEIPTS:
            evidence = (
                self.root
                / "validation"
                / "candidate-v0.3.0"
                / "evidence"
                / "source"
                / f"{gate.lower()}.txt"
            )
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text(
                f"pre-existing passed check evidence for {gate}\n",
                encoding="utf-8",
            )
        gates = {
            gate: self._gate_input(gate)
            for gate in GATE_RECEIPTS
        }
        candidate = candidate_identity_from_repository(
            self.root,
            checksums_path=Path("bundle/CHECKSUMS.sha256"),
            profile_checksums_path=Path("domain-profile/CHECKSUMS.sha256"),
            build_receipt_path=Path("bundle/build-receipt.json"),
            candidate_commit_sha=self.candidate_commit_sha,
        )
        self.archive_path = (
            self.root / "dist" / "okf-landregistry-0.3.0-candidate-a.zip"
        )
        self.archive_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(self.archive_path, "w") as archive:
            for path in sorted((self.root / "bundle").rglob("*")):
                if path.is_file():
                    write_zip_member(
                        archive,
                        "okf-landregistry-0.3.0/"
                        + path.relative_to(self.root / "bundle").as_posix(),
                        path.read_bytes(),
                    )
        self.archive_receipt_path = (
            self.root
            / "validation"
            / "candidate-v0.3.0"
            / "evidence"
            / "release-candidate-archive-a.json"
        )
        write_json(
            self.archive_receipt_path,
            {
                "schema": "okf-hmlr-candidate-archive.v1",
                "version": "0.3.0",
                "candidate_at": NOW,
                "publication_state": "unreleased-candidate",
                "release_root_sha256": candidate.release_root_sha256,
                "candidate": asdict(candidate),
                "path": self.archive_path.relative_to(self.root).as_posix(),
                "bytes": self.archive_path.stat().st_size,
                "sha256": sha256_file(self.archive_path),
            },
        )
        archive_receipt = json.loads(
            self.archive_receipt_path.read_text(encoding="utf-8")
        )
        _version, metadata_documents = expected_release_metadata_documents(
            self.root,
            candidate=candidate,
            archive_receipt=archive_receipt,
        )
        metadata_paths: list[Path] = []
        metadata_directory = (
            self.root
            / "validation"
            / "candidate-v0.3.0"
            / "evidence"
            / "release-metadata"
        )
        metadata_directory.mkdir(parents=True, exist_ok=True)
        for filename, content in metadata_documents.items():
            metadata_path = metadata_directory / filename
            metadata_path.write_bytes(content)
            metadata_paths.append(metadata_path)
        gates["G8"]["evidence"].extend(  # type: ignore[union-attr]
            [
                self.archive_receipt_path.relative_to(self.root).as_posix(),
                self.archive_path.relative_to(self.root).as_posix(),
                *[
                    path.relative_to(self.root).as_posix()
                    for path in metadata_paths
                ],
            ]
        )
        pre_g9_input = (
            self.root
            / "validation"
            / "candidate-v0.3.0"
            / "evidence"
            / "assembly-inputs"
            / "pre-g9-input.json"
        )
        write_json(
            pre_g9_input,
            {
                "schema": "okf-pre-g9-assembly-input.v1",
                "generated_at": NOW,
                "gates": gates,
            },
        )
        pre_g9_output = PRE_G9_OUTPUT
        assemble_pre_g9_evidence(
            self.root,
            input_path=pre_g9_input.relative_to(self.root),
            candidate_commit_sha=self.candidate_commit_sha,
            output_directory=pre_g9_output,
        )
        pre_g9_manifest_path = (
            self.root / pre_g9_output / "pre-g9-evidence.json"
        )
        pre_g9_manifest = json.loads(
            pre_g9_manifest_path.read_text(encoding="utf-8")
        )
        risk_register = self.root / "governance" / "risk-register.json"
        approved_claims = [
            "This fixture is an AI-generated proof of concept.",
            "This fixture does not claim completed human assurance.",
        ]
        residual_risk_ids = ["RISK-FIXTURE", "RISK-HUMAN-AUDIT"]
        independent_review = {
            "identity": "fixture-release-agent",
            "kind": "ai-agent",
            "role": "release-reviewer",
            "reviewed_at": NOW,
            "independent": True,
            "outcome": "recommend_approval",
        }
        independent_review_path = (
            self.root
            / "validation"
            / "candidate-v0.3.0"
            / "evidence"
            / "source"
            / "independent-release-review.json"
        )
        write_json(
            independent_review_path,
            {
                "$schema": SCHEMA_ID,
                "schema": "okf-independent-release-review-evidence.v1",
                "candidate": asdict(candidate),
                "independent_review": independent_review,
                "pre_g9_manifest_sha256": sha256_file(
                    pre_g9_manifest_path
                ),
                "approved_claims": approved_claims,
                "residual_risk_ids": residual_risk_ids,
            },
        )
        write_json(
            self.input_path,
            {
                "schema": "okf-release-assembly-input.v2",
                "generated_at": NOW,
                "gates": gates,
                "release": {
                    "status": "approved",
                    "version": "0.3.0",
                    "canonical_url": "https://example.test/okf/",
                    "claims_reviewed": True,
                    "approved_claims": approved_claims,
                    "residual_risks_reviewed": True,
                    "residual_risk_ids": residual_risk_ids,
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
                        "binding": {
                            "version": "0.3.0",
                            "canonical_url": "https://example.test/okf/",
                            "candidate": asdict(candidate),
                            "pre_g9_manifest": {
                                "path": pre_g9_manifest_path.relative_to(
                                    self.root
                                ).as_posix(),
                                "sha256": sha256_file(pre_g9_manifest_path),
                            },
                            "approved_receipts": [
                                {
                                    "gate": receipt["gate"],
                                    "sha256": receipt["sha256"],
                                }
                                for receipt in pre_g9_manifest["receipts"]
                            ],
                            "approved_claims": approved_claims,
                            "residual_risks": {
                                "register": {
                                    "path": "governance/risk-register.json",
                                    "sha256": sha256_file(risk_register),
                                },
                                "ids": [
                                    "RISK-FIXTURE",
                                    "RISK-HUMAN-AUDIT",
                                ],
                            },
                            "human_audit": {
                                "status": "not_completed",
                                "residual_risk_id": "RISK-HUMAN-AUDIT",
                                "notes": (
                                    "Independent AI-agent review is recorded; "
                                    "a human audit was not completed."
                                ),
                            },
                            "independent_review": independent_review,
                            "independent_review_evidence": {
                                "path": independent_review_path.relative_to(
                                    self.root
                                ).as_posix(),
                                "sha256": sha256_file(
                                    independent_review_path
                                ),
                            },
                        },
                    },
                    "independent_review": independent_review,
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
            input_path=self.input_path.relative_to(self.root),
            candidate_commit_sha=self.candidate_commit_sha,
            output_directory=FINAL_G9_OUTPUT,
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
            manifest_path=FINAL_G9_OUTPUT / "release-evidence.json",
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
                self.fixture.root / FINAL_G9_OUTPUT / "receipts" / "g1.json"
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
                self.fixture.root / FINAL_G9_OUTPUT / "release-record.json"
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
        self.assertEqual(
            asdict(candidate),
            release["owner_approval"]["binding"]["candidate"],
        )
        self.assertEqual(
            release["approved_claims"],
            release["owner_approval"]["binding"]["approved_claims"],
        )
        self.assertEqual(
            {
                reference["gate"]: reference["sha256"]
                for reference in release["approved_receipts"]
            },
            {
                reference["gate"]: reference["sha256"]
                for reference in release["owner_approval"]["binding"][
                    "approved_receipts"
                ]
            },
        )
        pre_g9_reference = release["owner_approval"]["binding"][
            "pre_g9_manifest"
        ]
        self.assertEqual(
            pre_g9_reference["sha256"],
            sha256_file(self.fixture.root / pre_g9_reference["path"]),
        )
        risk_binding = release["owner_approval"]["binding"][
            "residual_risks"
        ]
        self.assertEqual(
            {"RISK-FIXTURE", "RISK-HUMAN-AUDIT"},
            set(risk_binding["ids"]),
        )
        self.assertEqual(
            release["version"],
            release["owner_approval"]["binding"]["version"],
        )
        self.assertEqual(
            release["canonical_url"],
            release["owner_approval"]["binding"]["canonical_url"],
        )
        self.assertEqual(
            release["human_audit"],
            release["owner_approval"]["binding"]["human_audit"],
        )

    def test_pre_g9_assembly_produces_only_exact_g1_g8_receipts(self) -> None:
        document = self.fixture.read_input()
        document.pop("release")
        document["schema"] = "okf-pre-g9-assembly-input.v1"
        self.fixture.rewrite_input(document)

        output_directory = PRE_G9_OUTPUT

        assemble_pre_g9_evidence(
            self.fixture.root,
            input_path=self.fixture.input_path.relative_to(self.fixture.root),
            candidate_commit_sha=self.fixture.candidate_commit_sha,
            output_directory=output_directory,
            replace=True,
        )

        output = self.fixture.root / output_directory
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

    def test_versioned_final_g9_output_preserves_historical_evidence(
        self,
    ) -> None:
        historical_paths = [
            self.fixture.root / "validation" / "release-evidence.json",
            self.fixture.root / "validation" / "release-record.json",
            *[
                self.fixture.root
                / "validation"
                / "receipts"
                / f"{gate.lower()}.json"
                for gate in GATE_RECEIPTS
            ],
        ]
        historical_bytes = {
            path.relative_to(self.fixture.root).as_posix(): path.read_bytes()
            for path in historical_paths
        }
        output_directory = FINAL_G9_OUTPUT

        assemble_release_evidence(
            self.fixture.root,
            input_path=self.fixture.input_path.relative_to(self.fixture.root),
            candidate_commit_sha=self.fixture.candidate_commit_sha,
            output_directory=output_directory,
        )

        for relative_name, expected in historical_bytes.items():
            self.assertEqual(
                expected,
                (self.fixture.root / relative_name).read_bytes(),
                relative_name,
            )
        manifest_path = (
            self.fixture.root / output_directory / "release-evidence.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(
            all(
                reference["path"].startswith(
                    "validation/candidate-v0.3.0/final-g9/"
                )
                for reference in manifest["receipts"]
            )
        )
        candidate = candidate_identity_from_repository(
            self.fixture.root,
            checksums_path=Path("bundle/CHECKSUMS.sha256"),
            profile_checksums_path=Path(
                "domain-profile/CHECKSUMS.sha256"
            ),
            build_receipt_path=Path("bundle/build-receipt.json"),
            candidate_commit_sha=self.fixture.candidate_commit_sha,
        )
        self.assertEqual(
            candidate,
            validate_release_evidence(
                self.fixture.root,
                manifest_path=(
                    output_directory / "release-evidence.json"
                ),
                schema_path=(
                    self.fixture.root
                    / "schemas"
                    / "release-evidence.schema.json"
                ),
                expected_candidate=candidate,
            ),
        )

    def test_pre_g9_rejects_missing_g8_archive_receipt(self) -> None:
        document = self.fixture.read_input()
        document.pop("release")
        document["schema"] = "okf-pre-g9-assembly-input.v1"
        archive_receipt_name = self.fixture.archive_receipt_path.relative_to(
            self.fixture.root
        ).as_posix()
        document["gates"]["G8"]["evidence"] = [  # type: ignore[index]
            path
            for path in document["gates"]["G8"]["evidence"]  # type: ignore[index]
            if path != archive_receipt_name
        ]
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "missing exact version-scoped archive or release metadata evidence",
        ):
            assemble_pre_g9_evidence(
                self.fixture.root,
                input_path=self.fixture.input_path.relative_to(
                    self.fixture.root
                ),
                candidate_commit_sha=self.fixture.candidate_commit_sha,
                output_directory=PRE_G9_OUTPUT,
                replace=True,
            )

    def test_pre_g9_rejects_generic_g8_archive_decoy(self) -> None:
        document = self.fixture.read_input()
        document.pop("release")
        document["schema"] = "okf-pre-g9-assembly-input.v1"
        archive_receipt_name = self.fixture.archive_receipt_path.relative_to(
            self.fixture.root
        ).as_posix()
        decoy = (
            self.fixture.root
            / "validation"
            / "candidate-v0.3.0"
            / "evidence"
            / "source"
            / "generic-g8-decoy.json"
        )
        write_json(decoy, {"schema": "okf-generic-g8-evidence.v1"})
        evidence = document["gates"]["G8"]["evidence"]  # type: ignore[index]
        document["gates"]["G8"]["evidence"] = [  # type: ignore[index]
            decoy.relative_to(self.fixture.root).as_posix()
            if path == archive_receipt_name
            else path
            for path in evidence
        ]
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "missing exact version-scoped archive or release metadata evidence",
        ):
            assemble_pre_g9_evidence(
                self.fixture.root,
                input_path=self.fixture.input_path.relative_to(
                    self.fixture.root
                ),
                candidate_commit_sha=self.fixture.candidate_commit_sha,
                output_directory=PRE_G9_OUTPUT,
                replace=True,
            )

    def test_pre_g9_rejects_mismatched_g8_receipt_and_zip(self) -> None:
        archive_receipt = json.loads(
            self.fixture.archive_receipt_path.read_text(encoding="utf-8")
        )
        archive_receipt["bytes"] += 1
        write_json(self.fixture.archive_receipt_path, archive_receipt)
        document = self.fixture.read_input()
        document.pop("release")
        document["schema"] = "okf-pre-g9-assembly-input.v1"
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "archive receipt byte count does not match the ZIP",
        ):
            assemble_pre_g9_evidence(
                self.fixture.root,
                input_path=self.fixture.input_path.relative_to(
                    self.fixture.root
                ),
                candidate_commit_sha=self.fixture.candidate_commit_sha,
                output_directory=PRE_G9_OUTPUT,
                replace=True,
            )

    def test_pre_g9_rejects_ungoverned_archive_metadata(self) -> None:
        with zipfile.ZipFile(self.fixture.archive_path, "a") as archive:
            archive.comment = b"ungoverned archive comment"
        archive_receipt = json.loads(
            self.fixture.archive_receipt_path.read_text(encoding="utf-8")
        )
        archive_receipt["bytes"] = self.fixture.archive_path.stat().st_size
        archive_receipt["sha256"] = sha256_file(self.fixture.archive_path)
        write_json(self.fixture.archive_receipt_path, archive_receipt)
        document = self.fixture.read_input()
        document.pop("release")
        document["schema"] = "okf-pre-g9-assembly-input.v1"
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "archive comment is not the governed empty value",
        ):
            assemble_pre_g9_evidence(
                self.fixture.root,
                input_path=self.fixture.input_path.relative_to(
                    self.fixture.root
                ),
                candidate_commit_sha=self.fixture.candidate_commit_sha,
                output_directory=PRE_G9_OUTPUT,
                replace=True,
            )

    def test_pre_g9_receipts_equal_later_release_receipts(self) -> None:
        full_input = self.fixture.read_input()
        pre_g9_input = dict(full_input)
        pre_g9_input.pop("release")
        pre_g9_input["schema"] = "okf-pre-g9-assembly-input.v1"
        self.fixture.rewrite_input(pre_g9_input)
        assemble_pre_g9_evidence(
            self.fixture.root,
            input_path=self.fixture.input_path.relative_to(self.fixture.root),
            candidate_commit_sha=self.fixture.candidate_commit_sha,
            output_directory=PRE_G9_OUTPUT,
            replace=True,
        )
        pre_g9_receipts = {
            gate: (
                self.fixture.root
                / PRE_G9_OUTPUT
                / "receipts"
                / f"{gate.lower()}.json"
            ).read_bytes()
            for gate in GATE_RECEIPTS
        }

        self.fixture.rewrite_input(full_input)
        assemble_release_evidence(
            self.fixture.root,
            input_path=self.fixture.input_path.relative_to(self.fixture.root),
            candidate_commit_sha=self.fixture.candidate_commit_sha,
            output_directory=FINAL_G9_OUTPUT,
        )
        final_receipts = {
            gate: (
                self.fixture.root
                / FINAL_G9_OUTPUT
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
                / FINAL_G9_OUTPUT
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

    def test_owner_and_reviewer_identity_text_must_be_canonical(self) -> None:
        original = self.fixture.read_input()
        for target, replacement in (
            ("owner", " "),
            ("reviewer", "fixture-owner "),
        ):
            with self.subTest(target=target):
                document = json.loads(json.dumps(original))
                if target == "owner":
                    document["release"]["owner_approval"]["identity"] = (
                        replacement
                    )
                else:
                    document["release"]["independent_review"]["identity"] = (
                        replacement
                    )
                self.fixture.rewrite_input(document)
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "non-empty string|leading or trailing whitespace",
                ):
                    self.fixture.assemble()

    def test_gate_review_chronology_is_bounded_by_gate_execution(self) -> None:
        document = self.fixture.read_input()
        reviewer = document["gates"]["G3"]["review"]["reviewers"][0]
        reviewer["reviewed_at"] = "2099-01-01T00:00:00Z"
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "reviewed after the gate executed_at"
        ):
            self.fixture.assemble()

        document = self.fixture.read_input()
        reviewer = document["gates"]["G3"]["review"]["reviewers"][0]
        reviewer["reviewed_at"] = "2026-07-29T12:01:00Z"
        document["gates"]["G3"]["executed_at"] = "2026-07-29T12:02:00Z"
        reviewed_check = next(
            iter(document["gates"]["G3"]["reviewed_checks"].values())
        )
        reviewed_check["completed_at"] = "2026-07-29T12:01:01Z"
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "completed after its matching reviewer"
        ):
            self.fixture.assemble()

    def test_generic_v1_owner_approval_input_is_rejected(self) -> None:
        document = self.fixture.read_input()
        document["schema"] = "okf-release-assembly-input.v1"
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "schema must be 'okf-release-assembly-input.v2'",
        ):
            self.fixture.assemble()

    def test_owner_must_bind_exact_candidate_commit_and_root(self) -> None:
        original = self.fixture.read_input()
        for field, replacement in (
            ("candidate_commit_sha", "0" * 40),
            ("release_root_sha256", "0" * 64),
        ):
            with self.subTest(field=field):
                document = json.loads(json.dumps(original))
                document["release"]["owner_approval"]["binding"][
                    "candidate"
                ][field] = replacement
                self.fixture.rewrite_input(document)
                with self.assertRaisesRegex(
                    ReleaseEvidenceError,
                    "binding.candidate differs from the exact repository candidate",
                ):
                    self.fixture.assemble()

    def test_owner_must_bind_release_identity_and_human_audit(self) -> None:
        original = self.fixture.read_input()
        cases = (
            ("version", "9.9.9", "binding.version does not match"),
            (
                "canonical_url",
                "https://different.example.test/",
                "binding.canonical_url does not match",
            ),
        )
        for field, replacement, message in cases:
            with self.subTest(field=field):
                document = json.loads(json.dumps(original))
                document["release"]["owner_approval"]["binding"][
                    field
                ] = replacement
                self.fixture.rewrite_input(document)
                with self.assertRaisesRegex(ReleaseEvidenceError, message):
                    self.fixture.assemble()

        document = json.loads(json.dumps(original))
        document["release"]["owner_approval"]["binding"][
            "human_audit"
        ]["notes"] = "Different audit claim."
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "binding.human_audit does not match"
        ):
            self.fixture.assemble()

    def test_release_coordinates_must_equal_governed_build_config(self) -> None:
        original = self.fixture.read_input()
        cases = (
            (
                "version",
                "9.9.9",
                "must equal governed.*build-config.json version",
            ),
            (
                "canonical_url",
                "https://different.example.test/",
                "must equal governed.*build-config.json publication_base",
            ),
        )
        for field, replacement, message in cases:
            with self.subTest(field=field):
                document = json.loads(json.dumps(original))
                document["release"][field] = replacement
                document["release"]["owner_approval"]["binding"][field] = (
                    replacement
                )
                self.fixture.rewrite_input(document)
                with self.assertRaisesRegex(ReleaseEvidenceError, message):
                    self.fixture.assemble()

    def test_owner_must_bind_exact_independent_review(self) -> None:
        document = self.fixture.read_input()
        document["release"]["owner_approval"]["binding"][
            "independent_review"
        ]["role"] = "different-review-role"
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "binding.independent_review does not match",
        ):
            self.fixture.assemble()

    def test_owner_must_bind_independent_review_evidence_bytes(self) -> None:
        document = self.fixture.read_input()
        document["release"]["owner_approval"]["binding"][
            "independent_review_evidence"
        ]["sha256"] = "0" * 64
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "independent_review_evidence digest mismatch",
        ):
            self.fixture.assemble()

    def test_independent_review_evidence_must_be_a_regular_file(self) -> None:
        document = self.fixture.read_input()
        reference = document["release"]["owner_approval"]["binding"][
            "independent_review_evidence"
        ]
        path = self.fixture.root / reference["path"]
        target = path.with_name("independent-release-review-target.json")
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target.name)
        with self.assertRaisesRegex(ReleaseEvidenceError, "symbolic link"):
            self.fixture.assemble()

    def test_release_chronology_requires_review_before_owner_approval(
        self,
    ) -> None:
        document = self.fixture.read_input()
        document["release"]["owner_approval"]["approved_at"] = (
            "2026-07-29T11:59:59Z"
        )
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "owner approval predates the independent review",
        ):
            self.fixture.assemble()

    def test_release_chronology_requires_utc_timestamps(self) -> None:
        document = self.fixture.read_input()
        document["release"]["owner_approval"]["approved_at"] = (
            "2026-07-29T13:00:00+01:00"
        )
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "must be an RFC 3339 UTC timestamp ending in 'Z'",
        ):
            self.fixture.assemble()

    def test_final_manifest_must_follow_owner_approval(self) -> None:
        document = self.fixture.read_input()
        document["generated_at"] = "2026-07-29T11:59:59Z"
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "owner approval is after the final evidence manifest",
        ):
            self.fixture.assemble()

    def test_pre_g9_manifest_must_follow_gate_execution(self) -> None:
        document = self.fixture.read_input()
        pre_g9_input = dict(document)
        pre_g9_input.pop("release")
        pre_g9_input["schema"] = "okf-pre-g9-assembly-input.v1"
        pre_g9_input["generated_at"] = "2026-07-29T11:59:59Z"
        self.fixture.rewrite_input(pre_g9_input)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "after pre-G9 generated_at"
        ):
            assemble_pre_g9_evidence(
                self.fixture.root,
                input_path=self.fixture.input_path.relative_to(
                    self.fixture.root
                ),
                candidate_commit_sha=self.fixture.candidate_commit_sha,
                output_directory=PRE_G9_OUTPUT,
                replace=True,
            )

    def test_owner_must_bind_exact_pre_g9_manifest_digest(self) -> None:
        document = self.fixture.read_input()
        document["release"]["owner_approval"]["binding"][
            "pre_g9_manifest"
        ]["sha256"] = "0" * 64
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "pre_g9_manifest digest mismatch"
        ):
            self.fixture.assemble()

    def test_owner_must_bind_exact_g1_g8_receipt_hashes(self) -> None:
        document = self.fixture.read_input()
        document["release"]["owner_approval"]["binding"][
            "approved_receipts"
        ][0]["sha256"] = "0" * 64
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "approved_receipts do not match the exact G1-G8 assembly",
        ):
            self.fixture.assemble()

    def test_post_review_gate_change_invalidates_owner_approval(self) -> None:
        document = self.fixture.read_input()
        document["gates"]["G1"]["checks"]["schema-valid"][
            "summary"
        ] = "Changed after the owner reviewed the pre-G9 receipt."
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "pre-G9 receipt hashes do not match the exact G1-G8 assembly",
        ):
            self.fixture.assemble()

    def test_owner_must_bind_the_approved_claims(self) -> None:
        document = self.fixture.read_input()
        document["release"]["owner_approval"]["binding"][
            "approved_claims"
        ].pop()
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "approved_claims do not match release.approved_claims",
        ):
            self.fixture.assemble()

    def test_owner_must_bind_governed_residual_risk_register(self) -> None:
        document = self.fixture.read_input()
        document["release"]["owner_approval"]["binding"][
            "residual_risks"
        ]["register"]["sha256"] = "0" * 64
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "residual_risks.register digest mismatch"
        ):
            self.fixture.assemble()

    def test_owner_must_approve_the_complete_governed_risk_set(self) -> None:
        document = self.fixture.read_input()
        document["release"]["owner_approval"]["binding"][
            "residual_risks"
        ]["ids"].remove("RISK-FIXTURE")
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "residual-risk IDs do not equal the governed residual-risk set",
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
        first_metadata = {
            path.relative_to(self.fixture.root).as_posix(): (
                path.stat().st_ino,
                path.stat().st_mtime_ns,
            )
            for path in paths
        }
        self.fixture.assemble(replace=True)
        second = {
            path.relative_to(self.fixture.root).as_posix(): path.read_bytes()
            for path in paths
        }
        second_metadata = {
            path.relative_to(self.fixture.root).as_posix(): (
                path.stat().st_ino,
                path.stat().st_mtime_ns,
            )
            for path in paths
        }
        self.assertEqual(first, second)
        self.assertEqual(first_metadata, second_metadata)

    def test_non_identical_replace_fails_before_changing_any_output(
        self,
    ) -> None:
        document = self.fixture.read_input()
        document.pop("release")
        document["schema"] = "okf-pre-g9-assembly-input.v1"
        self.fixture.rewrite_input(document)
        output_directory = PRE_G9_OUTPUT
        assemble_pre_g9_evidence(
            self.fixture.root,
            input_path=self.fixture.input_path.relative_to(self.fixture.root),
            candidate_commit_sha=self.fixture.candidate_commit_sha,
            output_directory=output_directory,
            replace=True,
        )
        output = self.fixture.root / output_directory
        output_paths = sorted(path for path in output.rglob("*") if path.is_file())
        original = {
            path.relative_to(output).as_posix(): path.read_bytes()
            for path in output_paths
        }

        changed = self.fixture.read_input()
        changed["gates"]["G1"]["checks"]["schema-valid"][
            "summary"
        ] = "A different but otherwise valid summary."
        self.fixture.rewrite_input(changed)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "refusing to replace non-byte-identical release evidence output",
        ):
            assemble_pre_g9_evidence(
                self.fixture.root,
                input_path=self.fixture.input_path.relative_to(
                    self.fixture.root
                ),
                candidate_commit_sha=self.fixture.candidate_commit_sha,
                output_directory=output_directory,
                replace=True,
            )

        self.assertEqual(
            original,
            {
                path.relative_to(output).as_posix(): path.read_bytes()
                for path in output_paths
            },
        )

    def test_cli_requires_an_explicit_output_directory(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / "scripts" / "assemble_release_evidence.py"),
                "--input",
                "validation/unused-input.json",
                "--candidate-commit-sha",
                "0" * 40,
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("--output-directory", result.stderr)

    def test_python_api_rejects_non_versioned_output_directory(self) -> None:
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "final-g9 output directory must be exactly",
        ):
            assemble_release_evidence(
                self.fixture.root,
                input_path=self.fixture.input_path.relative_to(
                    self.fixture.root
                ),
                candidate_commit_sha=self.fixture.candidate_commit_sha,
                output_directory=Path("../validation"),
            )

        document = self.fixture.read_input()
        document.pop("release")
        document["schema"] = "okf-pre-g9-assembly-input.v1"
        self.fixture.rewrite_input(document)
        with self.assertRaisesRegex(
            ReleaseEvidenceError,
            "pre-g9 output directory must be exactly",
        ):
            assemble_pre_g9_evidence(
                self.fixture.root,
                input_path=self.fixture.input_path.relative_to(
                    self.fixture.root
                ),
                candidate_commit_sha=self.fixture.candidate_commit_sha,
                output_directory=Path("validation/pre-g9"),
            )

    def test_python_api_requires_an_explicit_output_directory(self) -> None:
        with self.assertRaises(TypeError):
            assemble_release_evidence(
                self.fixture.root,
                input_path=self.fixture.input_path.relative_to(
                    self.fixture.root
                ),
                candidate_commit_sha=self.fixture.candidate_commit_sha,
            )
        with self.assertRaises(TypeError):
            assemble_pre_g9_evidence(
                self.fixture.root,
                input_path=self.fixture.input_path.relative_to(
                    self.fixture.root
                ),
                candidate_commit_sha=self.fixture.candidate_commit_sha,
            )

    def test_cli_rejects_a_non_versioned_output_directory(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / "scripts" / "assemble_release_evidence.py"),
                "--repository-root",
                str(self.fixture.root),
                "--input",
                self.fixture.input_path.relative_to(
                    self.fixture.root
                ).as_posix(),
                "--candidate-commit-sha",
                self.fixture.candidate_commit_sha,
                "--output-directory",
                "validation",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(1, result.returncode)
        self.assertIn(
            "final-g9 output directory must be exactly",
            result.stderr,
        )

    def test_symbolic_link_receipt_directory_fails_closed(self) -> None:
        outside = self.fixture.root / "outside"
        outside.mkdir()
        output = self.fixture.root / FINAL_G9_OUTPUT
        output.mkdir(parents=True)
        (output / "receipts").symlink_to(
            outside, target_is_directory=True
        )
        with self.assertRaisesRegex(
            ReleaseEvidenceError, "symbolic-link|escapes repository"
        ):
            self.fixture.assemble()

    def test_late_target_appearance_does_not_replace_or_publish_a_set(
        self,
    ) -> None:
        real_link = os.link
        appeared: list[tuple[int, str]] = []

        def appear_before_link(
            source: str,
            destination: str,
            *,
            src_dir_fd: int,
            dst_dir_fd: int,
            follow_symlinks: bool,
        ) -> None:
            if not appeared:
                descriptor = os.open(
                    destination,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o644,
                    dir_fd=dst_dir_fd,
                )
                os.write(descriptor, b"concurrent external output\n")
                os.close(descriptor)
                appeared.append((dst_dir_fd, destination))
            real_link(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
                follow_symlinks=follow_symlinks,
            )

        with (
            mock.patch(
                "scripts.assemble_release_evidence.os.link",
                side_effect=appear_before_link,
            ),
            self.assertRaisesRegex(
                ReleaseEvidenceError, "appeared during publication"
            ),
        ):
            self.fixture.assemble()

        output = self.fixture.root / FINAL_G9_OUTPUT
        self.assertFalse((output / "release-evidence.json").exists())
        published = sorted(path for path in output.rglob("*") if path.is_file())
        self.assertEqual(1, len(published))
        self.assertEqual(b"concurrent external output\n", published[0].read_bytes())
        self.assertEqual(
            [],
            list(output.parent.glob(".release-evidence-*")),
        )

    def test_mid_set_publication_failure_rolls_back_every_created_link(
        self,
    ) -> None:
        real_link = os.link
        links = 0

        def fail_second_link(
            source: str,
            destination: str,
            *,
            src_dir_fd: int,
            dst_dir_fd: int,
            follow_symlinks: bool,
        ) -> None:
            nonlocal links
            links += 1
            if links == 2:
                raise OSError("injected publication failure")
            real_link(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
                follow_symlinks=follow_symlinks,
            )

        with (
            mock.patch(
                "scripts.assemble_release_evidence.os.link",
                side_effect=fail_second_link,
            ),
            self.assertRaisesRegex(
                ReleaseEvidenceError,
                "could not publish the complete release evidence set",
            ),
        ):
            self.fixture.assemble()

        output = self.fixture.root / FINAL_G9_OUTPUT
        self.assertEqual(
            [],
            [path for path in output.rglob("*") if path.is_file()]
            if output.exists()
            else [],
        )
        self.assertEqual([], list(output.parent.glob(".release-evidence-*")))

    def test_output_directory_swap_is_detected_and_held_links_are_removed(
        self,
    ) -> None:
        real_link = os.link
        moved = False
        output = self.fixture.root / FINAL_G9_OUTPUT
        displaced = output.with_name("displaced-final-g9")

        def swap_after_first_link(
            source: str,
            destination: str,
            *,
            src_dir_fd: int,
            dst_dir_fd: int,
            follow_symlinks: bool,
        ) -> None:
            nonlocal moved
            real_link(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
                follow_symlinks=follow_symlinks,
            )
            if not moved:
                output.rename(displaced)
                (output / "receipts").mkdir(parents=True)
                moved = True

        with (
            mock.patch(
                "scripts.assemble_release_evidence.os.link",
                side_effect=swap_after_first_link,
            ),
            self.assertRaisesRegex(
                ReleaseEvidenceError,
                "output directory changed during publication",
            ),
        ):
            self.fixture.assemble()

        self.assertEqual(
            [],
            [path for path in displaced.rglob("*") if path.is_file()],
        )
        self.assertEqual(
            [],
            [path for path in output.rglob("*") if path.is_file()],
        )
        self.assertEqual([], list(output.parent.glob(".release-evidence-*")))


if __name__ == "__main__":
    unittest.main()

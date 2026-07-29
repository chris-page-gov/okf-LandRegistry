#!/usr/bin/env python3
"""Validate digest-bound G1-G9 release evidence for the exact repository candidate."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ID = (
    "https://chris-page-gov.github.io/okf-LandRegistry/"
    "schemas/release-evidence.schema.json"
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
GATE_RECEIPTS = tuple(f"G{number}" for number in range(1, 9))
ALL_GATES = (*GATE_RECEIPTS, "G9")
REVIEWED_GATES = frozenset({"G3", "G5", "G6"})
MAX_JSON_BYTES = 5_000_000

REQUIRED_CHECKS = {
    "G1": {
        "schema-valid",
        "json-yaml-equivalent",
        "references-closed",
        "profile-pack-rehashed",
    },
    "G2": {
        "envelopes-rehashed",
        "media-types-valid",
        "terminal-outcomes-complete",
        "omissions-explicit",
    },
    "G3": {
        "field-inventory-reviewed",
        "rights-access-complete",
        "prohibited-content-absent",
        "independent-rights-review",
    },
    "G4": {
        "okf-core-valid",
        "data-plane-valid",
        "checksums-valid",
        "routes-valid",
    },
    "G5": {
        "independent-question-review",
        "hard-failures-zero",
        "mrr-threshold",
        "recall-at-10-threshold",
        "source-caveat-coverage",
    },
    "G6": {
        "automated-journeys",
        "manual-accessibility-journeys",
        "security-critical-zero",
        "performance-budgets",
    },
    "G7": {
        "clean-build-a",
        "clean-build-b",
        "byte-identical",
        "committed-bundle-identical",
    },
    "G8": {
        "artifact-manifest-complete",
        "dependency-provenance",
        "workflow-provenance",
        "sbom-recorded",
    },
}


class ReleaseEvidenceError(ValueError):
    """Raised when release evidence is absent, unsafe or internally inconsistent."""


@dataclass(frozen=True)
class CandidateIdentity:
    """Digest-bound identity shared by the manifest and every receipt."""

    candidate_commit_sha: str
    release_root_sha256: str
    checksums_sha256: str
    profile_pack_root_sha256: str
    snapshot_manifest_sha256: str


def sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseEvidenceError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ReleaseEvidenceError(f"non-finite JSON number is not allowed: {value}")


def load_json(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ReleaseEvidenceError(f"cannot inspect JSON file {path}: {exc}") from exc
    if size > MAX_JSON_BYTES:
        raise ReleaseEvidenceError(
            f"JSON file exceeds {MAX_JSON_BYTES} bytes: {path}"
        )
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except ReleaseEvidenceError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseEvidenceError(f"invalid JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseEvidenceError(f"JSON document must be an object: {path}")
    return value


def safe_repository_file(
    repository_root: Path, relative_name: str, *, purpose: str
) -> Path:
    if not relative_name or "\\" in relative_name or "\x00" in relative_name:
        raise ReleaseEvidenceError(f"unsafe {purpose} path: {relative_name!r}")
    relative = PurePosixPath(relative_name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ReleaseEvidenceError(f"unsafe {purpose} path: {relative_name!r}")

    root = repository_root.resolve()
    candidate = root.joinpath(*relative.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ReleaseEvidenceError(
            f"{purpose} file is missing or unreadable: {relative_name!r}"
        ) from exc
    if root not in resolved.parents:
        raise ReleaseEvidenceError(
            f"{purpose} path escapes repository: {relative_name!r}"
        )

    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ReleaseEvidenceError(
                f"{purpose} path contains a symbolic link: {relative_name!r}"
            )
    if not candidate.is_file():
        raise ReleaseEvidenceError(f"{purpose} is not a file: {relative_name!r}")
    return candidate


def repository_argument(
    repository_root: Path, path: Path, *, purpose: str
) -> Path:
    root = repository_root.resolve()
    absolute = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        relative = absolute.relative_to(root).as_posix()
    except ValueError as exc:
        raise ReleaseEvidenceError(f"{purpose} must be inside {root}: {path}") from exc
    return safe_repository_file(root, relative, purpose=purpose)


def validate_checksum_manifest(path: Path, root_marker: str) -> str:
    digest_lines: list[str] = []
    declared_roots: list[str] = []
    seen_paths: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ReleaseEvidenceError(f"cannot read checksum manifest {path}: {exc}") from exc

    for line_number, line in enumerate(lines, start=1):
        if line.startswith(root_marker):
            declared_roots.append(line.removeprefix(root_marker))
            continue
        if not line:
            raise ReleaseEvidenceError(
                f"{path}:{line_number}: blank lines are not allowed"
            )
        if line.startswith("#"):
            raise ReleaseEvidenceError(
                f"{path}:{line_number}: unsupported checksum comment"
            )
        try:
            digest, name = line.split("  ", 1)
        except ValueError as exc:
            raise ReleaseEvidenceError(
                f"{path}:{line_number}: expected '<sha256>  <path>'"
            ) from exc
        if SHA256.fullmatch(digest) is None:
            raise ReleaseEvidenceError(
                f"{path}:{line_number}: invalid artifact SHA-256"
            )
        if name in seen_paths:
            raise ReleaseEvidenceError(
                f"{path}:{line_number}: duplicate artifact path {name!r}"
            )
        seen_paths.add(name)
        artifact = safe_repository_file(path.parent, name, purpose="checksummed artifact")
        actual = sha256_file(artifact)
        if actual != digest:
            raise ReleaseEvidenceError(
                f"artifact digest mismatch for {name!r}: "
                f"declared {digest}, calculated {actual}"
            )
        digest_lines.append(line)

    if not digest_lines:
        raise ReleaseEvidenceError(f"checksum manifest has no entries: {path}")
    if len(declared_roots) != 1 or SHA256.fullmatch(declared_roots[0]) is None:
        raise ReleaseEvidenceError(
            f"checksum manifest must have one valid {root_marker.strip()} marker: {path}"
        )
    calculated = hashlib.sha256(
        ("\n".join(digest_lines) + "\n").encode("utf-8")
    ).hexdigest()
    if declared_roots[0] != calculated:
        raise ReleaseEvidenceError(
            f"checksum root mismatch in {path}: "
            f"declared {declared_roots[0]}, calculated {calculated}"
        )
    return calculated


def current_commit(repository_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReleaseEvidenceError(f"cannot resolve repository HEAD: {exc}") from exc
    return result.stdout.strip()


def _git_command(
    repository_root: Path, arguments: list[str]
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=repository_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise ReleaseEvidenceError(f"cannot execute git: {exc}") from exc


def validate_governed_candidate_commit(
    repository_root: Path,
    *,
    candidate_commit_sha: str,
    build_receipt_path: Path,
) -> str:
    """Bind a prior candidate tree to the current evidence commit without a cycle."""

    root = repository_root.resolve()
    if COMMIT_SHA.fullmatch(candidate_commit_sha) is None:
        raise ReleaseEvidenceError(
            "governed candidate commit must be exactly 40 lowercase "
            "hexadecimal characters"
        )
    evidence_commit_sha = current_commit(root)
    if COMMIT_SHA.fullmatch(evidence_commit_sha) is None:
        raise ReleaseEvidenceError(
            "evidence commit must be exactly 40 lowercase hexadecimal characters"
        )

    candidate_object = _git_command(
        root, ["rev-parse", "--verify", f"{candidate_commit_sha}^{{commit}}"]
    )
    if candidate_object.returncode != 0:
        raise ReleaseEvidenceError(
            f"governed candidate commit does not exist: {candidate_commit_sha}"
        )
    if candidate_object.stdout.strip() != candidate_commit_sha:
        raise ReleaseEvidenceError(
            "governed candidate commit did not resolve to the declared full commit"
        )

    ancestor = _git_command(
        root,
        [
            "merge-base",
            "--is-ancestor",
            candidate_commit_sha,
            evidence_commit_sha,
        ],
    )
    if ancestor.returncode == 1:
        raise ReleaseEvidenceError(
            "governed candidate commit is not an ancestor of the evidence commit"
        )
    if ancestor.returncode != 0:
        raise ReleaseEvidenceError(
            "could not establish governed candidate ancestry: "
            f"{ancestor.stderr.strip()}"
        )

    build_receipt_file = repository_argument(
        root, build_receipt_path, purpose="build receipt"
    )
    build_receipt = load_json(build_receipt_file)
    snapshot = build_receipt.get("snapshot")
    snapshot_name = snapshot.get("manifest_path") if isinstance(snapshot, dict) else None
    if not isinstance(snapshot_name, str):
        raise ReleaseEvidenceError(
            "build receipt does not identify the governed snapshot manifest"
        )
    snapshot_manifest = safe_repository_file(
        root, snapshot_name, purpose="snapshot manifest"
    )
    snapshot_tree = snapshot_manifest.parent.relative_to(root).as_posix()
    governed_paths = {"bundle", "domain-profile", snapshot_tree}

    governed_inputs = build_receipt.get("governed_inputs")
    if not isinstance(governed_inputs, list) or not governed_inputs:
        raise ReleaseEvidenceError(
            "build receipt has no non-empty governed input inventory"
        )
    for index, material in enumerate(governed_inputs):
        if not isinstance(material, dict):
            raise ReleaseEvidenceError(
                f"build receipt governed input {index} is not an object"
            )
        name = material.get("path")
        digest = material.get("sha256")
        if not isinstance(name, str) or not isinstance(digest, str):
            raise ReleaseEvidenceError(
                f"build receipt governed input {index} lacks path or SHA-256"
            )
        if SHA256.fullmatch(digest) is None:
            raise ReleaseEvidenceError(
                f"build receipt governed input {name!r} has invalid SHA-256"
            )
        material_file = safe_repository_file(
            root, name, purpose="governed build input"
        )
        actual_material_digest = sha256_file(material_file)
        if actual_material_digest != digest:
            raise ReleaseEvidenceError(
                f"governed build input digest mismatch for {name!r}: "
                f"declared {digest}, calculated {actual_material_digest}"
            )
        governed_paths.add(name)
    governed_path_list = sorted(governed_paths)

    changed = _git_command(
        root,
        [
            "diff",
            "--quiet",
            candidate_commit_sha,
            evidence_commit_sha,
            "--",
            *governed_path_list,
        ],
    )
    if changed.returncode == 1:
        names = _git_command(
            root,
            [
                "diff",
                "--name-only",
                candidate_commit_sha,
                evidence_commit_sha,
                "--",
                *governed_path_list,
            ],
        )
        changed_names = ", ".join(names.stdout.splitlines())
        raise ReleaseEvidenceError(
            "governed candidate tree changed before the evidence commit: "
            f"{changed_names}"
        )
    if changed.returncode != 0:
        raise ReleaseEvidenceError(
            f"cannot compare governed candidate tree: {changed.stderr.strip()}"
        )

    worktree = _git_command(
        root,
        [
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            *governed_path_list,
        ],
    )
    if worktree.returncode != 0:
        raise ReleaseEvidenceError(
            f"cannot inspect governed candidate worktree: {worktree.stderr.strip()}"
        )
    if worktree.stdout.strip():
        changed_names = ", ".join(
            line[3:] for line in worktree.stdout.splitlines() if len(line) > 3
        )
        raise ReleaseEvidenceError(
            "governed candidate tree has uncommitted or untracked changes: "
            f"{changed_names}"
        )
    return evidence_commit_sha


def candidate_identity_from_repository(
    repository_root: Path,
    *,
    checksums_path: Path,
    profile_checksums_path: Path,
    build_receipt_path: Path,
    candidate_commit_sha: str,
) -> CandidateIdentity:
    root = repository_root.resolve()
    checksums = repository_argument(root, checksums_path, purpose="bundle checksums")
    profile_checksums = repository_argument(
        root, profile_checksums_path, purpose="profile checksums"
    )
    build_receipt_file = repository_argument(
        root, build_receipt_path, purpose="build receipt"
    )

    release_root = validate_checksum_manifest(
        checksums, "# release-root-sha256: "
    )
    profile_root = validate_checksum_manifest(
        profile_checksums, "# pack-root-sha256: "
    )
    build_receipt = load_json(build_receipt_file)
    if build_receipt.get("domain_profile_pack_root_sha256") != profile_root:
        raise ReleaseEvidenceError(
            "build receipt profile root does not match the validated profile pack"
        )

    snapshot = build_receipt.get("snapshot")
    if not isinstance(snapshot, dict):
        raise ReleaseEvidenceError("build receipt has no snapshot object")
    snapshot_name = snapshot.get("manifest_path")
    snapshot_digest = snapshot.get("source_manifest_sha256")
    if not isinstance(snapshot_name, str) or SHA256.fullmatch(
        snapshot_digest if isinstance(snapshot_digest, str) else ""
    ) is None:
        raise ReleaseEvidenceError(
            "build receipt snapshot must contain a safe manifest path and SHA-256"
        )
    snapshot_manifest = safe_repository_file(
        root, snapshot_name, purpose="snapshot manifest"
    )
    actual_snapshot_digest = sha256_file(snapshot_manifest)
    if snapshot_digest != actual_snapshot_digest:
        raise ReleaseEvidenceError(
            "build receipt snapshot digest does not match the frozen manifest: "
            f"declared {snapshot_digest}, calculated {actual_snapshot_digest}"
        )

    if COMMIT_SHA.fullmatch(candidate_commit_sha) is None:
        raise ReleaseEvidenceError(
            "governed candidate commit must be exactly 40 lowercase "
            "hexadecimal characters"
        )

    return CandidateIdentity(
        candidate_commit_sha=candidate_commit_sha,
        release_root_sha256=release_root,
        checksums_sha256=sha256_file(checksums),
        profile_pack_root_sha256=profile_root,
        snapshot_manifest_sha256=actual_snapshot_digest,
    )


def schema_validator(schema_path: Path) -> Draft202012Validator:
    schema = load_json(schema_path)
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise ReleaseEvidenceError(f"invalid release evidence schema: {exc}") from exc
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_document_schema(
    validator: Draft202012Validator, document: dict[str, Any], *, label: str
) -> None:
    errors = sorted(
        validator.iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if not errors:
        return
    details: list[str] = []
    for error in errors[:5]:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        details.append(f"{location}: {error.message}")
    if len(errors) > 5:
        details.append(f"... and {len(errors) - 5} more schema errors")
    raise ReleaseEvidenceError(f"{label} is not schema-valid: {'; '.join(details)}")


def require_candidate(
    document: dict[str, Any], expected: CandidateIdentity, *, label: str
) -> None:
    actual = document.get("candidate")
    expected_dict = asdict(expected)
    if actual != expected_dict:
        raise ReleaseEvidenceError(
            f"{label} candidate identity differs from the exact repository candidate"
        )


def validate_evidence_references(
    repository_root: Path, receipt: dict[str, Any], *, gate: str
) -> None:
    seen: set[str] = set()
    for reference in receipt["evidence"]:
        name = reference["path"]
        if name in seen:
            raise ReleaseEvidenceError(f"{gate} has duplicate evidence path {name!r}")
        seen.add(name)
        evidence_file = safe_repository_file(
            repository_root, name, purpose=f"{gate} evidence"
        )
        actual = sha256_file(evidence_file)
        if actual != reference["sha256"]:
            raise ReleaseEvidenceError(
                f"{gate} evidence digest mismatch for {name!r}: "
                f"declared {reference['sha256']}, calculated {actual}"
            )


def validate_gate_receipt(
    repository_root: Path,
    receipt: dict[str, Any],
    *,
    gate: str,
    expected_candidate: CandidateIdentity,
) -> None:
    if receipt.get("schema") != "okf-gate-receipt.v1" or receipt.get("gate") != gate:
        raise ReleaseEvidenceError(f"{gate} reference does not identify a {gate} receipt")
    require_candidate(receipt, expected_candidate, label=f"{gate} receipt")
    if receipt["status"] != "pass":
        raise ReleaseEvidenceError(
            f"{gate} is not passed: receipt status is {receipt['status']!r}"
        )
    if receipt["failures"]:
        raise ReleaseEvidenceError(f"{gate} pass receipt contains failures")
    if receipt["waivers"]:
        raise ReleaseEvidenceError(f"{gate} pass receipt contains waivers")

    check_ids: set[str] = set()
    for check in receipt["checks"]:
        check_id = check["id"]
        if check_id in check_ids:
            raise ReleaseEvidenceError(f"{gate} has duplicate check ID {check_id!r}")
        check_ids.add(check_id)
        if check["status"] != "pass":
            raise ReleaseEvidenceError(
                f"{gate} check {check_id!r} is {check['status']!r}, not pass"
            )
    missing_checks = REQUIRED_CHECKS[gate] - check_ids
    if missing_checks:
        raise ReleaseEvidenceError(
            f"{gate} is missing required checks: {', '.join(sorted(missing_checks))}"
        )

    reviewers = receipt["review"]["reviewers"]
    reviewer_ids = [reviewer["identity"] for reviewer in reviewers]
    if len(reviewer_ids) != len(set(reviewer_ids)):
        raise ReleaseEvidenceError(f"{gate} has duplicate reviewer identities")
    for reviewed_check in receipt["reviewed_checks"]:
        if reviewed_check["status"] != "pass":
            raise ReleaseEvidenceError(
                f"{gate} reviewed check {reviewed_check['id']!r} is not passed"
            )
        if reviewed_check["reviewer_identity"] not in reviewer_ids:
            raise ReleaseEvidenceError(
                f"{gate} reviewed check {reviewed_check['id']!r} names an "
                "undeclared reviewer"
            )

    if gate in REVIEWED_GATES:
        if receipt["review"]["mode"] not in {
            "automated-agent-review",
            "human-review",
            "mixed",
        }:
            raise ReleaseEvidenceError(
                f"{gate} requires an explicit reviewer mode"
            )
        if not reviewers or not any(
            reviewer["independent"] is True for reviewer in reviewers
        ):
            raise ReleaseEvidenceError(
                f"{gate} requires a named independent reviewer"
            )
        if not receipt["reviewed_checks"]:
            raise ReleaseEvidenceError(f"{gate} requires passed reviewed checks")

    validate_evidence_references(repository_root, receipt, gate=gate)


def validate_release_record(
    release_record: dict[str, Any],
    *,
    expected_candidate: CandidateIdentity,
    receipt_hashes: dict[str, str],
) -> None:
    if (
        release_record.get("schema") != "okf-release-record.v1"
        or release_record.get("gate") != "G9"
    ):
        raise ReleaseEvidenceError("G9 reference is not a release record")
    require_candidate(release_record, expected_candidate, label="G9 release record")
    if release_record["status"] != "approved":
        raise ReleaseEvidenceError(
            f"G9 is not approved: release status is {release_record['status']!r}"
        )
    if release_record["owner_approval"]["approved"] is not True:
        raise ReleaseEvidenceError("G9 owner approval is not affirmative")
    if release_record["owner_approval"]["role"] != "project-owner":
        raise ReleaseEvidenceError("G9 owner approval must use role 'project-owner'")
    if release_record["claims_reviewed"] is not True:
        raise ReleaseEvidenceError("G9 does not affirm review of release claims")
    if release_record["residual_risks_reviewed"] is not True:
        raise ReleaseEvidenceError("G9 does not affirm review of residual risks")

    human_audit = release_record["human_audit"]
    if human_audit["status"] == "not_completed":
        residual_risk_id = human_audit["residual_risk_id"]
        if (
            not isinstance(residual_risk_id, str)
            or not residual_risk_id
            or residual_risk_id not in release_record["residual_risk_ids"]
        ):
            raise ReleaseEvidenceError(
                "incomplete human audit must be declared as a reviewed residual risk"
            )
        if "reviewer" in human_audit:
            raise ReleaseEvidenceError(
                "incomplete human audit must not name a completed human reviewer"
            )
    else:
        human_reviewer = human_audit.get("reviewer")
        if (
            not isinstance(human_reviewer, dict)
            or human_reviewer.get("kind") != "human"
        ):
            raise ReleaseEvidenceError(
                "completed human audit must name a human reviewer"
            )

    canonical = urlparse(release_record["canonical_url"])
    if canonical.scheme != "https" or not canonical.netloc:
        raise ReleaseEvidenceError("G9 canonical URL must be an absolute HTTPS URL")

    reviewer = release_record["independent_review"]
    if reviewer["independent"] is not True:
        raise ReleaseEvidenceError("G9 release review is not independent")
    if reviewer["outcome"] != "recommend_approval":
        raise ReleaseEvidenceError(
            "G9 independent reviewer does not recommend approval"
        )
    if reviewer["identity"] == release_record["owner_approval"]["identity"]:
        raise ReleaseEvidenceError(
            "G9 independent reviewer and project owner must be different identities"
        )

    approved: dict[str, str] = {}
    for reference in release_record["approved_receipts"]:
        gate = reference["gate"]
        if gate in approved:
            raise ReleaseEvidenceError(
                f"G9 has duplicate approved receipt for {gate}"
            )
        approved[gate] = reference["sha256"]
    expected_hashes = {gate: receipt_hashes[gate] for gate in GATE_RECEIPTS}
    if approved != expected_hashes:
        raise ReleaseEvidenceError(
            "G9 approved receipt hashes do not match the G1-G8 evidence manifest"
        )


def declared_candidate_commit(
    repository_root: Path, *, manifest_path: Path, schema_path: Path
) -> str:
    manifest_file = repository_argument(
        repository_root.resolve(),
        manifest_path,
        purpose="release evidence manifest",
    )
    validator = schema_validator(schema_path)
    manifest = load_json(manifest_file)
    validate_document_schema(validator, manifest, label="release evidence manifest")
    if manifest.get("schema") != "okf-release-evidence-manifest.v1":
        raise ReleaseEvidenceError("document is not a release evidence manifest")
    candidate = manifest.get("candidate")
    value = (
        candidate.get("candidate_commit_sha")
        if isinstance(candidate, dict)
        else None
    )
    if not isinstance(value, str) or COMMIT_SHA.fullmatch(value) is None:
        raise ReleaseEvidenceError(
            "release evidence manifest has no valid governed candidate commit"
        )
    return value


def validate_release_evidence(
    repository_root: Path,
    *,
    manifest_path: Path,
    schema_path: Path,
    expected_candidate: CandidateIdentity,
) -> CandidateIdentity:
    root = repository_root.resolve()
    manifest_file = repository_argument(
        root, manifest_path, purpose="release evidence manifest"
    )
    validator = schema_validator(schema_path)
    manifest = load_json(manifest_file)
    validate_document_schema(validator, manifest, label="release evidence manifest")
    if manifest.get("schema") != "okf-release-evidence-manifest.v1":
        raise ReleaseEvidenceError("document is not a release evidence manifest")
    if manifest["status"] != "complete":
        raise ReleaseEvidenceError(
            f"release evidence manifest is not complete: {manifest['status']!r}"
        )
    require_candidate(manifest, expected_candidate, label="release evidence manifest")

    references: dict[str, dict[str, Any]] = {}
    seen_paths: set[str] = set()
    for reference in manifest["receipts"]:
        gate = reference["gate"]
        if gate in references:
            raise ReleaseEvidenceError(f"duplicate receipt reference for {gate}")
        if reference["path"] in seen_paths:
            raise ReleaseEvidenceError(
                f"duplicate receipt path {reference['path']!r}"
            )
        references[gate] = reference
        seen_paths.add(reference["path"])
    if set(references) != set(ALL_GATES):
        missing = sorted(set(ALL_GATES) - set(references))
        extra = sorted(set(references) - set(ALL_GATES))
        raise ReleaseEvidenceError(
            "release evidence must reference exactly G1-G9; "
            f"missing={missing}, extra={extra}"
        )

    receipts: dict[str, dict[str, Any]] = {}
    receipt_hashes: dict[str, str] = {}
    for gate in ALL_GATES:
        reference = references[gate]
        receipt_file = safe_repository_file(
            root, reference["path"], purpose=f"{gate} receipt"
        )
        actual_hash = sha256_file(receipt_file)
        if actual_hash != reference["sha256"]:
            raise ReleaseEvidenceError(
                f"{gate} receipt digest mismatch for {reference['path']!r}: "
                f"declared {reference['sha256']}, calculated {actual_hash}"
            )
        receipt = load_json(receipt_file)
        validate_document_schema(validator, receipt, label=f"{gate} receipt")
        receipts[gate] = receipt
        receipt_hashes[gate] = actual_hash

    for gate in GATE_RECEIPTS:
        validate_gate_receipt(
            root,
            receipts[gate],
            gate=gate,
            expected_candidate=expected_candidate,
        )
    validate_release_record(
        receipts["G9"],
        expected_candidate=expected_candidate,
        receipt_hashes=receipt_hashes,
    )
    return expected_candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("validation/release-evidence.json"),
        help="repository-relative release evidence manifest",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=ROOT / "schemas" / "release-evidence.schema.json",
        help="release evidence JSON Schema",
    )
    parser.add_argument(
        "--checksums",
        type=Path,
        default=Path("bundle/CHECKSUMS.sha256"),
        help="repository-relative bundle checksum manifest",
    )
    parser.add_argument(
        "--profile-checksums",
        type=Path,
        default=Path("domain-profile/CHECKSUMS.sha256"),
        help="repository-relative domain profile checksum manifest",
    )
    parser.add_argument(
        "--build-receipt",
        type=Path,
        default=Path("bundle/build-receipt.json"),
        help="repository-relative bundle build receipt",
    )
    parser.add_argument(
        "--candidate-commit-sha",
        help=(
            "governed base commit containing the unchanged candidate tree; "
            "defaults to the manifest value"
        ),
    )
    args = parser.parse_args()

    try:
        declared_commit = declared_candidate_commit(
            ROOT,
            manifest_path=args.manifest,
            schema_path=args.schema,
        )
        if (
            args.candidate_commit_sha is not None
            and args.candidate_commit_sha != declared_commit
        ):
            raise ReleaseEvidenceError(
                "candidate commit override does not match the evidence manifest"
            )
        candidate_commit_sha = args.candidate_commit_sha or declared_commit
        candidate = candidate_identity_from_repository(
            ROOT,
            checksums_path=args.checksums,
            profile_checksums_path=args.profile_checksums,
            build_receipt_path=args.build_receipt,
            candidate_commit_sha=candidate_commit_sha,
        )
        evidence_commit_sha = validate_governed_candidate_commit(
            ROOT,
            candidate_commit_sha=candidate_commit_sha,
            build_receipt_path=args.build_receipt,
        )
        validate_release_evidence(
            ROOT,
            manifest_path=args.manifest,
            schema_path=args.schema,
            expected_candidate=candidate,
        )
    except (OSError, UnicodeError, ReleaseEvidenceError) as exc:
        print(f"release evidence failed closed: {exc}", file=sys.stderr)
        return 1

    print(
        "release evidence validated for exact candidate: "
        f"candidate commit {candidate.candidate_commit_sha}, "
        f"evidence commit {evidence_commit_sha}, "
        f"release root {candidate.release_root_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Assemble, but never perform, exact-candidate release checks.

The input is a small attestation manifest.  It must use this shape:

{
  "schema": "okf-release-assembly-input.v2",
  "generated_at": "<RFC 3339 date-time>",
  "gates": {
    "G1": {
      "status": "pass",
      "executed_at": "<RFC 3339 date-time>",
      "validator": {
        "name": "...",
        "version": "...",
        "artifact_path": "repository/path/to/validator-or-lock",
        "command": ["...", "..."]
      },
      "checks": {
        "<required check id>": {"status": "pass", "summary": "..."}
      },
      "evidence": ["repository/path/to/existing/evidence", "..."],
      "failures": [],
      "waivers": [],
      "review": {"mode": "...", "reviewers": [...]},
      "reviewed_checks": {
        "<review id>": {
          "status": "pass",
          "reviewer_identity": "...",
          "completed_at": "<RFC 3339 date-time>",
          "execution_mode": "..."
        }
      }
    },
    "...": {}
  },
  "release": {
    "status": "approved",
    "version": "...",
    "canonical_url": "https://...",
    "claims_reviewed": true,
    "approved_claims": ["Exact public claim approved by the owner", "..."],
    "residual_risks_reviewed": true,
    "residual_risk_ids": ["..."],
    "human_audit": {
      "status": "not_completed",
      "residual_risk_id": "...",
      "notes": "..."
    },
    "owner_approval": {
      "identity": "...",
      "kind": "human",
      "role": "project-owner",
      "approved_at": "<RFC 3339 date-time>",
      "approved": true,
      "binding": {
        "version": "...",
        "canonical_url": "https://...",
        "candidate": {"candidate_commit_sha": "...", "...": "..."},
        "pre_g9_manifest": {"path": "...", "sha256": "..."},
        "approved_receipts": [{"gate": "G1", "sha256": "..."}, "..."],
        "approved_claims": ["Exact public claim approved by the owner", "..."],
        "residual_risks": {
          "register": {"path": "governance/risk-register.json", "sha256": "..."},
          "ids": ["..."]
        },
        "human_audit": {"status": "not_completed", "...": "..."},
        "independent_review": {"identity": "...", "reviewed_at": "...", "...": "..."},
        "independent_review_evidence": {"path": "...", "sha256": "..."}
      }
    },
    "independent_review": {
      "kind": "ai-agent",
      "independent": true,
      "outcome": "recommend_approval",
      ...
    }
  }
}

All pass outcomes, timestamps, commands, reviewer identities and approvals are
therefore supplied by evidence producers and the project owner.  This module
does not run a check, infer a pass, add a waiver, create supporting evidence or
claim that a human audit occurred.  It derives CandidateIdentity using the
fail-closed release checker, rehashes existing evidence and validator
artefacts, validates each output against the release-evidence schema, and
writes deterministic receipts.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.check_release_evidence import (
    GATE_RECEIPTS,
    MAX_EVIDENCE_BYTES,
    MAX_JSON_BYTES,
    REVIEWED_GATES,
    REQUIRED_CHECKS,
    SCHEMA_ID,
    CandidateIdentity,
    ReleaseCoordinates,
    ReleaseEvidenceError,
    canonical_identity_text,
    candidate_identity_from_repository,
    load_json,
    load_json_bytes,
    parse_utc_timestamp,
    read_repository_file_bytes,
    release_coordinates_from_build_config,
    repository_argument,
    safe_repository_file,
    schema_validator,
    sha256_file,
    validate_document_schema,
    validate_gate_receipt,
    validate_g8_archive_evidence,
    validate_governed_candidate_commit,
    validate_independent_review_evidence_document,
    validate_release_record,
)


ROOT = Path(__file__).resolve().parents[1]
INPUT_SCHEMA = "okf-release-assembly-input.v2"
PRE_G9_INPUT_SCHEMA = "okf-pre-g9-assembly-input.v1"
PRE_G9_MANIFEST_SCHEMA = "okf-pre-g9-evidence-manifest.v1"
OUTPUT_RECEIPT_DIRECTORY = "receipts"
GATE_INPUT_KEYS = frozenset(
    {
        "status",
        "executed_at",
        "validator",
        "checks",
        "evidence",
        "failures",
        "waivers",
        "review",
        "reviewed_checks",
    }
)
RELEASE_INPUT_KEYS = frozenset(
    {
        "status",
        "version",
        "canonical_url",
        "claims_reviewed",
        "approved_claims",
        "residual_risks_reviewed",
        "residual_risk_ids",
        "human_audit",
        "owner_approval",
        "independent_review",
    }
)


def canonical_json_bytes(value: dict[str, Any]) -> bytes:
    """Return the repository's deterministic pretty-printed JSON encoding."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseEvidenceError(f"{label} must be an object")
    return value


def _exact_keys(
    value: dict[str, Any],
    expected: set[str] | frozenset[str],
    *,
    label: str,
) -> None:
    actual = set(value)
    if actual == set(expected):
        return
    missing = sorted(set(expected) - actual)
    extra = sorted(actual - set(expected))
    raise ReleaseEvidenceError(
        f"{label} has an invalid field set; missing={missing}, extra={extra}"
    )


def _non_empty_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReleaseEvidenceError(f"{label} must be a non-empty string")
    return value


def _safe_output_directory(repository_root: Path, relative_name: Path) -> Path:
    name = relative_name.as_posix()
    if (
        not name
        or name == "."
        or "\\" in name
        or "\x00" in name
    ):
        raise ReleaseEvidenceError(f"unsafe output directory: {name!r}")
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ReleaseEvidenceError(f"unsafe output directory: {name!r}")

    root = repository_root.resolve()
    candidate = root.joinpath(*relative.parts)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ReleaseEvidenceError(
            f"output directory escapes repository: {name!r}"
        ) from exc

    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ReleaseEvidenceError(
                f"output directory contains a symbolic link: {name!r}"
            )
        if current.exists() and not current.is_dir():
            raise ReleaseEvidenceError(
                f"output directory component is not a directory: {name!r}"
            )
    return candidate


def _version_scoped_output_directory(
    repository_root: Path,
    relative_name: Path,
    *,
    version: str,
    phase: str,
) -> Path:
    """Require the one versioned writer location for this release phase."""

    expected = Path("validation") / f"candidate-v{version}" / phase
    if relative_name.as_posix() != expected.as_posix():
        raise ReleaseEvidenceError(
            f"{phase} output directory must be exactly "
            f"{expected.as_posix()!r}; received {relative_name.as_posix()!r}"
        )
    return _safe_output_directory(repository_root, relative_name)


def _output_paths(output_directory: Path) -> dict[str, Path]:
    paths = {
        gate: output_directory
        / OUTPUT_RECEIPT_DIRECTORY
        / f"{gate.lower()}.json"
        for gate in GATE_RECEIPTS
    }
    paths["G9"] = output_directory / "release-record.json"
    paths["manifest"] = output_directory / "release-evidence.json"
    return paths


def _pre_g9_output_paths(output_directory: Path) -> dict[str, Path]:
    paths = {
        gate: output_directory
        / OUTPUT_RECEIPT_DIRECTORY
        / f"{gate.lower()}.json"
        for gate in GATE_RECEIPTS
    }
    paths["manifest"] = output_directory / "pre-g9-evidence.json"
    return paths


def _preflight_output_paths(
    repository_root: Path,
    output_directory: Path,
    paths: dict[str, Path],
    *,
    replace: bool,
) -> None:
    root = repository_root.resolve()
    for label, path in paths.items():
        try:
            path.resolve().relative_to(root)
        except ValueError as exc:
            raise ReleaseEvidenceError(
                f"output target for {label} escapes repository: {path}"
            ) from exc

        current = output_directory
        relative = path.relative_to(output_directory)
        for part in relative.parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise ReleaseEvidenceError(
                    f"output target for {label} contains a symbolic-link "
                    f"directory: {current}"
                )
            if current.exists() and not current.is_dir():
                raise ReleaseEvidenceError(
                    f"output parent for {label} is not a directory: {current}"
                )
        if path.is_symlink():
            raise ReleaseEvidenceError(
                f"refusing symbolic-link output for {label}: {path}"
            )
        if not path.exists():
            continue
        if not path.is_file():
            raise ReleaseEvidenceError(
                f"output target for {label} is not a regular file: {path}"
            )
        if not replace:
            raise ReleaseEvidenceError(
                f"output target already exists for {label}: {path}; "
                "use --replace only after reviewing the existing evidence"
            )


def _reviewer(
    value: Any,
    *,
    label: str,
) -> dict[str, Any]:
    reviewer = _object(value, label=label)
    _exact_keys(
        reviewer,
        {"identity", "kind", "role", "reviewed_at", "independent"},
        label=label,
    )
    canonical_identity_text(reviewer["identity"], label=f"{label}.identity")
    canonical_identity_text(reviewer["role"], label=f"{label}.role")
    if reviewer["kind"] not in {"ai-agent", "human"}:
        raise ReleaseEvidenceError(
            f"{label}.kind must be 'ai-agent' or 'human'"
        )
    if not isinstance(reviewer["independent"], bool):
        raise ReleaseEvidenceError(f"{label}.independent must be a boolean")
    _non_empty_string(reviewer["reviewed_at"], label=f"{label}.reviewed_at")
    return reviewer


def _review(
    value: Any,
    *,
    gate: str,
    identity_registry: dict[str, tuple[str, bool]],
) -> dict[str, Any]:
    review = _object(value, label=f"{gate}.review")
    _exact_keys(review, {"mode", "reviewers"}, label=f"{gate}.review")
    if review["mode"] not in {
        "automated",
        "automated-agent-review",
        "human-review",
        "mixed",
    }:
        raise ReleaseEvidenceError(f"{gate}.review.mode is unsupported")
    if not isinstance(review["reviewers"], list):
        raise ReleaseEvidenceError(f"{gate}.review.reviewers must be an array")

    reviewers: list[dict[str, Any]] = []
    local_identities: set[str] = set()
    for index, raw_reviewer in enumerate(review["reviewers"]):
        reviewer = _reviewer(
            raw_reviewer,
            label=f"{gate}.review.reviewers[{index}]",
        )
        identity = reviewer["identity"]
        if identity in local_identities:
            raise ReleaseEvidenceError(
                f"{gate} has duplicate reviewer identity {identity!r}"
            )
        local_identities.add(identity)
        signature = (reviewer["kind"], reviewer["independent"])
        previous = identity_registry.get(identity)
        if previous is not None and previous != signature:
            raise ReleaseEvidenceError(
                f"reviewer identity {identity!r} has inconsistent kind or "
                "independence across gates"
            )
        identity_registry[identity] = signature
        reviewers.append(reviewer)
    return {"mode": review["mode"], "reviewers": reviewers}


def _validator(
    repository_root: Path,
    value: Any,
    *,
    gate: str,
    forbidden_paths: set[Path],
) -> dict[str, Any]:
    validator = _object(value, label=f"{gate}.validator")
    _exact_keys(
        validator,
        {"name", "version", "artifact_path", "command"},
        label=f"{gate}.validator",
    )
    name = _non_empty_string(
        validator["name"], label=f"{gate}.validator.name"
    )
    version = _non_empty_string(
        validator["version"], label=f"{gate}.validator.version"
    )
    artefact_name = _non_empty_string(
        validator["artifact_path"],
        label=f"{gate}.validator.artifact_path",
    )
    artefact = safe_repository_file(
        repository_root, artefact_name, purpose=f"{gate} validator artefact"
    )
    if artefact.resolve() in forbidden_paths:
        raise ReleaseEvidenceError(
            f"{gate} validator artefact cannot reference generated release "
            f"evidence: {artefact_name!r}"
        )
    command = validator["command"]
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(part, str) or not part for part in command)
    ):
        raise ReleaseEvidenceError(
            f"{gate}.validator.command must be a non-empty array of "
            "non-empty strings"
        )
    return {
        "name": name,
        "version": version,
        "sha256": sha256_bytes(
            read_repository_file_bytes(
                repository_root,
                artefact_name,
                purpose=f"{gate} validator artefact",
                max_bytes=MAX_EVIDENCE_BYTES,
            )
        ),
        "command": list(command),
    }


def _checks(value: Any, *, gate: str) -> list[dict[str, Any]]:
    checks = _object(value, label=f"{gate}.checks")
    if not checks:
        raise ReleaseEvidenceError(f"{gate}.checks must not be empty")
    missing = REQUIRED_CHECKS[gate] - set(checks)
    if missing:
        raise ReleaseEvidenceError(
            f"{gate} is missing required input checks: "
            f"{', '.join(sorted(missing))}"
        )

    result: list[dict[str, Any]] = []
    for check_id in sorted(checks):
        _non_empty_string(check_id, label=f"{gate} check id")
        check = _object(checks[check_id], label=f"{gate}.checks.{check_id}")
        _exact_keys(
            check,
            {"status", "summary"},
            label=f"{gate}.checks.{check_id}",
        )
        if check["status"] != "pass":
            raise ReleaseEvidenceError(
                f"{gate} input check {check_id!r} is not pass"
            )
        summary = _non_empty_string(
            check["summary"],
            label=f"{gate}.checks.{check_id}.summary",
        )
        result.append(
            {"id": check_id, "status": "pass", "summary": summary}
        )
    return result


def _evidence(
    repository_root: Path,
    value: Any,
    *,
    gate: str,
    forbidden_paths: set[Path],
) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ReleaseEvidenceError(f"{gate}.evidence must be a non-empty array")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw_name in enumerate(value):
        name = _non_empty_string(
            raw_name, label=f"{gate}.evidence[{index}]"
        )
        if name in seen:
            raise ReleaseEvidenceError(
                f"{gate} has duplicate evidence path {name!r}"
            )
        seen.add(name)
        path = safe_repository_file(
            repository_root, name, purpose=f"{gate} evidence"
        )
        if path.resolve() in forbidden_paths:
            raise ReleaseEvidenceError(
                f"{gate} evidence cannot reference generated release evidence: "
                f"{name!r}"
            )
        result.append(
            {
                "path": name,
                "sha256": sha256_bytes(
                    read_repository_file_bytes(
                        repository_root,
                        name,
                        purpose=f"{gate} evidence",
                        max_bytes=MAX_EVIDENCE_BYTES,
                    )
                ),
            }
        )
    return result


def _reviewed_checks(
    value: Any,
    *,
    gate: str,
    review: dict[str, Any],
) -> list[dict[str, Any]]:
    checks = _object(value, label=f"{gate}.reviewed_checks")
    reviewer_ids = {
        reviewer["identity"] for reviewer in review["reviewers"]
    }
    result: list[dict[str, Any]] = []
    for check_id in sorted(checks):
        _non_empty_string(check_id, label=f"{gate} reviewed check id")
        check = _object(
            checks[check_id], label=f"{gate}.reviewed_checks.{check_id}"
        )
        _exact_keys(
            check,
            {
                "status",
                "reviewer_identity",
                "completed_at",
                "execution_mode",
            },
            label=f"{gate}.reviewed_checks.{check_id}",
        )
        if check["status"] != "pass":
            raise ReleaseEvidenceError(
                f"{gate} reviewed input check {check_id!r} is not pass"
            )
        reviewer_identity = canonical_identity_text(
            check["reviewer_identity"],
            label=(
                f"{gate}.reviewed_checks.{check_id}.reviewer_identity"
            ),
        )
        if reviewer_identity not in reviewer_ids:
            raise ReleaseEvidenceError(
                f"{gate} reviewed check {check_id!r} names undeclared "
                f"reviewer {reviewer_identity!r}"
            )
        completed_at = _non_empty_string(
            check["completed_at"],
            label=f"{gate}.reviewed_checks.{check_id}.completed_at",
        )
        execution_mode = check["execution_mode"]
        if execution_mode not in {
            "automated-agent",
            "interactive-browser",
            "document-review",
            "human",
        }:
            raise ReleaseEvidenceError(
                f"{gate} reviewed check {check_id!r} has unsupported "
                "execution_mode"
            )
        result.append(
            {
                "id": check_id,
                "status": "pass",
                "reviewer_identity": reviewer_identity,
                "completed_at": completed_at,
                "execution_mode": execution_mode,
            }
        )

    if gate in REVIEWED_GATES:
        if not result:
            raise ReleaseEvidenceError(
                f"{gate} requires at least one explicit reviewed check"
            )
        if not any(
            reviewer["independent"] is True
            for reviewer in review["reviewers"]
        ):
            raise ReleaseEvidenceError(
                f"{gate} requires an independent reviewer"
            )
    return result


def _gate_receipt(
    repository_root: Path,
    *,
    gate: str,
    raw_gate: Any,
    candidate: CandidateIdentity,
    forbidden_paths: set[Path],
    identity_registry: dict[str, tuple[str, bool]],
) -> dict[str, Any]:
    gate_input = _object(raw_gate, label=gate)
    _exact_keys(gate_input, GATE_INPUT_KEYS, label=gate)
    if gate_input["status"] != "pass":
        raise ReleaseEvidenceError(
            f"{gate} input status is not pass: {gate_input['status']!r}"
        )
    if gate_input["failures"] != []:
        raise ReleaseEvidenceError(f"{gate} input contains failures")
    if gate_input["waivers"] != []:
        raise ReleaseEvidenceError(f"{gate} input contains waivers")

    executed_at = _non_empty_string(
        gate_input["executed_at"], label=f"{gate}.executed_at"
    )
    review = _review(
        gate_input["review"],
        gate=gate,
        identity_registry=identity_registry,
    )
    receipt = {
        "$schema": SCHEMA_ID,
        "schema": "okf-gate-receipt.v1",
        "gate": gate,
        "status": "pass",
        "candidate": asdict(candidate),
        "executed_at": executed_at,
        "validator": _validator(
            repository_root,
            gate_input["validator"],
            gate=gate,
            forbidden_paths=forbidden_paths,
        ),
        "checks": _checks(gate_input["checks"], gate=gate),
        "evidence": _evidence(
            repository_root,
            gate_input["evidence"],
            gate=gate,
            forbidden_paths=forbidden_paths,
        ),
        "failures": [],
        "waivers": [],
        "review": review,
        "reviewed_checks": _reviewed_checks(
            gate_input["reviewed_checks"],
            gate=gate,
            review=review,
        ),
    }
    return receipt


def _sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ReleaseEvidenceError(
            f"{label} must be exactly 64 lowercase hexadecimal characters"
        )
    return value


def _non_empty_unique_strings(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ReleaseEvidenceError(f"{label} must be a non-empty array")
    result: list[str] = []
    for index, item in enumerate(value):
        text = _non_empty_string(item, label=f"{label}[{index}]")
        if text != text.strip():
            raise ReleaseEvidenceError(
                f"{label}[{index}] must not have leading or trailing whitespace"
            )
        result.append(text)
    if len(result) != len(set(result)):
        raise ReleaseEvidenceError(f"{label} must contain unique strings")
    return result


def _digest_reference(
    repository_root: Path,
    value: Any,
    *,
    label: str,
    purpose: str,
    forbidden_paths: set[Path],
    max_bytes: int = MAX_JSON_BYTES,
) -> tuple[dict[str, str], bytes]:
    reference = _object(value, label=label)
    _exact_keys(reference, {"path", "sha256"}, label=label)
    name = _non_empty_string(reference["path"], label=f"{label}.path")
    expected_digest = _sha256(
        reference["sha256"], label=f"{label}.sha256"
    )
    path = safe_repository_file(repository_root, name, purpose=purpose)
    if path.resolve() in forbidden_paths:
        raise ReleaseEvidenceError(
            f"{label} cannot reference generated release evidence: {name!r}"
        )
    file_bytes = read_repository_file_bytes(
        repository_root,
        name,
        purpose=purpose,
        max_bytes=max_bytes,
    )
    actual_digest = sha256_bytes(file_bytes)
    if actual_digest != expected_digest:
        raise ReleaseEvidenceError(
            f"{label} digest mismatch: declared {expected_digest}, "
            f"calculated {actual_digest}"
        )
    return {"path": name, "sha256": expected_digest}, file_bytes


def _approved_receipts(
    value: Any,
    *,
    label: str,
) -> tuple[list[dict[str, str]], dict[str, str]]:
    if not isinstance(value, list):
        raise ReleaseEvidenceError(f"{label} must be an array")
    approved: dict[str, str] = {}
    for index, raw_reference in enumerate(value):
        item_label = f"{label}[{index}]"
        reference = _object(raw_reference, label=item_label)
        _exact_keys(reference, {"gate", "sha256"}, label=item_label)
        gate = _non_empty_string(
            reference["gate"], label=f"{item_label}.gate"
        )
        if gate not in GATE_RECEIPTS:
            raise ReleaseEvidenceError(
                f"{item_label}.gate must identify G1-G8"
            )
        if gate in approved:
            raise ReleaseEvidenceError(
                f"{label} contains duplicate receipt for {gate}"
            )
        approved[gate] = _sha256(
            reference["sha256"], label=f"{item_label}.sha256"
        )
    if set(approved) != set(GATE_RECEIPTS):
        missing = sorted(set(GATE_RECEIPTS) - set(approved))
        extra = sorted(set(approved) - set(GATE_RECEIPTS))
        raise ReleaseEvidenceError(
            f"{label} must bind exactly G1-G8; missing={missing}, extra={extra}"
        )
    return (
        [
            {"gate": gate, "sha256": approved[gate]}
            for gate in GATE_RECEIPTS
        ],
        approved,
    )


def _pre_g9_approval_reference(
    repository_root: Path,
    value: Any,
    *,
    candidate: CandidateIdentity,
    receipt_hashes: dict[str, str],
    forbidden_paths: set[Path],
) -> tuple[dict[str, str], datetime]:
    reference, manifest_bytes = _digest_reference(
        repository_root,
        value,
        label="release.owner_approval.binding.pre_g9_manifest",
        purpose="owner-approved pre-G9 manifest",
        forbidden_paths=forbidden_paths,
    )
    manifest = load_json_bytes(
        manifest_bytes, label="owner-approved pre-G9 manifest"
    )
    _exact_keys(
        manifest,
        {
            "schema",
            "status",
            "generated_at",
            "candidate",
            "receipts",
            "limitations",
        },
        label="owner-approved pre-G9 manifest",
    )
    if manifest["schema"] != PRE_G9_MANIFEST_SCHEMA:
        raise ReleaseEvidenceError(
            "owner-approved pre-G9 manifest has the wrong schema"
        )
    if manifest["status"] != "ready_for_owner_review":
        raise ReleaseEvidenceError(
            "owner-approved pre-G9 manifest is not ready_for_owner_review"
        )
    if manifest["candidate"] != asdict(candidate):
        raise ReleaseEvidenceError(
            "owner-approved pre-G9 manifest candidate differs from the "
            "exact repository candidate"
        )
    generated_at = parse_utc_timestamp(
        manifest["generated_at"],
        label="owner-approved pre-G9 manifest.generated_at",
    )
    _non_empty_unique_strings(
        manifest["limitations"],
        label="owner-approved pre-G9 manifest.limitations",
    )

    raw_receipts = manifest["receipts"]
    if not isinstance(raw_receipts, list):
        raise ReleaseEvidenceError(
            "owner-approved pre-G9 manifest.receipts must be an array"
        )
    manifest_hashes: dict[str, str] = {}
    for index, raw_receipt in enumerate(raw_receipts):
        item_label = f"owner-approved pre-G9 manifest.receipts[{index}]"
        receipt = _object(raw_receipt, label=item_label)
        _exact_keys(
            receipt, {"gate", "path", "sha256"}, label=item_label
        )
        gate = _non_empty_string(
            receipt["gate"], label=f"{item_label}.gate"
        )
        if gate not in GATE_RECEIPTS:
            raise ReleaseEvidenceError(
                f"{item_label}.gate must identify G1-G8"
            )
        if gate in manifest_hashes:
            raise ReleaseEvidenceError(
                f"owner-approved pre-G9 manifest has duplicate {gate} receipt"
            )
        receipt_reference, _ = _digest_reference(
            repository_root,
            {"path": receipt["path"], "sha256": receipt["sha256"]},
            label=item_label,
            purpose=f"owner-approved pre-G9 {gate} receipt",
            forbidden_paths=forbidden_paths,
        )
        manifest_hashes[gate] = receipt_reference["sha256"]
    if manifest_hashes != receipt_hashes:
        raise ReleaseEvidenceError(
            "owner-approved pre-G9 receipt hashes do not match the exact "
            "G1-G8 assembly"
        )
    return reference, generated_at


def _governed_residual_risks(
    repository_root: Path,
    value: Any,
    *,
    release_risk_ids: list[str],
    forbidden_paths: set[Path],
) -> dict[str, Any]:
    residual_risks = _object(
        value, label="release.owner_approval.binding.residual_risks"
    )
    _exact_keys(
        residual_risks,
        {"register", "ids"},
        label="release.owner_approval.binding.residual_risks",
    )
    register_reference, register_bytes = _digest_reference(
        repository_root,
        residual_risks["register"],
        label="release.owner_approval.binding.residual_risks.register",
        purpose="owner-approved governed residual-risk register",
        forbidden_paths=forbidden_paths,
    )
    if register_reference["path"] != "governance/risk-register.json":
        raise ReleaseEvidenceError(
            "owner approval must bind governance/risk-register.json as the "
            "governed residual-risk register"
        )
    register = load_json_bytes(
        register_bytes,
        label="owner-approved governed residual-risk register",
    )
    if register.get("schema") != "okf-risk-register.v1":
        raise ReleaseEvidenceError(
            "owner-approved governed residual-risk register has the wrong schema"
        )
    raw_risks = register.get("risks")
    if not isinstance(raw_risks, list) or not raw_risks:
        raise ReleaseEvidenceError(
            "owner-approved governed residual-risk register has no risks"
        )
    governed_ids: list[str] = []
    for index, risk in enumerate(raw_risks):
        if not isinstance(risk, dict):
            raise ReleaseEvidenceError(
                f"governed residual risk {index} is not an object"
            )
        risk_id = _non_empty_string(
            risk.get("id"), label=f"governed residual risk {index}.id"
        )
        if "residual" not in risk or "release_disposition" not in risk:
            raise ReleaseEvidenceError(
                f"governed residual risk {risk_id!r} lacks residual or "
                "release disposition"
            )
        governed_ids.append(risk_id)
    if len(governed_ids) != len(set(governed_ids)):
        raise ReleaseEvidenceError(
            "governed residual-risk register contains duplicate risk IDs"
        )

    approved_ids = _non_empty_unique_strings(
        residual_risks["ids"],
        label="release.owner_approval.binding.residual_risks.ids",
    )
    if set(approved_ids) != set(governed_ids):
        raise ReleaseEvidenceError(
            "owner-approved residual-risk IDs do not equal the governed "
            "residual-risk set"
        )
    if set(release_risk_ids) != set(governed_ids):
        raise ReleaseEvidenceError(
            "release.residual_risk_ids do not equal the governed "
            "residual-risk set"
        )
    return {
        "register": register_reference,
        "ids": list(approved_ids),
    }


def _release_record(
    value: Any,
    *,
    repository_root: Path,
    candidate: CandidateIdentity,
    expected_coordinates: ReleaseCoordinates,
    receipt_hashes: dict[str, str],
    gate_receipts: dict[str, dict[str, Any]],
    gate_identity_registry: dict[str, tuple[str, bool]],
    forbidden_paths: set[Path],
) -> dict[str, Any]:
    release = _object(value, label="release")
    _exact_keys(release, RELEASE_INPUT_KEYS, label="release")
    if release["status"] != "approved":
        raise ReleaseEvidenceError("release input status is not approved")
    release_version = _non_empty_string(
        release["version"], label="release.version"
    )
    canonical_url = _non_empty_string(
        release["canonical_url"], label="release.canonical_url"
    )
    if release_version != expected_coordinates.version:
        raise ReleaseEvidenceError(
            "release.version must equal governed "
            "source/build-config.json version"
        )
    if canonical_url != expected_coordinates.canonical_url:
        raise ReleaseEvidenceError(
            "release.canonical_url must equal governed "
            "source/build-config.json publication_base"
        )
    if release["claims_reviewed"] is not True:
        raise ReleaseEvidenceError(
            "release input must explicitly affirm claims_reviewed"
        )
    approved_claims = _non_empty_unique_strings(
        release["approved_claims"], label="release.approved_claims"
    )
    if release["residual_risks_reviewed"] is not True:
        raise ReleaseEvidenceError(
            "release input must explicitly affirm residual_risks_reviewed"
        )

    residual_risk_ids = _non_empty_unique_strings(
        release["residual_risk_ids"], label="release.residual_risk_ids"
    )

    human_audit = _object(
        release["human_audit"], label="release.human_audit"
    )
    _exact_keys(
        human_audit,
        {"status", "residual_risk_id", "notes"},
        label="release.human_audit",
    )
    if human_audit["status"] != "not_completed":
        raise ReleaseEvidenceError(
            "this AI-agent assembler requires human audit status "
            "'not_completed'; completed human audit evidence needs a separate "
            "reviewed workflow"
        )
    audit_risk = _non_empty_string(
        human_audit["residual_risk_id"],
        label="release.human_audit.residual_risk_id",
    )
    if audit_risk not in residual_risk_ids:
        raise ReleaseEvidenceError(
            "the not-completed human audit is not retained in "
            "release.residual_risk_ids"
        )
    _non_empty_string(
        human_audit["notes"], label="release.human_audit.notes"
    )

    owner = _object(
        release["owner_approval"], label="release.owner_approval"
    )
    _exact_keys(
        owner,
        {
            "identity",
            "kind",
            "role",
            "approved_at",
            "approved",
            "binding",
        },
        label="release.owner_approval",
    )
    owner_identity = canonical_identity_text(
        owner["identity"], label="release.owner_approval.identity"
    )
    if owner["kind"] not in {"human", "organisation"}:
        raise ReleaseEvidenceError(
            "release.owner_approval.kind must be 'human' or 'organisation'"
        )
    if owner["role"] != "project-owner":
        raise ReleaseEvidenceError(
            "release.owner_approval.role must be 'project-owner'"
        )
    _non_empty_string(
        owner["approved_at"], label="release.owner_approval.approved_at"
    )
    if owner["approved"] is not True:
        raise ReleaseEvidenceError(
            "release.owner_approval.approved must be true"
        )

    binding = _object(
        owner["binding"], label="release.owner_approval.binding"
    )
    _exact_keys(
        binding,
        {
            "version",
            "canonical_url",
            "candidate",
            "pre_g9_manifest",
            "approved_receipts",
            "approved_claims",
            "residual_risks",
            "human_audit",
            "independent_review",
            "independent_review_evidence",
        },
        label="release.owner_approval.binding",
    )
    if binding["version"] != release_version:
        raise ReleaseEvidenceError(
            "release.owner_approval.binding.version does not match "
            "release.version"
        )
    if binding["canonical_url"] != canonical_url:
        raise ReleaseEvidenceError(
            "release.owner_approval.binding.canonical_url does not match "
            "release.canonical_url"
        )
    if binding["candidate"] != asdict(candidate):
        raise ReleaseEvidenceError(
            "release.owner_approval.binding.candidate differs from the exact "
            "repository candidate"
        )
    pre_g9_reference, pre_g9_generated_at = _pre_g9_approval_reference(
        repository_root,
        binding["pre_g9_manifest"],
        candidate=candidate,
        receipt_hashes=receipt_hashes,
        forbidden_paths=forbidden_paths,
    )
    approved_receipt_list, approved_receipt_hashes = _approved_receipts(
        binding["approved_receipts"],
        label="release.owner_approval.binding.approved_receipts",
    )
    if approved_receipt_hashes != receipt_hashes:
        raise ReleaseEvidenceError(
            "release.owner_approval.binding.approved_receipts do not match "
            "the exact G1-G8 assembly"
        )
    owner_claims = _non_empty_unique_strings(
        binding["approved_claims"],
        label="release.owner_approval.binding.approved_claims",
    )
    if owner_claims != approved_claims:
        raise ReleaseEvidenceError(
            "release.owner_approval.binding.approved_claims do not match "
            "release.approved_claims"
        )
    governed_residual_risks = _governed_residual_risks(
        repository_root,
        binding["residual_risks"],
        release_risk_ids=residual_risk_ids,
        forbidden_paths=forbidden_paths,
    )
    if binding["human_audit"] != human_audit:
        raise ReleaseEvidenceError(
            "release.owner_approval.binding.human_audit does not match "
            "release.human_audit"
        )
    owner_record = {
        "identity": owner_identity,
        "kind": owner["kind"],
        "role": owner["role"],
        "approved_at": owner["approved_at"],
        "approved": True,
        "binding": {
            "version": release_version,
            "canonical_url": canonical_url,
            "candidate": asdict(candidate),
            "pre_g9_manifest": pre_g9_reference,
            "approved_receipts": approved_receipt_list,
            "approved_claims": list(owner_claims),
            "residual_risks": governed_residual_risks,
            "human_audit": dict(human_audit),
        },
    }

    independent_review = _object(
        release["independent_review"],
        label="release.independent_review",
    )
    _exact_keys(
        independent_review,
        {
            "identity",
            "kind",
            "role",
            "reviewed_at",
            "independent",
            "outcome",
        },
        label="release.independent_review",
    )
    review_identity = canonical_identity_text(
        independent_review["identity"],
        label="release.independent_review.identity",
    )
    if independent_review["kind"] != "ai-agent":
        raise ReleaseEvidenceError(
            "release independent review must disclose kind 'ai-agent'"
        )
    if independent_review["independent"] is not True:
        raise ReleaseEvidenceError(
            "release independent review must be explicitly independent"
        )
    if independent_review["outcome"] != "recommend_approval":
        raise ReleaseEvidenceError(
            "release independent review does not recommend approval"
        )
    canonical_identity_text(
        independent_review["role"],
        label="release.independent_review.role",
    )
    _non_empty_string(
        independent_review["reviewed_at"],
        label="release.independent_review.reviewed_at",
    )
    if review_identity == owner_identity:
        raise ReleaseEvidenceError(
            "release independent reviewer and project owner identities match"
        )
    previous = gate_identity_registry.get(review_identity)
    if previous is not None and previous != ("ai-agent", True):
        raise ReleaseEvidenceError(
            "release independent reviewer identity conflicts with its gate "
            "review metadata"
        )
    for identity, (_, independent) in gate_identity_registry.items():
        if identity == owner_identity and independent:
            raise ReleaseEvidenceError(
                "project owner is declared as an independent gate reviewer"
            )

    if binding["independent_review"] != independent_review:
        raise ReleaseEvidenceError(
            "release.owner_approval.binding.independent_review does not "
            "match release.independent_review"
        )
    review_evidence_reference, review_evidence_bytes = _digest_reference(
        repository_root,
        binding["independent_review_evidence"],
        label=(
            "release.owner_approval.binding.independent_review_evidence"
        ),
        purpose="owner-approved independent release-review evidence",
        forbidden_paths=forbidden_paths,
    )
    review_evidence = load_json_bytes(
        review_evidence_bytes,
        label="owner-approved independent release-review evidence",
    )
    validate_independent_review_evidence_document(
        review_evidence,
        expected_candidate=candidate,
        expected_review=independent_review,
        pre_g9_manifest_sha256=pre_g9_reference["sha256"],
        approved_claims=approved_claims,
        residual_risk_ids=residual_risk_ids,
    )

    gate_times = {
        gate: parse_utc_timestamp(
            gate_receipts[gate].get("executed_at"),
            label=f"{gate}.executed_at",
        )
        for gate in GATE_RECEIPTS
    }
    reviewed_at = parse_utc_timestamp(
        independent_review["reviewed_at"],
        label="release.independent_review.reviewed_at",
    )
    approved_at = parse_utc_timestamp(
        owner["approved_at"],
        label="release.owner_approval.approved_at",
    )
    later_gate = max(gate_times, key=gate_times.__getitem__)
    if gate_times[later_gate] > pre_g9_generated_at:
        raise ReleaseEvidenceError(
            f"release chronology is invalid: {later_gate}.executed_at is "
            "after the pre-G9 manifest"
        )
    if pre_g9_generated_at > reviewed_at:
        raise ReleaseEvidenceError(
            "release chronology is invalid: independent review predates "
            "the pre-G9 manifest"
        )
    if reviewed_at > approved_at:
        raise ReleaseEvidenceError(
            "release chronology is invalid: owner approval predates the "
            "independent review"
        )
    owner_record["binding"]["independent_review"] = dict(
        independent_review
    )
    owner_record["binding"]["independent_review_evidence"] = (
        review_evidence_reference
    )

    return {
        "$schema": SCHEMA_ID,
        "schema": "okf-release-record.v1",
        "gate": "G9",
        "status": "approved",
        "candidate": asdict(candidate),
        "version": release_version,
        "canonical_url": canonical_url,
        "claims_reviewed": True,
        "approved_claims": list(approved_claims),
        "residual_risks_reviewed": True,
        "residual_risk_ids": list(residual_risk_ids),
        "human_audit": dict(human_audit),
        "owner_approval": owner_record,
        "independent_review": dict(independent_review),
        "approved_receipts": [
            {"gate": gate, "sha256": receipt_hashes[gate]}
            for gate in GATE_RECEIPTS
        ],
    }


def _write_documents_atomically(
    repository_root: Path,
    output_directory: Path,
    *,
    documents: dict[Path, bytes],
    replace: bool,
) -> None:
    """Publish complete files without following or replacing path entries.

    POSIX does not provide a portable atomic transaction for a populated set
    of files.  Each complete staged file is therefore linked atomically into
    a held directory, the manifest is linked last as the set's commit marker,
    and every link created by a failed invocation is rolled back.
    """

    root = repository_root.resolve()
    output_relative = output_directory.relative_to(root)
    document_rows: list[tuple[Path, bytes]] = []
    for target, content in documents.items():
        relative = target.relative_to(output_directory)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or len(relative.parts) not in {1, 2}
            or (len(relative.parts) == 2 and relative.parts[0] != "receipts")
        ):
            raise ReleaseEvidenceError(
                f"unsupported release evidence output path: {relative}"
            )
        document_rows.append((relative, content))

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    read_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    read_flags |= getattr(os, "O_NOFOLLOW", 0)
    read_flags |= getattr(os, "O_NONBLOCK", 0)
    write_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    write_flags |= getattr(os, "O_CLOEXEC", 0)
    write_flags |= getattr(os, "O_NOFOLLOW", 0)

    def same_inode(first: os.stat_result, second: os.stat_result) -> bool:
        return (
            first.st_dev,
            first.st_ino,
            stat.S_IFMT(first.st_mode),
        ) == (
            second.st_dev,
            second.st_ino,
            stat.S_IFMT(second.st_mode),
        )

    def close_all(descriptors: list[int]) -> None:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass

    def open_directory_chain(
        relative: Path, *, create: bool
    ) -> tuple[list[int], list[bool]]:
        descriptors = [os.open(root, directory_flags)]
        created = [False]
        try:
            for part in relative.parts:
                parent = descriptors[-1]
                made = False
                try:
                    before = os.stat(
                        part, dir_fd=parent, follow_symlinks=False
                    )
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(part, mode=0o755, dir_fd=parent)
                        made = True
                    except FileExistsError:
                        pass
                    before = os.stat(
                        part, dir_fd=parent, follow_symlinks=False
                    )
                if not stat.S_ISDIR(before.st_mode):
                    raise ReleaseEvidenceError(
                        "release evidence output path contains a non-directory "
                        "or symbolic link: "
                        f"{Path(*relative.parts[:len(descriptors)])}"
                    )
                child = os.open(part, directory_flags, dir_fd=parent)
                opened = os.fstat(child)
                if not same_inode(before, opened):
                    os.close(child)
                    raise ReleaseEvidenceError(
                        "release evidence output directory changed while "
                        f"being opened: {relative.as_posix()!r}"
                    )
                descriptors.append(child)
                created.append(made)
            return descriptors, created
        except BaseException:
            close_all(descriptors)
            raise

    def open_child_directory(
        parent: int, name: str, *, create: bool
    ) -> tuple[int, bool]:
        made = False
        try:
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            if not create:
                raise
            try:
                os.mkdir(name, mode=0o755, dir_fd=parent)
                made = True
            except FileExistsError:
                pass
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(before.st_mode):
            raise ReleaseEvidenceError(
                f"release evidence output directory is not real: {name!r}"
            )
        child = os.open(name, directory_flags, dir_fd=parent)
        if not same_inode(before, os.fstat(child)):
            os.close(child)
            raise ReleaseEvidenceError(
                f"release evidence output directory changed: {name!r}"
            )
        return child, made

    def write_complete_file(parent: int, name: str, content: bytes) -> None:
        descriptor = os.open(
            name,
            write_flags,
            0o644,
            dir_fd=parent,
        )
        try:
            view = memoryview(content)
            written = 0
            while written < len(view):
                count = os.write(descriptor, view[written:])
                if count <= 0:
                    raise ReleaseEvidenceError(
                        f"short staged evidence write for {name!r}"
                    )
                written += count
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def read_complete_file(parent: int, name: str) -> bytes:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_JSON_BYTES:
            raise ReleaseEvidenceError(
                "existing release evidence output is not a bounded regular "
                f"file: {name!r}"
            )
        descriptor = os.open(name, read_flags, dir_fd=parent)
        try:
            opened = os.fstat(descriptor)
            if not same_inode(before, opened):
                raise ReleaseEvidenceError(
                    f"existing release evidence output changed: {name!r}"
                )
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = os.read(descriptor, min(64 * 1024, MAX_JSON_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_JSON_BYTES:
                    raise ReleaseEvidenceError(
                        f"existing release evidence output is too large: {name!r}"
                    )
            after = os.fstat(descriptor)
            after_path = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not (same_inode(opened, after) and same_inode(opened, after_path)):
                raise ReleaseEvidenceError(
                    f"existing release evidence output changed: {name!r}"
                )
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    def remove_created_chain(
        descriptors: list[int], created: list[bool], parts: tuple[str, ...]
    ) -> None:
        for index in range(len(parts), 0, -1):
            if not created[index]:
                continue
            try:
                visible = os.stat(
                    parts[index - 1],
                    dir_fd=descriptors[index - 1],
                    follow_symlinks=False,
                )
                if not same_inode(visible, os.fstat(descriptors[index])):
                    continue
                os.rmdir(parts[index - 1], dir_fd=descriptors[index - 1])
            except OSError:
                pass

    output_chain: list[int] = []
    output_created: list[bool] = []
    output_receipts: int | None = None
    output_receipts_created = False
    stage_root: int | None = None
    stage_receipts: int | None = None
    stage_name: str | None = None
    linked: list[tuple[int, str]] = []
    try:
        output_chain, output_created = open_directory_chain(
            output_relative, create=True
        )
        output_root = output_chain[-1]
        if any(len(relative.parts) == 2 for relative, _ in document_rows):
            output_receipts, output_receipts_created = open_child_directory(
                output_root, "receipts", create=True
            )

        parent_descriptor = output_chain[-2]
        for _ in range(100):
            candidate_name = f".release-evidence-{secrets.token_hex(12)}"
            try:
                os.mkdir(candidate_name, mode=0o700, dir_fd=parent_descriptor)
                stage_name = candidate_name
                break
            except FileExistsError:
                continue
        if stage_name is None:
            raise ReleaseEvidenceError(
                "could not reserve a release evidence staging directory"
            )
        stage_root = os.open(
            stage_name, directory_flags, dir_fd=parent_descriptor
        )
        if any(len(relative.parts) == 2 for relative, _ in document_rows):
            os.mkdir("receipts", mode=0o700, dir_fd=stage_root)
            stage_receipts = os.open(
                "receipts", directory_flags, dir_fd=stage_root
            )

        def directories(relative: Path) -> tuple[int, int]:
            if len(relative.parts) == 1:
                return stage_root, output_root  # type: ignore[return-value]
            return stage_receipts, output_receipts  # type: ignore[return-value]

        for relative, content in document_rows:
            source_parent, _ = directories(relative)
            write_complete_file(source_parent, relative.name, content)

        pending: list[tuple[Path, bytes]] = []
        for relative, content in document_rows:
            _, target_parent = directories(relative)
            try:
                existing = read_complete_file(target_parent, relative.name)
            except FileNotFoundError:
                pending.append((relative, content))
                continue
            if not replace:
                raise ReleaseEvidenceError(
                    "release evidence output appeared or already exists: "
                    f"{(output_relative / relative).as_posix()!r}"
                )
            if existing != content:
                raise ReleaseEvidenceError(
                    "refusing to replace non-byte-identical release evidence "
                    f"output: {(output_relative / relative).as_posix()!r}"
                )

        manifest_names = {"pre-g9-evidence.json", "release-evidence.json"}
        pending.sort(
            key=lambda row: (
                row[0].name in manifest_names,
                row[0].as_posix(),
            )
        )
        for relative, _ in pending:
            source_parent, target_parent = directories(relative)
            try:
                os.link(
                    relative.name,
                    relative.name,
                    src_dir_fd=source_parent,
                    dst_dir_fd=target_parent,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise ReleaseEvidenceError(
                    "release evidence output appeared during publication: "
                    f"{(output_relative / relative).as_posix()!r}"
                ) from exc
            except OSError as exc:
                raise ReleaseEvidenceError(
                    "could not publish the complete release evidence set: "
                    f"{(output_relative / relative).as_posix()!r}"
                ) from exc
            linked.append((target_parent, relative.name))

        verification_chain, _ = open_directory_chain(
            output_relative, create=False
        )
        try:
            if not same_inode(
                os.fstat(output_root), os.fstat(verification_chain[-1])
            ):
                raise ReleaseEvidenceError(
                    "release evidence output directory changed during publication"
                )
        finally:
            close_all(verification_chain)

        if output_receipts is not None:
            receipts_chain, _ = open_directory_chain(
                output_relative / "receipts", create=False
            )
            try:
                if not same_inode(
                    os.fstat(output_receipts), os.fstat(receipts_chain[-1])
                ):
                    raise ReleaseEvidenceError(
                        "release evidence receipts directory changed during "
                        "publication"
                    )
            finally:
                close_all(receipts_chain)

        for relative, content in document_rows:
            _, target_parent = directories(relative)
            if read_complete_file(target_parent, relative.name) != content:
                raise ReleaseEvidenceError(
                    "post-publication release evidence mismatch: "
                    f"{(output_relative / relative).as_posix()!r}"
                )
    except BaseException:
        for parent, name in reversed(linked):
            try:
                os.unlink(name, dir_fd=parent)
            except OSError:
                pass
        if (
            output_receipts_created
            and output_receipts is not None
            and output_chain
        ):
            try:
                visible_receipts = os.stat(
                    "receipts",
                    dir_fd=output_chain[-1],
                    follow_symlinks=False,
                )
                if same_inode(
                    visible_receipts, os.fstat(output_receipts)
                ):
                    os.rmdir("receipts", dir_fd=output_chain[-1])
            except OSError:
                pass
        if output_chain:
            remove_created_chain(
                output_chain, output_created, output_relative.parts
            )
        raise
    finally:
        if stage_receipts is not None:
            for relative, _ in document_rows:
                if len(relative.parts) == 2:
                    try:
                        os.unlink(relative.name, dir_fd=stage_receipts)
                    except OSError:
                        pass
            os.close(stage_receipts)
            if stage_root is not None:
                try:
                    os.rmdir("receipts", dir_fd=stage_root)
                except OSError:
                    pass
        if stage_root is not None:
            for relative, _ in document_rows:
                if len(relative.parts) == 1:
                    try:
                        os.unlink(relative.name, dir_fd=stage_root)
                    except OSError:
                        pass
        if stage_name is not None and output_chain:
            try:
                visible_stage = os.stat(
                    stage_name,
                    dir_fd=output_chain[-2],
                    follow_symlinks=False,
                )
                if stage_root is None or same_inode(
                    visible_stage, os.fstat(stage_root)
                ):
                    os.rmdir(stage_name, dir_fd=output_chain[-2])
            except OSError:
                pass
        if stage_root is not None:
            os.close(stage_root)
        if output_receipts is not None:
            os.close(output_receipts)
        close_all(output_chain)


def assemble_pre_g9_evidence(
    repository_root: Path,
    *,
    input_path: Path,
    candidate_commit_sha: str,
    output_directory: Path,
    schema_path: Path = Path("schemas/release-evidence.schema.json"),
    checksums_path: Path = Path("bundle/CHECKSUMS.sha256"),
    profile_checksums_path: Path = Path("domain-profile/CHECKSUMS.sha256"),
    build_receipt_path: Path = Path("bundle/build-receipt.json"),
    replace: bool = False,
) -> CandidateIdentity:
    """Assemble exact G1-G8 receipts for owner review without creating G9."""

    root = repository_root.resolve()
    input_file = repository_argument(
        root, input_path, purpose="pre-G9 assembly input"
    )
    schema_file = repository_argument(
        root, schema_path, purpose="release evidence schema"
    )
    expected_coordinates = release_coordinates_from_build_config(
        root,
        build_receipt_path=build_receipt_path,
    )
    output = _version_scoped_output_directory(
        root,
        output_directory,
        version=expected_coordinates.version,
        phase="pre-g9",
    )
    paths = _pre_g9_output_paths(output)
    _preflight_output_paths(root, output, paths, replace=replace)

    forbidden_paths = {path.resolve() for path in paths.values()}
    if input_file.resolve() in forbidden_paths:
        raise ReleaseEvidenceError(
            "pre-G9 assembly input cannot be a generated output path"
        )

    input_document = load_json_bytes(
        read_repository_file_bytes(
            root,
            input_file.relative_to(root).as_posix(),
            purpose="pre-G9 assembly input",
            max_bytes=MAX_JSON_BYTES,
        ),
        label="pre-G9 assembly input",
    )
    _exact_keys(
        input_document,
        {"schema", "generated_at", "gates"},
        label="pre-G9 assembly input",
    )
    if input_document["schema"] != PRE_G9_INPUT_SCHEMA:
        raise ReleaseEvidenceError(
            "pre-G9 assembly input schema must be "
            f"{PRE_G9_INPUT_SCHEMA!r}"
        )
    generated_at = _non_empty_string(
        input_document["generated_at"], label="generated_at"
    )
    generated_time = parse_utc_timestamp(
        generated_at, label="pre-G9 assembly input.generated_at"
    )
    raw_gates = _object(input_document["gates"], label="gates")
    expected_gates = set(GATE_RECEIPTS)
    if set(raw_gates) != expected_gates:
        missing = sorted(expected_gates - set(raw_gates))
        extra = sorted(set(raw_gates) - expected_gates)
        raise ReleaseEvidenceError(
            "pre-G9 input must map exactly G1-G8; "
            f"missing={missing}, extra={extra}"
        )

    candidate = candidate_identity_from_repository(
        root,
        checksums_path=checksums_path,
        profile_checksums_path=profile_checksums_path,
        build_receipt_path=build_receipt_path,
        candidate_commit_sha=candidate_commit_sha,
    )
    validate_governed_candidate_commit(
        root,
        candidate_commit_sha=candidate_commit_sha,
        build_receipt_path=build_receipt_path,
    )
    validator = schema_validator(schema_file, repository_root=root)
    identity_registry: dict[str, tuple[str, bool]] = {}
    gate_bytes: dict[str, bytes] = {}
    gate_hashes: dict[str, str] = {}
    for gate in GATE_RECEIPTS:
        receipt = _gate_receipt(
            root,
            gate=gate,
            raw_gate=raw_gates[gate],
            candidate=candidate,
            forbidden_paths=forbidden_paths,
            identity_registry=identity_registry,
        )
        validate_document_schema(
            validator, receipt, label=f"{gate} assembled pre-G9 receipt"
        )
        validate_gate_receipt(
            root,
            receipt,
            gate=gate,
            expected_candidate=candidate,
        )
        encoded = canonical_json_bytes(receipt)
        gate_bytes[gate] = encoded
        gate_hashes[gate] = sha256_bytes(encoded)

    validate_g8_archive_evidence(
        root,
        load_json_bytes(gate_bytes["G8"], label="assembled pre-G9 G8 receipt"),
        expected_candidate=candidate,
        expected_coordinates=expected_coordinates,
    )

    later_gate = max(
        GATE_RECEIPTS,
        key=lambda gate: parse_utc_timestamp(
            raw_gates[gate]["executed_at"], label=f"{gate}.executed_at"
        ),
    )
    if parse_utc_timestamp(
        raw_gates[later_gate]["executed_at"],
        label=f"{later_gate}.executed_at",
    ) > generated_time:
        raise ReleaseEvidenceError(
            f"pre-G9 chronology is invalid: {later_gate}.executed_at is "
            "after pre-G9 generated_at"
        )

    receipt_references = [
        {
            "gate": gate,
            "path": paths[gate].relative_to(root).as_posix(),
            "sha256": gate_hashes[gate],
        }
        for gate in GATE_RECEIPTS
    ]
    manifest = {
        "schema": PRE_G9_MANIFEST_SCHEMA,
        "status": "ready_for_owner_review",
        "generated_at": generated_at,
        "candidate": asdict(candidate),
        "receipts": receipt_references,
        "limitations": [
            "This manifest contains G1-G8 receipts only; it is not G9 owner approval.",
            "It does not authorise deployment, publication or any public-URL claim.",
        ],
    }

    documents = {
        paths[gate]: gate_bytes[gate] for gate in GATE_RECEIPTS
    }
    documents[paths["manifest"]] = canonical_json_bytes(manifest)
    _write_documents_atomically(
        root,
        output,
        documents=documents,
        replace=replace,
    )

    for gate in GATE_RECEIPTS:
        if sha256_file(paths[gate]) != gate_hashes[gate]:
            raise ReleaseEvidenceError(
                f"post-write pre-G9 receipt digest mismatch for {gate}"
            )
    written_manifest = load_json(paths["manifest"])
    if written_manifest != manifest:
        raise ReleaseEvidenceError("post-write pre-G9 manifest mismatch")
    return candidate


def assemble_release_evidence(
    repository_root: Path,
    *,
    input_path: Path,
    candidate_commit_sha: str,
    output_directory: Path,
    schema_path: Path = Path("schemas/release-evidence.schema.json"),
    checksums_path: Path = Path("bundle/CHECKSUMS.sha256"),
    profile_checksums_path: Path = Path("domain-profile/CHECKSUMS.sha256"),
    build_receipt_path: Path = Path("bundle/build-receipt.json"),
    replace: bool = False,
) -> CandidateIdentity:
    """Create schema-valid G1-G9 documents from explicit passed attestations."""

    root = repository_root.resolve()
    input_file = repository_argument(
        root, input_path, purpose="release assembly input"
    )
    schema_file = repository_argument(
        root, schema_path, purpose="release evidence schema"
    )
    expected_coordinates = release_coordinates_from_build_config(
        root,
        build_receipt_path=build_receipt_path,
    )
    output = _version_scoped_output_directory(
        root,
        output_directory,
        version=expected_coordinates.version,
        phase="final-g9",
    )
    paths = _output_paths(output)
    _preflight_output_paths(
        root,
        output,
        paths,
        replace=replace,
    )

    forbidden_paths = {path.resolve() for path in paths.values()}
    if input_file.resolve() in forbidden_paths:
        raise ReleaseEvidenceError(
            "release assembly input cannot be a generated output path"
        )

    input_document = load_json_bytes(
        read_repository_file_bytes(
            root,
            input_file.relative_to(root).as_posix(),
            purpose="release assembly input",
            max_bytes=MAX_JSON_BYTES,
        ),
        label="release assembly input",
    )
    _exact_keys(
        input_document,
        {"schema", "generated_at", "gates", "release"},
        label="release assembly input",
    )
    if input_document["schema"] != INPUT_SCHEMA:
        raise ReleaseEvidenceError(
            f"release assembly input schema must be {INPUT_SCHEMA!r}"
        )
    generated_at = _non_empty_string(
        input_document["generated_at"], label="generated_at"
    )
    generated_time = parse_utc_timestamp(
        generated_at, label="release assembly input.generated_at"
    )
    raw_gates = _object(input_document["gates"], label="gates")
    expected_gates = set(GATE_RECEIPTS)
    if set(raw_gates) != expected_gates:
        missing = sorted(expected_gates - set(raw_gates))
        extra = sorted(set(raw_gates) - expected_gates)
        raise ReleaseEvidenceError(
            "release input must map exactly G1-G8; "
            f"missing={missing}, extra={extra}"
        )

    candidate = candidate_identity_from_repository(
        root,
        checksums_path=checksums_path,
        profile_checksums_path=profile_checksums_path,
        build_receipt_path=build_receipt_path,
        candidate_commit_sha=candidate_commit_sha,
    )
    validate_governed_candidate_commit(
        root,
        candidate_commit_sha=candidate_commit_sha,
        build_receipt_path=build_receipt_path,
    )
    validator = schema_validator(schema_file, repository_root=root)
    identity_registry: dict[str, tuple[str, bool]] = {}
    gate_receipts: dict[str, dict[str, Any]] = {}
    gate_bytes: dict[str, bytes] = {}
    gate_hashes: dict[str, str] = {}
    for gate in GATE_RECEIPTS:
        receipt = _gate_receipt(
            root,
            gate=gate,
            raw_gate=raw_gates[gate],
            candidate=candidate,
            forbidden_paths=forbidden_paths,
            identity_registry=identity_registry,
        )
        validate_document_schema(
            validator, receipt, label=f"{gate} assembled receipt"
        )
        validate_gate_receipt(
            root,
            receipt,
            gate=gate,
            expected_candidate=candidate,
        )
        gate_receipts[gate] = receipt
        encoded = canonical_json_bytes(receipt)
        gate_bytes[gate] = encoded
        gate_hashes[gate] = sha256_bytes(encoded)

    validate_g8_archive_evidence(
        root,
        gate_receipts["G8"],
        expected_candidate=candidate,
        expected_coordinates=expected_coordinates,
    )

    release_record = _release_record(
        input_document["release"],
        repository_root=root,
        candidate=candidate,
        expected_coordinates=expected_coordinates,
        receipt_hashes=gate_hashes,
        gate_receipts=gate_receipts,
        gate_identity_registry=identity_registry,
        forbidden_paths=forbidden_paths,
    )
    validate_document_schema(
        validator, release_record, label="assembled G9 release record"
    )
    validate_release_record(
        root,
        release_record,
        expected_candidate=candidate,
        receipt_hashes=gate_hashes,
        gate_receipts=gate_receipts,
        expected_coordinates=expected_coordinates,
    )
    owner_approved_at = parse_utc_timestamp(
        release_record["owner_approval"]["approved_at"],
        label="release.owner_approval.approved_at",
    )
    if owner_approved_at > generated_time:
        raise ReleaseEvidenceError(
            "release chronology is invalid: owner approval is after the "
            "final evidence manifest generated_at"
        )
    release_bytes = canonical_json_bytes(release_record)

    receipt_references = [
        {
            "gate": gate,
            "path": paths[gate].relative_to(root).as_posix(),
            "sha256": gate_hashes[gate],
        }
        for gate in GATE_RECEIPTS
    ]
    receipt_references.append(
        {
            "gate": "G9",
            "path": paths["G9"].relative_to(root).as_posix(),
            "sha256": sha256_bytes(release_bytes),
        }
    )
    manifest = {
        "$schema": SCHEMA_ID,
        "schema": "okf-release-evidence-manifest.v1",
        "status": "complete",
        "generated_at": generated_at,
        "candidate": asdict(candidate),
        "receipts": receipt_references,
    }
    validate_document_schema(
        validator, manifest, label="assembled release evidence manifest"
    )

    documents = {
        paths[gate]: gate_bytes[gate] for gate in GATE_RECEIPTS
    }
    documents[paths["G9"]] = release_bytes
    documents[paths["manifest"]] = canonical_json_bytes(manifest)
    _write_documents_atomically(
        root,
        output,
        documents=documents,
        replace=replace,
    )

    for gate in GATE_RECEIPTS:
        if sha256_file(paths[gate]) != gate_hashes[gate]:
            raise ReleaseEvidenceError(
                f"post-write receipt digest mismatch for {gate}"
            )
    if sha256_file(paths["G9"]) != receipt_references[-1]["sha256"]:
        raise ReleaseEvidenceError("post-write receipt digest mismatch for G9")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=ROOT,
        help="repository root (defaults to this script's repository)",
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="repository-relative explicit release attestation input",
    )
    parser.add_argument(
        "--candidate-commit-sha",
        required=True,
        help="exact governed candidate commit (40 lowercase hexadecimal chars)",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        required=True,
        help="explicit repository-relative, version-scoped output directory",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path("schemas/release-evidence.schema.json"),
        help="repository-relative release evidence schema",
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
        help="repository-relative domain-profile checksum manifest",
    )
    parser.add_argument(
        "--build-receipt",
        type=Path,
        default=Path("bundle/build-receipt.json"),
        help="repository-relative bundle build receipt",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help=(
            "accept existing known receipt/manifest files only when their "
            "bytes are identical to the would-be outputs"
        ),
    )
    parser.add_argument(
        "--pre-g9",
        action="store_true",
        help=(
            "assemble G1-G8 receipts and a non-approval owner-review index; "
            "do not create a G9 record"
        ),
    )
    args = parser.parse_args()

    try:
        assembler = (
            assemble_pre_g9_evidence
            if args.pre_g9
            else assemble_release_evidence
        )
        candidate = assembler(
            args.repository_root,
            input_path=args.input,
            candidate_commit_sha=args.candidate_commit_sha,
            output_directory=args.output_directory,
            schema_path=args.schema,
            checksums_path=args.checksums,
            profile_checksums_path=args.profile_checksums,
            build_receipt_path=args.build_receipt,
            replace=args.replace,
        )
    except (OSError, UnicodeError, ReleaseEvidenceError) as exc:
        print(
            f"release evidence assembly failed closed: {exc}",
            file=sys.stderr,
        )
        return 1

    evidence_kind = "pre-G9 G1-G8" if args.pre_g9 else "G1-G9 release"
    print(
        f"assembled {evidence_kind} evidence without running checks for exact "
        f"candidate: commit {candidate.candidate_commit_sha}, "
        f"release root {candidate.release_root_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

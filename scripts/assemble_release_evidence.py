#!/usr/bin/env python3
"""Assemble, but never perform, exact-candidate release checks.

The input is a small attestation manifest.  It must use this shape:

{
  "schema": "okf-release-assembly-input.v1",
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
    "residual_risks_reviewed": true,
    "residual_risk_ids": ["..."],
    "human_audit": {
      "status": "not_completed",
      "residual_risk_id": "...",
      "notes": "..."
    },
    "owner_approval": {...},
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
artifacts, validates each output against the release-evidence schema, and
writes deterministic receipts.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.check_release_evidence import (
    GATE_RECEIPTS,
    REVIEWED_GATES,
    REQUIRED_CHECKS,
    SCHEMA_ID,
    CandidateIdentity,
    ReleaseEvidenceError,
    candidate_identity_from_repository,
    load_json,
    repository_argument,
    safe_repository_file,
    schema_validator,
    sha256_file,
    validate_document_schema,
    validate_gate_receipt,
    validate_governed_candidate_commit,
    validate_release_record,
)


ROOT = Path(__file__).resolve().parents[1]
INPUT_SCHEMA = "okf-release-assembly-input.v1"
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
    _non_empty_string(reviewer["identity"], label=f"{label}.identity")
    _non_empty_string(reviewer["role"], label=f"{label}.role")
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
    artifact_name = _non_empty_string(
        validator["artifact_path"],
        label=f"{gate}.validator.artifact_path",
    )
    artifact = safe_repository_file(
        repository_root,
        artifact_name,
        purpose=f"{gate} validator artifact",
    )
    if artifact.resolve() in forbidden_paths:
        raise ReleaseEvidenceError(
            f"{gate} validator artifact cannot reference generated release "
            f"evidence: {artifact_name!r}"
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
        "sha256": sha256_file(artifact),
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
        result.append({"path": name, "sha256": sha256_file(path)})
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
        reviewer_identity = _non_empty_string(
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


def _release_record(
    value: Any,
    *,
    candidate: CandidateIdentity,
    receipt_hashes: dict[str, str],
    gate_identity_registry: dict[str, tuple[str, bool]],
) -> dict[str, Any]:
    release = _object(value, label="release")
    _exact_keys(release, RELEASE_INPUT_KEYS, label="release")
    if release["status"] != "approved":
        raise ReleaseEvidenceError("release input status is not approved")
    if release["claims_reviewed"] is not True:
        raise ReleaseEvidenceError(
            "release input must explicitly affirm claims_reviewed"
        )
    if release["residual_risks_reviewed"] is not True:
        raise ReleaseEvidenceError(
            "release input must explicitly affirm residual_risks_reviewed"
        )

    residual_risk_ids = release["residual_risk_ids"]
    if (
        not isinstance(residual_risk_ids, list)
        or not residual_risk_ids
        or any(
            not isinstance(risk_id, str) or not risk_id
            for risk_id in residual_risk_ids
        )
        or len(residual_risk_ids) != len(set(residual_risk_ids))
    ):
        raise ReleaseEvidenceError(
            "release.residual_risk_ids must be a non-empty unique string array"
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
        {"identity", "kind", "role", "approved_at", "approved"},
        label="release.owner_approval",
    )
    owner_identity = _non_empty_string(
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
    review_identity = _non_empty_string(
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
    _non_empty_string(
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

    return {
        "$schema": SCHEMA_ID,
        "schema": "okf-release-record.v1",
        "gate": "G9",
        "status": "approved",
        "candidate": asdict(candidate),
        "version": _non_empty_string(
            release["version"], label="release.version"
        ),
        "canonical_url": _non_empty_string(
            release["canonical_url"], label="release.canonical_url"
        ),
        "claims_reviewed": True,
        "residual_risks_reviewed": True,
        "residual_risk_ids": list(residual_risk_ids),
        "human_audit": dict(human_audit),
        "owner_approval": dict(owner),
        "independent_review": dict(independent_review),
        "approved_receipts": [
            {"gate": gate, "sha256": receipt_hashes[gate]}
            for gate in GATE_RECEIPTS
        ],
    }


def _write_documents_atomically(
    output_directory: Path,
    *,
    documents: dict[Path, bytes],
) -> None:
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(
        tempfile.mkdtemp(
            prefix=".release-evidence-",
            dir=output_directory.parent,
        )
    )
    try:
        for target, content in documents.items():
            relative = target.relative_to(output_directory)
            staged = staging_root / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(content)

        output_directory.mkdir(parents=True, exist_ok=True)
        for target in sorted(documents, key=lambda item: item.as_posix()):
            relative = target.relative_to(output_directory)
            staged = staging_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, target)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def assemble_release_evidence(
    repository_root: Path,
    *,
    input_path: Path,
    candidate_commit_sha: str,
    output_directory: Path = Path("validation"),
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
    output = _safe_output_directory(root, output_directory)
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

    input_document = load_json(input_file)
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

    validator = schema_validator(schema_file)
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
            validator, receipt, label=f"{gate} assembled receipt"
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

    release_record = _release_record(
        input_document["release"],
        candidate=candidate,
        receipt_hashes=gate_hashes,
        gate_identity_registry=identity_registry,
    )
    validate_document_schema(
        validator, release_record, label="assembled G9 release record"
    )
    validate_release_record(
        release_record,
        expected_candidate=candidate,
        receipt_hashes=gate_hashes,
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
    _write_documents_atomically(output, documents=documents)

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
        default=Path("validation"),
        help="repository-relative output directory",
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
        help="replace only the known generated receipt/manifest files",
    )
    args = parser.parse_args()

    try:
        candidate = assemble_release_evidence(
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

    print(
        "assembled release evidence without running checks for exact candidate: "
        f"commit {candidate.candidate_commit_sha}, "
        f"release root {candidate.release_root_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

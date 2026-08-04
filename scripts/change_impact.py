#!/usr/bin/env python3
"""Classify repository changes against the governed artifact dependency graph.

The report is advisory release-planning evidence. It never passes a validation
gate, waives a required check, or approves a release candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any, Iterable, Sequence

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
MAX_JSON_BYTES = 5_000_000
CONTROL_RELATIVE_PATHS = {
    "requirements": Path("governance/requirements.json"),
    "risks": Path("governance/risk-register.json"),
    "traceability": Path("governance/traceability.json"),
}


class ChangeImpactError(ValueError):
    """Raised when change-impact input or policy is unsafe or inconsistent."""


def sha256_file(path: Path) -> str:
    """Return the lowercase SHA-256 digest for *path*."""

    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ChangeImpactError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ChangeImpactError(f"non-finite JSON number is not allowed: {value}")


def load_json_object(path: Path) -> dict[str, Any]:
    """Read a bounded JSON object while rejecting duplicate keys."""

    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ChangeImpactError(f"cannot inspect JSON file {path}: {exc}") from exc
    if size > MAX_JSON_BYTES:
        raise ChangeImpactError(
            f"JSON file exceeds {MAX_JSON_BYTES} bytes: {path}"
        )
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except ChangeImpactError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ChangeImpactError(f"invalid JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ChangeImpactError(f"JSON document must be an object: {path}")
    return value


def normalise_repository_path(value: str) -> str:
    """Validate and return one canonical repository-relative POSIX path."""

    if (
        not value
        or value.startswith("/")
        or value.endswith("/")
        or value.startswith("./")
        or "//" in value
        or "\\" in value
        or "\x00" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise ChangeImpactError(f"unsafe repository path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ChangeImpactError(f"unsafe repository path: {value!r}")
    return value


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Compile the small, path-aware glob language used by the control."""

    expression: list[str] = ["^"]
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                index += 2
                if index < len(pattern) and pattern[index] == "/":
                    expression.append("(?:.*/)?")
                    index += 1
                else:
                    expression.append(".*")
                continue
            expression.append("[^/]*")
        elif character == "?":
            expression.append("[^/]")
        else:
            expression.append(re.escape(character))
        index += 1
    expression.append("$")
    return re.compile("".join(expression))


def path_matches(path: str, pattern: str) -> bool:
    """Return whether a concrete repository path matches a graph pattern."""

    return _glob_regex(pattern).fullmatch(path) is not None


def _format_validation_errors(errors: Iterable[Any]) -> str:
    messages: list[str] = []
    for error in errors:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        messages.append(f"{location}: {error.message}")
    return "; ".join(messages)


def _id_set(document: dict[str, Any], member: str) -> set[str]:
    values = document.get(member)
    if not isinstance(values, list):
        raise ChangeImpactError(f"governance control lacks array {member!r}")
    result: set[str] = set()
    for row in values:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ChangeImpactError(f"governance control has invalid {member!r} row")
        identifier = row["id"]
        if identifier in result:
            raise ChangeImpactError(f"duplicate {member!r} id: {identifier}")
        result.add(identifier)
    return result


def _generated_prefix(pattern: str) -> str:
    if not pattern.endswith("/**") or any(
        wildcard in pattern[:-3] for wildcard in "*?"
    ):
        raise ChangeImpactError(
            "generated_roots entries must have the form '<directory>/**': "
            f"{pattern!r}"
        )
    return pattern[:-3]


def validate_graph(
    graph: dict[str, Any],
    *,
    repository_root: Path = ROOT,
    schema: dict[str, Any] | None = None,
) -> None:
    """Validate graph syntax and close all references to governance controls."""

    graph_schema = schema or load_json_object(
        repository_root / "schemas" / "artifact-dependency-graph.schema.json"
    )
    Draft202012Validator.check_schema(graph_schema)
    validator = Draft202012Validator(
        graph_schema,
        format_checker=FormatChecker(),
    )
    errors = sorted(
        validator.iter_errors(graph),
        key=lambda error: tuple(str(part) for part in error.path),
    )
    if errors:
        raise ChangeImpactError(
            "artifact dependency graph is schema-invalid: "
            + _format_validation_errors(errors)
        )

    tests = graph["tests"]
    test_ids = [test["id"] for test in tests]
    if len(test_ids) != len(set(test_ids)):
        raise ChangeImpactError("artifact dependency graph has duplicate test ids")

    stages = graph["stages"]
    stage_ids = [stage["id"] for stage in stages]
    if len(stage_ids) != len(set(stage_ids)):
        raise ChangeImpactError("artifact dependency graph has duplicate stage ids")
    referenced_test_ids = {
        test_id for stage in stages for test_id in stage["test_ids"]
    }
    unused_test_ids = set(test_ids) - referenced_test_ids
    if unused_test_ids:
        raise ChangeImpactError(
            "artifact dependency graph has unreferenced test ids: "
            + ", ".join(sorted(unused_test_ids))
        )

    requirements = load_json_object(
        repository_root / "governance" / "requirements.json"
    )
    risks = load_json_object(repository_root / "governance" / "risk-register.json")
    requirement_ids = _id_set(requirements, "requirements")
    risk_ids = _id_set(risks, "risks")
    all_gates = set(graph["all_release_gates"])
    validation_ids = set(graph["validation_gate_map"])
    generated_prefixes = [
        _generated_prefix(pattern) for pattern in graph["generated_roots"]
    ]

    mapped_validation_gates = {
        gate
        for gates in graph["validation_gate_map"].values()
        for gate in gates
    }
    unknown_validation_gates = mapped_validation_gates - all_gates
    if unknown_validation_gates:
        raise ChangeImpactError(
            "validation_gate_map references unknown release gates: "
            + ", ".join(sorted(unknown_validation_gates))
        )

    for stage in stages:
        label = f"stage {stage['id']!r}"
        duplicated_inputs = set(stage["inputs"]) & set(stage["validation_inputs"])
        if duplicated_inputs:
            raise ChangeImpactError(
                f"{label} repeats paths in inputs and validation_inputs: "
                + ", ".join(sorted(duplicated_inputs))
            )
        checks = (
            ("test_ids", set(test_ids)),
            ("requirement_ids", requirement_ids),
            ("risk_ids", risk_ids),
            ("validation_refs", validation_ids),
            ("release_gates", all_gates),
        )
        for member, known in checks:
            unknown = set(stage[member]) - known
            if unknown:
                raise ChangeImpactError(
                    f"{label} has unknown {member}: {', '.join(sorted(unknown))}"
                )
        for output in stage["outputs"]:
            if not any(
                output == prefix or output.startswith(f"{prefix}/")
                for prefix in generated_prefixes
            ):
                raise ChangeImpactError(
                    f"{label} output is outside generated_roots: {output!r}"
                )


def load_graph(*, repository_root: Path = ROOT) -> dict[str, Any]:
    """Load and fully validate the repository dependency graph."""

    graph = load_json_object(
        repository_root / "governance" / "artifact-dependency-graph.json"
    )
    validate_graph(graph, repository_root=repository_root)
    return graph


def _traceability_matches(
    artifact_path: str,
    *,
    changed_paths: Sequence[str],
    predicted_outputs: Sequence[str],
) -> bool:
    return any(
        path_matches(path, artifact_path) for path in changed_paths
    ) or any(
        path_matches(artifact_path, output) for output in predicted_outputs
    )


def _enrich_from_governance(
    *,
    repository_root: Path,
    graph: dict[str, Any],
    changed_paths: Sequence[str],
    predicted_outputs: set[str],
    requirement_ids: set[str],
    risk_ids: set[str],
    validation_refs: set[str],
) -> None:
    requirements = load_json_object(
        repository_root / "governance" / "requirements.json"
    )["requirements"]
    risks = load_json_object(
        repository_root / "governance" / "risk-register.json"
    )["risks"]
    traceability = load_json_object(
        repository_root / "governance" / "traceability.json"
    )["rows"]

    for row in traceability:
        artifact_paths = row.get("artifact_paths", [])
        if any(
            _traceability_matches(
                artifact,
                changed_paths=changed_paths,
                predicted_outputs=sorted(predicted_outputs),
            )
            for artifact in artifact_paths
        ):
            requirement_ids.update(row.get("requirement_ids", []))
            risk_ids.update(row.get("risk_ids", []))
            validation_refs.update(row.get("validation_refs", []))

    requirement_rows = {
        row["id"]: row
        for row in requirements
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    for requirement_id in tuple(requirement_ids):
        row = requirement_rows.get(requirement_id, {})
        validation_refs.update(row.get("verification_refs", []))

    for row in risks:
        if set(row.get("controls", [])) & requirement_ids:
            risk_ids.add(row["id"])

    unknown_validations = validation_refs - set(graph["validation_gate_map"])
    if unknown_validations:
        raise ChangeImpactError(
            "governance controls reference validation ids absent from "
            "validation_gate_map: "
            + ", ".join(sorted(unknown_validations))
        )


def analyse_paths(
    paths: Iterable[str],
    *,
    graph: dict[str, Any] | None = None,
    repository_root: Path = ROOT,
    comparison: dict[str, str] | None = None,
    changes: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, fail-closed impact report for changed paths."""

    policy = graph or load_graph(repository_root=repository_root)
    validate_graph(policy, repository_root=repository_root)
    changed_paths = sorted({normalise_repository_path(path) for path in paths})
    generated_roots = policy["generated_roots"]
    generated_paths = {
        path
        for path in changed_paths
        if any(path_matches(path, pattern) for pattern in generated_roots)
    }
    authored_paths = [path for path in changed_paths if path not in generated_paths]

    matched_stages: list[dict[str, Any]] = []
    input_matched_paths: set[str] = set()
    predicted_outputs: set[str] = set()
    requirement_ids: set[str] = set()
    risk_ids: set[str] = set()
    test_ids: set[str] = set()
    validation_refs: set[str] = set()
    release_gates: set[str] = set()
    stage1_review_required = False

    for stage in policy["stages"]:
        input_matches = sorted(
            path
            for path in authored_paths
            if any(path_matches(path, pattern) for pattern in stage["inputs"])
        )
        validation_input_matches = sorted(
            path
            for path in authored_paths
            if any(
                path_matches(path, pattern)
                for pattern in stage["validation_inputs"]
            )
        )
        stage_matches = sorted(set(input_matches) | set(validation_input_matches))
        if not stage_matches:
            continue
        input_matched_paths.update(stage_matches)
        matched_stages.append(
            {
                "id": stage["id"],
                "matched_paths": stage_matches,
                "input_matches": input_matches,
                "validation_input_matches": validation_input_matches,
            }
        )
        if input_matches:
            predicted_outputs.update(stage["outputs"])
        requirement_ids.update(stage["requirement_ids"])
        risk_ids.update(stage["risk_ids"])
        test_ids.update(stage["test_ids"])
        validation_refs.update(stage["validation_refs"])
        release_gates.update(stage["release_gates"])
        stage1_review_required = (
            stage1_review_required or stage["stage1_review"]
        )

    explained_generated_paths = {
        path
        for path in generated_paths
        if any(path_matches(path, output) for output in predicted_outputs)
    }
    unexplained_generated_paths = generated_paths - explained_generated_paths
    unmatched_paths = (
        set(authored_paths) - input_matched_paths
    ) | unexplained_generated_paths

    _enrich_from_governance(
        repository_root=repository_root,
        graph=policy,
        changed_paths=changed_paths,
        predicted_outputs=predicted_outputs,
        requirement_ids=requirement_ids,
        risk_ids=risk_ids,
        validation_refs=validation_refs,
    )
    for validation in validation_refs:
        release_gates.update(policy["validation_gate_map"][validation])

    manual_review_required = bool(unmatched_paths)
    if manual_review_required:
        release_gates.update(policy["all_release_gates"])

    test_index = {test["id"]: test for test in policy["tests"]}
    selected_tests = [
        {
            "id": identifier,
            "command": test_index[identifier]["command"],
            "description": test_index[identifier].get("description"),
        }
        for identifier in sorted(test_ids)
    ]
    ordered_release_gates = sorted(
        release_gates,
        key=lambda gate: int(gate.removeprefix("G")),
    )
    stage_index = {stage["id"]: stage for stage in policy["stages"]}
    matched_stage_ids = [stage["id"] for stage in matched_stages]
    gate_work = [
        {
            "gate": gate,
            "status": "not_run",
            "selected_by": {
                "stages": [
                    stage_id
                    for stage_id in matched_stage_ids
                    if gate in stage_index[stage_id]["release_gates"]
                ],
                "validation_refs": sorted(
                    validation
                    for validation in validation_refs
                    if gate in policy["validation_gate_map"][validation]
                ),
                "unknown_change_policy": manual_review_required,
            },
        }
        for gate in ordered_release_gates
    ]
    control_hashes = {
        name: sha256_file(repository_root / path)
        for name, path in CONTROL_RELATIVE_PATHS.items()
    }
    graph_path = (
        repository_root / "governance" / "artifact-dependency-graph.json"
    )
    schema_path = (
        repository_root / "schemas" / "artifact-dependency-graph.schema.json"
    )
    release_approval_required = bool(changed_paths) and (
        "G9" in release_gates or bool(predicted_outputs) or bool(generated_paths)
    )

    normalized_changes = list(changes or [])
    if not normalized_changes:
        normalized_changes = [
            {"status": "provided", "paths": [path]} for path in changed_paths
        ]
    normalized_changes.sort(
        key=lambda row: (tuple(row.get("paths", [])), row.get("status", ""))
    )

    return {
        "schema": "okf-change-impact-report.v1",
        "graph": {
            "path": "governance/artifact-dependency-graph.json",
            "version": policy["version"],
            "sha256": sha256_file(graph_path),
            "unknown_change_policy": policy["unknown_change_policy"],
            "schema_path": "schemas/artifact-dependency-graph.schema.json",
            "schema_sha256": sha256_file(schema_path),
        },
        "control_sha256": control_hashes,
        "comparison": comparison or {"mode": "paths"},
        "changes": normalized_changes,
        "changed_paths": changed_paths,
        "matched_stages": matched_stages,
        "affected": {
            "generated_artifacts": sorted(predicted_outputs),
            "requirement_ids": sorted(requirement_ids),
            "risk_ids": sorted(risk_ids),
            "test_ids": sorted(test_ids),
            "tests": selected_tests,
            "validation_refs": sorted(validation_refs),
            "release_gates": ordered_release_gates,
            "gate_work": gate_work,
        },
        "explained_generated_paths": sorted(explained_generated_paths),
        "unexplained_generated_paths": sorted(unexplained_generated_paths),
        "unmatched_paths": sorted(unmatched_paths),
        "stage1_review_required": stage1_review_required,
        "manual_review_required": manual_review_required,
        "release_approval_required": release_approval_required,
        "decision": (
            "manual-review-required"
            if manual_review_required
            else "classified"
        ),
        "limitations": [
            "This report selects work; it does not pass or waive a validation gate.",
            "Current release evidence remains bound to the exact governed release root.",
            "G9 remains a project-owner decision for the exact candidate digest.",
        ],
    }


def parse_name_status_z(payload: bytes) -> list[dict[str, Any]]:
    """Parse ``git diff --name-status -z`` output, including renames/copies."""

    if not payload:
        return []
    fields = payload.split(b"\x00")
    if fields[-1] != b"":
        raise ChangeImpactError("git name-status output is not NUL-terminated")
    fields.pop()
    changes: list[dict[str, Any]] = []
    index = 0
    while index < len(fields):
        try:
            status = fields[index].decode("ascii")
        except UnicodeDecodeError as exc:
            raise ChangeImpactError("git change status is not ASCII") from exc
        index += 1
        if re.fullmatch(r"[ACDMRTUXB][0-9]*", status) is None:
            raise ChangeImpactError(f"unsupported git change status: {status!r}")
        path_count = 2 if status.startswith(("R", "C")) else 1
        if not status or index + path_count > len(fields):
            raise ChangeImpactError("truncated git name-status output")
        decoded_paths: list[str] = []
        for raw_path in fields[index : index + path_count]:
            try:
                path = raw_path.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ChangeImpactError(
                    "changed repository path is not valid UTF-8"
                ) from exc
            decoded_paths.append(normalise_repository_path(path))
        index += path_count
        changes.append({"status": status, "paths": decoded_paths})
    return changes


def _validate_revision(revision: str) -> str:
    if (
        not revision
        or revision.startswith("-")
        or "\x00" in revision
        or any(character.isspace() for character in revision)
    ):
        raise ChangeImpactError(f"unsafe git revision: {revision!r}")
    return revision


def _run_git(repository_root: Path, arguments: Sequence[str]) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repository_root,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError):
            detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise ChangeImpactError(
            "git command failed" + (f": {detail}" if detail else "")
        ) from exc
    return completed.stdout


def resolve_revision(repository_root: Path, revision: str) -> str:
    """Resolve one safe revision expression to an exact commit SHA."""

    safe_revision = _validate_revision(revision)
    resolved = _run_git(
        repository_root,
        ["rev-parse", "--verify", f"{safe_revision}^{{commit}}"],
    ).decode("ascii", errors="strict").strip()
    if re.fullmatch(r"[0-9a-f]{40}", resolved) is None:
        raise ChangeImpactError(f"git revision did not resolve to a commit: {revision}")
    return resolved


def git_changes(
    repository_root: Path,
    *,
    base: str,
    head: str,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Return normalized diff entries and an exact comparison identity."""

    base_sha = resolve_revision(repository_root, base)
    head_sha = resolve_revision(repository_root, head)
    payload = _run_git(
        repository_root,
        [
            "diff",
            "--name-status",
            "-z",
            "--find-renames",
            base_sha,
            head_sha,
            "--",
        ],
    )
    return (
        parse_name_status_z(payload),
        {
            "mode": "git",
            "base": base_sha,
            "head": head_sha,
        },
    )


def canonical_json(value: dict[str, Any]) -> str:
    """Serialize a report with deterministic key and array ordering."""

    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Classify changed repository paths against the governed artifact "
            "dependency graph."
        )
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="Changed repository-relative path; repeat for multiple paths.",
    )
    parser.add_argument("--base", help="Base Git revision for a committed diff.")
    parser.add_argument(
        "--head",
        help="Head Git revision for --base (default: HEAD).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write canonical JSON to this path instead of stdout.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 when an unknown or unexplained generated path needs review.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.path and arguments.base:
        parser.error("--path and --base are mutually exclusive")
    if arguments.head and not arguments.base:
        parser.error("--head requires --base")
    if not arguments.path and not arguments.base:
        parser.error("provide at least one --path or a --base revision")

    try:
        if arguments.base:
            changes, comparison = git_changes(
                ROOT,
                base=arguments.base,
                head=arguments.head or "HEAD",
            )
            paths = [
                path
                for change in changes
                for path in change["paths"]
            ]
        else:
            changes = [
                {
                    "status": "provided",
                    "paths": [normalise_repository_path(path)],
                }
                for path in arguments.path
            ]
            comparison = {"mode": "paths"}
            paths = [path for change in changes for path in change["paths"]]

        report = analyse_paths(
            paths,
            repository_root=ROOT,
            comparison=comparison,
            changes=changes,
        )
        rendered = canonical_json(report)
        if arguments.output is None:
            sys.stdout.write(rendered)
        else:
            try:
                arguments.output.write_text(rendered, encoding="utf-8")
            except OSError as exc:
                raise ChangeImpactError(
                    f"cannot write report {arguments.output}: {exc}"
                ) from exc
        return 1 if arguments.check and report["manual_review_required"] else 0
    except ChangeImpactError as exc:
        parser.exit(2, f"change-impact error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())

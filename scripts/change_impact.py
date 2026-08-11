#!/usr/bin/env python3
"""Classify repository changes against the governed artefact dependency graph.

The report is advisory release-planning evidence. It never passes a validation
gate, waives a required check, or approves a release candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import subprocess
import sys
import time
from typing import Any, Iterable, Sequence

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
MAX_JSON_BYTES = 5_000_000
MAX_GIT_STDOUT_BYTES = 16 * 1024 * 1024
MAX_GIT_STDERR_BYTES = 256 * 1024
GIT_COMMAND_TIMEOUT_SECONDS = 30
CONTROL_RELATIVE_PATHS = {
    "requirements": Path("governance/requirements.json"),
    "risks": Path("governance/risk-register.json"),
    "traceability": Path("governance/traceability.json"),
}
CAUSAL_BUILD_OUTPUTS = (
    "bundle/**",
    "bundle/build-receipt.json",
    "bundle/CHECKSUMS.sha256",
)

# This is deliberately a second, executable copy of the causal contract rather
# than data read from the graph that it protects.  Without an independent
# bootstrap, the graph can remove one of its own build inputs (or reclassify it
# as generated) and then issue a receipt that omits the byte that weakened the
# contract.  A reviewed causal change therefore updates this tuple, the graph
# and the closure tests together.
REQUIRED_BUILD_INPUT_PATTERNS = (
    "contracts/okf-explorer.consumer-lock.json",
    "domain-profile/CHECKSUMS.sha256",
    "domain-profile/domain-profile.json",
    "domain-profile/evidence-register.jsonl",
    "evaluation/questions.json",
    "governance/ai-model-usage.json",
    "governance/artifact-dependency-graph.json",
    "governance/rights-review.json",
    "okf.semantic.json",
    "pages/.nojekyll",
    "pages/404.html",
    "pages/accessibility.html",
    "pages/favicon.svg",
    "pages/index.html",
    "pages/manifest.webmanifest",
    "pages/search-contract.json",
    "pages/styles.css",
    "profiles/bundle-wiki/v1.vendor-lock.json",
    "profiles/bundle-wiki/v1/**",
    "requirements-lock.txt",
    "schemas/artifact-dependency-graph.schema.json",
    "schemas/curated-rights-access.schema.json",
    "schemas/relationship-runtime-row.schema.json",
    "schemas/semantic-assertion.schema.json",
    "scripts/acquire.py",
    "scripts/build.py",
    "scripts/change_impact.py",
    "scripts/evaluate.py",
    "scripts/python_runtime_contract.py",
    "source/build-config.json",
    "source/cpsv-service-mappings.json",
    "source/curated-records.json",
    "source/curated-rights-access.json",
    "source/input-manifest-v0.2.0.json",
    "source/jsonld-context.json",
    "source/observations/govuk-content-locale-translations-2026-07-29.json",
    "source/publisher-registry.json",
    "source/snapshots/2026-07-29T091915Z/**",
    "source/source-register.json",
    "source/type-kind-crosswalk.json",
    "standards/cpsv-ap/3.2.0.vendor-lock.json",
    "standards/cpsv-ap/3.2.0/**",
)
REQUIRED_GENERATED_ROOT_PATTERNS = (
    "bundle/**",
    "dist/**",
    "evaluation/latest-report.json",
    "validation/**",
)


class ChangeImpactError(ValueError):
    """Raised when change-impact input or policy is unsafe or inconsistent."""


def validate_executable_causal_bootstrap(graph: dict[str, Any]) -> None:
    """Bind this producer's graph to its independent executable boundary.

    Generic graph validation is also used on small, self-contained fixture and
    downstream producer repositories, so the Land Registry-specific exact set
    is a producer preflight rather than a universal OKF graph constraint.
    """

    build_inputs = graph.get("build_inputs")
    generated_roots = graph.get("generated_roots")
    observed_build_inputs = (
        tuple(build_inputs) if isinstance(build_inputs, list) else ()
    )
    if observed_build_inputs != REQUIRED_BUILD_INPUT_PATTERNS:
        missing = sorted(
            set(REQUIRED_BUILD_INPUT_PATTERNS)
            - set(build_inputs if isinstance(build_inputs, list) else [])
        )
        unexpected = sorted(
            set(build_inputs if isinstance(build_inputs, list) else [])
            - set(REQUIRED_BUILD_INPUT_PATTERNS)
        )
        raise ChangeImpactError(
            "build_inputs differs from the executable causal bootstrap: "
            f"missing={missing!r}, unexpected={unexpected!r}"
        )
    if (
        tuple(generated_roots)
        if isinstance(generated_roots, list)
        else ()
    ) != REQUIRED_GENERATED_ROOT_PATTERNS:
        raise ChangeImpactError(
            "generated_roots differs from the executable causal bootstrap; "
            "generated classifications cannot be changed by the graph alone"
        )


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


def normalise_dependency_pattern(
    value: Any,
    *,
    field: str,
    input_pattern: bool,
) -> tuple[str, bool]:
    """Accept one canonical literal or one tree selected by a final ``/**``."""

    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(character in value for character in "\\:~")
    ):
        raise ChangeImpactError(
            f"{field} is not a safe repository-relative pattern: {value!r}"
        )
    recursive = value.endswith("/**")
    literal = value[:-3] if recursive else value
    if not literal or any(character in literal for character in "*?[]"):
        raise ChangeImpactError(
            f"{field} must be a literal or use one trailing '/**': {value!r}"
        )
    parts = literal.split("/")
    if (
        any(part in {"", ".", ".."} for part in parts)
        or PurePosixPath(literal).is_absolute()
        or PurePosixPath(*parts).as_posix() != literal
    ):
        raise ChangeImpactError(
            f"{field} is not canonical and repository-relative: {value!r}"
        )
    if input_pattern and parts[0] in {"dist", "validation"}:
        raise ChangeImpactError(
            f"{field} must not consume mutable {parts[0]}/ evidence: {value!r}"
        )
    return literal, recursive


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


def _generated_pattern(pattern: str) -> tuple[str, bool]:
    """Return one exact generated file or recursive generated directory."""

    return normalise_dependency_pattern(
        pattern,
        field="generated_roots entry",
        input_pattern=False,
    )


def _generated_pattern_covers(output: str, pattern: tuple[str, bool]) -> bool:
    literal, recursive = pattern
    return output == literal or (recursive and output.startswith(f"{literal}/"))


def _dependency_pattern_covers(selected: str, covering: str) -> bool:
    """Return whether *covering* admits every path selected by *selected*."""

    selected_literal, selected_recursive = normalise_dependency_pattern(
        selected,
        field="selected dependency pattern",
        input_pattern=True,
    )
    covering_literal, covering_recursive = normalise_dependency_pattern(
        covering,
        field="covering dependency pattern",
        input_pattern=True,
    )
    if not covering_recursive:
        return not selected_recursive and selected_literal == covering_literal
    return selected_literal == covering_literal or selected_literal.startswith(
        covering_literal + "/"
    )


def _dependency_patterns_overlap(left: str, right: str) -> bool:
    """Return whether two literal-or-tree dependency patterns can intersect."""

    left_literal, left_recursive = normalise_dependency_pattern(
        left,
        field="left dependency pattern",
        input_pattern=False,
    )
    right_literal, right_recursive = normalise_dependency_pattern(
        right,
        field="right dependency pattern",
        input_pattern=False,
    )
    if left_literal == right_literal:
        return True
    return (
        left_recursive and right_literal.startswith(left_literal + "/")
    ) or (
        right_recursive and left_literal.startswith(right_literal + "/")
    )


def validate_build_input_contract(graph: dict[str, Any]) -> None:
    """Validate build causality without reading assurance-only controls.

    The full graph validator additionally resolves tests, requirements and
    risks. The deterministic builder deliberately calls only this relational
    subset after JSON Schema validation, so those assurance controls cannot
    influence bundle bytes or the build receipt.
    """

    build_inputs = graph.get("build_inputs")
    stages = graph.get("stages")
    generated_roots = graph.get("generated_roots")
    if not isinstance(build_inputs, list) or not build_inputs:
        raise ChangeImpactError("artefact dependency graph has no build_inputs")
    if not isinstance(stages, list) or not stages:
        raise ChangeImpactError("artefact dependency graph has no stages")
    if not isinstance(generated_roots, list) or not generated_roots:
        raise ChangeImpactError("artefact dependency graph has no generated roots")

    for index, pattern in enumerate(generated_roots):
        normalise_dependency_pattern(
            pattern,
            field=f"generated_roots[{index}]",
            input_pattern=False,
        )

    stage_inputs: list[tuple[dict[str, Any], str]] = []
    for stage_index, stage in enumerate(stages):
        if not isinstance(stage, dict):
            raise ChangeImpactError(
                f"artefact dependency graph stage {stage_index} is not an object"
            )
        inputs = stage.get("inputs")
        if not isinstance(inputs, list):
            raise ChangeImpactError(
                f"artefact dependency graph stage {stage_index} has no inputs"
            )
        for input_index, pattern in enumerate(inputs):
            normalise_dependency_pattern(
                pattern,
                field=f"stage {stage_index} inputs[{input_index}]",
                input_pattern=True,
            )
            stage_inputs.append((stage, pattern))

    for index, pattern in enumerate(build_inputs):
        normalise_dependency_pattern(
            pattern,
            field=f"build_inputs[{index}]",
            input_pattern=True,
        )
        if any(
            _dependency_patterns_overlap(pattern, generated)
            for generated in generated_roots
        ):
            raise ChangeImpactError(
                f"build_inputs[{index}] overlaps a generated root: {pattern!r}"
            )
        covering_stages = [
            stage
            for stage, stage_pattern in stage_inputs
            if _dependency_pattern_covers(pattern, stage_pattern)
        ]
        if not covering_stages:
            raise ChangeImpactError(
                f"build_inputs[{index}] is not covered by any stage input: "
                f"{pattern!r}"
            )
        selected_tests = {
            test_id
            for stage in covering_stages
            for test_id in stage.get("test_ids", [])
        }
        selected_validations = {
            validation
            for stage in covering_stages
            for validation in stage.get("validation_refs", [])
        }
        missing_tests = {"build-semantics", "bundle"} - selected_tests
        if missing_tests or "VAL-REPRODUCIBILITY" not in selected_validations:
            details = []
            if missing_tests:
                details.append("tests " + ", ".join(sorted(missing_tests)))
            if "VAL-REPRODUCIBILITY" not in selected_validations:
                details.append("VAL-REPRODUCIBILITY")
            raise ChangeImpactError(
                f"build_inputs[{index}] lacks causal rebuild routing for "
                f"{pattern!r}: " + "; ".join(details)
            )



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
            "artefact dependency graph is schema-invalid: "
            + _format_validation_errors(errors)
        )

    validate_build_input_contract(graph)

    tests = graph["tests"]
    test_ids = [test["id"] for test in tests]
    if len(test_ids) != len(set(test_ids)):
        raise ChangeImpactError("artefact dependency graph has duplicate test ids")
    for test in tests:
        for relative in test["repository_paths"]:
            path = repository_root / normalise_repository_path(relative)
            if not path.is_file() or path.is_symlink():
                raise ChangeImpactError(
                    f"test {test['id']!r} references an absent repository path: "
                    f"{relative}"
                )

    stages = graph["stages"]
    stage_ids = [stage["id"] for stage in stages]
    if len(stage_ids) != len(set(stage_ids)):
        raise ChangeImpactError("artefact dependency graph has duplicate stage ids")
    referenced_test_ids = {
        test_id for stage in stages for test_id in stage["test_ids"]
    }
    unused_test_ids = set(test_ids) - referenced_test_ids
    if unused_test_ids:
        raise ChangeImpactError(
            "artefact dependency graph has unreferenced test ids: "
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
    generated_patterns = [
        _generated_pattern(pattern) for pattern in graph["generated_roots"]
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
        for member in ("inputs", "validation_inputs", "outputs"):
            for index, pattern in enumerate(stage[member]):
                normalise_dependency_pattern(
                    pattern,
                    field=f"{label} {member}[{index}]",
                    input_pattern=member in {"inputs", "validation_inputs"},
                )
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
                _generated_pattern_covers(output, pattern)
                for pattern in generated_patterns
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
    if repository_root.resolve() == ROOT.resolve():
        validate_executable_causal_bootstrap(graph)
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
    if repository_root.resolve() == ROOT.resolve():
        validate_executable_causal_bootstrap(policy)
    changed_paths = sorted({normalise_repository_path(path) for path in paths})
    generated_roots = policy["generated_roots"]
    generated_paths = {
        path
        for path in changed_paths
        if any(path_matches(path, pattern) for pattern in generated_roots)
    }
    authored_paths = [path for path in changed_paths if path not in generated_paths]
    causal_build_paths = {
        path
        for path in authored_paths
        if any(path_matches(path, pattern) for pattern in policy["build_inputs"])
    }

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
                "causal_build_input_matches": sorted(
                    set(input_matches) & causal_build_paths
                ),
                "validation_input_matches": validation_input_matches,
            }
        )
        if input_matches:
            causal_stage_matches = set(input_matches) & causal_build_paths
            predicted_outputs.update(
                output
                for output in stage["outputs"]
                if not output.startswith("bundle/") or causal_stage_matches
            )
        requirement_ids.update(stage["requirement_ids"])
        risk_ids.update(stage["risk_ids"])
        test_ids.update(stage["test_ids"])
        validation_refs.update(stage["validation_refs"])
        release_gates.update(stage["release_gates"])
        stage1_review_required = (
            stage1_review_required or stage["stage1_review"]
        )

    if causal_build_paths:
        predicted_outputs.update(CAUSAL_BUILD_OUTPUTS)

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


def _run_git(
    repository_root: Path,
    arguments: Sequence[str],
    *,
    maximum_stdout_bytes: int = MAX_GIT_STDOUT_BYTES,
) -> bytes:
    """Run one read-only Git query with bounded time and output."""

    if maximum_stdout_bytes < 0:
        raise ChangeImpactError("Git stdout byte limit must be non-negative")
    command = ["git", *arguments]
    try:
        process = subprocess.Popen(
            command,
            cwd=repository_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise ChangeImpactError(f"cannot execute Git command: {exc}") from exc
    if process.stdout is None or process.stderr is None:  # pragma: no cover
        process.kill()
        process.wait()
        raise ChangeImpactError("Git command did not expose bounded output pipes")
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {
        "stdout": maximum_stdout_bytes,
        "stderr": MAX_GIT_STDERR_BYTES,
    }
    deadline = time.monotonic() + GIT_COMMAND_TIMEOUT_SECONDS
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ChangeImpactError(
                    "Git command exceeded its governed time ceiling"
                )
            events = selector.select(remaining)
            if not events:
                raise ChangeImpactError(
                    "Git command exceeded its governed time ceiling"
                )
            for key, _mask in events:
                chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target = buffers[key.data]
                target.extend(chunk)
                if len(target) > limits[key.data]:
                    raise ChangeImpactError(
                        f"Git {key.data} exceeds its governed "
                        f"{limits[key.data]}-byte ceiling"
                    )
        try:
            returncode = process.wait(
                timeout=max(0.1, deadline - time.monotonic())
            )
        except subprocess.TimeoutExpired as exc:
            raise ChangeImpactError(
                "Git command exceeded its governed time ceiling"
            ) from exc
    except BaseException:
        process.kill()
        process.wait()
        raise
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    stdout = bytes(buffers["stdout"])
    stderr = bytes(buffers["stderr"])
    if returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise ChangeImpactError(
            "Git command failed" + (f": {detail}" if detail else "")
        )
    return stdout


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
    """Return normalised diff entries and an exact comparison identity."""

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
    """Serialise a report with deterministic key and array ordering."""

    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Classify changed repository paths against the governed artefact "
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

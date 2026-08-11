#!/usr/bin/env python3
"""Validate an OKF domain profile's schema, references and equivalent form."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from ruamel.yaml import YAML


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "domain-profile.schema.json"
LR_SCHEMA_ID = (
    "https://chris-page-gov.github.io/okf-LandRegistry/"
    "schemas/domain-profile.schema.json"
)
RDFS_RESOURCE = "http://www.w3.org/2000/01/rdf-schema#Resource"
DECLARED_SET_NAMES = {
    "active_relationship_predicate_iris",
    "active_entity_class_iris",
    "active_entity_type_ids",
    "authorised_zero_entity_class_iris",
    "authorised_zero_entity_type_ids",
    "authorised_zero_relationship_predicate_iris",
    "derivation_rule_iris",
    "entity_type_ids",
    "identity_family_ids",
    "relationship_type_ids",
    "relationship_plane_iris",
    "rights_ids",
    "source_family_ids",
    "source_native_types",
}
CORE_PLANE = {
    "id": "PLANE-CORE",
    "iri": "urn:okf:hmlr:plane:core",
    "name": "core",
    "default": True,
    "lifecycle": "active",
    "implementation_state": "active-emitted",
    "assertion_statuses": ["normalized"],
}
RULE_BASE = "https://chris-page-gov.github.io/okf-LandRegistry/id/rule/"
SOURCE_OBSERVATION_RULE_IRI = RULE_BASE + "governed-source-observation-v1"
EXPECTED_RULE_IRIS_BY_RELATIONSHIP_ID = {
    "REL-CATALOGUE-RECORD": RULE_BASE
    + "governed-relationship-projection-27f6291191be7c95-v1",
    "REL-PRIMARY-TOPIC": RULE_BASE
    + "governed-relationship-projection-f78f68c996b85205-v1",
    "REL-TRANSLATION-OF-WORK": RULE_BASE
    + "govuk-content-available-translations-v1",
    "REL-PUBLISHER": RULE_BASE + "governed-publisher-registry-v1",
    "REL-RIGHTS": RULE_BASE
    + "governed-relationship-projection-b94fbf0180909ba1-v1",
    "REL-WAS-DERIVED-FROM": RULE_BASE
    + "governed-relationship-projection-0f5c6f8cc55425d2-v1",
    "REL-WAS-GENERATED-BY": RULE_BASE
    + "governed-relationship-projection-8dcb9aba08d76baf-v1",
    "REL-CATALOGUE-RESOURCE": RULE_BASE
    + "governed-relationship-projection-9a1b63f3bf2c5dd5-v1",
    "REL-CATALOGUE-DATASET": RULE_BASE
    + "governed-relationship-projection-bc32c5dd40ea558b-v1",
    "REL-SOURCE": RULE_BASE
    + "governed-relationship-projection-cbbd3bd6906d57d1-v1",
    "REL-LANGUAGE": RULE_BASE
    + "governed-relationship-projection-99159868f703c54a-v1",
    "REL-COMPETENT-AUTHORITY": RULE_BASE
    + "cpsv-ap-3.2.0-competent-authority-v1",
    "REL-SPATIAL": RULE_BASE
    + "governed-relationship-projection-5dd3cd31ca7139ab-v1",
}
DELEGATED_AUTHORITIES = {
    "source/publisher-registry.json": "#/publishers",
    "source/cpsv-service-mappings.json": "#/decisions",
    "source/curated-rights-access.json": "#/classifications",
    "source/source-register.json": "#/source_families",
    "governance/rights-review.json": "#/assessments",
}
PREDICATE_EVIDENCE_FIELDS = {
    "source_artifact",
    "source_field",
    "source_sha256",
    "source_value_sha256",
}
PREDICATE_REGISTRY_V2_EVIDENCE_ID = "EV-OKF-PREDICATE-REGISTRY-V2"
PREDICATE_REGISTRY_V2_PROFILE = (
    "https://chris-page-gov.github.io/okf-explorer/"
    "profile/predicate-registry/v2/"
)
PREDICATE_REGISTRY_V2_SCHEMA_ID = (
    PREDICATE_REGISTRY_V2_PROFILE + "predicate-registry.schema.json"
)
PREDICATE_REGISTRY_V2_RELEASE = {
    "annotated_tag_object_sha": "b5918192b1e3969ca2b069a4d56b3d26884ea96c",
    "commit_sha": "839d4ba4c2d02abc6ef02b3ca1dcbf6a4008e7c8",
    "evidence_ref": PREDICATE_REGISTRY_V2_EVIDENCE_ID,
    "published_at": "2026-08-11T12:34:04Z",
    "release_tag": "v0.6.1",
    "version": "0.6.1",
}
PREDICATE_REGISTRY_V2_PROFILE_LOCK = {
    "bytes": 744,
    "identity_sha256": (
        "75e444a35fdfe28fc111b6f0490cb8a0d569d20c1e4b62410174ead2608d86c6"
    ),
    "path": "profiles/predicate-registry/v2.lock.json",
    "profile": PREDICATE_REGISTRY_V2_PROFILE,
    "schema_bytes": 7551,
    "schema_id": PREDICATE_REGISTRY_V2_SCHEMA_ID,
    "schema_path": "profiles/predicate-registry/v2/predicate-registry.schema.json",
    "schema_sha256": (
        "037151379a1ec0cbfe0666d41592585a891a63929f1fcf2845d1eb3de8dd5069"
    ),
    "sha256": (
        "3d1f7cdbb423628f3938e5aef299ae09013f56be515ff2155475c5325ffd0110"
    ),
}
PREDICATE_REGISTRY_V2_PROFILE_FILES = [
    {
        "path": "index.md",
        "bytes": 4841,
        "sha256": (
            "c65a17f0f3e8e17009394a21bf48a5f74dc6c326f5a6f73aa074027f5ad2caba"
        ),
    },
    {
        "path": "predicate-registry.schema.json",
        "bytes": 7551,
        "sha256": (
            "037151379a1ec0cbfe0666d41592585a891a63929f1fcf2845d1eb3de8dd5069"
        ),
    },
]
PREDICATE_REGISTRY_V2_REQUIRED_FIELDS = [
    "schema",
    "profile",
    "snapshot",
    "generated_at",
    "predicates",
    "counts",
    "root_sha256",
]
PREDICATE_REGISTRY_V2_WIRE_IMPLEMENTATION = {
    "assertion_count_rules": {
        "active-emitted": "minimum-one",
        "authorised-zero-evidence": "exactly-zero",
    },
    "derivation": "derived-from-complete-governed-relationship-set",
    "field": "implementation",
    "required_fields": ["state", "assertions_emitted"],
    "state_values": ["active-emitted", "authorised-zero-evidence"],
}
PREDICATE_REGISTRY_V2_ASSERTIONS_EMITTED = 22_267
RELATIONSHIP_TYPE_LABEL_IDS = {
    "Catalogue record": {"TYPE-CATALOGUE-RECORD"},
    "Dataset": {"TYPE-DATASET"},
    "Dataset distribution metadata": {"TYPE-DISTRIBUTION"},
    "Derived metadata catalogue": {"TYPE-CATALOGUE"},
    "Evidence resource": {
        "TYPE-EVIDENCE-BINDING",
        "TYPE-EVIDENCE-RESOURCE",
    },
    "Language authority concept": {"TYPE-LANGUAGE"},
    "Location": {"TYPE-LOCATION"},
    "Official collection": {"TYPE-COLLECTION"},
    "Official publication or guidance record": {"TYPE-PUBLICATION"},
    "Operational service or API product": {"TYPE-SERVICE"},
    "Provenance activity": {
        "TYPE-DERIVATION-ACTIVITY",
        "TYPE-PROVENANCE-ACTIVITY",
    },
    "Provenance agent": {"TYPE-PROVENANCE-AGENT"},
    "Public source-code repository": {"TYPE-REPOSITORY"},
    "Publisher organisation": {"TYPE-PUBLISHER"},
    "Relationship assertion": {"TYPE-RELATIONSHIP-ASSERTION"},
    "Rights and access statement": {"TYPE-RIGHTS-STATEMENT"},
    "Route-bearing source resource": {"TYPE-SOURCE-RESOURCE"},
    "Standard or profile": {"TYPE-STANDARD"},
}


def load_document(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        value = json.loads(text)
    elif path.suffix.lower() in {".yaml", ".yml"}:
        value = YAML(typ="safe").load(text)
    else:
        raise ValueError(f"{path}: expected .json, .yaml or .yml")
    if not isinstance(value, dict):
        raise ValueError(f"{path}: profile must be an object")
    return value


def canonical_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _compact_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _set_identity(values: list[str]) -> dict[str, Any]:
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "ordering": "sorted-codepoint-compact-json",
        "sha256": _compact_sha256(ordered),
    }


def _json_pointer(value: Any, pointer: Any, *, label: str) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("#"):
        raise ValueError(f"{label} is not a local JSON Pointer")
    if pointer == "#":
        return value
    if not pointer.startswith("#/"):
        raise ValueError(f"{label} is not a local JSON Pointer")
    current = value
    for encoded in pointer[2:].split("/"):
        part = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdecimal() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise ValueError(f"{label} does not resolve: {pointer}")
    return current


def _repository_path(relative: Any, *, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"{label} is absent")
    parsed = urlsplit(relative)
    if parsed.scheme or parsed.netloc or relative.startswith("//"):
        raise ValueError(f"{label} is a URL, not a repository-relative path")
    if "\\" in relative or any(
        part in {"", ".", ".."} for part in relative.split("/")
    ):
        raise ValueError(f"{label} is not a safe repository-relative path")
    pure = PurePosixPath(relative)
    if pure.is_absolute():
        raise ValueError(f"{label} is not a safe repository-relative path")
    path = ROOT
    for part in pure.parts:
        path /= part
        if path.is_symlink():
            raise ValueError(f"{label} traverses a symbolic link: {relative}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"{label} is absent: {relative}") from error
    if not resolved.is_relative_to(ROOT.resolve()) or not resolved.is_file():
        raise ValueError(f"{label} is absent or escapes the repository: {relative}")
    return resolved


def _local_evidence_path(
    location: Any,
    *,
    label: str,
    repository_root: Path | None = None,
) -> Path:
    """Resolve a local evidence path without URL confusion or symlink traversal."""

    if not isinstance(location, str) or not location:
        raise ValueError(f"{label} is absent")
    parsed = urlsplit(location)
    if parsed.scheme or parsed.netloc or location.startswith("//"):
        raise ValueError(f"{label} is a URL, not a local evidence path")
    if "\\" in location or any(
        part in {"", "."} for part in location.split("/")
    ):
        raise ValueError(f"{label} is not a safe local evidence path")
    pure = PurePosixPath(location)
    if pure.is_absolute():
        raise ValueError(f"{label} is not a safe local evidence path")

    root = (repository_root or ROOT).resolve()
    current = root / "domain-profile"
    if current.is_symlink() or not current.is_dir():
        raise ValueError(f"{label} base directory is missing or unsafe")
    for part in pure.parts:
        if part == "..":
            current = current.parent
        else:
            current /= part
            if current.is_symlink():
                raise ValueError(f"{label} traverses a symbolic link: {location}")
        try:
            current.relative_to(root)
        except ValueError as error:
            raise ValueError(
                f"{label} escapes the repository: {location}"
            ) from error
    try:
        resolved = current.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"{label} is absent: {location}") from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError(f"{label} is absent or escapes the repository: {location}")
    return resolved


def _strict_http_url(value: str) -> str | None:
    if (
        not value
        or value != value.strip()
        or any(character.isspace() or ord(character) < 0x21 for character in value)
        or any(character in value for character in "'\"\\{}|^`")
        or re.search(r"%(?![0-9A-Fa-f]{2})", value)
    ):
        return "contains whitespace, control characters, unsafe delimiters or a malformed escape"
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return "has an invalid authority or port"
    if parsed.scheme not in {"http", "https"}:
        return "does not use HTTP(S)"
    if not parsed.netloc or not parsed.hostname:
        return "has no host"
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        return "contains credentials"
    if port is not None and not 1 <= port <= 65535:
        return "has a port outside 1-65535"
    host = parsed.hostname
    if host != host.casefold() or not re.fullmatch(
        r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)*"
        r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?",
        host,
    ):
        return "has a non-canonical or malformed host"
    return None


def _walk_scalars(value: Any, path: tuple[Any, ...] = ()) -> list[tuple[tuple[Any, ...], Any]]:
    found: list[tuple[tuple[Any, ...], Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.extend(_walk_scalars(child, (*path, key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_scalars(child, (*path, index)))
    else:
        found.append((path, value))
    return found


def schema_errors(value: dict[str, Any]) -> list[str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path))
    rendered: list[str] = []
    for error in errors:
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        rendered.append(f"{location}: {error.message}")
    return rendered


def walk_objects(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(walk_objects(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(walk_objects(child))
    return found


def referenced_values(value: dict[str, Any], key: str) -> list[str]:
    result: list[str] = []
    for item in walk_objects(value):
        refs = item.get(key)
        if isinstance(refs, list):
            result.extend(ref for ref in refs if isinstance(ref, str))
    return result


def reference_errors(value: dict[str, Any]) -> list[str]:
    objects = walk_objects(value)
    ids = [item["id"] for item in objects if isinstance(item.get("id"), str)]
    counts = Counter(ids)
    errors = [
        f"id {identifier!r} is declared {count} times"
        for identifier, count in sorted(counts.items())
        if count > 1
    ]

    sections: dict[str, set[str]] = {}
    for section in (
        "claims",
        "sources",
        "users",
        "tasks",
        "terminology",
        "standards",
        "rights_access_privacy",
        "validation",
        "constraints",
        "gaps",
        "decisions",
        "traceability",
        "evidence",
    ):
        rows = value.get(section, [])
        sections[section] = {
            row["id"]
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        }

    expected_refs = {
        "evidence_refs": sections["evidence"],
        "user_ids": sections["users"],
        "task_refs": sections["tasks"],
        "validation_refs": sections["validation"],
        "decision_refs": sections["decisions"],
        "gap_refs": sections["gaps"],
    }
    for key, allowed in expected_refs.items():
        missing = sorted(set(referenced_values(value, key)) - allowed)
        errors.extend(f"{key} references unknown id {identifier!r}" for identifier in missing)

    rights_ids = sections["rights_access_privacy"]
    for item in objects:
        if "rights_ref" in item and item.get("rights_ref") not in rights_ids:
            errors.append(
                f"{item.get('id', '<unknown>')!r} references unknown rights_ref "
                f"{item.get('rights_ref')!r}"
            )

    denominator_ids = {
        item["id"]
        for item in value.get("scope", {}).get("denominators", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    for source in value.get("sources", []):
        denominator_ref = source.get("coverage_denominator_ref")
        if denominator_ref is not None and denominator_ref not in denominator_ids:
            errors.append(
                f"source {source.get('id', '<unknown>')!r} references unknown "
                f"coverage_denominator_ref {denominator_ref!r}"
            )

    decisions = {
        item["id"]: item
        for item in value.get("decisions", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    declared_blockers = set(
        value.get("build_recommendation", {}).get("blocking_decision_ids", [])
    )
    actual_blockers = {
        identifier
        for identifier, item in decisions.items()
        if item.get("blocking_for_build") is True and item.get("status") == "open"
    }
    for identifier in sorted(declared_blockers - set(decisions)):
        errors.append(f"blocking_decision_ids references unknown decision {identifier!r}")
    if declared_blockers != actual_blockers:
        errors.append(
            "build_recommendation.blocking_decision_ids must exactly match open "
            "decisions with blocking_for_build=true"
        )

    if value.get("status") == "approved" and actual_blockers:
        errors.append("an approved domain profile cannot retain an open build-blocking decision")

    return errors


def local_evidence_digest_errors(value: dict[str, Any]) -> list[str]:
    """Verify every locally cited evidence file by exact bytes."""

    errors: list[str] = []
    evidence_rows = value.get("evidence")
    if not isinstance(evidence_rows, list):
        return ["profile evidence is not an array"]
    for row in evidence_rows:
        if not isinstance(row, dict):
            errors.append("profile evidence contains a non-object row")
            continue
        identifier = str(row.get("id", "<unknown>"))
        location = row.get("location")
        if not isinstance(location, str) or not location:
            errors.append(f"evidence {identifier!r} location is absent")
            continue
        parsed = urlsplit(location)
        if parsed.scheme or parsed.netloc or location.startswith("//"):
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                errors.append(
                    f"evidence {identifier!r} location uses an unsupported URL form"
                )
            continue
        try:
            path = _local_evidence_path(
                location, label=f"evidence {identifier!r} location"
            )
        except ValueError as error:
            errors.append(str(error))
            continue
        expected = row.get("sha256")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            errors.append(
                f"local evidence {identifier!r} requires an exact lowercase SHA-256"
            )
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected != actual:
            errors.append(
                f"local evidence {identifier!r} sha256 differs: "
                f"{expected!r} != {actual!r}"
            )
    return errors


def evidence_register_errors(value: dict[str, Any]) -> list[str]:
    """Require the standalone JSONL register to equal embedded profile evidence."""

    try:
        register_path = _repository_path(
            "domain-profile/evidence-register.jsonl",
            label="domain-profile evidence register",
        )
        rows = [
            json.loads(line)
            for line in register_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [f"domain-profile evidence register is invalid: {error}"]
    if rows != value.get("evidence"):
        return ["domain-profile evidence register differs from embedded evidence"]
    return []


def _repository_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _strict_utc(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a whole-second UTC timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise ValueError(
            f"{label} must be a whole-second UTC timestamp"
        ) from error
    return parsed.replace(tzinfo=UTC)


def predicate_registry_v2_evidence_errors(
    value: dict[str, Any], build_config: dict[str, Any]
) -> list[str]:
    """Bind delivered Stage 1 policy to the exact released v2 profile bytes."""

    errors: list[str] = []
    evidence_rows = {
        row.get("id"): row
        for row in value.get("evidence", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    evidence = evidence_rows.get(PREDICATE_REGISTRY_V2_EVIDENCE_ID)
    expected_locator = (
        "release.tag=v0.6.1; "
        "release.commit=839d4ba4c2d02abc6ef02b3ca1dcbf6a4008e7c8; "
        "release.annotated_tag_object="
        "b5918192b1e3969ca2b069a4d56b3d26884ea96c; "
        "release.published_at=2026-08-11T12:34:04Z; "
        f"profile={PREDICATE_REGISTRY_V2_PROFILE}; "
        "identity.sha256="
        "75e444a35fdfe28fc111b6f0490cb8a0d569d20c1e4b62410174ead2608d86c6; "
        "schema.bytes=7551; schema.sha256="
        "037151379a1ec0cbfe0666d41592585a891a63929f1fcf2845d1eb3de8dd5069"
    )
    expected_evidence = {
        "authority": "OKF Explorer v0.6.1 release",
        "location": "../profiles/predicate-registry/v2.lock.json",
        "locator": expected_locator,
        "observed_at": "2026-08-11T12:41:02Z",
        "retrieved_at": "2026-08-11T12:41:02Z",
        "sha256": PREDICATE_REGISTRY_V2_PROFILE_LOCK["sha256"],
        "verification": "support-checked",
    }
    if evidence is None or any(
        evidence.get(field) != expected
        for field, expected in expected_evidence.items()
    ):
        errors.append("released predicate-registry v2 evidence differs")

    expected_lock = {
        "schema": "okf-profile-extension-lock.v1",
        "profile": PREDICATE_REGISTRY_V2_PROFILE,
        "file_count": 2,
        "identity": {
            "algorithm": "sha256",
            "canonicalisation": (
                "profile-extension-lock-lines-v1: UTF-8 lines in lexical path "
                "order: <path> TAB <bytes> TAB <sha256> LF"
            ),
            "sha256": PREDICATE_REGISTRY_V2_PROFILE_LOCK["identity_sha256"],
        },
        "files": PREDICATE_REGISTRY_V2_PROFILE_FILES,
    }
    try:
        lock_path = _repository_path(
            PREDICATE_REGISTRY_V2_PROFILE_LOCK["path"],
            label="predicate-registry v2 profile lock",
        )
        lock_bytes = lock_path.read_bytes()
        if len(lock_bytes) != PREDICATE_REGISTRY_V2_PROFILE_LOCK["bytes"]:
            errors.append("predicate-registry v2 profile-lock byte count differs")
        if hashlib.sha256(lock_bytes).hexdigest() != PREDICATE_REGISTRY_V2_PROFILE_LOCK[
            "sha256"
        ]:
            errors.append("predicate-registry v2 profile-lock digest differs")
        lock = json.loads(lock_bytes)
        if lock != expected_lock:
            errors.append("predicate-registry v2 profile lock differs")

        profile_root = lock_path.parent / "v2"
        profile_entries = list(profile_root.iterdir())
        actual_names = {path.name for path in profile_entries}
        expected_names = {row["path"] for row in PREDICATE_REGISTRY_V2_PROFILE_FILES}
        if actual_names != expected_names or any(
            path.is_symlink() or not path.is_file() for path in profile_entries
        ):
            errors.append("predicate-registry v2 profile inventory differs")
        identity_lines: list[str] = []
        for row in PREDICATE_REGISTRY_V2_PROFILE_FILES:
            material_path = _repository_path(
                f"profiles/predicate-registry/v2/{row['path']}",
                label=f"predicate-registry v2 material {row['path']}",
            )
            material = material_path.read_bytes()
            if len(material) != row["bytes"]:
                errors.append(
                    f"predicate-registry v2 material byte count differs: {row['path']}"
                )
            if hashlib.sha256(material).hexdigest() != row["sha256"]:
                errors.append(
                    f"predicate-registry v2 material digest differs: {row['path']}"
                )
            identity_lines.append(
                f"{row['path']}\t{row['bytes']}\t{row['sha256']}\n"
            )
        identity = hashlib.sha256(
            "".join(identity_lines).encode("utf-8")
        ).hexdigest()
        if identity != PREDICATE_REGISTRY_V2_PROFILE_LOCK["identity_sha256"]:
            errors.append("predicate-registry v2 profile identity differs")
        schema = _repository_json(
            ROOT / PREDICATE_REGISTRY_V2_PROFILE_LOCK["schema_path"],
            label="predicate-registry v2 schema",
        )
        if schema.get("$id") != PREDICATE_REGISTRY_V2_SCHEMA_ID:
            errors.append("predicate-registry v2 schema identity differs")
        Draft202012Validator.check_schema(schema)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"predicate-registry v2 profile evidence is invalid: {error}")

    try:
        release_at = _strict_utc(
            PREDICATE_REGISTRY_V2_RELEASE["published_at"],
            label="predicate-registry v2 release published_at",
        )
        observed_at = _strict_utc(
            None if evidence is None else evidence.get("observed_at"),
            label="predicate-registry v2 evidence observed_at",
        )
        retrieved_at = _strict_utc(
            None if evidence is None else evidence.get("retrieved_at"),
            label="predicate-registry v2 evidence retrieved_at",
        )
        prepared_at = _strict_utc(
            value.get("prepared_at"), label="profile prepared_at"
        )
        generated_at = _strict_utc(
            build_config.get("generated_at"),
            label="source/build-config.json generated_at",
        )
        if not (
            release_at < observed_at
            and observed_at <= retrieved_at
            and retrieved_at < prepared_at
            and prepared_at < generated_at
        ):
            errors.append(
                "predicate-registry v2 release, observation, profile and build "
                "chronology differs"
            )
    except ValueError as error:
        errors.append(str(error))
    return errors


def source_governance_chronology_errors(
    value: dict[str, Any],
    source_register: dict[str, Any],
    publisher_registry: dict[str, Any],
    build_config: dict[str, Any],
) -> list[str]:
    """Keep source observation, governance review and generation distinct."""

    errors: list[str] = []
    try:
        prepared_at = _strict_utc(
            value.get("prepared_at"), label="profile prepared_at"
        )
        generated_at = _strict_utc(
            build_config.get("generated_at"),
            label="source/build-config.json generated_at",
        )
        for label, document in (
            ("source/source-register.json", source_register),
            ("source/publisher-registry.json", publisher_registry),
        ):
            observed_at = _strict_utc(
                document.get("observed_at"), label=f"{label} observed_at"
            )
            reviewed_at = _strict_utc(
                document.get("reviewed_at"), label=f"{label} reviewed_at"
            )
            if observed_at > reviewed_at:
                errors.append(f"{label} observed_at is after reviewed_at")
            if reviewed_at >= generated_at:
                errors.append(
                    f"{label} reviewed_at is not before build generated_at"
                )
            if prepared_at < reviewed_at:
                errors.append(f"profile prepared_at predates {label} review")
    except ValueError as error:
        errors.append(str(error))
    return errors


def semantic_contract_errors(value: dict[str, Any]) -> list[str]:
    """Validate semantic URLs, pointers, declared sets and cross-table closure."""

    errors: list[str] = []
    for path, scalar in _walk_scalars(value):
        if (
            isinstance(scalar, str)
            and scalar.startswith(("http://", "https://"))
            and (not path or path[-1] != "iri_pattern")
        ):
            problem = _strict_http_url(scalar)
            if problem:
                rendered = ".".join(str(part) for part in path)
                errors.append(f"{rendered} {problem}: {scalar!r}")

    semantic = value.get("semantic_model")
    if not isinstance(semantic, dict):
        return [*errors, "semantic_model is not an object"]
    authority = semantic.get("semantic_authority")
    if not isinstance(authority, dict):
        return [*errors, "semantic_model.semantic_authority is not an object"]

    pointer_contract = {
        "class_decisions_pointer": ("#/semantic_model/entity_types", list),
        "source_native_class_decisions_pointer": (
            "#/semantic_model/source_native_class_decisions",
            list,
        ),
        "relationship_decisions_pointer": (
            "#/semantic_model/relationship_types",
            list,
        ),
        "relationship_plane_decisions_pointer": (
            "#/semantic_model/relationship_planes",
            list,
        ),
        "derivation_rule_decisions_pointer": (
            "#/semantic_model/derivation_rules",
            list,
        ),
        "controlled_vocabulary_decisions_pointer": (
            "#/semantic_model/controlled_vocabulary_terms",
            list,
        ),
        "jurisdiction_decisions_pointer": (
            "#/semantic_model/jurisdiction_decisions",
            list,
        ),
        "identity_decisions_pointer": (
            "#/semantic_model/identifier_schemes",
            list,
        ),
        "source_rights_evidence_crosswalk_pointer": ("#/sources", list),
        "rights_decisions_pointer": ("#/rights_access_privacy", list),
    }
    for field, (expected, expected_type) in pointer_contract.items():
        pointer = authority.get(field)
        if pointer != expected:
            errors.append(
                f"semantic authority {field} must be the exact pointer {expected!r}"
            )
            continue
        try:
            target = _json_pointer(value, pointer, label=f"semantic authority {field}")
        except ValueError as error:
            errors.append(str(error))
            continue
        if not isinstance(target, expected_type):
            errors.append(f"semantic authority {field} resolves to the wrong type")

    def unique_field(rows: Any, field: str, label: str) -> dict[str, dict[str, Any]]:
        indexed: dict[str, dict[str, Any]] = {}
        if not isinstance(rows, list):
            errors.append(f"{label} is not an array")
            return indexed
        for row in rows:
            key = row.get(field) if isinstance(row, dict) else None
            if not isinstance(key, str) or not key:
                errors.append(f"{label} contains a row without {field}")
                continue
            if key in indexed:
                errors.append(f"{label} repeats {field} {key!r}")
            indexed[key] = row
        return indexed

    entity_rows = semantic.get("entity_types", [])
    entity_types = unique_field(entity_rows, "id", "entity types")
    native_rows = semantic.get("source_native_class_decisions", [])
    native_types = unique_field(
        native_rows, "source_native_type", "source-native class decisions"
    )
    relationships = unique_field(
        semantic.get("relationship_types", []), "id", "relationship types"
    )
    planes = unique_field(
        semantic.get("relationship_planes", []), "id", "relationship planes"
    )
    derivation_rules = unique_field(
        semantic.get("derivation_rules", []), "id", "derivation rules"
    )

    identity_family_ids: list[str] = []
    for scheme in semantic.get("identifier_schemes", []):
        if not isinstance(scheme, dict):
            continue
        for family in scheme.get("identity_families", []):
            if isinstance(family, dict) and isinstance(family.get("id"), str):
                identity_family_ids.append(family["id"])
    repeated_families = sorted(
        key for key, count in Counter(identity_family_ids).items() if count > 1
    )
    errors.extend(f"identity families repeat id {key!r}" for key in repeated_families)

    for label, rows in (
        ("controlled vocabulary", semantic.get("controlled_vocabulary_terms", [])),
        ("jurisdiction", semantic.get("jurisdiction_decisions", [])),
    ):
        observed: dict[str, str] = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            for source_value in row.get("source_values", []):
                if source_value in observed:
                    errors.append(
                        f"{label} source value {source_value!r} is repeated by "
                        f"{observed[source_value]!r} and {row.get('id')!r}"
                    )
                observed[source_value] = str(row.get("id"))

    active_entity_classes = {
        class_iri
        for row in entity_types.values()
        if row.get("implementation_state") == "active-emitted"
        for class_iri in row.get("class_iris", [])
    }
    zero_entity_classes = {
        class_iri
        for row in entity_types.values()
        if row.get("implementation_state") == "authorised-zero-evidence"
        for class_iri in row.get("class_iris", [])
    }
    overlap = sorted(active_entity_classes & zero_entity_classes)
    if overlap:
        errors.append(f"active and authorised-zero entity classes overlap: {overlap}")
    governed_classes = active_entity_classes | zero_entity_classes | {RDFS_RESOURCE}

    for row in native_types.values():
        unknown = sorted(set(row.get("class_iris", [])) - active_entity_classes)
        if unknown:
            errors.append(
                f"source-native type {row.get('source_native_type')!r} uses "
                f"classes not active in entity authority: {unknown}"
            )
    for row in [
        *semantic.get("controlled_vocabulary_terms", []),
        *semantic.get("jurisdiction_decisions", []),
    ]:
        if not isinstance(row, dict):
            continue
        row_classes = row.get("class_iris", [row.get("class_iri")])
        unknown = sorted(
            class_iri
            for class_iri in row_classes
            if class_iri and class_iri not in active_entity_classes
        )
        if unknown:
            errors.append(
                f"semantic decision {row.get('id')!r} uses non-active classes: {unknown}"
            )

    if len(planes) != 1:
        errors.append("Stage 1 must declare exactly one relationship plane")
    core_plane = planes.get(CORE_PLANE["id"])
    if core_plane is None or any(
        core_plane.get(field) != expected for field, expected in CORE_PLANE.items()
    ):
        errors.append("Stage 1 core relationship-plane declaration differs")
    plane_iris = [row.get("iri") for row in planes.values()]
    if any(not isinstance(iri, str) or not iri for iri in plane_iris):
        errors.append("Stage 1 relationship plane lacks an IRI")
    elif len(plane_iris) != len(set(plane_iris)):
        errors.append("Stage 1 relationship-plane IRIs collide")

    active_relationship_ids = {
        identifier
        for identifier, row in relationships.items()
        if row.get("implementation_state") == "active-emitted"
    }
    zero_relationship_ids = {
        identifier
        for identifier, row in relationships.items()
        if row.get("implementation_state") == "authorised-zero-evidence"
    }
    if active_relationship_ids != set(EXPECTED_RULE_IRIS_BY_RELATIONSHIP_ID):
        errors.append("active relationship IDs differ from the reviewed rule closure")
    rule_iris: dict[str, str] = {}
    covered_relationships: list[str] = []
    source_observation_rules: list[dict[str, Any]] = []
    for rule_id, row in derivation_rules.items():
        iri = row.get("iri")
        if not isinstance(iri, str) or _strict_http_url(iri):
            errors.append(f"derivation rule {rule_id!r} lacks a valid HTTP(S) IRI")
            continue
        if iri in rule_iris:
            errors.append(
                f"derivation rule IRI {iri!r} is repeated by "
                f"{rule_iris[iri]!r} and {rule_id!r}"
            )
        rule_iris[iri] = rule_id
        role = row.get("rule_role")
        if role == "source-observation":
            source_observation_rules.append(row)
            if iri != SOURCE_OBSERVATION_RULE_IRI:
                errors.append("source-observation rule IRI differs")
            continue
        if role != "relationship-derivation":
            errors.append(f"derivation rule {rule_id!r} has an unknown role")
            continue
        refs = row.get("relationship_type_refs")
        if not isinstance(refs, list):
            errors.append(
                f"relationship derivation rule {rule_id!r} lacks relationship refs"
            )
            continue
        covered_relationships.extend(refs)
        unknown = sorted(set(refs) - active_relationship_ids)
        if unknown:
            errors.append(
                f"derivation rule {rule_id!r} governs inactive or unknown "
                f"relationships: {unknown}"
            )
        for relationship_id in refs:
            expected_iri = EXPECTED_RULE_IRIS_BY_RELATIONSHIP_ID.get(relationship_id)
            if expected_iri is not None and iri != expected_iri:
                errors.append(
                    f"derivation rule for {relationship_id!r} has an unexpected IRI"
                )
    repeated_relationships = sorted(
        identifier
        for identifier, count in Counter(covered_relationships).items()
        if count != 1
    )
    if set(covered_relationships) != active_relationship_ids or repeated_relationships:
        errors.append(
            "derivation rules do not cover each active relationship exactly once"
        )
    if set(covered_relationships) & zero_relationship_ids:
        errors.append("a derivation rule governs an authorised-zero relationship")
    if len(source_observation_rules) != 1:
        errors.append("Stage 1 must declare exactly one source-observation rule")

    families_by_id = {
        family.get("id"): family
        for scheme in semantic.get("identifier_schemes", [])
        if isinstance(scheme, dict)
        for family in scheme.get("identity_families", [])
        if isinstance(family, dict)
    }
    rule_family = families_by_id.get("IDF-RULE")
    if (
        rule_family is None
        or rule_family.get("membership_policy") != "exact-closed-set"
        or rule_family.get("closed_member_iris_pointer")
        != "#/semantic_model/derivation_rules"
        or rule_family.get("closed_member_iri_field") != "iri"
    ):
        errors.append("IDF-RULE does not bind the exact derivation-rule member set")

    predicates: dict[str, str] = {}
    for relationship_id, row in relationships.items():
        predicate = row.get("predicate_iri")
        if predicate in predicates:
            errors.append(
                f"relationship predicate {predicate!r} is repeated by "
                f"{predicates[predicate]!r} and {relationship_id!r}"
            )
        elif isinstance(predicate, str):
            predicates[predicate] = relationship_id
        for field in ("source_type_ids", "target_type_ids"):
            unknown_types = sorted(set(row.get(field, [])) - set(entity_types))
            if unknown_types:
                errors.append(
                    f"relationship {relationship_id!r} {field} references unknown "
                    f"entity types: {unknown_types}"
                )
        for labels_field, ids_field in (
            ("source_types", "source_type_ids"),
            ("target_types", "target_type_ids"),
        ):
            labels = row.get(labels_field, [])
            unknown_labels = sorted(
                label
                for label in labels
                if label not in RELATIONSHIP_TYPE_LABEL_IDS
            )
            if unknown_labels:
                errors.append(
                    f"relationship {relationship_id!r} {labels_field} uses unknown "
                    f"governed labels: {unknown_labels}"
                )
                continue
            expected_type_ids = sorted(
                {
                    type_id
                    for label in labels
                    for type_id in RELATIONSHIP_TYPE_LABEL_IDS[label]
                }
            )
            if row.get(ids_field) != expected_type_ids:
                errors.append(
                    f"relationship {relationship_id!r} {labels_field} does not "
                    f"project exactly to {ids_field}"
                )
        for field in ("domain_class_iris", "range_class_iris"):
            unknown_classes = sorted(set(row.get(field, [])) - governed_classes)
            if unknown_classes:
                errors.append(
                    f"relationship {relationship_id!r} {field} uses undeclared "
                    f"classes: {unknown_classes}"
                )
        policy = row.get("registry_evidence_policy", {})
        if set(policy.get("minimum_fields", [])) != PREDICATE_EVIDENCE_FIELDS:
            errors.append(
                f"relationship {relationship_id!r} has incomplete registry evidence fields"
            )

    projection_policy = authority.get("predicate_registry_projection_policy")
    expected_projection_counts = {
        "predicates": len(relationships),
        "active_emitted": len(active_relationship_ids),
        "authorised_zero_evidence": len(zero_relationship_ids),
        "assertions_emitted": PREDICATE_REGISTRY_V2_ASSERTIONS_EMITTED,
    }
    expected_projection_policy = {
        "compatibility": "separate-full-v2-schema-alongside-v1",
        "consumer_dependency": "okf-explorer-v0.6.2",
        "consumer_lock_requirements": {
            "pins_separate_v2_schema_bytes": True,
            "required_projection_schema": "okf-predicate-registry.v2",
            "supported_registry_schemas": [
                "okf-predicate-registry.v1",
                "okf-predicate-registry.v2",
            ],
        },
        "consumer_release": PREDICATE_REGISTRY_V2_RELEASE,
        "current_delivery": "delivered",
        "evidence_refs": [PREDICATE_REGISTRY_V2_EVIDENCE_ID],
        "expected_counts": expected_projection_counts,
        "profile_lock": PREDICATE_REGISTRY_V2_PROFILE_LOCK,
        "registry_required_fields": PREDICATE_REGISTRY_V2_REQUIRED_FIELDS,
        "root_sha256_binding": {
            "canonical_material": [
                "schema",
                "profile",
                "snapshot",
                "generated_at",
                "predicates",
                "counts",
            ],
            "excluded_field": "root_sha256",
        },
        "stage1_authoring_state_field": "implementation_state",
        "wire_implementation": PREDICATE_REGISTRY_V2_WIRE_IMPLEMENTATION,
    }
    if projection_policy != expected_projection_policy:
        errors.append("released predicate-registry v2 projection policy differs")

    required_entity_classes = {
        "TYPE-BUNDLE": {"https://chris-page-gov.github.io/okf-explorer/ns#Bundle"},
        "TYPE-EVIDENCE-RESOURCE": {
            "http://www.w3.org/ns/prov#Entity",
            "https://chris-page-gov.github.io/okf-explorer/ns#EvidenceResource",
        },
        "TYPE-EVIDENCE-BINDING": {
            "http://www.w3.org/ns/prov#Entity",
            "https://chris-page-gov.github.io/okf-explorer/ns#EvidenceBinding",
        },
    }
    for entity_id, classes in required_entity_classes.items():
        row = entity_types.get(entity_id)
        if row is None or set(row.get("class_iris", [])) != classes:
            errors.append(f"{entity_id} class authority is absent or differs")

    required_identity_patterns = {
        "IDF-EVIDENCE-RESOURCE": "/id/evidence-resource/",
        "IDF-EVIDENCE-BINDING": "/id/evidence/",
        "IDF-LOCAL-AGENT": "/id/agent/",
        "IDF-EXTERNAL-GITHUB-ORGANISATION": "GitHub organisation URL",
    }
    families_by_id = {
        family.get("id"): family
        for scheme in semantic.get("identifier_schemes", [])
        if isinstance(scheme, dict)
        for family in scheme.get("identity_families", [])
        if isinstance(family, dict)
    }
    for family_id, marker in required_identity_patterns.items():
        family = families_by_id.get(family_id)
        if family is None or marker not in str(family.get("iri_pattern")):
            errors.append(f"identity family {family_id} is absent or differs")

    declared_values = {
        "active_entity_class_iris": sorted(active_entity_classes),
        "active_entity_type_ids": [
            identifier
            for identifier, row in entity_types.items()
            if row.get("implementation_state") == "active-emitted"
        ],
        "active_relationship_predicate_iris": [
            row["predicate_iri"]
            for row in relationships.values()
            if row.get("implementation_state") == "active-emitted"
        ],
        "authorised_zero_relationship_predicate_iris": [
            row["predicate_iri"]
            for row in relationships.values()
            if row.get("implementation_state") == "authorised-zero-evidence"
        ],
        "derivation_rule_iris": list(rule_iris),
        "authorised_zero_entity_class_iris": sorted(zero_entity_classes),
        "authorised_zero_entity_type_ids": [
            identifier
            for identifier, row in entity_types.items()
            if row.get("implementation_state") == "authorised-zero-evidence"
        ],
        "entity_type_ids": list(entity_types),
        "identity_family_ids": identity_family_ids,
        "relationship_type_ids": list(relationships),
        "relationship_plane_iris": list(plane_iris),
        "rights_ids": [
            row.get("id")
            for row in value.get("rights_access_privacy", [])
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        ],
        "source_family_ids": [
            row["source_families"][0]
            for row in value.get("sources", [])
            if isinstance(row, dict)
            and isinstance(row.get("source_families"), list)
            and len(row["source_families"]) == 1
            and isinstance(row["source_families"][0], str)
        ],
        "source_native_types": list(native_types),
    }
    declared_sets = authority.get("declared_sets")
    if not isinstance(declared_sets, dict) or set(declared_sets) != DECLARED_SET_NAMES:
        errors.append("semantic authority declared-set names are incomplete or unexpected")
    else:
        for name, values in declared_values.items():
            if declared_sets.get(name) != _set_identity(values):
                errors.append(f"semantic authority declared set {name!r} differs")

    policy = authority.get("class_route_delivery_index_policy")
    if not isinstance(policy, dict):
        errors.append("class-route delivery-index policy is absent")
    else:
        try:
            schema_path = _repository_path(
                policy.get("schema_path"), label="class-route schema path"
            )
            class_route_schema = _repository_json(
                schema_path, label="class-route delivery-index schema"
            )
            if class_route_schema.get("$id") != policy.get("schema_id"):
                errors.append("class-route schema identity differs from its policy")
        except ValueError as error:
            errors.append(str(error))
        if policy.get("role") != "deterministic-delivery-index" or "not ontology authority" not in str(
            policy.get("authority_statement")
        ):
            errors.append("class-route sidecar is not explicitly delivery-only")

    return errors


def delegated_authority_errors(value: dict[str, Any]) -> list[str]:
    """Verify every external semantic authority by exact bytes and record set."""

    errors: list[str] = []
    authority = value.get("semantic_model", {}).get("semantic_authority", {})
    rows = authority.get("delegated_authorities")
    if not isinstance(rows, list):
        return ["delegated semantic authorities are not an array"]
    indexed: dict[str, dict[str, Any]] = {}
    paths: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            errors.append("delegated semantic authority lacks a string id")
            continue
        identifier = row["id"]
        relative = row.get("path")
        if identifier in indexed:
            errors.append(f"delegated semantic authority repeats id {identifier!r}")
        indexed[identifier] = row
        if isinstance(relative, str):
            if relative in paths:
                errors.append(
                    f"delegated semantic authority path {relative!r} is repeated"
                )
            paths[relative] = identifier
    if set(paths) != set(DELEGATED_AUTHORITIES):
        errors.append(
            "delegated semantic authority paths differ: "
            f"{sorted(set(paths) ^ set(DELEGATED_AUTHORITIES))}"
        )

    for relative, expected_pointer in DELEGATED_AUTHORITIES.items():
        row = next(
            (
                candidate
                for candidate in rows
                if isinstance(candidate, dict) and candidate.get("path") == relative
            ),
            None,
        )
        if row is None:
            continue
        try:
            path = _repository_path(relative, label=f"delegated authority {relative}")
            raw = path.read_bytes()
            document = json.loads(raw)
            if not isinstance(document, dict):
                raise ValueError(f"delegated authority {relative} is not an object")
            records = _json_pointer(
                document,
                row.get("record_pointer"),
                label=f"delegated authority {relative} record_pointer",
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            errors.append(str(error))
            continue
        if row.get("record_pointer") != expected_pointer:
            errors.append(
                f"delegated authority {relative} record pointer must be "
                f"{expected_pointer!r}"
            )
        if not isinstance(records, list):
            errors.append(f"delegated authority {relative} record set is not an array")
            continue
        actual_version = document.get("version") or "digest-pinned-unversioned"
        checks = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "schema_id": document.get("schema"),
            "version": actual_version,
            "record_count": len(records),
            "completeness": "exhaustive-set-exact",
        }
        for field, expected in checks.items():
            if row.get(field) != expected:
                errors.append(
                    f"delegated authority {relative} {field} differs: "
                    f"{row.get(field)!r} != {expected!r}"
                )

    publisher_delegation = next(
        (
            row
            for row in rows
            if isinstance(row, dict)
            and row.get("path") == "source/publisher-registry.json"
        ),
        None,
    )
    legacy_path = authority.get("publisher_registry_path")
    if legacy_path != "../source/publisher-registry.json" or publisher_delegation is None:
        errors.append("publisher registry compatibility path or delegation differs")
    for source in value.get("sources", []):
        if not isinstance(source, dict):
            continue
        binding = source.get("publisher_binding")
        if not isinstance(binding, dict) or binding.get("registry_path") != legacy_path:
            errors.append(
                f"source {source.get('id')!r} publisher registry path differs"
            )

    return errors


def repository_contract_errors(value: dict[str, Any]) -> list[str]:
    """Close Stage 1 over the exact governed Stage 2 contract."""

    errors: list[str] = delegated_authority_errors(value)
    try:
        inventory_path = ROOT / "research" / "source-family-inventory.json"
        inventory = _repository_json(
            inventory_path, label="research source-family inventory"
        )
        source_register = _repository_json(
            ROOT / "source" / "source-register.json",
            label="runtime source register",
        )
        publisher_registry = _repository_json(
            ROOT / "source" / "publisher-registry.json",
            label="publisher registry",
        )
        build_config = _repository_json(
            ROOT / "source" / "build-config.json",
            label="build configuration",
        )
        rights_review = _repository_json(
            ROOT / "governance" / "rights-review.json",
            label="governed rights review",
        )
        cpsv_mappings = _repository_json(
            ROOT / "source" / "cpsv-service-mappings.json",
            label="CPSV-AP service mappings",
        )
    except ValueError as error:
        return [str(error)]

    errors.extend(predicate_registry_v2_evidence_errors(value, build_config))

    profile_sources = value.get("sources", [])
    inventory_sources = inventory.get("source_families", [])
    runtime_sources = source_register.get("source_families", [])
    governed_rights = rights_review.get("assessments", [])
    if not all(
        isinstance(rows, list)
        for rows in (
            profile_sources,
            inventory_sources,
            runtime_sources,
            governed_rights,
        )
    ):
        return ["Stage 1 source and rights contracts must all be arrays"]

    def index_rows(rows: list[Any], *, label: str) -> dict[str, dict[str, Any]]:
        indexed: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                errors.append(f"{label} contains a row without a string id")
                continue
            identifier = row["id"]
            if identifier in indexed:
                errors.append(f"{label} repeats id {identifier!r}")
            indexed[identifier] = row
        return indexed

    profile_by_id = index_rows(profile_sources, label="profile sources")
    inventory_by_id = index_rows(inventory_sources, label="research sources")
    runtime_by_id = index_rows(runtime_sources, label="runtime sources")
    rights_by_id = index_rows(governed_rights, label="governed rights")
    profile_rights = index_rows(
        value.get("rights_access_privacy", []), label="profile rights"
    )

    if set(profile_by_id) != set(inventory_by_id):
        errors.append(
            "profile and research source identities differ: "
            f"{sorted(set(profile_by_id) ^ set(inventory_by_id))}"
        )
    if set(profile_rights) != set(rights_by_id):
        errors.append(
            "profile and governed rights identities differ: "
            f"{sorted(set(profile_rights) ^ set(rights_by_id))}"
        )
    for rights_id in sorted(set(profile_rights) & set(rights_by_id)):
        profile_right = profile_rights[rights_id]
        governed_right = rights_by_id[rights_id]
        for field in ("layer", "status", "licence", "attribution", "evidence_refs"):
            if profile_right.get(field) != governed_right.get(field):
                errors.append(
                    f"profile right {rights_id!r} {field} differs from governed rights"
                )

    mapped_families: list[str] = []
    for profile_id, source in profile_by_id.items():
        families = source.get("source_families")
        if (
            not isinstance(families, list)
            or len(families) != 1
            or not isinstance(families[0], str)
            or not families[0]
        ):
            errors.append(
                f"profile source {profile_id!r} must map to exactly one runtime "
                "source family"
            )
            continue
        family_id = families[0]
        mapped_families.append(family_id)
        runtime = runtime_by_id.get(family_id)
        if runtime is None:
            errors.append(
                f"profile source {profile_id!r} maps unknown runtime family "
                f"{family_id!r}"
            )
            continue
        primary_right = runtime.get("primary_rights_ref")
        if not isinstance(primary_right, str) or primary_right not in rights_by_id:
            errors.append(
                f"runtime source {family_id!r} lacks a governed primary_rights_ref"
            )
        elif source.get("rights_ref") != primary_right:
            errors.append(
                f"profile source {profile_id!r} rights_ref differs from runtime "
                f"family {family_id!r}"
            )
        if sorted(source.get("evidence_refs", [])) != sorted(
            runtime.get("evidence_refs", [])
        ):
            errors.append(
                f"profile source {profile_id!r} evidence_refs differ from runtime "
                f"family {family_id!r}"
            )
        publisher_binding = source.get("publisher_binding")
        if (
            not isinstance(publisher_binding, dict)
            or publisher_binding.get("strategy")
            != runtime.get("publisher_treatment")
        ):
            errors.append(
                f"profile source {profile_id!r} publisher strategy differs from "
                f"runtime family {family_id!r}"
            )
    if len(mapped_families) != len(set(mapped_families)):
        errors.append("profile source-family crosswalk is not one-to-one")
    if set(mapped_families) != set(runtime_by_id):
        errors.append(
            "profile and runtime source-family identities differ: "
            f"{sorted(set(mapped_families) ^ set(runtime_by_id))}"
        )

    inventory_bytes = inventory_path.read_bytes()
    input_snapshot = value.get("input_snapshot", {})
    if not isinstance(input_snapshot, dict):
        errors.append("profile input_snapshot must be an object")
    else:
        if input_snapshot.get("item_count") != len(inventory_sources):
            errors.append("profile input_snapshot.item_count differs from inventory")
        if input_snapshot.get("byte_count") != len(inventory_bytes):
            errors.append("profile input_snapshot.byte_count differs from inventory")
        if input_snapshot.get("inventory_sha256") != hashlib.sha256(
            inventory_bytes
        ).hexdigest():
            errors.append("profile input_snapshot.inventory_sha256 differs")

    runtime_overrides = sorted(
        (
            {
                "access_state": override.get("access_state"),
                "additional_evidence_refs": override.get(
                    "additional_evidence_refs", []
                ),
                "canonical_source_host": override.get("canonical_source_host"),
                "primary_rights_ref": override.get("primary_rights_ref"),
                "rights_state": override.get("rights_state"),
                "source_family": family.get("id"),
            }
            for family in runtime_sources
            if isinstance(family, dict)
            for override in family.get("rights_overrides", [])
            if isinstance(override, dict)
        ),
        key=lambda row: (str(row["source_family"]), str(row["canonical_source_host"])),
    )
    profile_overrides = sorted(
        value.get("semantic_model", {})
        .get("semantic_authority", {})
        .get("source_rights_overrides", []),
        key=lambda row: (str(row.get("source_family")), str(row.get("canonical_source_host"))),
    )
    if profile_overrides != runtime_overrides:
        errors.append("Stage 1 source-rights overrides differ from the runtime register")

    required_standards = {
        "STD-CPSVAP-320",
        "STD-DCMI-TERMS-20200120",
        "STD-FOAF-099",
        "STD-SCHEMAORG-LIVING",
        "STD-RDF-11",
        "STD-SKOS-20090818",
        "STD-CPOV-212",
        "STD-EU-ATU-TYPE",
        "STD-EU-LANGUAGE-AUTHORITY",
        "STD-OKF-PREDICATE-REGISTRY-V2",
    }
    standards = index_rows(value.get("standards", []), label="profile standards")
    if not required_standards <= set(standards):
        errors.append(
            "profile omits emitted semantic standards/vocabularies: "
            f"{sorted(required_standards - set(standards))}"
        )

    try:
        prepared_at = _strict_utc(
            value.get("prepared_at"), label="profile prepared_at"
        )
        reviewed_times = [
            _strict_utc(row["reviewed_at"], label="CPSV reviewed_at")
            for row in walk_objects(cpsv_mappings)
            if "reviewed_at" in row
        ]
        if reviewed_times and prepared_at < max(reviewed_times):
            errors.append("profile prepared_at predates a governed CPSV review")
    except ValueError as error:
        errors.append(str(error))
    errors.extend(
        source_governance_chronology_errors(
            value,
            source_register,
            publisher_registry,
            build_config,
        )
    )

    for trace in value.get("traceability", []):
        if not isinstance(trace, dict):
            continue
        for planned in trace.get("planned_artifacts", []):
            if not isinstance(planned, str) or not planned:
                errors.append("traceability contains an invalid planned artefact")
                continue
            matches = list(ROOT.glob(planned))
            if not matches:
                errors.append(
                    f"traceability planned artefact does not exist: {planned}"
                )
    return errors


def validate(value: dict[str, Any]) -> list[str]:
    errors = schema_errors(value)
    if not errors:
        errors.extend(reference_errors(value))
    if not errors:
        errors.extend(local_evidence_digest_errors(value))
    if not errors:
        errors.extend(evidence_register_errors(value))
    if not errors:
        errors.extend(semantic_contract_errors(value))
    if not errors:
        errors.extend(repository_contract_errors(value))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path, help="domain-profile JSON or YAML")
    parser.add_argument(
        "--equivalent",
        type=Path,
        help="optional JSON/YAML counterpart that must represent exactly the same data",
    )
    args = parser.parse_args()

    try:
        profile = load_document(args.profile)
        errors = validate(profile)
        if args.equivalent:
            equivalent = load_document(args.equivalent)
            equivalent_errors = validate(equivalent)
            errors.extend(
                f"{args.equivalent}: {error}" for error in equivalent_errors
            )
            if canonical_bytes(profile) != canonical_bytes(equivalent):
                errors.append(
                    f"{args.profile} and {args.equivalent} do not represent the same data"
                )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"domain profile validation failed: {error}", file=sys.stderr)
        return 1

    if errors:
        print("domain profile validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    digest = hashlib.sha256(canonical_bytes(profile)).hexdigest()
    print(
        "domain profile validation passed: "
        f"{profile['profile_id']} version {profile['version']} "
        f"canonical-sha256 {digest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

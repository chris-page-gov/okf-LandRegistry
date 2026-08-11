#!/usr/bin/env python3
"""Validate digest-bound G1-G9 release evidence for the exact repository candidate."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import stat
import struct
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterator
import unicodedata
from urllib.parse import urlparse
import zipfile

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ID = (
    "https://chris-page-gov.github.io/okf-LandRegistry/"
    "schemas/release-evidence.schema.json"
)
RELEASE_EVIDENCE_SCHEMA_PATH = "schemas/release-evidence.schema.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
GIT_OBJECT_ID = re.compile(r"^[0-9a-f]{40,64}$")
RELEASE_VERSION = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"
)
RFC3339_UTC = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)
GATE_RECEIPTS = tuple(f"G{number}" for number in range(1, 9))
ALL_GATES = (*GATE_RECEIPTS, "G9")
REVIEWED_GATES = frozenset({"G3", "G5", "G6"})
MAX_JSON_BYTES = 5_000_000
MAX_EVIDENCE_BYTES = 50_000_000
MAX_BUNDLE_ARTEFACT_BYTES = 100 * 1024 * 1024
MAX_BUNDLE_CHECKSUM_ENTRIES = 2_000
MAX_BUNDLE_AGGREGATE_BYTES = 1024 * 1024 * 1024
MAX_PROFILE_CHECKSUM_ENTRIES = 100
MAX_PROFILE_AGGREGATE_BYTES = MAX_EVIDENCE_BYTES
MAX_GOVERNED_INPUTS = 1_000
MAX_GOVERNED_INPUT_AGGREGATE_BYTES = 100 * 1024 * 1024
MAX_EVIDENCE_REFERENCES = 256
MAX_EVIDENCE_REFERENCE_AGGREGATE_BYTES = 500 * 1024 * 1024
MAX_GIT_INVENTORY_BYTES = 16 * 1024 * 1024
MAX_GIT_DIAGNOSTIC_BYTES = 256 * 1024
MAX_GIT_INVENTORY_PATHS = 100_000
MAX_EVIDENCE_COMMITS = 1_000
GIT_COMMAND_TIMEOUT_SECONDS = 30
MAX_ARCHIVE_MEMBERS = MAX_BUNDLE_CHECKSUM_ENTRIES + 1
MAX_ARCHIVE_UNCOMPRESSED_BYTES = MAX_BUNDLE_AGGREGATE_BYTES + MAX_JSON_BYTES
LEGACY_UNBOUND_RELEASE_IDENTITIES = frozenset(
    {
        (
            "0.1.0",
            "edbe2cb61a3d916bc149519ad489f957c5ee3a38",
            "a3c15f0ee3c104e8b8f82ab770edd1b2ec9b16b2d72a9e2c4a6a1281e5b80c3c",
            "fa911660f568e7a69c3aa63df7ac18f9c391da05dc7d24d6cd8d5a94e4c2617c",
            "0534e05b3840ef4382d18ce1f4dec78676f9b423de4a7749053f1f7da9295480",
            "143a70620c26fbac72febe0939c263faa76120d2d821f4b097550a5804270df6",
            "6a50ff8e542c59d7d270aff20a6ce0582e58e8d66a350361cc456bcd1474657d",
            "746e5a840fbacb195d738d5be17246da1f2969cce2743b1d3acc072ba8d13b62",
        ),
        (
            "0.2.0",
            "40482c865dc4332162f1e93756d94ca93abe3559",
            "a3e0bdf7846893ce29255f6f20a509dad18ef2b367ba3dfbe48c28191377a704",
            "1ddbe5ce950ba716b2d88e8d8fd4ef0b0c29596610819ee7994bd1d1d9a0f2d9",
            "47f0a5c1a89c78cdeda8e57623db46036753de752766588874fa5835a36a0d95",
            "f4c30b27d4238a080957bf71403c73a43fc4d570a04d19b9be3c7c657b8f61dc",
            "46aadf4563f878285de3155124870be7144ec91f9f00e825c0e297b5618e2c11",
            "facafcc21bf0b69a8b97a47df0cfd334b0a45f30680c6dc69c9c623f5423be9f",
        ),
    }
)
PRE_G9_MANIFEST_SCHEMA = "okf-pre-g9-evidence-manifest.v1"
GOVERNED_RISK_REGISTER = "governance/risk-register.json"


@dataclass(frozen=True)
class GitTreeEntry:
    """One exact path entry from a Git index or commit tree."""

    mode: str
    object_id: str


@dataclass(frozen=True)
class ArchiveMemberIdentity:
    """Expected digest and uncompressed size for one archive member."""

    sha256: str
    bytes: int


@dataclass(frozen=True)
class CandidateClosure:
    """Exact identity and inventory count proved from one Git view."""

    governed_count: int
    release_root_sha256: str
    checksums_sha256: str
    profile_pack_root_sha256: str
    snapshot_manifest_sha256: str

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


@dataclass(frozen=True)
class ReleaseCoordinates:
    version: str
    canonical_url: str
    build_config_sha256: str
    publication_state: str
    generated_at: str
    release_at: str | None


def sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_open_descriptor(
    descriptor: int,
    *,
    label: str,
    max_bytes: int,
) -> bytes:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise ReleaseEvidenceError(f"{label} is not a regular file")
    if before.st_size > max_bytes:
        raise ReleaseEvidenceError(
            f"{label} exceeds the {max_bytes}-byte read limit"
        )
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(65_536, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise ReleaseEvidenceError(
                f"{label} exceeds the {max_bytes}-byte read limit"
            )
    after = os.fstat(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ReleaseEvidenceError(f"{label} changed while it was being read")
    if total != after.st_size:
        raise ReleaseEvidenceError(
            f"{label} size changed or could not be read completely"
        )
    return b"".join(chunks)


def _read_regular_path_bytes(
    path: Path,
    *,
    label: str,
    max_bytes: int,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseEvidenceError(f"cannot open {label}: {exc}") from exc
    try:
        return _read_open_descriptor(
            descriptor,
            label=label,
            max_bytes=max_bytes,
        )
    finally:
        os.close(descriptor)


@contextmanager
def _open_repository_file_descriptor(
    repository_root: Path,
    relative_name: str,
    *,
    purpose: str,
) -> Iterator[int]:
    safe_repository_file(repository_root, relative_name, purpose=purpose)
    relative = PurePosixPath(relative_name)
    root = repository_root.resolve()
    supports_directory_walk = (
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_NONBLOCK")
        and os.open in os.supports_dir_fd
    )
    if not supports_directory_walk:
        raise ReleaseEvidenceError(
            "secure repository-relative file opening is unavailable; "
            f"refusing the race-prone fallback for {purpose}"
        )

    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | os.O_NONBLOCK
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptors: list[int] = []
    try:
        current = os.open(root, directory_flags)
        descriptors.append(current)
        for part in relative.parts[:-1]:
            current = os.open(part, directory_flags, dir_fd=current)
            descriptors.append(current)
        file_descriptor = os.open(
            relative.parts[-1], file_flags, dir_fd=current
        )
        descriptors.append(file_descriptor)
        yield file_descriptor
    except OSError as exc:
        raise ReleaseEvidenceError(
            f"cannot safely open {purpose} {relative_name!r}: {exc}"
        ) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def read_repository_file_bytes(
    repository_root: Path,
    relative_name: str,
    *,
    purpose: str,
    max_bytes: int = MAX_JSON_BYTES,
) -> bytes:
    """Read one bounded regular repository file without following symlinks."""

    with _open_repository_file_descriptor(
        repository_root,
        relative_name,
        purpose=purpose,
    ) as file_descriptor:
        return _read_open_descriptor(
            file_descriptor,
            label=purpose,
            max_bytes=max_bytes,
        )


def _sha256_open_descriptor(
    descriptor: int,
    *,
    label: str,
    max_bytes: int,
    aggregate_bytes_remaining: int,
) -> tuple[str, int, tuple[int, int, int, int, int]]:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise ReleaseEvidenceError(f"{label} is not a regular file")
    if before.st_size > max_bytes:
        raise ReleaseEvidenceError(
            f"{label} exceeds the {max_bytes}-byte read limit"
        )
    if before.st_size > aggregate_bytes_remaining:
        raise ReleaseEvidenceError(
            f"{label} exceeds the remaining "
            f"{aggregate_bytes_remaining}-byte aggregate allowance"
        )

    digest = hashlib.sha256()
    total = 0
    read_ceiling = min(max_bytes, aggregate_bytes_remaining)
    while True:
        chunk = os.read(descriptor, min(65_536, read_ceiling + 1 - total))
        if not chunk:
            break
        digest.update(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise ReleaseEvidenceError(
                f"{label} exceeds the {max_bytes}-byte read limit"
            )
        if total > aggregate_bytes_remaining:
            raise ReleaseEvidenceError(
                f"{label} exceeds the remaining "
                f"{aggregate_bytes_remaining}-byte aggregate allowance"
            )

    after = os.fstat(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ReleaseEvidenceError(f"{label} changed while it was being read")
    if total != after.st_size:
        raise ReleaseEvidenceError(
            f"{label} size changed or could not be read completely"
        )
    return (
        digest.hexdigest(),
        total,
        (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ),
    )


def sha256_repository_file(
    repository_root: Path,
    relative_name: str,
    *,
    purpose: str,
    max_bytes: int,
    aggregate_bytes_remaining: int,
) -> tuple[str, int]:
    """Stream one securely opened repository file into SHA-256."""

    with _open_repository_file_descriptor(
        repository_root,
        relative_name,
        purpose=purpose,
    ) as file_descriptor:
        digest, size, hashed_identity = _sha256_open_descriptor(
            file_descriptor,
            label=purpose,
            max_bytes=max_bytes,
            aggregate_bytes_remaining=aggregate_bytes_remaining,
        )
        with _open_repository_file_descriptor(
            repository_root,
            relative_name,
            purpose=f"{purpose} post-read identity",
        ) as current_descriptor:
            current = os.fstat(current_descriptor)
            current_identity = (
                current.st_dev,
                current.st_ino,
                current.st_size,
                current.st_mtime_ns,
                current.st_ctime_ns,
            )
            if not stat.S_ISREG(current.st_mode):
                raise ReleaseEvidenceError(f"{purpose} is not a regular file")
            if current_identity != hashed_identity:
                raise ReleaseEvidenceError(
                    f"{purpose} path changed while it was being hashed"
                )
        return digest, size


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseEvidenceError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ReleaseEvidenceError(f"non-finite JSON number is not allowed: {value}")


def load_json_bytes(value: bytes, *, label: str) -> dict[str, Any]:
    try:
        document = json.loads(
            value.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except ReleaseEvidenceError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseEvidenceError(f"invalid JSON in {label}: {exc}") from exc
    if not isinstance(document, dict):
        raise ReleaseEvidenceError(f"JSON document must be an object: {label}")
    return document


def load_json(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ReleaseEvidenceError(f"cannot inspect JSON file {path}: {exc}") from exc
    if size > MAX_JSON_BYTES:
        raise ReleaseEvidenceError(
            f"JSON file exceeds {MAX_JSON_BYTES} bytes: {path}"
        )
    value = _read_regular_path_bytes(
        path,
        label=f"JSON file {path}",
        max_bytes=MAX_JSON_BYTES,
    )
    return load_json_bytes(value, label=str(path))


def read_repository_json_with_digest(
    repository_root: Path,
    relative_name: str,
    *,
    purpose: str,
) -> tuple[dict[str, Any], str]:
    value = read_repository_file_bytes(
        repository_root,
        relative_name,
        purpose=purpose,
        max_bytes=MAX_JSON_BYTES,
    )
    return load_json_bytes(value, label=purpose), sha256_bytes(value)


def parse_utc_timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or RFC3339_UTC.fullmatch(value) is None:
        raise ReleaseEvidenceError(
            f"{label} must be an RFC 3339 UTC timestamp ending in 'Z'"
        )
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as exc:
        raise ReleaseEvidenceError(
            f"{label} is not a parseable RFC 3339 UTC timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(
        parsed
    ):
        raise ReleaseEvidenceError(f"{label} is not UTC")
    return parsed


def canonical_release_version(value: Any, *, label: str) -> str:
    """Return one canonical three-component release version."""

    if not isinstance(value, str) or RELEASE_VERSION.fullmatch(value) is None:
        raise ReleaseEvidenceError(
            f"{label} must be a canonical major.minor.patch version"
        )
    return value


def canonical_identity_text(value: Any, *, label: str) -> str:
    """Validate a stable human/agent identity or role without normalising it."""

    if not isinstance(value, str) or not value:
        raise ReleaseEvidenceError(f"{label} must be a non-empty string")
    if value != value.strip():
        raise ReleaseEvidenceError(
            f"{label} must not have leading or trailing whitespace"
        )
    if any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        for character in value
    ):
        raise ReleaseEvidenceError(f"{label} must not contain control characters")
    return value


def canonical_publication_base(value: Any, *, label: str) -> str:
    """Return one canonical credential-free HTTPS publication base URL."""

    if not isinstance(value, str) or not value:
        raise ReleaseEvidenceError(f"{label} must be a non-empty string")
    if any(character.isspace() for character in value):
        raise ReleaseEvidenceError(f"{label} contains literal whitespace")
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in value):
        raise ReleaseEvidenceError(f"{label} contains non-canonical characters")
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise ReleaseEvidenceError(f"{label} contains a malformed percent escape")
    unsafe = sorted(set(value) & set("\"'<>\\^`{|}"))
    if unsafe:
        raise ReleaseEvidenceError(
            f"{label} contains unsafe delimiter(s) {unsafe}"
        )
    try:
        parsed = urlparse(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ReleaseEvidenceError(
            f"{label} has an invalid authority or port"
        ) from exc
    if parsed.scheme != "https" or not parsed.netloc or not host:
        raise ReleaseEvidenceError(f"{label} must be absolute HTTPS")
    if (
        "@" in parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ReleaseEvidenceError(f"credentials are forbidden in {label}")
    if port is not None and not 1 <= port <= 65535:
        raise ReleaseEvidenceError(f"{label} has an out-of-range port")
    expected_authority = host.casefold()
    if port is not None:
        expected_authority += f":{port}"
    if parsed.netloc != expected_authority:
        raise ReleaseEvidenceError(f"{label} has a non-canonical authority")
    if parsed.query or parsed.fragment:
        raise ReleaseEvidenceError(f"{label} must not contain a query or fragment")
    if not parsed.path.endswith("/"):
        raise ReleaseEvidenceError(f"{label} must end in '/'")
    if any(segment in {".", ".."} for segment in parsed.path.split("/")):
        raise ReleaseEvidenceError(f"{label} contains a dot path segment")
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


def governed_release_coordinates_from_documents(
    build_receipt: dict[str, Any],
    build_config: dict[str, Any],
) -> tuple[str, str, str, str, str | None]:
    """Validate release coordinates repeated by one candidate build receipt."""

    if build_config.get("schema") != "okf-hmlr-build-config.v1":
        raise ReleaseEvidenceError(
            "source/build-config.json has the wrong schema"
        )
    version = build_config.get("version")
    canonical_url = build_config.get("publication_base")
    version = canonical_release_version(
        version,
        label="source/build-config.json version",
    )
    canonical_url = canonical_publication_base(
        canonical_url,
        label="source/build-config.json publication_base",
    )
    publication_state = build_config.get("publication_state")
    generated_at = build_config.get("generated_at")
    release_at = build_config.get("release_at")
    if not isinstance(publication_state, str) or not publication_state:
        raise ReleaseEvidenceError(
            "source/build-config.json has no publication_state"
        )
    if not isinstance(generated_at, str):
        raise ReleaseEvidenceError(
            "source/build-config.json has no generated_at"
        )
    parse_utc_timestamp(
        generated_at,
        label="source/build-config.json generated_at",
    )
    if release_at is not None:
        parse_utc_timestamp(
            release_at,
            label="source/build-config.json release_at",
        )
    if build_receipt.get("version") != version:
        raise ReleaseEvidenceError(
            "build receipt version does not match governed "
            "source/build-config.json"
        )
    if build_receipt.get("publication_base") != canonical_url:
        raise ReleaseEvidenceError(
            "build receipt publication_base does not match governed "
            "source/build-config.json"
        )
    for field, expected_value in (
        ("publication_state", publication_state),
        ("generated_at", generated_at),
        ("release_at", release_at),
    ):
        if build_receipt.get(field) != expected_value:
            raise ReleaseEvidenceError(
                f"build receipt {field} does not match governed "
                "source/build-config.json"
            )
    return (
        version,
        canonical_url,
        publication_state,
        generated_at,
        release_at,
    )


def release_coordinates_from_build_config(
    repository_root: Path,
    *,
    build_receipt_path: Path,
) -> ReleaseCoordinates:
    root = repository_root.resolve()
    build_receipt_file = repository_argument(
        root, build_receipt_path, purpose="build receipt"
    )
    build_receipt_name = build_receipt_file.relative_to(root).as_posix()
    build_receipt, _ = read_repository_json_with_digest(
        root,
        build_receipt_name,
        purpose="build receipt",
    )
    governed_inputs = build_receipt.get("governed_inputs")
    if not isinstance(governed_inputs, list):
        raise ReleaseEvidenceError(
            "build receipt has no governed input inventory"
        )
    matches = [
        item
        for item in governed_inputs
        if isinstance(item, dict)
        and item.get("path") == "source/build-config.json"
    ]
    if len(matches) != 1:
        raise ReleaseEvidenceError(
            "build receipt must bind source/build-config.json exactly once"
        )
    declared_digest = _exact_sha256(
        matches[0].get("sha256"),
        label="build receipt source/build-config.json SHA-256",
    )
    build_config, actual_digest = read_repository_json_with_digest(
        root,
        "source/build-config.json",
        purpose="governed source/build-config.json",
    )
    if actual_digest != declared_digest:
        raise ReleaseEvidenceError(
            "source/build-config.json digest does not match the build receipt: "
            f"declared {declared_digest}, calculated {actual_digest}"
        )
    (
        version,
        canonical_url,
        publication_state,
        generated_at,
        release_at,
    ) = governed_release_coordinates_from_documents(build_receipt, build_config)
    return ReleaseCoordinates(
        version=version,
        canonical_url=canonical_url,
        build_config_sha256=actual_digest,
        publication_state=publication_state,
        generated_at=generated_at,
        release_at=release_at,
    )


def _parse_checksum_manifest_bytes(
    manifest_bytes: bytes,
    *,
    label: str,
    root_marker: str,
    max_entries: int,
) -> tuple[list[tuple[str, str, str]], str]:
    """Parse the one canonical checksum-manifest grammar."""

    entries: list[tuple[str, str, str]] = []
    declared_roots: list[str] = []
    seen_paths: set[str] = set()
    try:
        lines = manifest_bytes.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ReleaseEvidenceError(
            f"cannot decode checksum manifest {label}: {exc}"
        ) from exc

    for line_number, line in enumerate(lines, start=1):
        if line.startswith(root_marker):
            declared_roots.append(line.removeprefix(root_marker))
            continue
        if not line:
            raise ReleaseEvidenceError(
                f"{label}:{line_number}: blank lines are not allowed"
            )
        if line.startswith("#"):
            raise ReleaseEvidenceError(
                f"{label}:{line_number}: unsupported checksum comment"
            )
        try:
            digest, name = line.split("  ", 1)
        except ValueError as exc:
            raise ReleaseEvidenceError(
                f"{label}:{line_number}: expected '<sha256>  <path>'"
            ) from exc
        if SHA256.fullmatch(digest) is None:
            raise ReleaseEvidenceError(
                f"{label}:{line_number}: invalid artefact SHA-256"
            )
        _canonical_git_path(name, purpose=f"{label} checksum entry")
        if name == "CHECKSUMS.sha256":
            raise ReleaseEvidenceError(
                f"{label}:{line_number}: checksum manifest must not list itself"
            )
        if name in seen_paths:
            raise ReleaseEvidenceError(
                f"{label}:{line_number}: duplicate artefact path {name!r}"
            )
        seen_paths.add(name)
        if len(entries) >= max_entries:
            raise ReleaseEvidenceError(
                f"checksum manifest exceeds the {max_entries}-entry limit: "
                f"{label}"
            )
        entries.append((digest, name, line))

    if not entries:
        raise ReleaseEvidenceError(f"checksum manifest has no entries: {label}")
    if len(declared_roots) != 1 or SHA256.fullmatch(declared_roots[0]) is None:
        raise ReleaseEvidenceError(
            "checksum manifest must have one valid "
            f"{root_marker.strip()} marker: {label}"
        )
    calculated = hashlib.sha256(
        ("\n".join(line for _digest, _name, line in entries) + "\n").encode(
            "utf-8"
        )
    ).hexdigest()
    if declared_roots[0] != calculated:
        raise ReleaseEvidenceError(
            f"checksum root mismatch in {label}: "
            f"declared {declared_roots[0]}, calculated {calculated}"
        )
    return entries, calculated


def validate_checksum_manifest(
    path: Path,
    root_marker: str,
    *,
    max_artefact_bytes: int,
    max_entries: int,
    max_aggregate_bytes: int,
) -> tuple[str, str]:
    if min(max_artefact_bytes, max_entries, max_aggregate_bytes) < 1:
        raise ReleaseEvidenceError(
            "checksum manifest resource limits must all be positive"
        )
    manifest_bytes = read_repository_file_bytes(
        path.parent,
        path.name,
        purpose=f"checksum manifest {path}",
        max_bytes=MAX_JSON_BYTES,
    )
    entries, calculated = _parse_checksum_manifest_bytes(
        manifest_bytes,
        label=str(path),
        root_marker=root_marker,
        max_entries=max_entries,
    )

    aggregate_bytes = 0
    for digest, name, _line in entries:
        actual, artefact_size = sha256_repository_file(
            path.parent,
            name,
            purpose="checksummed artefact",
            max_bytes=max_artefact_bytes,
            aggregate_bytes_remaining=max_aggregate_bytes - aggregate_bytes,
        )
        if actual != digest:
            raise ReleaseEvidenceError(
                f"artefact digest mismatch for {name!r}: "
                f"declared {digest}, calculated {actual}"
            )
        aggregate_bytes += artefact_size
    return calculated, sha256_bytes(manifest_bytes)


def current_commit(repository_root: Path) -> str:
    result = _git_command_bytes(
        repository_root,
        ["rev-parse", "--verify", "HEAD"],
        maximum_stdout_bytes=64,
    )
    if result.returncode != 0:
        diagnostic = result.stderr.decode(
            "utf-8", errors="replace"
        ).strip()
        raise ReleaseEvidenceError(
            "cannot resolve repository HEAD"
            + (f": {diagnostic}" if diagnostic else "")
        )
    try:
        commit = result.stdout.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ReleaseEvidenceError(
            "repository HEAD is not an ASCII commit identity"
        ) from exc
    if COMMIT_SHA.fullmatch(commit) is None:
        raise ReleaseEvidenceError(
            "repository HEAD is not exactly one 40-character commit identity"
        )
    return commit


def _git_command(
    repository_root: Path,
    arguments: list[str],
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = _git_command_bytes(
        repository_root,
        arguments,
        environment=environment,
    )
    return subprocess.CompletedProcess(
        result.args,
        result.returncode,
        result.stdout.decode("utf-8", errors="replace"),
        result.stderr.decode("utf-8", errors="replace"),
    )


def _git_command_bytes(
    repository_root: Path,
    arguments: list[str],
    *,
    environment: dict[str, str] | None = None,
    input_bytes: bytes | None = None,
    maximum_stdout_bytes: int = MAX_GIT_INVENTORY_BYTES,
) -> subprocess.CompletedProcess[bytes]:
    """Run Git with bounded streamed stdout/stderr and an optional fixed input."""

    if maximum_stdout_bytes < 0:
        raise ReleaseEvidenceError("Git stdout byte limit must be non-negative")
    command = ["git", *arguments]
    input_handle = tempfile.TemporaryFile()
    if input_bytes is not None:
        if len(input_bytes) > MAX_GIT_INVENTORY_BYTES:
            input_handle.close()
            raise ReleaseEvidenceError(
                "Git input exceeds the governed inventory-byte ceiling"
            )
        input_handle.write(input_bytes)
        input_handle.seek(0)
    try:
        process = subprocess.Popen(
            command,
            cwd=repository_root,
            stdin=input_handle if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, **(environment or {})},
        )
    except OSError as exc:
        input_handle.close()
        raise ReleaseEvidenceError(f"cannot execute git: {exc}") from exc
    if process.stdout is None or process.stderr is None:  # pragma: no cover
        process.kill()
        process.wait()
        input_handle.close()
        raise ReleaseEvidenceError("Git did not expose bounded output pipes")
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {
        "stdout": maximum_stdout_bytes,
        "stderr": MAX_GIT_DIAGNOSTIC_BYTES,
    }
    deadline = time.monotonic() + GIT_COMMAND_TIMEOUT_SECONDS
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReleaseEvidenceError(
                    "Git command exceeded its governed time ceiling"
                )
            events = selector.select(remaining)
            if not events:
                raise ReleaseEvidenceError(
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
                    raise ReleaseEvidenceError(
                        f"Git {key.data} exceeds its governed "
                        f"{limits[key.data]}-byte ceiling"
                    )
        try:
            returncode = process.wait(
                timeout=max(0.1, deadline - time.monotonic())
            )
        except subprocess.TimeoutExpired as exc:
            raise ReleaseEvidenceError(
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
        input_handle.close()
    return subprocess.CompletedProcess(
        command,
        returncode,
        bytes(buffers["stdout"]),
        bytes(buffers["stderr"]),
    )


def _nul_records(
    value: bytes,
    *,
    label: str,
    max_records: int = MAX_GIT_INVENTORY_PATHS,
) -> list[bytes]:
    """Parse a complete NUL-delimited Git result without line splitting."""

    if len(value) > MAX_GIT_INVENTORY_BYTES:
        raise ReleaseEvidenceError(
            f"{label} exceeds the governed Git inventory-byte ceiling"
        )
    if not value:
        return []
    if not value.endswith(b"\0"):
        raise ReleaseEvidenceError(f"{label} is not NUL-terminated")
    records = value[:-1].split(b"\0")
    if any(not record for record in records):
        raise ReleaseEvidenceError(f"{label} contains an empty path record")
    if len(records) > max_records:
        raise ReleaseEvidenceError(
            f"{label} exceeds the governed {max_records}-record ceiling"
        )
    return records


def _bounded_nul_input(records: Iterator[bytes], *, label: str) -> bytes:
    """Build one bounded NUL-delimited Git input after checking each record."""

    value = bytearray()
    count = 0
    for record in records:
        if not record or b"\0" in record:
            raise ReleaseEvidenceError(f"{label} contains an unsafe record")
        count += 1
        if count > MAX_GIT_INVENTORY_PATHS:
            raise ReleaseEvidenceError(
                f"{label} exceeds the governed path-count ceiling"
            )
        if len(value) + len(record) + 1 > MAX_GIT_INVENTORY_BYTES:
            raise ReleaseEvidenceError(
                f"{label} exceeds the governed inventory-byte ceiling"
            )
        value.extend(record)
        value.append(0)
    return bytes(value)


MUTABLE_VALIDATION_PREFIX = b"validation/candidate-v0.3.0/"
MUTABLE_DIST_PATHS = frozenset(
    {
        b"dist/okf-landregistry-0.3.0-candidate-a.zip",
        b"dist/okf-landregistry-0.3.0-candidate-b.zip",
    }
)


def _is_abandoned_metadata_stage(value: bytes) -> bool:
    return any(
        part.startswith(b".release-metadata-")
        for part in value.split(b"/")
    )


def _is_evidence_root_path(value: bytes) -> bool:
    """Return whether a path is anywhere under an evidence root."""

    return not _is_abandoned_metadata_stage(value) and (
        value.startswith(b"validation/") or value.startswith(b"dist/")
    )


def _is_mutable_evidence_path(value: bytes) -> bool:
    """Return whether a path is in the exact v0.3 post-candidate surface."""

    return not _is_abandoned_metadata_stage(value) and (
        value.startswith(MUTABLE_VALIDATION_PREFIX) or value in MUTABLE_DIST_PATHS
    )


def _display_git_path(value: bytes) -> str:
    """Render an arbitrary Git pathname without allowing line confusion."""

    return ascii(value.decode("utf-8", errors="backslashreplace"))[1:-1]


def _canonical_git_path(value: str, *, purpose: str) -> tuple[str, bytes]:
    """Validate a canonical repository-relative path for literal Git plumbing."""

    if not value or "\\" in value or "\x00" in value:
        raise ReleaseEvidenceError(f"unsafe {purpose} path: {value!r}")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or "." in relative.parts
        or relative.as_posix() != value
    ):
        raise ReleaseEvidenceError(f"unsafe {purpose} path: {value!r}")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError as exc:
        raise ReleaseEvidenceError(
            f"{purpose} path is not valid UTF-8: {value!r}"
        ) from exc
    return value, encoded


def _index_blob_bytes(
    repository_root: Path,
    *,
    relative_name: str,
    purpose: str,
    max_bytes: int,
    environment: dict[str, str] | None = None,
) -> bytes:
    """Read one exact regular stage-zero blob from the candidate index."""

    name, encoded_name = _canonical_git_path(relative_name, purpose=purpose)
    staged = _git_command_bytes(
        repository_root,
        [
            "--literal-pathspecs",
            "ls-files",
            "--stage",
            "-z",
            "--",
            name,
        ],
        environment=environment,
    )
    if staged.returncode != 0:
        diagnostic = staged.stderr.decode("utf-8", errors="replace").strip()
        raise ReleaseEvidenceError(
            f"cannot inspect staged {purpose} blob {name!r}: {diagnostic}"
        )
    records = _nul_records(staged.stdout, label=f"staged {purpose} index entry")
    if len(records) != 1:
        raise ReleaseEvidenceError(
            f"staged {purpose} must resolve to exactly one regular stage-zero "
            f"blob: {name!r}"
        )
    metadata, separator, returned_name = records[0].partition(b"\t")
    fields = metadata.split(b" ")
    if (
        not separator
        or returned_name != encoded_name
        or len(fields) != 3
        or fields[0] not in {b"100644", b"100755"}
        or fields[2] != b"0"
    ):
        raise ReleaseEvidenceError(
            f"staged {purpose} is not a regular stage-zero blob: {name!r}"
        )
    try:
        object_id = fields[1].decode("ascii")
    except UnicodeError as exc:
        raise ReleaseEvidenceError(
            f"staged {purpose} has an invalid object ID: {name!r}"
        ) from exc
    if GIT_OBJECT_ID.fullmatch(object_id) is None:
        raise ReleaseEvidenceError(
            f"staged {purpose} has an invalid object ID: {name!r}"
        )

    size = _git_command(repository_root, ["cat-file", "-s", object_id])
    try:
        object_size = int(size.stdout.strip())
    except ValueError as exc:
        raise ReleaseEvidenceError(
            f"cannot determine staged {purpose} byte size: {name!r}"
        ) from exc
    if size.returncode != 0 or object_size < 0 or object_size > max_bytes:
        diagnostic = size.stderr.strip()
        raise ReleaseEvidenceError(
            f"staged {purpose} exceeds the {max_bytes}-byte limit or cannot "
            f"be sized: {name!r} {diagnostic}"
        )
    blob = _git_command_bytes(
        repository_root,
        ["cat-file", "blob", object_id],
        maximum_stdout_bytes=max_bytes,
    )
    if blob.returncode != 0 or len(blob.stdout) != object_size:
        diagnostic = blob.stderr.decode("utf-8", errors="replace").strip()
        raise ReleaseEvidenceError(
            f"cannot read complete staged {purpose} bytes: {name!r} "
            f"{diagnostic}"
        )
    return blob.stdout


def _stage_zero_index_entries(
    repository_root: Path,
    *,
    environment: dict[str, str] | None = None,
) -> dict[str, GitTreeEntry]:
    """Return exact canonical stage-zero entries after rejecting conflicts."""

    unmerged = _git_command_bytes(
        repository_root,
        ["ls-files", "--unmerged", "-z", "--"],
        environment=environment,
    )
    if unmerged.returncode != 0:
        diagnostic = unmerged.stderr.decode("utf-8", errors="replace").strip()
        raise ReleaseEvidenceError(
            f"cannot inspect staged candidate conflicts: {diagnostic}"
        )
    conflicts = _nul_records(
        unmerged.stdout,
        label="staged candidate conflict inventory",
    )
    if conflicts:
        names = sorted(
            {
                _display_git_path(record.partition(b"\t")[2])
                for record in conflicts
                if b"\t" in record
            }
        )
        raise ReleaseEvidenceError(
            "staged candidate index contains unresolved conflicts: "
            + ", ".join(names)
        )

    staged = _git_command_bytes(
        repository_root,
        ["ls-files", "--stage", "-z", "--"],
        environment=environment,
    )
    if staged.returncode != 0:
        diagnostic = staged.stderr.decode("utf-8", errors="replace").strip()
        raise ReleaseEvidenceError(
            f"cannot enumerate staged candidate index: {diagnostic}"
        )
    entries: dict[str, GitTreeEntry] = {}
    for record in _nul_records(staged.stdout, label="staged candidate index"):
        metadata, separator, encoded_name = record.partition(b"\t")
        fields = metadata.split(b" ")
        if not separator or len(fields) != 3 or fields[2] != b"0":
            raise ReleaseEvidenceError(
                "staged candidate index contains a non-stage-zero entry"
            )
        try:
            name = encoded_name.decode("utf-8")
        except UnicodeError as exc:
            raise ReleaseEvidenceError(
                "staged candidate index contains a non-UTF-8 path"
            ) from exc
        canonical, _ = _canonical_git_path(name, purpose="staged index")
        if _is_abandoned_metadata_stage(encoded_name):
            raise ReleaseEvidenceError(
                "staged candidate index contains an abandoned release-metadata "
                f"staging path: {canonical!r}"
            )
        if canonical in entries:
            raise ReleaseEvidenceError(
                f"staged candidate index repeats path {canonical!r}"
            )
        try:
            mode = fields[0].decode("ascii")
            object_id = fields[1].decode("ascii")
        except UnicodeError as exc:
            raise ReleaseEvidenceError(
                "staged candidate index contains invalid entry metadata"
            ) from exc
        if (
            mode not in {"100644", "100755", "120000", "160000"}
            or GIT_OBJECT_ID.fullmatch(object_id) is None
        ):
            raise ReleaseEvidenceError(
                f"staged candidate index has an unsupported entry: {canonical!r}"
            )
        entries[canonical] = GitTreeEntry(mode=mode, object_id=object_id)
    return entries


@contextmanager
def _immutable_staged_index(
    repository_root: Path,
) -> Iterator[tuple[dict[str, str], dict[str, GitTreeEntry]]]:
    """Validate one immutable index snapshot and detect later index drift."""

    captured = _stage_zero_index_entries(repository_root)
    with tempfile.TemporaryDirectory(prefix="okf-staged-index-") as temporary:
        index_path = Path(temporary) / "index"
        environment = {"GIT_INDEX_FILE": str(index_path)}
        initialised = _git_command(
            repository_root,
            ["read-tree", "--empty"],
            environment=environment,
        )
        if initialised.returncode != 0:
            raise ReleaseEvidenceError(
                "cannot initialise immutable staged index snapshot: "
                f"{initialised.stderr.strip()}"
            )
        index_info = _bounded_nul_input(
            (
                f"{entry.mode} {entry.object_id}\t{name}".encode("utf-8")
                for name, entry in sorted(captured.items())
            ),
            label="immutable staged index snapshot",
        )
        populated = _git_command_bytes(
            repository_root,
            ["update-index", "-z", "--index-info"],
            environment=environment,
            input_bytes=index_info,
        )
        if populated.returncode != 0:
            diagnostic = populated.stderr.decode(
                "utf-8", errors="replace"
            ).strip()
            raise ReleaseEvidenceError(
                "cannot populate immutable staged index snapshot: " + diagnostic
            )
        if _stage_zero_index_entries(
            repository_root,
            environment=environment,
        ) != captured:
            raise ReleaseEvidenceError(
                "immutable staged index snapshot differs from the captured index"
            )
        try:
            yield environment, captured
        except Exception:
            raise
        else:
            if _stage_zero_index_entries(repository_root) != captured:
                raise ReleaseEvidenceError(
                    "repository index changed while the staged candidate was "
                    "being validated"
                )


def _candidate_tree_entries(
    repository_root: Path,
    *,
    candidate_commit_sha: str,
) -> dict[str, GitTreeEntry]:
    """Return every exact leaf entry from one candidate commit tree."""

    tree = _git_command_bytes(
        repository_root,
        [
            "ls-tree",
            "-r",
            "--full-tree",
            "-z",
            candidate_commit_sha,
        ],
    )
    if tree.returncode != 0:
        diagnostic = tree.stderr.decode("utf-8", errors="replace").strip()
        raise ReleaseEvidenceError(
            f"cannot enumerate candidate commit tree: {diagnostic}"
        )
    entries: dict[str, GitTreeEntry] = {}
    for record in _nul_records(tree.stdout, label="candidate commit tree"):
        metadata, separator, encoded_name = record.partition(b"\t")
        fields = metadata.split(b" ")
        if not separator or len(fields) != 3:
            raise ReleaseEvidenceError(
                "candidate commit tree contains an invalid entry"
            )
        try:
            mode = fields[0].decode("ascii")
            object_type = fields[1].decode("ascii")
            object_id = fields[2].decode("ascii")
            name = encoded_name.decode("utf-8")
        except UnicodeError as exc:
            raise ReleaseEvidenceError(
                "candidate commit tree contains invalid entry metadata"
            ) from exc
        canonical, _ = _canonical_git_path(name, purpose="candidate tree")
        if _is_abandoned_metadata_stage(encoded_name):
            raise ReleaseEvidenceError(
                "candidate commit tree contains an abandoned release-metadata "
                f"staging path: {canonical!r}"
            )
        expected_type = "commit" if mode == "160000" else "blob"
        if (
            mode not in {"100644", "100755", "120000", "160000"}
            or object_type != expected_type
            or GIT_OBJECT_ID.fullmatch(object_id) is None
            or canonical in entries
        ):
            raise ReleaseEvidenceError(
                f"candidate commit tree has an unsupported entry: {canonical!r}"
            )
        entries[canonical] = GitTreeEntry(mode=mode, object_id=object_id)
    if not entries:
        raise ReleaseEvidenceError("candidate commit tree is empty")
    return entries


class CandidateTreeBlobReader:
    """Read bounded blobs through one process and one absolute deadline."""

    def __init__(
        self,
        repository_root: Path,
        *,
        candidate_commit_sha: str,
        entries: dict[str, GitTreeEntry] | None = None,
    ) -> None:
        if COMMIT_SHA.fullmatch(candidate_commit_sha) is None:
            raise ReleaseEvidenceError(
                "candidate commit must be exactly 40 lowercase hexadecimal characters"
            )
        self.repository_root = repository_root.resolve()
        self.entries = entries or _candidate_tree_entries(
            self.repository_root,
            candidate_commit_sha=candidate_commit_sha,
        )
        self._deadline = time.monotonic() + GIT_COMMAND_TIMEOUT_SECONDS
        try:
            self._process = subprocess.Popen(
                ["git", "cat-file", "--batch"],
                cwd=self.repository_root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise ReleaseEvidenceError(
                f"cannot start bounded Git blob reader: {exc}"
            ) from exc
        if (
            self._process.stdin is None
            or self._process.stdout is None
            or self._process.stderr is None
        ):  # pragma: no cover
            self._process.kill()
            self._process.wait()
            raise ReleaseEvidenceError("Git blob reader did not expose its protocol pipes")
        self._selector = selectors.DefaultSelector()
        self._selector.register(
            self._process.stdout,
            selectors.EVENT_READ,
            "stdout",
        )
        self._selector.register(
            self._process.stderr,
            selectors.EVENT_READ,
            "stderr",
        )
        self._buffer = bytearray()
        self._stderr_buffer = bytearray()
        self._closed = False

    def __enter__(self) -> "CandidateTreeBlobReader":
        return self

    def _diagnostic(self) -> str:
        return bytes(self._stderr_buffer).decode(
            "utf-8", errors="replace"
        ).strip()

    def _remaining(self) -> float:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise ReleaseEvidenceError(
                "Git blob reader exceeded its governed total time ceiling"
            )
        return remaining

    def _consume_ready_streams(self, *, accept_stdout: bool = True) -> bool:
        """Drain one readiness set, returning whether stdout supplied bytes."""

        events = self._selector.select(self._remaining())
        if not events:
            raise ReleaseEvidenceError(
                "Git blob reader exceeded its governed total time ceiling"
            )
        received_stdout = False
        for key, _mask in events:
            chunk = os.read(key.fileobj.fileno(), 64 * 1024)
            if not chunk:
                self._selector.unregister(key.fileobj)
                continue
            if key.data == "stderr":
                self._stderr_buffer.extend(chunk)
                if len(self._stderr_buffer) > MAX_GIT_DIAGNOSTIC_BYTES:
                    raise ReleaseEvidenceError(
                        "Git blob reader stderr exceeds its governed "
                        f"{MAX_GIT_DIAGNOSTIC_BYTES}-byte ceiling"
                    )
            else:
                if not accept_stdout:
                    raise ReleaseEvidenceError(
                        "Git blob reader emitted unexpected stdout while closing"
                    )
                self._buffer.extend(chunk)
                received_stdout = True
        return received_stdout

    def _fill(self) -> None:
        while True:
            received_stdout = self._consume_ready_streams()
            if received_stdout:
                return
            if self._process.stdout not in {
                key.fileobj for key in self._selector.get_map().values()
            }:
                diagnostic = self._diagnostic()
                raise ReleaseEvidenceError(
                    "Git blob reader ended unexpectedly"
                    + (f": {diagnostic}" if diagnostic else "")
                )

    def _readline(self, maximum_bytes: int) -> bytes:
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                if newline > maximum_bytes:
                    raise ReleaseEvidenceError(
                        "Git blob reader header exceeds its byte ceiling"
                    )
                value = bytes(self._buffer[:newline])
                del self._buffer[: newline + 1]
                return value
            if len(self._buffer) > maximum_bytes:
                raise ReleaseEvidenceError(
                    "Git blob reader header exceeds its byte ceiling"
                )
            self._fill()

    def _read_exact(self, size: int) -> bytes:
        value = bytearray()
        while len(value) < size:
            if not self._buffer:
                self._fill()
            count = min(size - len(value), len(self._buffer))
            value.extend(self._buffer[:count])
            del self._buffer[:count]
        return bytes(value)

    def read(self, name: str, purpose: str, max_bytes: int) -> bytes:
        """Read one named regular blob after enforcing its actual object size."""

        self._remaining()
        canonical, _encoded = _canonical_git_path(name, purpose=purpose)
        entry = self.entries.get(canonical)
        if entry is None or entry.mode not in {"100644", "100755"}:
            raise ReleaseEvidenceError(
                f"candidate {purpose} must resolve to exactly one regular blob: "
                f"{canonical!r}"
            )
        if max_bytes < 0:
            raise ReleaseEvidenceError(
                f"candidate {purpose} has a negative byte ceiling"
            )
        try:
            self._process.stdin.write(entry.object_id.encode("ascii") + b"\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ReleaseEvidenceError(
                f"cannot query candidate {purpose} blob: {canonical!r}"
            ) from exc
        header = self._readline(512)
        try:
            returned_id, object_type, size_text = header.decode("ascii").split(" ")
            object_size = int(size_text)
        except (UnicodeError, ValueError) as exc:
            raise ReleaseEvidenceError(
                f"candidate {purpose} blob has an invalid Git batch header"
            ) from exc
        if (
            returned_id != entry.object_id
            or object_type != "blob"
            or object_size < 0
            or object_size > max_bytes
        ):
            raise ReleaseEvidenceError(
                f"candidate {purpose} exceeds the {max_bytes}-byte limit or is not "
                f"a regular blob: {canonical!r}"
            )
        content = self._read_exact(object_size)
        if self._read_exact(1) != b"\n":
            raise ReleaseEvidenceError(
                f"candidate {purpose} blob has an invalid Git batch terminator"
            )
        return content

    def close(self, *, check: bool = True) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._process.stdin.close()
            try:
                while self._selector.get_map():
                    self._consume_ready_streams(accept_stdout=False)
                returncode = self._process.wait(timeout=self._remaining())
            except (ReleaseEvidenceError, subprocess.TimeoutExpired):
                self._process.kill()
                self._process.wait()
                if check:
                    raise ReleaseEvidenceError(
                        "Git blob reader did not terminate within its total time ceiling"
                    )
                return
            if check and returncode != 0:
                diagnostic = self._diagnostic()
                raise ReleaseEvidenceError(
                    "Git blob reader failed"
                    + (f": {diagnostic}" if diagnostic else "")
                )
        finally:
            self._selector.close()
            self._process.stdout.close()
            self._process.stderr.close()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is not None:
            self._closed = True
            try:
                self._process.kill()
            except OSError:
                pass
            try:
                self._process.wait(timeout=GIT_COMMAND_TIMEOUT_SECONDS)
            except (OSError, subprocess.TimeoutExpired):
                pass
            self._selector.close()
            for stream in (
                self._process.stdin,
                self._process.stdout,
                self._process.stderr,
            ):
                try:
                    stream.close()
                except OSError:
                    pass
            return
        self.close()


@contextmanager
def _materialised_git_shape(
    repository_root: Path,
    *,
    entries: dict[str, GitTreeEntry],
    blob_reader: Callable[[str, str, int], bytes],
) -> Iterator[Path]:
    """Materialise a bounded index/tree shape for canonical producer tooling."""

    exact_content_names = {
        "governance/requirements.json",
        "governance/risk-register.json",
    }
    if len(entries) > MAX_GIT_INVENTORY_PATHS:
        raise ReleaseEvidenceError(
            "candidate shape exceeds the governed path-count ceiling"
        )
    encoded_path_bytes = sum(
        len(name.encode("utf-8")) + 1 for name in entries
    )
    if encoded_path_bytes > MAX_GIT_INVENTORY_BYTES:
        raise ReleaseEvidenceError(
            "candidate shape exceeds the governed path-inventory byte ceiling"
        )
    with tempfile.TemporaryDirectory(prefix="okf-candidate-shape-") as temporary:
        shape_root = Path(temporary)
        initialised = _git_command(shape_root, ["init", "-q"])
        if initialised.returncode != 0:
            raise ReleaseEvidenceError(
                "cannot initialise candidate-shape repository: "
                f"{initialised.stderr.strip()}"
            )
        for name, entry in sorted(entries.items()):
            if ".git" in PurePosixPath(name).parts or entry.mode == "160000":
                raise ReleaseEvidenceError(
                    f"candidate contains an unsupported Git path or Gitlink: {name!r}"
                )
            path = shape_root.joinpath(*PurePosixPath(name).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            if entry.mode == "120000":
                path.symlink_to("__okf_candidate_symlink__")
                continue
            content = (
                blob_reader(name, "candidate-shape control", MAX_JSON_BYTES)
                if name in exact_content_names or path.name == ".gitignore"
                else b""
            )
            path.write_bytes(content)

        ignore_input = _bounded_nul_input(
            (name.encode("utf-8") for name in sorted(entries)),
            label="candidate ignore-policy inventory",
        )
        ignored = _git_command_bytes(
            shape_root,
            [
                "-c",
                "core.excludesFile=/dev/null",
                "check-ignore",
                "--no-index",
                "-z",
                "--stdin",
            ],
            input_bytes=ignore_input,
        )
        if ignored.returncode not in {0, 1}:
            diagnostic = ignored.stderr.decode(
                "utf-8", errors="replace"
            ).strip()
            raise ReleaseEvidenceError(
                f"cannot apply candidate ignore policy: {diagnostic}"
            )
        ignored_names = sorted(
            _display_git_path(value)
            for value in _nul_records(
                ignored.stdout,
                label="ignored candidate path inventory",
            )
        )
        if ignored_names:
            raise ReleaseEvidenceError(
                "candidate contains tracked paths excluded by its ignore policy: "
                + ", ".join(ignored_names)
            )

        staged = _git_command(shape_root, ["add", "-f", "--all", "--", "."])
        if staged.returncode != 0:
            raise ReleaseEvidenceError(
                "cannot populate candidate-shape index: "
                f"{staged.stderr.strip()}"
            )
        yield shape_root


def _canonical_governed_input_paths(
    repository_root: Path,
    graph: dict[str, Any],
    graph_schema: dict[str, Any],
) -> list[str]:
    """Use the producer's canonical graph validator and inventory expansion."""

    try:
        if __package__:
            from scripts import build as build_module
            from scripts.change_impact import validate_graph
        else:  # pragma: no cover - exercised by the direct CLI tests
            import build as build_module  # type: ignore[no-redef]
            from change_impact import validate_graph  # type: ignore[no-redef]
        validate_graph(
            graph,
            repository_root=repository_root,
            schema=graph_schema,
        )
        paths = build_module.dependency_graph_governed_input_paths(
            graph,
            repository_root=repository_root,
        )
    except (ImportError, OSError, ValueError) as exc:
        raise ReleaseEvidenceError(
            f"candidate artefact dependency graph is invalid: {exc}"
        ) from exc
    return [
        path.relative_to(repository_root).as_posix()
        for path in paths
    ]


def _canonical_build_input_paths(
    repository_root: Path,
    graph: dict[str, Any],
) -> list[str]:
    """Expand the graph's causal build-input role with producer tooling."""

    try:
        if __package__:
            from scripts import build as build_module
        else:  # pragma: no cover - exercised by the direct CLI tests
            import build as build_module  # type: ignore[no-redef]
        paths = build_module.dependency_graph_build_input_paths(
            graph,
            repository_root=repository_root,
        )
    except (ImportError, OSError, ValueError) as exc:
        raise ReleaseEvidenceError(
            f"candidate causal build-input inventory is invalid: {exc}"
        ) from exc
    return [
        path.relative_to(repository_root).as_posix()
        for path in paths
    ]


def _validate_staged_candidate_status(
    repository_root: Path,
    *,
    environment: dict[str, str],
) -> None:
    """Require a complete protected stage while leaving evidence unstaged."""

    status = _git_command_bytes(
        repository_root,
        [
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignored=no",
            "--no-renames",
            "--",
        ],
        environment=environment,
    )
    if status.returncode != 0:
        diagnostic = status.stderr.decode("utf-8", errors="replace").strip()
        raise ReleaseEvidenceError(
            f"cannot inspect staged candidate worktree: {diagnostic}"
        )

    staged_protected: list[bytes] = []
    incomplete_protected: list[tuple[bytes, bytes]] = []
    staged_evidence: list[tuple[bytes, bytes]] = []
    for record in _nul_records(
        status.stdout,
        label="staged candidate worktree path inventory",
    ):
        if len(record) < 4 or record[2:3] != b" ":
            raise ReleaseEvidenceError(
                "staged candidate worktree status has an invalid record"
            )
        status_code = record[:2]
        path = record[3:]
        index_state, worktree_state = status_code[:1], status_code[1:]
        if _is_evidence_root_path(path):
            if status_code != b"??" and index_state != b" ":
                staged_evidence.append((path, status_code))
            continue
        if (
            status_code == b"??"
            or index_state == b" "
            or worktree_state != b" "
        ):
            incomplete_protected.append((path, status_code))
        else:
            staged_protected.append(path)

    if staged_evidence:
        names = ", ".join(
            f"{code.decode('ascii', errors='replace')} "
            f"{_display_git_path(path)}"
            for path, code in sorted(staged_evidence)
        )
        raise ReleaseEvidenceError(
            "staged candidate contains evidence changes under validation/** "
            f"or dist/**: {names}"
        )
    if incomplete_protected:
        names = ", ".join(
            f"{code.decode('ascii', errors='replace')} "
            f"{_display_git_path(path)}"
            for path, code in sorted(incomplete_protected)
        )
        raise ReleaseEvidenceError(
            "staged candidate has unstaged or non-ignored untracked changes "
            f"outside validation/** and dist/**: {names}"
        )
    if not staged_protected:
        raise ReleaseEvidenceError("staged candidate contains no protected changes")

    diff_check = _git_command_bytes(
        repository_root,
        ["diff", "--cached", "--check", "--"],
        environment=environment,
    )
    if diff_check.returncode != 0:
        diagnostic = (diff_check.stdout + diff_check.stderr).decode(
            "utf-8", errors="replace"
        ).strip()
        raise ReleaseEvidenceError(
            "staged candidate fails git diff --cached --check"
            + (f": {diagnostic}" if diagnostic else "")
        )


def _validate_exact_git_checksum_manifest(
    *,
    entries: dict[str, GitTreeEntry],
    blob_reader: Callable[[str, str, int], bytes],
    manifest_name: str,
    root_marker: str,
    max_artefact_bytes: int,
    max_entries: int,
    max_aggregate_bytes: int,
    exact_directory_inventory: bool,
) -> tuple[str, str]:
    """Validate checksums and optional directory closure from exact Git blobs."""

    manifest_bytes = blob_reader(
        manifest_name,
        "checksum manifest",
        MAX_JSON_BYTES,
    )
    parsed, calculated = _parse_checksum_manifest_bytes(
        manifest_bytes,
        label=manifest_name,
        root_marker=root_marker,
        max_entries=max_entries,
    )
    parent = PurePosixPath(manifest_name).parent.as_posix()
    expected_names = {
        f"{parent}/{relative_name}" for _digest, relative_name, _line in parsed
    }
    if exact_directory_inventory:
        actual_names = {
            name
            for name in entries
            if name.startswith(parent + "/") and name != manifest_name
        }
        if actual_names != expected_names:
            missing = sorted(expected_names - actual_names)
            extra = sorted(actual_names - expected_names)
            raise ReleaseEvidenceError(
                f"{parent} Git inventory differs from {manifest_name}: "
                f"missing={missing!r}, extra={extra!r}"
            )

    aggregate_bytes = 0
    for digest, relative_name, _line in parsed:
        full_name = f"{parent}/{relative_name}"
        remaining = max_aggregate_bytes - aggregate_bytes
        if remaining < 0:
            raise ReleaseEvidenceError(
                f"{manifest_name} exceeds the {max_aggregate_bytes}-byte "
                "aggregate allowance"
            )
        content = blob_reader(
            full_name,
            "checksummed artefact",
            min(max_artefact_bytes, remaining),
        )
        aggregate_bytes += len(content)
        actual = sha256_bytes(content)
        if actual != digest:
            raise ReleaseEvidenceError(
                f"artefact digest mismatch for {relative_name!r}: "
                f"declared {digest}, calculated {actual}"
            )
    return calculated, sha256_bytes(manifest_bytes)


def _validate_exact_build_receipt(
    *,
    build_receipt: dict[str, Any],
    expected_paths: list[str],
    blob_reader: Callable[[str, str, int], bytes],
    label: str,
) -> tuple[int, str]:
    """Bind the bounded complete canonical input inventory to exact blobs."""

    snapshot = build_receipt.get("snapshot")
    snapshot_name = (
        snapshot.get("manifest_path") if isinstance(snapshot, dict) else None
    )
    if not isinstance(snapshot_name, str):
        raise ReleaseEvidenceError(
            f"{label} build receipt does not identify the governed snapshot manifest"
        )
    snapshot_bytes = blob_reader(
        snapshot_name,
        "snapshot manifest",
        MAX_EVIDENCE_BYTES,
    )
    snapshot_digest = snapshot.get("source_manifest_sha256")
    if (
        not isinstance(snapshot_digest, str)
        or SHA256.fullmatch(snapshot_digest) is None
        or sha256_bytes(snapshot_bytes) != snapshot_digest
    ):
        raise ReleaseEvidenceError(
            f"{label} snapshot manifest digest does not match the build receipt"
        )

    governed_inputs = build_receipt.get("governed_inputs")
    if not isinstance(governed_inputs, list) or not governed_inputs:
        raise ReleaseEvidenceError(
            f"{label} build receipt has no non-empty governed input inventory"
        )
    if len(governed_inputs) > MAX_GOVERNED_INPUTS:
        raise ReleaseEvidenceError(
            f"{label} build receipt exceeds the {MAX_GOVERNED_INPUTS}-entry "
            "governed-input limit"
        )
    declared_paths: list[str] = []
    declared_aggregate = 0
    for index, material in enumerate(governed_inputs):
        if not isinstance(material, dict):
            raise ReleaseEvidenceError(
                f"{label} build receipt governed input {index} is not an object"
            )
        if set(material) != {"path", "bytes", "sha256"}:
            raise ReleaseEvidenceError(
                f"{label} build receipt governed input {index} must contain "
                "exactly path, bytes and sha256"
            )
        name = material.get("path")
        declared_bytes = material.get("bytes")
        digest = material.get("sha256")
        if not isinstance(name, str) or not isinstance(digest, str):
            raise ReleaseEvidenceError(
                f"{label} build receipt governed input {index} lacks path or "
                "SHA-256"
            )
        _canonical_git_path(name, purpose="governed build input")
        if type(declared_bytes) is not int or declared_bytes < 0:
            raise ReleaseEvidenceError(
                f"{label} build receipt governed input {name!r} bytes must be "
                "a non-negative integer"
            )
        if SHA256.fullmatch(digest) is None:
            raise ReleaseEvidenceError(
                f"{label} build receipt governed input {name!r} has invalid "
                "SHA-256"
            )
        if name in declared_paths:
            raise ReleaseEvidenceError(
                f"{label} build receipt repeats governed input path {name!r}"
            )
        declared_paths.append(name)
        declared_aggregate += declared_bytes
        if declared_aggregate > MAX_GOVERNED_INPUT_AGGREGATE_BYTES:
            raise ReleaseEvidenceError(
                f"{label} build receipt governed inputs exceed the "
                f"{MAX_GOVERNED_INPUT_AGGREGATE_BYTES}-byte aggregate limit"
            )

    if declared_paths != expected_paths:
        missing = sorted(set(expected_paths) - set(declared_paths))
        extra = sorted(set(declared_paths) - set(expected_paths))
        raise ReleaseEvidenceError(
            f"{label} build receipt governed input inventory differs from the "
            f"candidate dependency graph: missing={missing!r}, extra={extra!r}"
        )

    actual_aggregate = 0
    for material in governed_inputs:
        name = material["path"]
        remaining = MAX_GOVERNED_INPUT_AGGREGATE_BYTES - actual_aggregate
        material_bytes = blob_reader(
            name,
            "governed build input",
            min(MAX_EVIDENCE_BYTES, remaining),
        )
        actual_aggregate += len(material_bytes)
        if len(material_bytes) != material["bytes"]:
            raise ReleaseEvidenceError(
                f"{label} governed build input byte count mismatch for {name!r}: "
                f"declared {material['bytes']}, calculated {len(material_bytes)}"
            )
        actual_digest = sha256_bytes(material_bytes)
        if actual_digest != material["sha256"]:
            raise ReleaseEvidenceError(
                f"{label} governed build input digest mismatch for {name!r}: "
                f"declared {material['sha256']}, calculated {actual_digest}"
            )
    return len(governed_inputs), snapshot_digest


def _validate_exact_candidate_closure(
    repository_root: Path,
    *,
    entries: dict[str, GitTreeEntry],
    baseline_entries: dict[str, GitTreeEntry],
    blob_reader: Callable[[str, str, int], bytes],
    build_receipt_name: str,
    checksums_name: str = "bundle/CHECKSUMS.sha256",
    profile_checksums_name: str = "domain-profile/CHECKSUMS.sha256",
    label: str,
) -> CandidateClosure:
    """Validate graph, repository, bundle and receipt closure from one Git view."""

    candidate_evidence_changes = sorted(
        name
        for name in set(entries) | set(baseline_entries)
        if entries.get(name) != baseline_entries.get(name)
        and _is_evidence_root_path(name.encode("utf-8"))
    )
    if candidate_evidence_changes:
        raise ReleaseEvidenceError(
            "candidate commit/index changes historical or post-candidate evidence "
            "paths that must remain outside C: "
            + ", ".join(candidate_evidence_changes)
        )

    graph = load_json_bytes(
        blob_reader(
            "governance/artifact-dependency-graph.json",
            "artefact dependency graph",
            MAX_JSON_BYTES,
        ),
        label=f"{label} artefact dependency graph",
    )
    graph_schema = load_json_bytes(
        blob_reader(
            "schemas/artifact-dependency-graph.schema.json",
            "artefact dependency graph schema",
            MAX_JSON_BYTES,
        ),
        label=f"{label} artefact dependency graph schema",
    )
    build_receipt = load_json_bytes(
        blob_reader(build_receipt_name, "build receipt", MAX_JSON_BYTES),
        label=f"{label} build receipt",
    )
    with _materialised_git_shape(
        repository_root,
        entries=entries,
        blob_reader=blob_reader,
    ) as shape_root:
        canonical_candidate_paths = _canonical_governed_input_paths(
            shape_root,
            graph,
            graph_schema,
        )
        receipt_expected_paths = _canonical_build_input_paths(
            shape_root,
            graph,
        )

    try:
        if __package__:
            from scripts.change_impact import path_matches
        else:  # pragma: no cover - exercised by direct CLI tests
            from change_impact import path_matches  # type: ignore[no-redef]
    except ImportError as exc:
        raise ReleaseEvidenceError(
            f"cannot load canonical candidate path matcher: {exc}"
        ) from exc
    generated_roots = graph.get("generated_roots")
    if not isinstance(generated_roots, list):
        raise ReleaseEvidenceError(
            "candidate artefact dependency graph has no generated roots"
        )
    uncovered = sorted(
        name
        for name in entries
        if name not in canonical_candidate_paths
        and not any(path_matches(name, pattern) for pattern in generated_roots)
        and not (
            name.startswith("review/")
            and baseline_entries.get(name) == entries[name]
        )
    )
    if uncovered:
        raise ReleaseEvidenceError(
            "candidate Git inventory contains paths outside the canonical "
            "governed inputs and generated roots: " + ", ".join(uncovered)
        )

    release_root, checksums_digest = _validate_exact_git_checksum_manifest(
        entries=entries,
        blob_reader=blob_reader,
        manifest_name=checksums_name,
        root_marker="# release-root-sha256: ",
        max_artefact_bytes=MAX_BUNDLE_ARTEFACT_BYTES,
        max_entries=MAX_BUNDLE_CHECKSUM_ENTRIES,
        max_aggregate_bytes=MAX_BUNDLE_AGGREGATE_BYTES,
        exact_directory_inventory=True,
    )
    profile_root, _profile_manifest_digest = _validate_exact_git_checksum_manifest(
        entries=entries,
        blob_reader=blob_reader,
        manifest_name=profile_checksums_name,
        root_marker="# pack-root-sha256: ",
        max_artefact_bytes=MAX_EVIDENCE_BYTES,
        max_entries=MAX_PROFILE_CHECKSUM_ENTRIES,
        max_aggregate_bytes=MAX_PROFILE_AGGREGATE_BYTES,
        exact_directory_inventory=True,
    )
    if build_receipt.get("domain_profile_pack_root_sha256") != profile_root:
        raise ReleaseEvidenceError(
            f"{label} build receipt profile root does not match the exact "
            "profile pack"
        )
    governed_count, snapshot_digest = _validate_exact_build_receipt(
        build_receipt=build_receipt,
        expected_paths=receipt_expected_paths,
        blob_reader=blob_reader,
        label=label,
    )
    return CandidateClosure(
        governed_count=governed_count,
        release_root_sha256=release_root,
        checksums_sha256=checksums_digest,
        profile_pack_root_sha256=profile_root,
        snapshot_manifest_sha256=snapshot_digest,
    )


def validate_staged_candidate(
    repository_root: Path,
    *,
    build_receipt_path: Path,
) -> int:
    """Validate one immutable candidate-index snapshot before committing it."""

    root = repository_root.resolve()
    build_receipt_name = _repository_git_argument_name(
        root,
        build_receipt_path,
        purpose="build receipt",
    )
    baseline_entries = _candidate_tree_entries(
        root,
        candidate_commit_sha=current_commit(root),
    )
    with _immutable_staged_index(root) as (environment, entries):
        _validate_staged_candidate_status(root, environment=environment)

        def read_staged(name: str, purpose: str, max_bytes: int) -> bytes:
            return _index_blob_bytes(
                root,
                relative_name=name,
                purpose=purpose,
                max_bytes=max_bytes,
                environment=environment,
            )

        closure = _validate_exact_candidate_closure(
            root,
            entries=entries,
            baseline_entries=baseline_entries,
            blob_reader=read_staged,
            build_receipt_name=build_receipt_name,
            label="staged candidate",
        )
        _validate_staged_candidate_status(root, environment=environment)
        return closure.governed_count


def _candidate_blob_bytes(
    repository_root: Path,
    *,
    candidate_commit_sha: str,
    relative_name: str,
    purpose: str,
    max_bytes: int,
) -> bytes:
    """Read one exact regular blob from a candidate commit without path guessing."""

    name, encoded_name = _canonical_git_path(relative_name, purpose=purpose)
    tree = _git_command_bytes(
        repository_root,
        [
            "--literal-pathspecs",
            "ls-tree",
            "--full-tree",
            "-z",
            candidate_commit_sha,
            "--",
            name,
        ],
    )
    if tree.returncode != 0:
        diagnostic = tree.stderr.decode("utf-8", errors="replace").strip()
        raise ReleaseEvidenceError(
            f"cannot inspect candidate {purpose} blob {name!r}: {diagnostic}"
        )
    records = _nul_records(
        tree.stdout,
        label=f"candidate {purpose} tree entry",
    )
    if len(records) != 1:
        raise ReleaseEvidenceError(
            f"candidate {purpose} must resolve to exactly one regular blob: {name!r}"
        )
    metadata, separator, returned_name = records[0].partition(b"\t")
    fields = metadata.split(b" ")
    if (
        not separator
        or returned_name != encoded_name
        or len(fields) != 3
        or fields[0] not in {b"100644", b"100755"}
        or fields[1] != b"blob"
    ):
        raise ReleaseEvidenceError(
            f"candidate {purpose} is not a regular blob: {name!r}"
        )
    try:
        object_id = fields[2].decode("ascii")
    except UnicodeError as exc:
        raise ReleaseEvidenceError(
            f"candidate {purpose} has an invalid object ID: {name!r}"
        ) from exc
    if GIT_OBJECT_ID.fullmatch(object_id) is None:
        raise ReleaseEvidenceError(
            f"candidate {purpose} has an invalid object ID: {name!r}"
        )

    size = _git_command(repository_root, ["cat-file", "-s", object_id])
    try:
        object_size = int(size.stdout.strip())
    except ValueError as exc:
        raise ReleaseEvidenceError(
            f"cannot determine candidate {purpose} byte size: {name!r}"
        ) from exc
    if size.returncode != 0 or object_size < 0 or object_size > max_bytes:
        diagnostic = size.stderr.strip()
        raise ReleaseEvidenceError(
            f"candidate {purpose} exceeds the {max_bytes}-byte limit or "
            f"cannot be sized: {name!r} {diagnostic}"
        )
    blob = _git_command_bytes(
        repository_root,
        ["cat-file", "blob", object_id],
        maximum_stdout_bytes=max_bytes,
    )
    if blob.returncode != 0 or len(blob.stdout) != object_size:
        diagnostic = blob.stderr.decode("utf-8", errors="replace").strip()
        raise ReleaseEvidenceError(
            f"cannot read complete candidate {purpose} bytes: {name!r} "
            f"{diagnostic}"
        )
    return blob.stdout


def read_candidate_blob_bytes(
    repository_root: Path,
    *,
    candidate_commit_sha: str,
    relative_name: str,
    purpose: str,
    max_bytes: int,
) -> bytes:
    """Read one bounded regular file from the named immutable candidate tree."""

    if COMMIT_SHA.fullmatch(candidate_commit_sha) is None:
        raise ReleaseEvidenceError(
            "candidate commit must be exactly 40 lowercase hexadecimal characters"
        )
    return _candidate_blob_bytes(
        repository_root.resolve(),
        candidate_commit_sha=candidate_commit_sha,
        relative_name=relative_name,
        purpose=purpose,
        max_bytes=max_bytes,
    )


def _git_object_size(
    repository_root: Path,
    *,
    object_id: str,
    purpose: str,
    max_bytes: int,
) -> int:
    """Return one bounded Git object size without materialising its bytes."""

    if GIT_OBJECT_ID.fullmatch(object_id) is None:
        raise ReleaseEvidenceError(f"{purpose} has an invalid Git object ID")
    sized = _git_command(repository_root, ["cat-file", "-s", object_id])
    try:
        size = int(sized.stdout.strip())
    except ValueError as exc:
        raise ReleaseEvidenceError(f"cannot determine {purpose} byte size") from exc
    if sized.returncode != 0 or size < 0 or size > max_bytes:
        raise ReleaseEvidenceError(
            f"{purpose} exceeds the {max_bytes}-byte limit or cannot be sized"
        )
    return size


def validate_committed_candidate_closure(
    repository_root: Path,
    *,
    candidate_commit_sha: str,
    checksums_path: Path,
    profile_checksums_path: Path,
    build_receipt_path: Path,
) -> tuple[CandidateIdentity, int]:
    """Prove the complete canonical candidate contract from exact commit blobs."""

    root = repository_root.resolve()
    if COMMIT_SHA.fullmatch(candidate_commit_sha) is None:
        raise ReleaseEvidenceError(
            "governed candidate commit must be exactly 40 lowercase "
            "hexadecimal characters"
        )
    entries = _candidate_tree_entries(
        root,
        candidate_commit_sha=candidate_commit_sha,
    )
    parent_result = _git_command(
        root,
        ["rev-list", "--parents", "-n", "1", candidate_commit_sha],
    )
    parent_fields = parent_result.stdout.strip().split()
    if (
        parent_result.returncode != 0
        or len(parent_fields) != 2
        or parent_fields[0] != candidate_commit_sha
        or COMMIT_SHA.fullmatch(parent_fields[1]) is None
    ):
        raise ReleaseEvidenceError(
            "governed candidate commit must have exactly one baseline parent"
        )
    baseline_entries = _candidate_tree_entries(
        root,
        candidate_commit_sha=parent_fields[1],
    )

    with CandidateTreeBlobReader(
        root,
        candidate_commit_sha=candidate_commit_sha,
        entries=entries,
    ) as reader:
        closure = _validate_exact_candidate_closure(
            root,
            entries=entries,
            baseline_entries=baseline_entries,
            blob_reader=reader.read,
            build_receipt_name=_repository_git_argument_name(
                root,
                build_receipt_path,
                purpose="build receipt",
            ),
            checksums_name=_repository_git_argument_name(
                root,
                checksums_path,
                purpose="bundle checksums",
            ),
            profile_checksums_name=_repository_git_argument_name(
                root,
                profile_checksums_path,
                purpose="profile checksums",
            ),
            label="committed candidate",
        )
    return (
        CandidateIdentity(
            candidate_commit_sha=candidate_commit_sha,
            release_root_sha256=closure.release_root_sha256,
            checksums_sha256=closure.checksums_sha256,
            profile_pack_root_sha256=closure.profile_pack_root_sha256,
            snapshot_manifest_sha256=closure.snapshot_manifest_sha256,
        ),
        closure.governed_count,
    )


def _repository_git_argument_name(
    repository_root: Path, path: Path, *, purpose: str
) -> str:
    """Return one canonical repository-relative Git pathname argument."""

    root = repository_root.resolve()
    if path.is_absolute():
        try:
            value = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise ReleaseEvidenceError(
                f"{purpose} must be inside {root}: {path}"
            ) from exc
    else:
        value = path.as_posix()
    return _canonical_git_path(value, purpose=purpose)[0]


def _reject_non_evidence_commit_changes(
    repository_root: Path,
    *,
    candidate_commit_sha: str,
    evidence_commit_sha: str,
) -> None:
    """Reject every committed post-candidate path outside evidence roots."""

    commits_result = _git_command(
        repository_root,
        [
            "rev-list",
            "--topo-order",
            "--reverse",
            f"{candidate_commit_sha}..{evidence_commit_sha}",
        ],
    )
    if commits_result.returncode != 0:
        raise ReleaseEvidenceError(
            "cannot enumerate governed candidate evidence commits: "
            f"{commits_result.stderr.strip()}"
        )
    commits = commits_result.stdout.splitlines()
    if len(commits) > MAX_EVIDENCE_COMMITS:
        raise ReleaseEvidenceError(
            "candidate-to-evidence history exceeds the governed "
            f"{MAX_EVIDENCE_COMMITS}-commit ceiling"
        )
    if any(COMMIT_SHA.fullmatch(commit) is None for commit in commits):
        raise ReleaseEvidenceError(
            "governed candidate commit inventory contains an invalid object ID"
        )

    protected: set[tuple[str, bytes]] = set()
    previous_commit = candidate_commit_sha
    for commit in commits:
        parents_result = _git_command(
            repository_root,
            ["rev-list", "--parents", "-n", "1", commit],
        )
        parent_fields = parents_result.stdout.strip().split()
        if (
            parents_result.returncode != 0
            or not parent_fields
            or parent_fields[0] != commit
            or any(COMMIT_SHA.fullmatch(value) is None for value in parent_fields)
        ):
            raise ReleaseEvidenceError(
                f"cannot inspect parents of evidence commit {commit}"
            )
        parents = parent_fields[1:]
        if parents != [previous_commit]:
            raise ReleaseEvidenceError(
                "candidate-to-evidence history must be a single-parent linear "
                f"chain; {commit} does not have sole parent {previous_commit}"
            )
        changed = _git_command_bytes(
            repository_root,
            [
                "diff-tree",
                "--no-commit-id",
                "--no-renames",
                "--name-only",
                "-r",
                "-z",
                previous_commit,
                commit,
                "--",
            ],
        )
        if changed.returncode != 0:
            diagnostic = changed.stderr.decode(
                "utf-8", errors="replace"
            ).strip()
            raise ReleaseEvidenceError(
                f"cannot inspect evidence commit {commit}: {diagnostic}"
            )
        for path in _nul_records(
            changed.stdout,
            label=f"evidence commit {commit} path inventory",
        ):
            if not _is_mutable_evidence_path(path):
                protected.add((commit, path))
        previous_commit = commit
    if previous_commit != evidence_commit_sha:
        raise ReleaseEvidenceError(
            "candidate-to-evidence history does not terminate at repository HEAD"
        )
    if protected:
        changed_names = ", ".join(
            f"{commit}:{_display_git_path(path)}"
            for commit, path in sorted(protected)
        )
        raise ReleaseEvidenceError(
            "governed candidate tree changed in commit history outside "
            "validation/candidate-v0.3.0/** and the exact v0.3 candidate "
            "archives after the candidate commit: "
            f"{changed_names}"
        )


def _reject_non_evidence_worktree_changes(repository_root: Path) -> None:
    """Reject staged, unstaged and non-ignored untracked protected paths."""

    status = _git_command_bytes(
        repository_root,
        [
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignored=no",
            "--no-renames",
            "--",
        ],
    )
    if status.returncode != 0:
        diagnostic = status.stderr.decode("utf-8", errors="replace").strip()
        raise ReleaseEvidenceError(
            f"cannot inspect governed candidate worktree: {diagnostic}"
        )

    protected: list[tuple[bytes, bytes]] = []
    for record in _nul_records(
        status.stdout,
        label="governed candidate worktree path inventory",
    ):
        if len(record) < 4 or record[2:3] != b" ":
            raise ReleaseEvidenceError(
                "governed candidate worktree status has an invalid record"
            )
        status_code = record[:2]
        path = record[3:]
        if not _is_mutable_evidence_path(path):
            protected.append((path, status_code))
    if protected:
        changed_names = ", ".join(
            f"{code.decode('ascii', errors='replace')} "
            f"{_display_git_path(path)}"
            for path, code in sorted(protected)
        )
        raise ReleaseEvidenceError(
            "governed candidate tree has staged, unstaged or non-ignored "
            "untracked changes outside validation/candidate-v0.3.0/** and "
            "the exact v0.3 candidate archives: "
            f"{changed_names}"
        )


def _validate_governed_candidate_receipt_inputs(
    repository_root: Path,
    *,
    candidate_commit_sha: str,
    build_receipt_name: str,
) -> None:
    """Rehash every candidate receipt input through one bounded blob process."""

    entries = _candidate_tree_entries(
        repository_root,
        candidate_commit_sha=candidate_commit_sha,
    )
    with CandidateTreeBlobReader(
        repository_root,
        candidate_commit_sha=candidate_commit_sha,
        entries=entries,
    ) as reader:
        build_receipt = load_json_bytes(
            reader.read(build_receipt_name, "build receipt", MAX_JSON_BYTES),
            label="candidate build receipt",
        )
        snapshot = build_receipt.get("snapshot")
        snapshot_name = (
            snapshot.get("manifest_path") if isinstance(snapshot, dict) else None
        )
        if not isinstance(snapshot_name, str):
            raise ReleaseEvidenceError(
                "build receipt does not identify the governed snapshot manifest"
            )
        snapshot_bytes = reader.read(
            snapshot_name,
            "snapshot manifest",
            MAX_EVIDENCE_BYTES,
        )
        snapshot_digest = snapshot.get("source_manifest_sha256")
        if (
            not isinstance(snapshot_digest, str)
            or SHA256.fullmatch(snapshot_digest) is None
            or sha256_bytes(snapshot_bytes) != snapshot_digest
        ):
            raise ReleaseEvidenceError(
                "candidate snapshot manifest digest does not match the build receipt"
            )

        governed_inputs = build_receipt.get("governed_inputs")
        if not isinstance(governed_inputs, list) or not governed_inputs:
            raise ReleaseEvidenceError(
                "build receipt has no non-empty governed input inventory"
            )
        if len(governed_inputs) > MAX_GOVERNED_INPUTS:
            raise ReleaseEvidenceError(
                f"build receipt exceeds the {MAX_GOVERNED_INPUTS}-entry "
                "governed-input limit"
            )
        seen: set[str] = set()
        declared_aggregate = 0
        actual_aggregate = 0
        for index, material in enumerate(governed_inputs):
            if not isinstance(material, dict):
                raise ReleaseEvidenceError(
                    f"build receipt governed input {index} is not an object"
                )
            if set(material) != {"path", "bytes", "sha256"}:
                raise ReleaseEvidenceError(
                    f"build receipt governed input {index} must contain exactly "
                    "path, bytes and sha256"
                )
            name = material.get("path")
            declared_bytes = material.get("bytes")
            digest = material.get("sha256")
            if not isinstance(name, str) or not isinstance(digest, str):
                raise ReleaseEvidenceError(
                    f"build receipt governed input {index} lacks path or SHA-256"
                )
            if type(declared_bytes) is not int or declared_bytes < 0:
                raise ReleaseEvidenceError(
                    f"build receipt governed input {name!r} bytes must be a "
                    "non-negative integer"
                )
            declared_aggregate += declared_bytes
            if declared_aggregate > MAX_GOVERNED_INPUT_AGGREGATE_BYTES:
                raise ReleaseEvidenceError(
                    "build receipt governed inputs exceed the "
                    f"{MAX_GOVERNED_INPUT_AGGREGATE_BYTES}-byte aggregate limit"
                )
            if SHA256.fullmatch(digest) is None:
                raise ReleaseEvidenceError(
                    f"build receipt governed input {name!r} has invalid SHA-256"
                )
            if name in seen:
                raise ReleaseEvidenceError(
                    f"build receipt repeats governed input path {name!r}"
                )
            seen.add(name)
            remaining = MAX_GOVERNED_INPUT_AGGREGATE_BYTES - actual_aggregate
            material_bytes = reader.read(
                name,
                "governed build input",
                min(MAX_EVIDENCE_BYTES, remaining),
            )
            actual_aggregate += len(material_bytes)
            if len(material_bytes) != declared_bytes:
                raise ReleaseEvidenceError(
                    f"governed build input byte count mismatch for {name!r}: "
                    f"declared {declared_bytes}, calculated {len(material_bytes)}"
                )
            actual_material_digest = sha256_bytes(material_bytes)
            if actual_material_digest != digest:
                raise ReleaseEvidenceError(
                    f"governed build input digest mismatch for {name!r}: "
                    f"declared {digest}, calculated {actual_material_digest}"
                )


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

    build_receipt_name = _repository_git_argument_name(
        root, build_receipt_path, purpose="build receipt"
    )
    _validate_governed_candidate_receipt_inputs(
        root,
        candidate_commit_sha=candidate_commit_sha,
        build_receipt_name=build_receipt_name,
    )
    _reject_non_evidence_commit_changes(
        root,
        candidate_commit_sha=candidate_commit_sha,
        evidence_commit_sha=evidence_commit_sha,
    )
    _reject_non_evidence_worktree_changes(root)
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

    release_root, checksums_digest = validate_checksum_manifest(
        checksums,
        "# release-root-sha256: ",
        max_artefact_bytes=MAX_BUNDLE_ARTEFACT_BYTES,
        max_entries=MAX_BUNDLE_CHECKSUM_ENTRIES,
        max_aggregate_bytes=MAX_BUNDLE_AGGREGATE_BYTES,
    )
    profile_root, _ = validate_checksum_manifest(
        profile_checksums,
        "# pack-root-sha256: ",
        max_artefact_bytes=MAX_EVIDENCE_BYTES,
        max_entries=MAX_PROFILE_CHECKSUM_ENTRIES,
        max_aggregate_bytes=MAX_PROFILE_AGGREGATE_BYTES,
    )
    build_receipt, _ = read_repository_json_with_digest(
        root,
        build_receipt_file.relative_to(root).as_posix(),
        purpose="build receipt",
    )
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
    snapshot_bytes = read_repository_file_bytes(
        root,
        snapshot_name,
        purpose="snapshot manifest",
        max_bytes=MAX_EVIDENCE_BYTES,
    )
    actual_snapshot_digest = sha256_bytes(snapshot_bytes)
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
        checksums_sha256=checksums_digest,
        profile_pack_root_sha256=profile_root,
        snapshot_manifest_sha256=actual_snapshot_digest,
    )


def schema_validator(
    schema_path: Path,
    *,
    repository_root: Path | None = None,
) -> Draft202012Validator:
    if repository_root is None:
        schema = load_json(schema_path)
    else:
        root = repository_root.resolve()
        schema_file = repository_argument(
            root, schema_path, purpose="release evidence schema"
        )
        schema_name = schema_file.relative_to(root).as_posix()
        schema_bytes = read_repository_file_bytes(
            root,
            schema_name,
            purpose="release evidence schema",
            max_bytes=MAX_JSON_BYTES,
        )
        schema = load_json_bytes(schema_bytes, label="release evidence schema")
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
    references = receipt["evidence"]
    if len(references) > MAX_EVIDENCE_REFERENCES:
        raise ReleaseEvidenceError(
            f"{gate} exceeds the {MAX_EVIDENCE_REFERENCES}-entry evidence "
            "reference limit"
        )
    aggregate_bytes = 0
    for reference in references:
        name = reference["path"]
        if name in seen:
            raise ReleaseEvidenceError(f"{gate} has duplicate evidence path {name!r}")
        seen.add(name)
        remaining = MAX_EVIDENCE_REFERENCE_AGGREGATE_BYTES - aggregate_bytes
        evidence_bytes = read_repository_file_bytes(
            repository_root,
            name,
            purpose=f"{gate} evidence",
            max_bytes=min(MAX_EVIDENCE_BYTES, remaining),
        )
        aggregate_bytes += len(evidence_bytes)
        actual = sha256_bytes(evidence_bytes)
        if actual != reference["sha256"]:
            raise ReleaseEvidenceError(
                f"{gate} evidence digest mismatch for {name!r}: "
                f"declared {reference['sha256']}, calculated {actual}"
            )


def _is_direct_dist_zip_path(value: str) -> bool:
    """Return whether a path is exactly one ZIP file below ``dist/``."""

    if not isinstance(value, str):
        return False
    path = PurePosixPath(value)
    return (
        len(path.parts) == 2
        and path.parts[0] == "dist"
        and path.name not in {"", ".zip"}
        and path.suffix == ".zip"
        and path.as_posix() == value
    )


def _governed_zip_filename(value: str) -> tuple[bytes, int]:
    """Return the exact filename bytes and language flag emitted by ZipFile."""

    try:
        return value.encode("ascii"), 0
    except UnicodeEncodeError:
        try:
            return value.encode("utf-8"), 0x800
        except UnicodeEncodeError as exc:
            raise ReleaseEvidenceError(
                "release archive member name is not valid Unicode"
            ) from exc


def _candidate_bundle_archive_members(
    repository_root: Path,
    *,
    expected_candidate: CandidateIdentity,
    expected_version: str,
) -> dict[str, ArchiveMemberIdentity]:
    """Return exact archive member digests and sizes from the candidate tree."""

    tree_entries = _candidate_tree_entries(
        repository_root,
        candidate_commit_sha=expected_candidate.candidate_commit_sha,
    )
    with CandidateTreeBlobReader(
        repository_root,
        candidate_commit_sha=expected_candidate.candidate_commit_sha,
        entries=tree_entries,
    ) as reader:
        manifest_bytes = reader.read(
            "bundle/CHECKSUMS.sha256",
            "bundle checksum manifest",
            MAX_EVIDENCE_BYTES,
        )
        actual_manifest_sha256 = sha256_bytes(manifest_bytes)
        if actual_manifest_sha256 != expected_candidate.checksums_sha256:
            raise ReleaseEvidenceError(
                "candidate bundle checksum manifest differs from the candidate identity"
            )
        parsed, calculated_root = _parse_checksum_manifest_bytes(
            manifest_bytes,
            label="candidate bundle/CHECKSUMS.sha256",
            root_marker="# release-root-sha256: ",
            max_entries=MAX_BUNDLE_CHECKSUM_ENTRIES,
        )
        if calculated_root != expected_candidate.release_root_sha256:
            raise ReleaseEvidenceError(
                "candidate bundle checksum manifest has another release root"
            )

        prefix = f"okf-landregistry-{expected_version}/"
        members: dict[str, ArchiveMemberIdentity] = {}
        aggregate = len(manifest_bytes)
        for digest, relative_name, _line in parsed:
            candidate_name = f"bundle/{relative_name}"
            remaining = MAX_BUNDLE_AGGREGATE_BYTES - (
                aggregate - len(manifest_bytes)
            )
            content = reader.read(
                candidate_name,
                f"candidate bundle artefact {relative_name!r}",
                min(MAX_BUNDLE_ARTEFACT_BYTES, remaining),
            )
            if sha256_bytes(content) != digest:
                raise ReleaseEvidenceError(
                    f"candidate bundle artefact digest differs: {relative_name!r}"
                )
            aggregate += len(content)
            members[f"{prefix}{relative_name}"] = ArchiveMemberIdentity(
                sha256=digest,
                bytes=len(content),
            )
        if aggregate > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ReleaseEvidenceError(
                "candidate archive member inventory exceeds the governed "
                "uncompressed-byte limit"
            )
        members[f"{prefix}CHECKSUMS.sha256"] = ArchiveMemberIdentity(
            sha256=actual_manifest_sha256,
            bytes=len(manifest_bytes),
        )
    return members


def validate_archive_receipt_document(
    repository_root: Path,
    archive_receipt: dict[str, Any],
    *,
    expected_candidate: CandidateIdentity,
    expected_version: str,
    expected_publication_state: str,
    expected_generated_at: str,
    expected_release_at: str | None,
    expected_archive_kind: str,
) -> tuple[str, Path]:
    """Bind one archive receipt to its exact governed, real ZIP bytes."""

    archive_schema = archive_receipt.get("schema")
    if not isinstance(archive_schema, str) or archive_schema not in {
        "okf-hmlr-release-archive.v1",
        "okf-hmlr-candidate-archive.v1",
    }:
        raise ReleaseEvidenceError(
            "archive receipt has an unsupported schema"
        )
    archive_contracts = {
        "candidate-a": (
            "okf-hmlr-candidate-archive.v1",
            f"dist/okf-landregistry-{expected_version}-candidate-a.zip",
        ),
        "candidate-b": (
            "okf-hmlr-candidate-archive.v1",
            f"dist/okf-landregistry-{expected_version}-candidate-b.zip",
        ),
        "release": (
            "okf-hmlr-release-archive.v1",
            f"dist/okf-landregistry-{expected_version}.zip",
        ),
    }
    if expected_archive_kind not in archive_contracts:
        raise ReleaseEvidenceError(
            f"unsupported governed archive kind: {expected_archive_kind!r}"
        )
    expected_schema, expected_archive_name = archive_contracts[
        expected_archive_kind
    ]
    if archive_schema != expected_schema:
        raise ReleaseEvidenceError(
            "archive receipt schema does not match its governed archive kind"
        )
    common_fields = {
        "schema",
        "version",
        "release_root_sha256",
        "candidate",
        "path",
        "bytes",
        "sha256",
    }
    expected_receipt_fields = (
        common_fields | {"candidate_at", "publication_state"}
        if archive_schema == "okf-hmlr-candidate-archive.v1"
        else common_fields | {"release_at"}
    )
    if set(archive_receipt) != expected_receipt_fields:
        missing = sorted(expected_receipt_fields - set(archive_receipt))
        extra = sorted(set(archive_receipt) - expected_receipt_fields)
        raise ReleaseEvidenceError(
            "archive receipt fields differ from the exact governed contract; "
            f"missing={missing}, extra={extra}"
        )
    if archive_receipt.get("version") != expected_version:
        raise ReleaseEvidenceError(
            "archive receipt version does not match the governed version"
        )
    if archive_receipt.get("candidate") != asdict(expected_candidate):
        raise ReleaseEvidenceError(
            "archive receipt does not bind the exact candidate"
        )
    if (
        archive_receipt.get("release_root_sha256")
        != expected_candidate.release_root_sha256
    ):
        raise ReleaseEvidenceError(
            "archive receipt does not bind the exact release root"
        )
    if archive_schema == "okf-hmlr-candidate-archive.v1":
        if (
            expected_publication_state != "digest-bound-external-evidence"
            or expected_release_at is not None
            or archive_receipt.get("publication_state")
            != "unreleased-candidate"
            or archive_receipt.get("candidate_at") != expected_generated_at
        ):
            raise ReleaseEvidenceError(
                "candidate archive receipt does not match the governed "
                "publication state and candidate timestamp"
            )
    elif (
        expected_release_at is None
        or archive_receipt.get("release_at") != expected_release_at
    ):
        raise ReleaseEvidenceError(
            "release archive receipt does not match the governed release timestamp"
        )

    archive_name = archive_receipt.get("path")
    if archive_name != expected_archive_name:
        raise ReleaseEvidenceError(
            "archive receipt path does not match its exact governed archive kind; "
            f"expected {expected_archive_name!r}"
        )
    archive_bytes = read_repository_file_bytes(
        repository_root,
        archive_name,
        purpose="designated G8 release archive",
        max_bytes=MAX_EVIDENCE_BYTES,
    )
    declared_bytes = archive_receipt.get("bytes")
    if type(declared_bytes) is not int or declared_bytes != len(archive_bytes):
        raise ReleaseEvidenceError(
            "archive receipt byte count does not match the ZIP"
        )
    declared_sha256 = archive_receipt.get("sha256")
    if (
        not isinstance(declared_sha256, str)
        or SHA256.fullmatch(declared_sha256) is None
        or declared_sha256 != sha256_bytes(archive_bytes)
    ):
        raise ReleaseEvidenceError(
            "archive receipt SHA-256 does not match the ZIP"
        )
    expected_members = _candidate_bundle_archive_members(
        repository_root,
        expected_candidate=expected_candidate,
        expected_version=expected_version,
    )
    archive_time_value = (
        expected_generated_at
        if archive_schema == "okf-hmlr-candidate-archive.v1"
        else expected_release_at
    )
    archive_time = parse_utc_timestamp(
        archive_time_value,
        label="governed archive timestamp",
    )
    expected_zip_time = (
        archive_time.year,
        archive_time.month,
        archive_time.day,
        archive_time.hour,
        archive_time.minute,
        archive_time.second - (archive_time.second % 2),
    )
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            if archive.comment != b"":
                raise ReleaseEvidenceError(
                    "release archive comment is not the governed empty value"
                )
            members = archive.infolist()
            if not members or not any(not member.is_dir() for member in members):
                raise ReleaseEvidenceError(
                    "release archive is not a non-empty ZIP"
                )
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise ReleaseEvidenceError(
                    "release archive exceeds the "
                    f"{MAX_ARCHIVE_MEMBERS}-member limit"
                )
            if any(member.flag_bits & 0x1 for member in members):
                raise ReleaseEvidenceError(
                    "release archive must not contain encrypted members"
                )
            for member in members:
                _encoded_name, expected_flags = _governed_zip_filename(
                    member.filename
                )
                if (
                    member.is_dir()
                    or member.comment != b""
                    or member.extra != b""
                    or member.create_system != 3
                    or member.create_version != 20
                    or member.extract_version != 20
                    or member.reserved != 0
                    or member.flag_bits != expected_flags
                    or member.volume != 0
                    or member.internal_attr != 0
                    or member.external_attr != (0o100644 << 16)
                    or member.compress_type != zipfile.ZIP_DEFLATED
                    or member.date_time != expected_zip_time
                ):
                    raise ReleaseEvidenceError(
                        "release archive member metadata is not the governed "
                        f"regular-file form: {member.filename!r}"
                    )
            member_names = [member.filename for member in members]
            if len(member_names) != len(set(member_names)):
                raise ReleaseEvidenceError(
                    "release archive contains duplicate member names"
                )
            for member_name in member_names:
                member_path = PurePosixPath(member_name)
                if (
                    "\\" in member_name
                    or "\x00" in member_name
                    or member_path.is_absolute()
                    or ".." in member_path.parts
                    or "." in member_path.parts
                    or member_path.as_posix() != member_name
                ):
                    raise ReleaseEvidenceError(
                        f"release archive contains an unsafe member: {member_name!r}"
                    )
            if any(
                member.orig_filename != member.filename for member in members
            ):
                raise ReleaseEvidenceError(
                    "release archive contains a non-canonical original member name"
                )
            if set(member_names) != set(expected_members):
                missing = sorted(set(expected_members) - set(member_names))
                extra = sorted(set(member_names) - set(expected_members))
                raise ReleaseEvidenceError(
                    "release archive member inventory differs from the candidate "
                    f"bundle; missing={missing}, extra={extra}"
                )
            if member_names != sorted(expected_members):
                raise ReleaseEvidenceError(
                    "release archive member order is not the governed sorted order"
                )
            declared_uncompressed = 0
            for member in members:
                expected_member = expected_members[member.filename]
                if member.file_size != expected_member.bytes:
                    raise ReleaseEvidenceError(
                        "release archive declared member size differs from the "
                        f"candidate bundle: {member.filename!r}"
                    )
                declared_uncompressed += member.file_size
                if declared_uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    raise ReleaseEvidenceError(
                        "release archive declared member sizes exceed the "
                        "governed aggregate limit"
                    )
            for member in members:
                expected_member = expected_members[member.filename]
                with archive.open(member) as handle:
                    digest = hashlib.sha256()
                    total = 0
                    while True:
                        chunk = handle.read(
                            min(65_536, expected_member.bytes + 1 - total)
                        )
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > expected_member.bytes:
                            raise ReleaseEvidenceError(
                                "release archive decompressed member exceeds the "
                                f"candidate byte count: {member.filename!r}"
                            )
                        digest.update(chunk)
                if total != expected_member.bytes:
                    raise ReleaseEvidenceError(
                        "release archive decompressed member byte count differs "
                        f"from the candidate bundle: {member.filename!r}"
                    )
                actual_member_digest = digest.hexdigest()
                if actual_member_digest != expected_member.sha256:
                    raise ReleaseEvidenceError(
                        "release archive member digest differs from the candidate "
                        f"bundle: {member.filename!r}"
                    )
            expected_dos_time = (
                (expected_zip_time[3] << 11)
                | (expected_zip_time[4] << 5)
                | (expected_zip_time[5] // 2)
            )
            expected_dos_date = (
                ((expected_zip_time[0] - 1980) << 9)
                | (expected_zip_time[1] << 5)
                | expected_zip_time[2]
            )
            expected_header_offset = 0
            for member in members:
                offset = member.header_offset
                if offset != expected_header_offset or offset + 30 > len(
                    archive_bytes
                ):
                    raise ReleaseEvidenceError(
                        "release archive local headers are not contiguously governed"
                    )
                (
                    signature,
                    extract_version,
                    reserved,
                    flags,
                    compression,
                    dos_time,
                    dos_date,
                    crc,
                    compressed_size,
                    uncompressed_size,
                    filename_length,
                    extra_length,
                ) = struct.unpack_from("<4s2B4H3L2H", archive_bytes, offset)
                encoded_name, expected_flags = _governed_zip_filename(
                    member.filename
                )
                name_start = offset + 30
                name_end = name_start + filename_length
                if (
                    signature != b"PK\x03\x04"
                    or extract_version != 20
                    or reserved != 0
                    or flags != expected_flags
                    or compression != zipfile.ZIP_DEFLATED
                    or dos_time != expected_dos_time
                    or dos_date != expected_dos_date
                    or crc != member.CRC
                    or compressed_size != member.compress_size
                    or uncompressed_size != member.file_size
                    or filename_length != len(encoded_name)
                    or extra_length != 0
                    or name_end > len(archive_bytes)
                    or archive_bytes[name_start:name_end] != encoded_name
                ):
                    raise ReleaseEvidenceError(
                        "release archive local header metadata is not governed: "
                        f"{member.filename!r}"
                    )
                expected_header_offset = name_end + member.compress_size
            if archive.start_dir != expected_header_offset:
                raise ReleaseEvidenceError(
                    "release archive local records do not end at the governed "
                    "central directory"
                )
            end_record_offset = len(archive_bytes) - 22
            if end_record_offset < archive.start_dir:
                raise ReleaseEvidenceError(
                    "release archive has no exact governed end record"
                )
            (
                end_signature,
                disk_number,
                central_disk,
                entries_on_disk,
                total_entries,
                central_size,
                central_offset,
                comment_length,
            ) = struct.unpack_from(
                "<4s4H2LH", archive_bytes, end_record_offset
            )
            if (
                end_signature != b"PK\x05\x06"
                or disk_number != 0
                or central_disk != 0
                or entries_on_disk != len(members)
                or total_entries != len(members)
                or central_offset != archive.start_dir
                or central_size != end_record_offset - archive.start_dir
                or comment_length != 0
            ):
                raise ReleaseEvidenceError(
                    "release archive end record is not the governed single-disk form"
                )
    except ReleaseEvidenceError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise ReleaseEvidenceError(
            f"release archive is not a readable ZIP: {exc}"
        ) from exc
    archive_path = repository_root.resolve().joinpath(
        *PurePosixPath(archive_name).parts
    )
    return archive_schema, archive_path


def validate_g8_archive_evidence(
    repository_root: Path,
    g8_receipt: dict[str, Any],
    *,
    expected_candidate: CandidateIdentity,
    expected_coordinates: ReleaseCoordinates,
) -> None:
    """Require exact candidate archive, SBOM and provenance evidence for G8."""

    version = canonical_release_version(
        expected_coordinates.version,
        label="governed G8 release version",
    )
    archive_receipt_name = (
        f"validation/candidate-v{version}/evidence/"
        "release-candidate-archive-a.json"
    )
    metadata_names = {
        "sbom.spdx.json": (
            f"validation/candidate-v{version}/evidence/"
            "release-metadata/sbom.spdx.json"
        ),
        "provenance.json": (
            f"validation/candidate-v{version}/evidence/"
            "release-metadata/provenance.json"
        ),
    }
    references: dict[str, str] = {}
    direct_zip_paths: set[str] = set()
    for index, reference in enumerate(g8_receipt.get("evidence", [])):
        if not isinstance(reference, dict):
            raise ReleaseEvidenceError(
                f"G8 evidence reference {index} is not an object"
            )
        name = reference.get("path")
        digest = reference.get("sha256")
        if not isinstance(name, str) or not isinstance(digest, str):
            raise ReleaseEvidenceError(
                f"G8 evidence reference {index} lacks path or SHA-256"
            )
        if name in references:
            raise ReleaseEvidenceError(f"G8 repeats evidence path {name!r}")
        references[name] = digest
        if _is_direct_dist_zip_path(name):
            direct_zip_paths.add(name)
        path = PurePosixPath(name)
        if (
            path.name in metadata_names
            and name != metadata_names[path.name]
        ):
            raise ReleaseEvidenceError(
                f"G8 release metadata uses an ungoverned path: {name!r}"
            )

    required_paths = {archive_receipt_name, *metadata_names.values()}
    missing_paths = sorted(required_paths - set(references))
    if missing_paths:
        raise ReleaseEvidenceError(
            "G8 is missing exact version-scoped archive or release metadata "
            f"evidence: {missing_paths!r}"
        )
    archive_receipt_bytes = read_repository_file_bytes(
        repository_root,
        archive_receipt_name,
        purpose="designated G8 archive receipt",
        max_bytes=MAX_JSON_BYTES,
    )
    archive_receipt_digest = sha256_bytes(archive_receipt_bytes)
    if references[archive_receipt_name] != archive_receipt_digest:
        raise ReleaseEvidenceError(
            "G8 designated archive receipt digest does not match its exact bytes"
        )
    archive_receipt = load_json_bytes(
        archive_receipt_bytes,
        label="designated G8 archive receipt",
    )
    validate_archive_receipt_document(
        repository_root,
        archive_receipt,
        expected_candidate=expected_candidate,
        expected_version=expected_coordinates.version,
        expected_publication_state=expected_coordinates.publication_state,
        expected_generated_at=expected_coordinates.generated_at,
        expected_release_at=expected_coordinates.release_at,
        expected_archive_kind="candidate-a",
    )
    archive_name = archive_receipt["path"]
    if direct_zip_paths != {archive_name}:
        raise ReleaseEvidenceError(
            "G8 must reference exactly the designated dist/*.zip archive; "
            f"declared={sorted(direct_zip_paths)}, expected={[archive_name]}"
        )
    if references.get(archive_name) != archive_receipt.get("sha256"):
        raise ReleaseEvidenceError(
            "G8 ZIP evidence SHA-256 differs from the designated archive receipt"
        )

    try:
        if __package__:
            from scripts.create_release_metadata import (
                expected_release_metadata_documents,
            )
        else:  # pragma: no cover - exercised by direct CLI tests
            from create_release_metadata import (  # type: ignore[no-redef]
                expected_release_metadata_documents,
            )
        derived_version, expected_documents = expected_release_metadata_documents(
            repository_root,
            candidate=expected_candidate,
            archive_receipt=archive_receipt,
        )
    except ReleaseEvidenceError:
        raise
    except Exception as exc:
        raise ReleaseEvidenceError(
            f"cannot derive exact-candidate G8 release metadata: {exc}"
        ) from exc
    if derived_version != version:
        raise ReleaseEvidenceError(
            "G8 release metadata version differs from governed release coordinates"
        )
    for filename, expected_bytes in expected_documents.items():
        expected_path = metadata_names[filename]
        actual_bytes = read_repository_file_bytes(
            repository_root,
            expected_path,
            purpose=f"G8 {filename}",
            max_bytes=MAX_JSON_BYTES,
        )
        actual_digest = sha256_bytes(actual_bytes)
        if references[expected_path] != actual_digest:
            raise ReleaseEvidenceError(
                f"G8 {filename} evidence digest differs from its exact bytes"
            )
        if actual_bytes != expected_bytes:
            raise ReleaseEvidenceError(
                f"G8 {filename} does not match exact candidate metadata"
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
    executed_at = parse_utc_timestamp(
        receipt.get("executed_at"), label=f"{gate}.executed_at"
    )

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
    reviewer_times: dict[str, datetime] = {}
    reviewer_ids: list[str] = []
    for index, reviewer in enumerate(reviewers):
        identity = canonical_identity_text(
            reviewer.get("identity"),
            label=f"{gate}.review.reviewers[{index}].identity",
        )
        canonical_identity_text(
            reviewer.get("role"),
            label=f"{gate}.review.reviewers[{index}].role",
        )
        reviewed_at = parse_utc_timestamp(
            reviewer.get("reviewed_at"),
            label=f"{gate}.review.reviewers[{index}].reviewed_at",
        )
        if reviewed_at > executed_at:
            raise ReleaseEvidenceError(
                f"{gate} reviewer {identity!r} reviewed after the gate "
                "executed_at time"
            )
        reviewer_ids.append(identity)
        reviewer_times[identity] = reviewed_at
    if len(reviewer_ids) != len(set(reviewer_ids)):
        raise ReleaseEvidenceError(f"{gate} has duplicate reviewer identities")
    for index, reviewed_check in enumerate(receipt["reviewed_checks"]):
        if reviewed_check["status"] != "pass":
            raise ReleaseEvidenceError(
                f"{gate} reviewed check {reviewed_check['id']!r} is not passed"
            )
        reviewer_identity = canonical_identity_text(
            reviewed_check.get("reviewer_identity"),
            label=f"{gate}.reviewed_checks[{index}].reviewer_identity",
        )
        if reviewer_identity not in reviewer_ids:
            raise ReleaseEvidenceError(
                f"{gate} reviewed check {reviewed_check['id']!r} names an "
                "undeclared reviewer"
            )
        completed_at = parse_utc_timestamp(
            reviewed_check.get("completed_at"),
            label=f"{gate}.reviewed_checks[{index}].completed_at",
        )
        if completed_at > reviewer_times[reviewer_identity]:
            raise ReleaseEvidenceError(
                f"{gate} reviewed check {reviewed_check['id']!r} completed "
                "after its matching reviewer reviewed_at time"
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


def _exact_object(
    value: Any,
    *,
    label: str,
    fields: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseEvidenceError(f"{label} must be an object")
    actual = set(value)
    if actual != fields:
        missing = sorted(fields - actual)
        extra = sorted(actual - fields)
        raise ReleaseEvidenceError(
            f"{label} has an invalid field set; "
            f"missing={missing}, extra={extra}"
        )
    return value


def _exact_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise ReleaseEvidenceError(
            f"{label} must be exactly 64 lowercase hexadecimal characters"
        )
    return value


def _exact_string_list(value: Any, *, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(
            not isinstance(item, str)
            or not item
            or item != item.strip()
            for item in value
        )
        or len(value) != len(set(value))
    ):
        raise ReleaseEvidenceError(
            f"{label} must be a non-empty unique array of trimmed strings"
        )
    return list(value)


def validate_independent_review_evidence_document(
    document: dict[str, Any],
    *,
    expected_candidate: CandidateIdentity,
    expected_review: dict[str, Any],
    pre_g9_manifest_sha256: str,
    approved_claims: list[str],
    residual_risk_ids: list[str],
) -> None:
    evidence = _exact_object(
        document,
        label="independent release-review evidence",
        fields={
            "$schema",
            "schema",
            "candidate",
            "independent_review",
            "pre_g9_manifest_sha256",
            "approved_claims",
            "residual_risk_ids",
        },
    )
    if evidence["$schema"] != SCHEMA_ID or evidence["schema"] != (
        "okf-independent-release-review-evidence.v1"
    ):
        raise ReleaseEvidenceError(
            "independent release-review evidence has the wrong schema"
        )
    if evidence["candidate"] != asdict(expected_candidate):
        raise ReleaseEvidenceError(
            "independent release-review evidence has another candidate"
        )
    if evidence["independent_review"] != expected_review:
        raise ReleaseEvidenceError(
            "independent release-review evidence does not contain the exact "
            "bound review"
        )
    if evidence["pre_g9_manifest_sha256"] != pre_g9_manifest_sha256:
        raise ReleaseEvidenceError(
            "independent release-review evidence has another pre-G9 manifest"
        )
    if evidence["approved_claims"] != approved_claims:
        raise ReleaseEvidenceError(
            "independent release-review evidence has another approved-claim set"
        )
    if set(_exact_string_list(
        evidence["residual_risk_ids"],
        label="independent release-review evidence residual_risk_ids",
    )) != set(residual_risk_ids):
        raise ReleaseEvidenceError(
            "independent release-review evidence has another residual-risk set"
        )


def _exact_receipt_hashes(
    value: Any,
    *,
    label: str,
) -> dict[str, str]:
    if not isinstance(value, list):
        raise ReleaseEvidenceError(f"{label} must be an array")
    result: dict[str, str] = {}
    for index, raw_reference in enumerate(value):
        item_label = f"{label}[{index}]"
        reference = _exact_object(
            raw_reference,
            label=item_label,
            fields={"gate", "sha256"},
        )
        gate = reference["gate"]
        if gate not in GATE_RECEIPTS:
            raise ReleaseEvidenceError(
                f"{item_label}.gate must identify G1-G8"
            )
        if gate in result:
            raise ReleaseEvidenceError(
                f"{label} contains duplicate receipt for {gate}"
            )
        result[gate] = _exact_sha256(
            reference["sha256"], label=f"{item_label}.sha256"
        )
    if set(result) != set(GATE_RECEIPTS):
        missing = sorted(set(GATE_RECEIPTS) - set(result))
        extra = sorted(set(result) - set(GATE_RECEIPTS))
        raise ReleaseEvidenceError(
            f"{label} must bind exactly G1-G8; "
            f"missing={missing}, extra={extra}"
        )
    return result


def _digest_bound_file(
    repository_root: Path,
    value: Any,
    *,
    label: str,
    purpose: str,
    max_bytes: int = MAX_JSON_BYTES,
) -> tuple[bytes, str]:
    reference = _exact_object(
        value,
        label=label,
        fields={"path", "sha256"},
    )
    name = reference["path"]
    if not isinstance(name, str) or not name:
        raise ReleaseEvidenceError(f"{label}.path must be a non-empty string")
    declared_digest = _exact_sha256(
        reference["sha256"], label=f"{label}.sha256"
    )
    file_bytes = read_repository_file_bytes(
        repository_root,
        name,
        purpose=purpose,
        max_bytes=max_bytes,
    )
    actual_digest = sha256_bytes(file_bytes)
    if actual_digest != declared_digest:
        raise ReleaseEvidenceError(
            f"{label} digest mismatch: declared {declared_digest}, "
            f"calculated {actual_digest}"
        )
    return file_bytes, declared_digest


def _validate_pre_g9_owner_binding(
    repository_root: Path,
    value: Any,
    *,
    expected_candidate: CandidateIdentity,
    expected_receipt_hashes: dict[str, str],
) -> datetime:
    manifest_bytes, _ = _digest_bound_file(
        repository_root,
        value,
        label="G9 owner binding pre_g9_manifest",
        purpose="G9 owner-bound pre-G9 manifest",
    )
    manifest = load_json_bytes(
        manifest_bytes, label="G9 owner-bound pre-G9 manifest"
    )
    manifest = _exact_object(
        manifest,
        label="G9 owner-bound pre-G9 manifest",
        fields={
            "schema",
            "status",
            "generated_at",
            "candidate",
            "receipts",
            "limitations",
        },
    )
    if manifest["schema"] != PRE_G9_MANIFEST_SCHEMA:
        raise ReleaseEvidenceError(
            "G9 owner-bound pre-G9 manifest has the wrong schema"
        )
    if manifest["status"] != "ready_for_owner_review":
        raise ReleaseEvidenceError(
            "G9 owner-bound pre-G9 manifest is not ready_for_owner_review"
        )
    if manifest["candidate"] != asdict(expected_candidate):
        raise ReleaseEvidenceError(
            "G9 owner-bound pre-G9 manifest candidate differs from the "
            "exact repository candidate"
        )
    _exact_string_list(
        manifest["limitations"],
        label="G9 owner-bound pre-G9 limitations",
    )
    raw_receipts = manifest["receipts"]
    if not isinstance(raw_receipts, list):
        raise ReleaseEvidenceError(
            "G9 owner-bound pre-G9 manifest receipts must be an array"
        )

    pre_g9_hashes: dict[str, str] = {}
    for index, raw_reference in enumerate(raw_receipts):
        item_label = f"G9 owner-bound pre-G9 receipts[{index}]"
        reference = _exact_object(
            raw_reference,
            label=item_label,
            fields={"gate", "path", "sha256"},
        )
        gate = reference["gate"]
        if gate not in GATE_RECEIPTS:
            raise ReleaseEvidenceError(
                f"{item_label}.gate must identify G1-G8"
            )
        if gate in pre_g9_hashes:
            raise ReleaseEvidenceError(
                f"G9 owner-bound pre-G9 manifest has duplicate {gate} receipt"
            )
        receipt_bytes, receipt_digest = _digest_bound_file(
            repository_root,
            {"path": reference["path"], "sha256": reference["sha256"]},
            label=item_label,
            purpose=f"G9 owner-bound pre-G9 {gate} receipt",
        )
        receipt = load_json_bytes(
            receipt_bytes,
            label=f"G9 owner-bound pre-G9 {gate} receipt",
        )
        if receipt.get("schema") != "okf-gate-receipt.v1":
            raise ReleaseEvidenceError(
                f"G9 owner-bound pre-G9 {gate} file is not a gate receipt"
            )
        if receipt.get("gate") != gate:
            raise ReleaseEvidenceError(
                f"G9 owner-bound pre-G9 {gate} receipt declares another gate"
            )
        if receipt.get("candidate") != asdict(expected_candidate):
            raise ReleaseEvidenceError(
                f"G9 owner-bound pre-G9 {gate} receipt has another candidate"
            )
        pre_g9_hashes[gate] = receipt_digest
    if pre_g9_hashes != expected_receipt_hashes:
        raise ReleaseEvidenceError(
            "G9 owner-bound pre-G9 receipt hashes do not match the exact "
            "G1-G8 evidence manifest"
        )
    return parse_utc_timestamp(
        manifest["generated_at"],
        label="G9 owner-bound pre-G9 generated_at",
    )


def _validate_governed_risk_binding(
    repository_root: Path,
    value: Any,
    *,
    release_risk_ids: list[str],
) -> None:
    residual_risks = _exact_object(
        value,
        label="G9 owner binding residual_risks",
        fields={"register", "ids"},
    )
    register_reference = _exact_object(
        residual_risks["register"],
        label="G9 owner binding residual_risks.register",
        fields={"path", "sha256"},
    )
    if register_reference["path"] != GOVERNED_RISK_REGISTER:
        raise ReleaseEvidenceError(
            f"G9 owner binding must identify {GOVERNED_RISK_REGISTER}"
        )
    register_bytes, _ = _digest_bound_file(
        repository_root,
        register_reference,
        label="G9 owner binding residual_risks.register",
        purpose="G9 owner-bound governed residual-risk register",
    )
    register = load_json_bytes(
        register_bytes,
        label="G9 owner-bound governed residual-risk register",
    )
    if register.get("schema") != "okf-risk-register.v1":
        raise ReleaseEvidenceError(
            "G9 owner-bound governed residual-risk register has the wrong schema"
        )
    risks = register.get("risks")
    if not isinstance(risks, list) or not risks:
        raise ReleaseEvidenceError(
            "G9 owner-bound governed residual-risk register has no risks"
        )
    governed_ids: list[str] = []
    for index, risk in enumerate(risks):
        if not isinstance(risk, dict):
            raise ReleaseEvidenceError(
                f"G9 governed residual risk {index} is not an object"
            )
        risk_id = risk.get("id")
        if not isinstance(risk_id, str) or not risk_id:
            raise ReleaseEvidenceError(
                f"G9 governed residual risk {index} has no valid ID"
            )
        if "residual" not in risk or "release_disposition" not in risk:
            raise ReleaseEvidenceError(
                f"G9 governed residual risk {risk_id!r} lacks residual or "
                "release disposition"
            )
        governed_ids.append(risk_id)
    if len(governed_ids) != len(set(governed_ids)):
        raise ReleaseEvidenceError(
            "G9 governed residual-risk register contains duplicate IDs"
        )
    owner_ids = _exact_string_list(
        residual_risks["ids"],
        label="G9 owner binding residual_risks.ids",
    )
    if set(owner_ids) != set(governed_ids):
        raise ReleaseEvidenceError(
            "G9 owner-bound residual-risk IDs do not equal the governed set"
        )
    if set(release_risk_ids) != set(governed_ids):
        raise ReleaseEvidenceError(
            "G9 release residual-risk IDs do not equal the governed set"
        )


def _validate_release_identity_registry(
    release_record: dict[str, Any],
    *,
    gate_receipts: dict[str, dict[str, Any]],
) -> None:
    """Apply the assembler's reviewer-identity rules to written evidence."""

    owner = release_record["owner_approval"]
    owner_identity = canonical_identity_text(
        owner.get("identity"), label="G9 owner_approval.identity"
    )
    canonical_identity_text(owner.get("role"), label="G9 owner_approval.role")

    registry: dict[str, tuple[str, bool]] = {}
    for gate in GATE_RECEIPTS:
        for index, reviewer in enumerate(
            gate_receipts[gate]["review"]["reviewers"]
        ):
            identity = canonical_identity_text(
                reviewer.get("identity"),
                label=f"{gate}.review.reviewers[{index}].identity",
            )
            signature = (reviewer["kind"], reviewer["independent"])
            previous = registry.get(identity)
            if previous is not None and previous != signature:
                raise ReleaseEvidenceError(
                    f"reviewer identity {identity!r} has inconsistent kind or "
                    "independence across gates"
                )
            registry[identity] = signature
            if identity == owner_identity and reviewer["independent"] is True:
                raise ReleaseEvidenceError(
                    "project owner is declared as an independent gate reviewer"
                )

    release_review = release_record["independent_review"]
    release_review_identity = canonical_identity_text(
        release_review.get("identity"),
        label="G9 independent_review.identity",
    )
    canonical_identity_text(
        release_review.get("role"), label="G9 independent_review.role"
    )
    previous = registry.get(release_review_identity)
    expected_signature = (release_review["kind"], True)
    if previous is not None and previous != expected_signature:
        raise ReleaseEvidenceError(
            "release independent reviewer identity conflicts with its gate "
            "review metadata"
        )
    if release_review_identity == owner_identity:
        raise ReleaseEvidenceError(
            "G9 independent reviewer and project owner must be different identities"
        )


def _validate_exact_owner_binding(
    repository_root: Path,
    release_record: dict[str, Any],
    *,
    expected_candidate: CandidateIdentity,
    expected_coordinates: ReleaseCoordinates,
    receipt_hashes: dict[str, str],
    gate_receipts: dict[str, dict[str, Any]],
) -> None:
    owner = release_record.get("owner_approval")
    if not isinstance(owner, dict):
        raise ReleaseEvidenceError("G9 owner approval must be an object")
    binding = _exact_object(
        owner.get("binding"),
        label="G9 owner approval binding",
        fields={
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
    )
    if (
        release_record["version"] != expected_coordinates.version
        or binding["version"] != expected_coordinates.version
    ):
        raise ReleaseEvidenceError(
            "G9 release version and owner binding must equal the governed "
            "source/build-config.json version"
        )
    if (
        release_record["canonical_url"] != expected_coordinates.canonical_url
        or binding["canonical_url"] != expected_coordinates.canonical_url
    ):
        raise ReleaseEvidenceError(
            "G9 canonical URL and owner binding must equal the governed "
            "source/build-config.json publication_base"
        )
    if binding["candidate"] != asdict(expected_candidate):
        raise ReleaseEvidenceError(
            "G9 owner binding candidate differs from the exact repository "
            "candidate"
        )
    expected_hashes = {
        gate: receipt_hashes[gate] for gate in GATE_RECEIPTS
    }
    owner_receipts = _exact_receipt_hashes(
        binding["approved_receipts"],
        label="G9 owner binding approved_receipts",
    )
    if owner_receipts != expected_hashes:
        raise ReleaseEvidenceError(
            "G9 owner binding receipt hashes do not match the exact G1-G8 "
            "evidence manifest"
        )
    approved_claims = _exact_string_list(
        release_record.get("approved_claims"),
        label="G9 approved_claims",
    )
    owner_claims = _exact_string_list(
        binding["approved_claims"],
        label="G9 owner binding approved_claims",
    )
    if owner_claims != approved_claims:
        raise ReleaseEvidenceError(
            "G9 owner binding approved claims do not match the release record"
        )
    if binding["human_audit"] != release_record["human_audit"]:
        raise ReleaseEvidenceError(
            "G9 owner binding human audit does not match the release record"
        )
    independent_review = release_record["independent_review"]
    if binding["independent_review"] != independent_review:
        raise ReleaseEvidenceError(
            "G9 owner binding independent review does not match the release "
            "record"
        )
    release_risk_ids = _exact_string_list(
        release_record["residual_risk_ids"],
        label="G9 residual_risk_ids",
    )
    _validate_governed_risk_binding(
        repository_root,
        binding["residual_risks"],
        release_risk_ids=release_risk_ids,
    )
    pre_g9_generated_at = _validate_pre_g9_owner_binding(
        repository_root,
        binding["pre_g9_manifest"],
        expected_candidate=expected_candidate,
        expected_receipt_hashes=expected_hashes,
    )
    review_evidence_bytes, _ = _digest_bound_file(
        repository_root,
        binding["independent_review_evidence"],
        label="G9 owner binding independent_review_evidence",
        purpose="G9 owner-bound independent release-review evidence",
    )
    review_evidence = load_json_bytes(
        review_evidence_bytes,
        label="G9 owner-bound independent release-review evidence",
    )
    validate_independent_review_evidence_document(
        review_evidence,
        expected_candidate=expected_candidate,
        expected_review=independent_review,
        pre_g9_manifest_sha256=binding["pre_g9_manifest"]["sha256"],
        approved_claims=approved_claims,
        residual_risk_ids=release_risk_ids,
    )

    gate_times = {
        gate: parse_utc_timestamp(
            gate_receipts[gate].get("executed_at"),
            label=f"{gate}.executed_at",
        )
        for gate in GATE_RECEIPTS
    }
    reviewed_at = parse_utc_timestamp(
        independent_review.get("reviewed_at"),
        label="G9 independent_review.reviewed_at",
    )
    approved_at = parse_utc_timestamp(
        owner.get("approved_at"), label="G9 owner_approval.approved_at"
    )
    later_gate = max(gate_times, key=gate_times.__getitem__)
    if gate_times[later_gate] > pre_g9_generated_at:
        raise ReleaseEvidenceError(
            f"G9 chronology is invalid: {later_gate}.executed_at is after "
            "the pre-G9 manifest"
        )
    if pre_g9_generated_at > reviewed_at:
        raise ReleaseEvidenceError(
            "G9 chronology is invalid: independent review predates the "
            "pre-G9 manifest"
        )
    if reviewed_at > approved_at:
        raise ReleaseEvidenceError(
            "G9 chronology is invalid: owner approval predates the "
            "independent review"
        )


def _is_frozen_legacy_release(
    release_record: dict[str, Any],
    *,
    expected_candidate: CandidateIdentity,
    release_record_bytes: bytes | None,
    manifest_bytes: bytes | None,
) -> bool:
    if release_record_bytes is None and manifest_bytes is None:
        return False
    if release_record_bytes is None or manifest_bytes is None:
        raise ReleaseEvidenceError(
            "legacy validation requires both actual G9 and manifest byte buffers"
        )
    parsed_release = load_json_bytes(
        release_record_bytes, label="frozen legacy G9 byte buffer"
    )
    if parsed_release != release_record:
        raise ReleaseEvidenceError(
            "legacy G9 document differs from its supplied byte buffer"
        )
    parsed_manifest = load_json_bytes(
        manifest_bytes, label="frozen legacy evidence-manifest byte buffer"
    )
    if parsed_manifest.get("schema") != "okf-release-evidence-manifest.v1":
        raise ReleaseEvidenceError(
            "frozen legacy manifest byte buffer has the wrong schema"
        )
    if parsed_manifest.get("candidate") != asdict(expected_candidate):
        raise ReleaseEvidenceError(
            "frozen legacy manifest byte buffer has another candidate"
        )
    references = parsed_manifest.get("receipts")
    if not isinstance(references, list):
        raise ReleaseEvidenceError(
            "frozen legacy manifest byte buffer has no receipt array"
        )
    g9_references = [
        reference
        for reference in references
        if isinstance(reference, dict) and reference.get("gate") == "G9"
    ]
    release_digest = sha256_bytes(release_record_bytes)
    if (
        len(g9_references) != 1
        or g9_references[0].get("sha256") != release_digest
    ):
        raise ReleaseEvidenceError(
            "frozen legacy manifest does not bind its actual G9 byte buffer"
        )
    release_identity = (
        release_record["version"],
        expected_candidate.candidate_commit_sha,
        expected_candidate.release_root_sha256,
        expected_candidate.checksums_sha256,
        expected_candidate.profile_pack_root_sha256,
        expected_candidate.snapshot_manifest_sha256,
        release_digest,
        sha256_bytes(manifest_bytes),
    )
    return release_identity in LEGACY_UNBOUND_RELEASE_IDENTITIES


def validate_release_record(
    repository_root: Path,
    release_record: dict[str, Any],
    *,
    expected_candidate: CandidateIdentity,
    receipt_hashes: dict[str, str],
    release_record_bytes: bytes | None = None,
    manifest_bytes: bytes | None = None,
    gate_receipts: dict[str, dict[str, Any]] | None = None,
    expected_coordinates: ReleaseCoordinates | None = None,
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

    is_frozen_legacy_release = _is_frozen_legacy_release(
        release_record,
        expected_candidate=expected_candidate,
        release_record_bytes=release_record_bytes,
        manifest_bytes=manifest_bytes,
    )

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
    elif not is_frozen_legacy_release:
        raise ReleaseEvidenceError(
            "completed human audit is unsupported until a separately "
            "digest-bound human-audit workflow is implemented"
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

    canonical_publication_base(
        release_record["canonical_url"], label="G9 canonical_url"
    )

    reviewer = release_record["independent_review"]
    owner_identity = canonical_identity_text(
        release_record["owner_approval"].get("identity"),
        label="G9 owner_approval.identity",
    )
    canonical_identity_text(
        release_record["owner_approval"].get("role"),
        label="G9 owner_approval.role",
    )
    reviewer_identity = canonical_identity_text(
        reviewer.get("identity"), label="G9 independent_review.identity"
    )
    canonical_identity_text(
        reviewer.get("role"), label="G9 independent_review.role"
    )
    if reviewer["independent"] is not True:
        raise ReleaseEvidenceError("G9 release review is not independent")
    if reviewer["outcome"] != "recommend_approval":
        raise ReleaseEvidenceError(
            "G9 independent reviewer does not recommend approval"
        )
    if reviewer_identity == owner_identity:
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
    if gate_receipts is not None:
        _validate_release_identity_registry(
            release_record, gate_receipts=gate_receipts
        )
    if is_frozen_legacy_release:
        return
    if expected_coordinates is None:
        raise ReleaseEvidenceError(
            "non-historical G9 validation requires governed release "
            "coordinates"
        )
    if gate_receipts is None or set(gate_receipts) != set(GATE_RECEIPTS):
        raise ReleaseEvidenceError(
            "non-historical G9 validation requires exact G1-G8 receipt "
            "documents"
        )
    _validate_exact_owner_binding(
        repository_root,
        release_record,
        expected_candidate=expected_candidate,
        expected_coordinates=expected_coordinates,
        receipt_hashes=receipt_hashes,
        gate_receipts=gate_receipts,
    )


def declared_candidate_commit(
    repository_root: Path, *, manifest_path: Path, schema_path: Path
) -> str:
    manifest_file = repository_argument(
        repository_root.resolve(),
        manifest_path,
        purpose="release evidence manifest",
    )
    validator = schema_validator(schema_path, repository_root=repository_root)
    root = repository_root.resolve()
    manifest_bytes = read_repository_file_bytes(
        root,
        manifest_file.relative_to(root).as_posix(),
        purpose="release evidence manifest",
        max_bytes=MAX_JSON_BYTES,
    )
    manifest = load_json_bytes(
        manifest_bytes,
        label="release evidence manifest",
    )
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


def validate_committed_release_evidence_closure(
    repository_root: Path,
    *,
    manifest_path: Path,
    schema_path: Path = Path(RELEASE_EVIDENCE_SCHEMA_PATH),
    evidence_commit_sha: str,
) -> str:
    """Require every final-evidence input read by the CLI to be exact HEAD bytes."""

    root = repository_root.resolve()
    if COMMIT_SHA.fullmatch(evidence_commit_sha) is None:
        raise ReleaseEvidenceError(
            "evidence commit must be exactly 40 lowercase hexadecimal characters"
        )
    cache: dict[str, bytes] = {}

    def committed_bytes(name: str, purpose: str, max_bytes: int) -> bytes:
        canonical, _ = _canonical_git_path(name, purpose=purpose)
        if canonical in cache:
            content = cache[canonical]
            if len(content) > max_bytes:
                raise ReleaseEvidenceError(
                    f"{purpose} exceeds the {max_bytes}-byte limit: {canonical!r}"
                )
            return content
        content = _candidate_blob_bytes(
            root,
            candidate_commit_sha=evidence_commit_sha,
            relative_name=canonical,
            purpose=f"committed {purpose}",
            max_bytes=max_bytes,
        )
        worktree_content = read_repository_file_bytes(
            root,
            canonical,
            purpose=f"worktree {purpose}",
            max_bytes=max_bytes,
        )
        if worktree_content != content:
            raise ReleaseEvidenceError(
                f"{purpose} is not the exact committed evidence blob at HEAD: "
                f"{canonical!r}"
            )
        cache[canonical] = content
        return content

    schema_name = _repository_git_argument_name(
        root,
        schema_path,
        purpose="release evidence schema",
    )
    if schema_name != RELEASE_EVIDENCE_SCHEMA_PATH:
        raise ReleaseEvidenceError(
            "final evidence validation must use the canonical committed schema "
            f"at {RELEASE_EVIDENCE_SCHEMA_PATH}"
        )
    committed_bytes(
        schema_name,
        "release evidence schema",
        MAX_JSON_BYTES,
    )

    manifest_name = _repository_git_argument_name(
        root,
        manifest_path,
        purpose="release evidence manifest",
    )
    manifest = load_json_bytes(
        committed_bytes(
            manifest_name,
            "release evidence manifest",
            MAX_JSON_BYTES,
        ),
        label="committed release evidence manifest",
    )
    candidate = manifest.get("candidate")
    candidate_commit_sha = (
        candidate.get("candidate_commit_sha")
        if isinstance(candidate, dict)
        else None
    )
    if (
        not isinstance(candidate_commit_sha, str)
        or COMMIT_SHA.fullmatch(candidate_commit_sha) is None
    ):
        raise ReleaseEvidenceError(
            "committed release evidence manifest has no valid candidate commit"
        )
    raw_receipts = manifest.get("receipts")
    if not isinstance(raw_receipts, list) or len(raw_receipts) > len(ALL_GATES):
        raise ReleaseEvidenceError(
            "committed release evidence manifest has an invalid receipt inventory"
        )

    receipts: dict[str, dict[str, Any]] = {}
    for index, reference in enumerate(raw_receipts):
        if not isinstance(reference, dict):
            raise ReleaseEvidenceError(
                f"committed receipt reference {index} is not an object"
            )
        gate = reference.get("gate")
        name = reference.get("path")
        if not isinstance(gate, str) or not isinstance(name, str):
            raise ReleaseEvidenceError(
                f"committed receipt reference {index} lacks gate or path"
            )
        receipt = load_json_bytes(
            committed_bytes(name, f"{gate} receipt", MAX_JSON_BYTES),
            label=f"committed {gate} receipt",
        )
        receipts[gate] = receipt

    for gate in GATE_RECEIPTS:
        receipt = receipts.get(gate)
        references = receipt.get("evidence") if isinstance(receipt, dict) else None
        if not isinstance(references, list):
            raise ReleaseEvidenceError(
                f"committed {gate} receipt has no evidence reference array"
            )
        if len(references) > MAX_EVIDENCE_REFERENCES:
            raise ReleaseEvidenceError(
                f"committed {gate} receipt exceeds the "
                f"{MAX_EVIDENCE_REFERENCES}-entry evidence reference limit"
            )
        aggregate = 0
        for index, reference in enumerate(references):
            name = reference.get("path") if isinstance(reference, dict) else None
            if not isinstance(name, str):
                raise ReleaseEvidenceError(
                    f"committed {gate} evidence reference {index} has no path"
                )
            remaining = MAX_EVIDENCE_REFERENCE_AGGREGATE_BYTES - aggregate
            content = committed_bytes(
                name,
                f"{gate} evidence",
                min(MAX_EVIDENCE_BYTES, remaining),
            )
            aggregate += len(content)

    release_record = receipts.get("G9")
    owner = (
        release_record.get("owner_approval")
        if isinstance(release_record, dict)
        else None
    )
    binding = owner.get("binding") if isinstance(owner, dict) else None
    if isinstance(binding, dict):
        pre_g9_reference = binding.get("pre_g9_manifest")
        pre_g9_name = (
            pre_g9_reference.get("path")
            if isinstance(pre_g9_reference, dict)
            else None
        )
        if not isinstance(pre_g9_name, str):
            raise ReleaseEvidenceError(
                "committed G9 owner binding has no pre-G9 manifest path"
            )
        pre_g9_manifest = load_json_bytes(
            committed_bytes(
                pre_g9_name,
                "G9 owner-bound pre-G9 manifest",
                MAX_JSON_BYTES,
            ),
            label="committed G9 owner-bound pre-G9 manifest",
        )
        pre_g9_receipts = pre_g9_manifest.get("receipts")
        if not isinstance(pre_g9_receipts, list) or len(pre_g9_receipts) > len(
            GATE_RECEIPTS
        ):
            raise ReleaseEvidenceError(
                "committed pre-G9 manifest has an invalid receipt inventory"
            )
        for index, reference in enumerate(pre_g9_receipts):
            name = reference.get("path") if isinstance(reference, dict) else None
            if not isinstance(name, str):
                raise ReleaseEvidenceError(
                    f"committed pre-G9 receipt reference {index} has no path"
                )
            committed_bytes(name, "pre-G9 receipt", MAX_JSON_BYTES)

        for member, purpose in (
            (
                "independent_review_evidence",
                "independent release-review evidence",
            ),
        ):
            reference = binding.get(member)
            name = reference.get("path") if isinstance(reference, dict) else None
            if not isinstance(name, str):
                raise ReleaseEvidenceError(
                    f"committed G9 owner binding has no {member} path"
                )
            committed_bytes(name, purpose, MAX_JSON_BYTES)
        residual_risks = binding.get("residual_risks")
        register = (
            residual_risks.get("register")
            if isinstance(residual_risks, dict)
            else None
        )
        register_name = register.get("path") if isinstance(register, dict) else None
        if not isinstance(register_name, str):
            raise ReleaseEvidenceError(
                "committed G9 owner binding has no residual-risk register path"
            )
        committed_bytes(
            register_name,
            "owner-bound residual-risk register",
            MAX_JSON_BYTES,
        )
    return candidate_commit_sha


def validate_release_evidence(
    repository_root: Path,
    *,
    manifest_path: Path,
    schema_path: Path,
    expected_candidate: CandidateIdentity,
    build_receipt_path: Path = Path("bundle/build-receipt.json"),
) -> CandidateIdentity:
    root = repository_root.resolve()
    manifest_file = repository_argument(
        root, manifest_path, purpose="release evidence manifest"
    )
    manifest_name = manifest_file.relative_to(root).as_posix()
    validator = schema_validator(schema_path, repository_root=root)
    manifest_bytes = read_repository_file_bytes(
        root,
        manifest_name,
        purpose="release evidence manifest",
        max_bytes=MAX_JSON_BYTES,
    )
    manifest = load_json_bytes(
        manifest_bytes, label="release evidence manifest"
    )
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
    receipt_byte_buffers: dict[str, bytes] = {}
    for gate in ALL_GATES:
        reference = references[gate]
        receipt_bytes = read_repository_file_bytes(
            root,
            reference["path"],
            purpose=f"{gate} receipt",
            max_bytes=MAX_JSON_BYTES,
        )
        actual_hash = sha256_bytes(receipt_bytes)
        if actual_hash != reference["sha256"]:
            raise ReleaseEvidenceError(
                f"{gate} receipt digest mismatch for {reference['path']!r}: "
                f"declared {reference['sha256']}, calculated {actual_hash}"
            )
        receipt = load_json_bytes(receipt_bytes, label=f"{gate} receipt")
        validate_document_schema(validator, receipt, label=f"{gate} receipt")
        receipts[gate] = receipt
        receipt_hashes[gate] = actual_hash
        receipt_byte_buffers[gate] = receipt_bytes

    for gate in GATE_RECEIPTS:
        validate_gate_receipt(
            root,
            receipts[gate],
            gate=gate,
            expected_candidate=expected_candidate,
        )
    release_record = receipts["G9"]
    is_frozen_legacy_release = _is_frozen_legacy_release(
        release_record,
        expected_candidate=expected_candidate,
        release_record_bytes=receipt_byte_buffers["G9"],
        manifest_bytes=manifest_bytes,
    )
    expected_coordinates = (
        None
        if is_frozen_legacy_release
        else release_coordinates_from_build_config(
            root,
            build_receipt_path=build_receipt_path,
        )
    )
    if expected_coordinates is not None:
        validate_g8_archive_evidence(
            root,
            receipts["G8"],
            expected_candidate=expected_candidate,
            expected_coordinates=expected_coordinates,
        )
    validate_release_record(
        root,
        release_record,
        expected_candidate=expected_candidate,
        receipt_hashes=receipt_hashes,
        release_record_bytes=receipt_byte_buffers["G9"],
        manifest_bytes=manifest_bytes,
        gate_receipts={gate: receipts[gate] for gate in GATE_RECEIPTS},
        expected_coordinates=expected_coordinates,
    )
    manifest_generated_at = parse_utc_timestamp(
        manifest.get("generated_at"),
        label="release evidence manifest.generated_at",
    )
    final_predecessors = {
        **{
            f"{gate}.executed_at": parse_utc_timestamp(
                receipts[gate].get("executed_at"),
                label=f"{gate}.executed_at",
            )
            for gate in GATE_RECEIPTS
        },
        "G9.independent_review.reviewed_at": parse_utc_timestamp(
            release_record["independent_review"].get("reviewed_at"),
            label="G9 independent_review.reviewed_at",
        ),
        "G9.owner_approval.approved_at": parse_utc_timestamp(
            release_record["owner_approval"].get("approved_at"),
            label="G9 owner_approval.approved_at",
        ),
    }
    later_event = max(final_predecessors, key=final_predecessors.__getitem__)
    if final_predecessors[later_event] > manifest_generated_at:
        raise ReleaseEvidenceError(
            "release evidence manifest chronology is invalid: "
            f"{later_event} is after manifest.generated_at"
        )
    return expected_candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    candidate_mode = parser.add_mutually_exclusive_group()
    candidate_mode.add_argument(
        "--staged-candidate",
        action="store_true",
        help=(
            "validate the exact staged candidate index before committing; "
            "does not validate G1-G9 evidence"
        ),
    )
    candidate_mode.add_argument(
        "--candidate-only",
        action="store_true",
        help=(
            "validate committed candidate blobs and post-candidate topology; "
            "does not validate G1-G9 evidence"
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help=(
            "explicit repository-relative release evidence manifest; required "
            "in normal evidence mode"
        ),
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

    if args.staged_candidate:
        if args.manifest is not None:
            parser.error("--manifest is not used with --staged-candidate")
        if args.candidate_commit_sha is not None:
            parser.error(
                "--candidate-commit-sha is not used with --staged-candidate"
            )
    elif args.candidate_only:
        if args.manifest is not None:
            parser.error("--manifest is not used with --candidate-only")
        if args.candidate_commit_sha is None:
            parser.error(
                "--candidate-commit-sha is required with --candidate-only"
            )
    elif args.manifest is None:
        parser.error("--manifest is required in normal evidence mode")

    try:
        if args.staged_candidate:
            governed_count = validate_staged_candidate(
                ROOT,
                build_receipt_path=args.build_receipt,
            )
            print(
                "staged candidate validated before freeze: "
                f"{governed_count} governed regular blobs"
            )
            return 0
        if args.candidate_only:
            candidate, _governed_count = validate_committed_candidate_closure(
                ROOT,
                candidate_commit_sha=args.candidate_commit_sha,
                checksums_path=args.checksums,
                profile_checksums_path=args.profile_checksums,
                build_receipt_path=args.build_receipt,
            )
            evidence_commit_sha = validate_governed_candidate_commit(
                ROOT,
                candidate_commit_sha=args.candidate_commit_sha,
                build_receipt_path=args.build_receipt,
            )
            print(
                "committed candidate topology validated: "
                f"candidate commit {args.candidate_commit_sha}, "
                f"evidence commit {evidence_commit_sha}, "
                f"release root {candidate.release_root_sha256}"
            )
            return 0

        evidence_commit_sha = current_commit(ROOT)
        declared_commit = validate_committed_release_evidence_closure(
            ROOT,
            manifest_path=args.manifest,
            schema_path=args.schema,
            evidence_commit_sha=evidence_commit_sha,
        )
        if (
            args.candidate_commit_sha is not None
            and args.candidate_commit_sha != declared_commit
        ):
            raise ReleaseEvidenceError(
                "candidate commit override does not match the evidence manifest"
            )
        candidate_commit_sha = args.candidate_commit_sha or declared_commit
        candidate, _governed_count = validate_committed_candidate_closure(
            ROOT,
            candidate_commit_sha=candidate_commit_sha,
            checksums_path=args.checksums,
            profile_checksums_path=args.profile_checksums,
            build_receipt_path=args.build_receipt,
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
            build_receipt_path=args.build_receipt,
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

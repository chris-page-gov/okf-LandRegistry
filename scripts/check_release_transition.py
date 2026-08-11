#!/usr/bin/env python3
"""Fail-closed checks for v0.3 release publication and Git transitions."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any
import unicodedata
from urllib.parse import urlsplit


ROOT_MARKER = "# release-root-sha256: "
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GITHUB_OBJECT_ID = re.compile(r"^[0-9a-f]{40}$")
GIT_OBJECT_ID_BYTES = re.compile(rb"^[0-9a-f]{40}$")
MAX_MANIFEST_BYTES = 5_000_000
MAX_MANIFEST_ENTRIES = 2_000
# GitHub blocks ordinary Git blobs larger than 100 MiB. The published bundle
# is the Pages source tree, so every checksummed member must fit that ceiling.
# Keep this aligned with check_release_evidence.MAX_BUNDLE_ARTEFACT_BYTES.
MAX_ARTEFACT_BYTES = 100 * 1024 * 1024
MAX_AGGREGATE_BYTES = 1024 * 1024 * 1024
MAX_PR_STATE_BYTES = 100_000
MAX_REQUIRED_CHECKS = 100
MAX_STAGED_PATHS = 4_096
MAX_STAGED_BLOB_BYTES = 50_000_000
MAX_STAGED_ARCHIVE_BYTES = 50_000_000
MAX_STAGED_AGGREGATE_BYTES = 500 * 1024 * 1024
MAX_INVENTORY_DIRECTORIES = 4_096
MAX_INVENTORY_DEPTH = 128
MAX_GIT_STDOUT_BYTES = 20 * 1024 * 1024
MAX_GIT_STDERR_BYTES = 1024 * 1024
MAX_GIT_STDIN_BYTES = 1024 * 1024
GIT_TIMEOUT_SECONDS = 30
EXPECTED_GITHUB_REPOSITORY = "chris-page-gov/okf-LandRegistry"
EXPECTED_DEFAULT_BRANCH = "main"
EXPECTED_RELEASE_BRANCH = "candidate/v0.3.0"
EXPECTED_REQUIRED_CHECK_NAME = "verify"
EXPECTED_REQUIRED_CHECK_WORKFLOW = "Verify and publish the HM Land Registry OKF"
EXPECTED_REQUIRED_CHECK_EVENT = "pull_request"
EXPECTED_WORKFLOW_PATH = ".github/workflows/pages.yml"
MUTABLE_VALIDATION_PREFIX = b"validation/candidate-v0.3.0/"
MUTABLE_DIST_PATHS = frozenset(
    {
        b"dist/okf-landregistry-0.3.0-candidate-a.zip",
        b"dist/okf-landregistry-0.3.0-candidate-b.zip",
    }
)
TEMPORARY_COMPONENT_SUFFIXES = (
    ".bak",
    ".lock",
    ".orig",
    ".partial",
    ".pyc",
    ".pyo",
    ".rej",
    ".swp",
    ".temp",
    ".tmp",
    "~",
)
PROHIBITED_ARTEFACT_COMPONENTS = frozenset(
    {
        ".coverage",
        ".ds_store",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "desktop.ini",
        "thumbs.db",
    }
)
ALLOWED_HIDDEN_BUNDLE_PATHS = frozenset({".nojekyll", ".okf-generated"})


class ReleaseTransitionError(ValueError):
    """Raised when a release transition is not exact and fail-closed."""


@dataclass(frozen=True)
class FileIdentity:
    """Filesystem identity rechecked around every release-file read."""

    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True)
class InventoryFile:
    path: Path
    identity: FileIdentity


def _file_identity(details: os.stat_result) -> FileIdentity:
    return FileIdentity(
        device=details.st_dev,
        inode=details.st_ino,
        mode=stat.S_IFMT(details.st_mode),
        size=details.st_size,
        modified_ns=details.st_mtime_ns,
        changed_ns=details.st_ctime_ns,
    )


def _display_path(value: bytes) -> str:
    return ascii(value.decode("utf-8", errors="backslashreplace"))[1:-1]


def _canonical_path(value: str, *, purpose: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise ReleaseTransitionError(f"unsafe {purpose} path: {value!r}")
    if not value.isprintable():
        raise ReleaseTransitionError(
            f"{purpose} path contains non-printable characters: {value!r}"
        )
    if unicodedata.normalize("NFC", value) != value:
        raise ReleaseTransitionError(
            f"{purpose} path is not in canonical NFC form: {value!r}"
        )
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or "." in relative.parts
        or ".." in relative.parts
        or relative.as_posix() != value
    ):
        raise ReleaseTransitionError(f"unsafe {purpose} path: {value!r}")
    if any(part != part.strip() for part in relative.parts):
        raise ReleaseTransitionError(
            f"{purpose} path has surrounding component whitespace: {value!r}"
        )
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ReleaseTransitionError(
            f"{purpose} path is not valid UTF-8: {value!r}"
        ) from exc
    return value


def _artefact_policy_reason(path: str, *, bundle: bool) -> str | None:
    """Return why a canonical path is an accidental local artefact, if any."""

    for component in PurePosixPath(path).parts:
        lowered = component.lower()
        if lowered in PROHIBITED_ARTEFACT_COMPONENTS:
            return f"prohibited local artefact component {component!r}"
        if lowered.endswith(TEMPORARY_COMPONENT_SUFFIXES) or component.startswith("~"):
            return f"temporary, cache or backup component {component!r}"
        if lowered.startswith(".release-metadata-"):
            return f"temporary release-metadata component {component!r}"
        if bundle and component.startswith(".") and path not in ALLOWED_HIDDEN_BUNDLE_PATHS:
            return f"hidden bundle component {component!r}"
    return None


def _secure_read_flags(*, purpose: str) -> int:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(no_follow, int):
        raise ReleaseTransitionError(
            f"cannot validate {purpose}: O_NOFOLLOW is unavailable"
        )
    non_block = getattr(os, "O_NONBLOCK", None)
    if not isinstance(non_block, int):
        raise ReleaseTransitionError(
            f"cannot validate {purpose}: O_NONBLOCK is unavailable"
        )
    flags = os.O_RDONLY | no_follow | non_block
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _bounded_regular_bytes(
    path: Path,
    *,
    limit: int,
    purpose: str,
    expected_identity: FileIdentity,
) -> bytes:
    flags = _secure_read_flags(purpose=purpose)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseTransitionError(f"cannot open {purpose} {path}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ReleaseTransitionError(f"{purpose} is not a regular file: {path}")
        if _file_identity(before) != expected_identity:
            raise ReleaseTransitionError(f"{purpose} changed during validation: {path}")
        if before.st_size > limit:
            raise ReleaseTransitionError(
                f"{purpose} exceeds the {limit}-byte limit: {path}"
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise ReleaseTransitionError(
                    f"{purpose} exceeds the {limit}-byte limit: {path}"
                )
        value = b"".join(chunks)
        if _file_identity(os.fstat(descriptor)) != expected_identity:
            raise ReleaseTransitionError(f"{purpose} changed during validation: {path}")
        return value
    finally:
        os.close(descriptor)


def _sha256_regular_file(
    path: Path, *, limit: int, expected_identity: FileIdentity
) -> tuple[str, int]:
    flags = _secure_read_flags(purpose="bundle artefact")
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseTransitionError(f"cannot open bundle artefact {path}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ReleaseTransitionError(f"bundle artefact is not regular: {path}")
        if _file_identity(before) != expected_identity:
            raise ReleaseTransitionError(
                f"bundle artefact changed during validation: {path}"
            )
        if before.st_size > limit:
            raise ReleaseTransitionError(
                f"bundle artefact exceeds the {limit}-byte limit: {path}"
            )
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise ReleaseTransitionError(
                    f"bundle artefact exceeds the {limit}-byte limit: {path}"
                )
            digest.update(chunk)
        if _file_identity(os.fstat(descriptor)) != expected_identity:
            raise ReleaseTransitionError(
                f"bundle artefact changed during validation: {path}"
            )
        return digest.hexdigest(), total
    finally:
        os.close(descriptor)


def _parse_manifest(manifest_bytes: bytes) -> tuple[list[tuple[str, str, str]], str]:
    try:
        text = manifest_bytes.decode("utf-8")
    except UnicodeError as exc:
        raise ReleaseTransitionError(
            f"bundle checksum manifest is not UTF-8: {exc}"
        ) from exc

    entries: list[tuple[str, str, str]] = []
    declared_roots: list[str] = []
    seen: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if line.startswith(ROOT_MARKER):
            declared_roots.append(line.removeprefix(ROOT_MARKER))
            continue
        if not line:
            raise ReleaseTransitionError(
                f"CHECKSUMS.sha256:{line_number}: blank lines are not allowed"
            )
        if line.startswith("#"):
            raise ReleaseTransitionError(
                f"CHECKSUMS.sha256:{line_number}: unsupported checksum comment"
            )
        try:
            digest, name = line.split("  ", 1)
        except ValueError as exc:
            raise ReleaseTransitionError(
                f"CHECKSUMS.sha256:{line_number}: expected '<sha256>  <path>'"
            ) from exc
        if SHA256.fullmatch(digest) is None:
            raise ReleaseTransitionError(
                f"CHECKSUMS.sha256:{line_number}: invalid artefact SHA-256"
            )
        _canonical_path(name, purpose="checksum entry")
        if name == "CHECKSUMS.sha256":
            raise ReleaseTransitionError("checksum manifest must not list itself")
        if name in seen:
            raise ReleaseTransitionError(f"duplicate checksum path: {name!r}")
        if len(entries) >= MAX_MANIFEST_ENTRIES:
            raise ReleaseTransitionError(
                f"checksum manifest exceeds {MAX_MANIFEST_ENTRIES} entries"
            )
        seen.add(name)
        entries.append((digest, name, line))

    if not entries:
        raise ReleaseTransitionError("checksum manifest contains no artefact entries")
    if len(declared_roots) != 1 or SHA256.fullmatch(declared_roots[0]) is None:
        raise ReleaseTransitionError(
            "checksum manifest must contain exactly one valid release-root marker"
        )
    calculated_root = hashlib.sha256(
        ("\n".join(line for _digest, _name, line in entries) + "\n").encode(
            "utf-8"
        )
    ).hexdigest()
    if declared_roots[0] != calculated_root:
        raise ReleaseTransitionError(
            "declared release root does not match checksum entries: "
            f"declared {declared_roots[0]}, calculated {calculated_root}"
        )
    return entries, calculated_root


def _actual_bundle_inventory(bundle: Path) -> dict[str, InventoryFile]:
    try:
        root_details = bundle.lstat()
    except OSError as exc:
        raise ReleaseTransitionError(f"cannot inspect bundle directory {bundle}: {exc}") from exc
    if not stat.S_ISDIR(root_details.st_mode) or bundle.is_symlink():
        raise ReleaseTransitionError(f"bundle root is not a real directory: {bundle}")

    files: dict[str, InventoryFile] = {}
    directory_count = 1

    def visit(directory: Path, prefix: str, depth: int) -> None:
        nonlocal directory_count
        if depth > MAX_INVENTORY_DEPTH:
            raise ReleaseTransitionError(
                f"bundle inventory exceeds its {MAX_INVENTORY_DEPTH}-level depth limit"
            )
        try:
            entries = os.scandir(directory)
        except OSError as exc:
            raise ReleaseTransitionError(
                f"cannot enumerate bundle directory {directory}: {exc}"
            ) from exc
        try:
            with entries:
                for entry in entries:
                    name = f"{prefix}/{entry.name}" if prefix else entry.name
                    _canonical_path(name, purpose="bundle inventory")
                    policy_reason = _artefact_policy_reason(name, bundle=True)
                    if policy_reason is not None:
                        raise ReleaseTransitionError(
                            f"bundle inventory contains {policy_reason}: {name!r}"
                        )
                    try:
                        details = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise ReleaseTransitionError(
                            f"cannot inspect bundle path {name!r}: {exc}"
                        ) from exc
                    if stat.S_ISLNK(details.st_mode):
                        raise ReleaseTransitionError(
                            f"bundle inventory contains a symbolic link: {name!r}"
                        )
                    if stat.S_ISDIR(details.st_mode):
                        directory_count += 1
                        if directory_count > MAX_INVENTORY_DIRECTORIES:
                            raise ReleaseTransitionError(
                                "bundle inventory exceeds its directory limit"
                            )
                        visit(Path(entry.path), name, depth + 1)
                        continue
                    if not stat.S_ISREG(details.st_mode):
                        raise ReleaseTransitionError(
                            f"bundle inventory contains a non-regular path: {name!r}"
                        )
                    if name in files:
                        raise ReleaseTransitionError(
                            f"duplicate bundle path: {name!r}"
                        )
                    files[name] = InventoryFile(
                        path=Path(entry.path),
                        identity=_file_identity(details),
                    )
                    if len(files) > MAX_MANIFEST_ENTRIES + 1:
                        raise ReleaseTransitionError(
                            "bundle inventory exceeds its file limit"
                        )
        except OSError as exc:
            raise ReleaseTransitionError(
                f"cannot enumerate bundle directory {directory}: {exc}"
            ) from exc

    visit(bundle, "", 1)
    return files


def validate_bundle_inventory(bundle: Path, expected_root: str | None = None) -> str:
    """Validate every actual bundle file, including ignored files."""

    files = _actual_bundle_inventory(bundle)
    checksum_file = files.get("CHECKSUMS.sha256")
    if checksum_file is None:
        raise ReleaseTransitionError("bundle/CHECKSUMS.sha256 is missing")
    entries, release_root = _parse_manifest(
        _bounded_regular_bytes(
            checksum_file.path,
            limit=MAX_MANIFEST_BYTES,
            purpose="bundle checksum manifest",
            expected_identity=checksum_file.identity,
        )
    )
    declared = {name for _digest, name, _line in entries}
    actual = set(files) - {"CHECKSUMS.sha256"}
    missing = sorted(declared - actual)
    unexpected = sorted(actual - declared)
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(repr(name) for name in missing))
        if unexpected:
            details.append("unexpected " + ", ".join(repr(name) for name in unexpected))
        raise ReleaseTransitionError(
            "bundle inventory differs from CHECKSUMS.sha256: " + "; ".join(details)
        )

    aggregate = 0
    for expected_digest, name, _line in entries:
        actual_digest, size = _sha256_regular_file(
            files[name].path,
            limit=MAX_ARTEFACT_BYTES,
            expected_identity=files[name].identity,
        )
        aggregate += size
        if aggregate > MAX_AGGREGATE_BYTES:
            raise ReleaseTransitionError(
                f"bundle artefacts exceed {MAX_AGGREGATE_BYTES} aggregate bytes"
            )
        if actual_digest != expected_digest:
            raise ReleaseTransitionError(
                f"bundle artefact digest mismatch for {name!r}: "
                f"declared {expected_digest}, calculated {actual_digest}"
            )

    if _actual_bundle_inventory(bundle) != files:
        raise ReleaseTransitionError("bundle inventory changed during validation")

    if expected_root is not None:
        if SHA256.fullmatch(expected_root) is None:
            raise ReleaseTransitionError(
                "expected release root must be 64 lowercase hexadecimal characters"
            )
        if expected_root != release_root:
            raise ReleaseTransitionError(
                f"approved release root {expected_root} does not match {release_root}"
            )
    return release_root


def _git(
    repository: Path,
    arguments: list[str],
    *,
    standard_input: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    command = ["git", *arguments]
    if standard_input is not None and len(standard_input) > MAX_GIT_STDIN_BYTES:
        raise ReleaseTransitionError(
            f"Git input exceeds {MAX_GIT_STDIN_BYTES} bytes"
        )
    try:
        with tempfile.TemporaryFile() as input_file:
            if standard_input is not None:
                input_file.write(standard_input)
                input_file.seek(0)
            try:
                process = subprocess.Popen(
                    command,
                    cwd=repository,
                    stdin=(
                        input_file
                        if standard_input is not None
                        else subprocess.DEVNULL
                    ),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except OSError as exc:
                raise ReleaseTransitionError(f"cannot execute Git: {exc}") from exc
            if process.stdout is None or process.stderr is None:  # pragma: no cover
                process.kill()
                process.wait()
                raise ReleaseTransitionError(
                    "Git did not expose bounded output pipes"
                )

            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            buffers = {"stdout": bytearray(), "stderr": bytearray()}
            limits = {
                "stdout": MAX_GIT_STDOUT_BYTES,
                "stderr": MAX_GIT_STDERR_BYTES,
            }
            deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
            try:
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ReleaseTransitionError(
                            f"Git exceeded its {GIT_TIMEOUT_SECONDS}-second time limit"
                        )
                    events = selector.select(remaining)
                    if not events:
                        raise ReleaseTransitionError(
                            f"Git exceeded its {GIT_TIMEOUT_SECONDS}-second time limit"
                        )
                    for key, _mask in events:
                        chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        target = buffers[key.data]
                        limit = limits[key.data]
                        if len(target) + len(chunk) > limit:
                            label = (
                                "output"
                                if key.data == "stdout"
                                else "diagnostic output"
                            )
                            raise ReleaseTransitionError(
                                f"Git {label} exceeds {limit} bytes"
                            )
                        target.extend(chunk)
                try:
                    return_code = process.wait(
                        timeout=max(0.1, deadline - time.monotonic())
                    )
                except subprocess.TimeoutExpired as exc:
                    raise ReleaseTransitionError(
                        f"Git exceeded its {GIT_TIMEOUT_SECONDS}-second time limit"
                    ) from exc
            except BaseException:
                process.kill()
                process.wait()
                raise
            finally:
                selector.close()
                process.stdout.close()
                process.stderr.close()
            return subprocess.CompletedProcess(
                command,
                return_code,
                bytes(buffers["stdout"]),
                bytes(buffers["stderr"]),
            )
    except OSError as exc:
        raise ReleaseTransitionError(f"cannot execute Git: {exc}") from exc


def _git_output(repository: Path, arguments: list[str], *, purpose: str) -> bytes:
    result = _git(repository, arguments)
    if result.returncode != 0:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
        raise ReleaseTransitionError(f"cannot {purpose}: {diagnostic}")
    return result.stdout


def _nul_records(value: bytes, *, purpose: str) -> list[bytes]:
    if not value:
        return []
    if not value.endswith(b"\0"):
        raise ReleaseTransitionError(f"Git returned incomplete NUL data for {purpose}")
    return value[:-1].split(b"\0")


def _is_allowed_evidence_path(path: bytes) -> bool:
    return path.startswith(MUTABLE_VALIDATION_PREFIX) or path in MUTABLE_DIST_PATHS


def _canonical_staged_path(path: bytes) -> bytes:
    try:
        decoded = path.decode("utf-8")
    except UnicodeError as exc:
        raise ReleaseTransitionError(
            f"staged path is not valid UTF-8: {_display_path(path)!r}"
        ) from exc
    canonical = _canonical_path(decoded, purpose="staged evidence")
    policy_reason = _artefact_policy_reason(canonical, bundle=False)
    if policy_reason is not None:
        raise ReleaseTransitionError(
            f"staged evidence path contains {policy_reason}: {decoded!r}"
        )
    for component in PurePosixPath(canonical).parts:
        if component.startswith("."):
            raise ReleaseTransitionError(
                "staged evidence path contains a hidden or temporary component: "
                f"{decoded!r}"
            )
    return path


def validate_staged_candidate(repository: Path) -> tuple[bytes, ...]:
    """Reject accidental artefacts and non-blob entries from the complete index."""

    records = _nul_records(
        _git_output(
            repository,
            ["ls-files", "--stage", "-z"],
            purpose="inspect the complete staged candidate",
        ),
        purpose="the complete staged candidate",
    )
    if not records:
        raise ReleaseTransitionError("the staged candidate index is empty")

    paths: list[bytes] = []
    seen: set[bytes] = set()
    for record in records:
        metadata, separator, path = record.partition(b"\t")
        fields = metadata.split(b" ")
        if not separator or len(fields) != 3:
            raise ReleaseTransitionError("Git returned malformed candidate index data")
        mode, object_id, stage = fields
        if stage != b"0" or mode not in {b"100644", b"100755"}:
            raise ReleaseTransitionError(
                "the staged candidate must contain regular stage-zero Git blobs only: "
                f"{_display_path(path)!r}"
            )
        if GIT_OBJECT_ID_BYTES.fullmatch(object_id) is None or object_id == b"0" * 40:
            raise ReleaseTransitionError(
                "the staged candidate has an invalid Git object ID: "
                f"{_display_path(path)!r}"
            )
        try:
            decoded = path.decode("utf-8")
        except UnicodeError as exc:
            raise ReleaseTransitionError(
                f"candidate path is not valid UTF-8: {_display_path(path)!r}"
            ) from exc
        canonical = _canonical_path(decoded, purpose="candidate")
        policy_reason = _artefact_policy_reason(canonical, bundle=False)
        if policy_reason is not None:
            raise ReleaseTransitionError(
                f"staged candidate contains {policy_reason}: {decoded!r}"
            )
        if path in seen:
            raise ReleaseTransitionError(
                f"the staged candidate contains a duplicate path: {decoded!r}"
            )
        seen.add(path)
        paths.append(path)
        if len(paths) > MAX_STAGED_PATHS:
            raise ReleaseTransitionError(
                f"the staged candidate exceeds the {MAX_STAGED_PATHS}-path limit"
            )
    return tuple(paths)


def _staged_blob_sizes(
    repository: Path, objects: list[bytes]
) -> list[int]:
    if not objects:
        return []
    query = b"".join(value + b"\n" for value in objects)
    result = _git(
        repository,
        ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        standard_input=query,
    )
    if result.returncode != 0:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
        raise ReleaseTransitionError(f"cannot inspect staged blob sizes: {diagnostic}")
    rows = result.stdout.splitlines()
    if len(rows) != len(objects):
        raise ReleaseTransitionError("Git returned incomplete staged blob metadata")
    sizes: list[int] = []
    for expected_object, row in zip(objects, rows, strict=True):
        fields = row.split(b" ")
        if len(fields) != 3 or fields[0] != expected_object or fields[1] != b"blob":
            raise ReleaseTransitionError("Git returned malformed staged blob metadata")
        try:
            size_text = fields[2].decode("ascii")
            size = int(size_text)
        except (UnicodeError, ValueError) as exc:
            raise ReleaseTransitionError(
                "Git returned a non-numeric staged blob size"
            ) from exc
        if size < 0 or str(size) != size_text:
            raise ReleaseTransitionError(
                "Git returned a non-canonical staged blob size"
            )
        sizes.append(size)
    return sizes


def validate_staged_evidence(repository: Path) -> tuple[bytes, ...]:
    """Require a non-empty, add-only, exact v0.3 evidence index transition."""

    records = _nul_records(
        _git_output(
            repository,
            [
                "diff",
                "--cached",
                "--raw",
                "-z",
                "--no-renames",
                "--no-abbrev",
            ],
            purpose="inspect staged evidence paths",
        ),
        purpose="staged evidence paths",
    )
    if not records:
        raise ReleaseTransitionError("no evidence paths are staged")
    if len(records) % 2:
        raise ReleaseTransitionError("Git returned malformed staged status data")

    paths: list[bytes] = []
    objects: list[bytes] = []
    for offset in range(0, len(records), 2):
        metadata, path = records[offset : offset + 2]
        fields = metadata.removeprefix(b":").split(b" ")
        if len(fields) != 5 or not metadata.startswith(b":"):
            raise ReleaseTransitionError("Git returned malformed staged metadata")
        old_mode, new_mode, old_object, new_object, status_value = fields
        if status_value != b"A" or old_mode != b"000000":
            raise ReleaseTransitionError(
                "evidence commits must add new files only; "
                f"{_display_path(path)!r} has status {_display_path(status_value)!r}"
            )
        _canonical_staged_path(path)
        if not _is_allowed_evidence_path(path):
            raise ReleaseTransitionError(
                f"staged path is outside the exact v0.3 evidence surface: "
                f"{_display_path(path)!r}"
            )
        if new_mode != b"100644":
            raise ReleaseTransitionError(
                "staged evidence must be one regular stage-zero Git blob: "
                f"{_display_path(path)!r} has mode {_display_path(new_mode)!r}"
            )
        if (
            GIT_OBJECT_ID_BYTES.fullmatch(old_object) is None
            or old_object != b"0" * 40
            or GIT_OBJECT_ID_BYTES.fullmatch(new_object) is None
            or new_object == b"0" * 40
        ):
            raise ReleaseTransitionError(
                f"staged evidence has invalid Git object IDs: {_display_path(path)!r}"
            )
        paths.append(path)
        objects.append(new_object)
        if len(paths) > MAX_STAGED_PATHS:
            raise ReleaseTransitionError(
                f"staged evidence exceeds the {MAX_STAGED_PATHS}-path limit"
            )

    aggregate = 0
    for path, size in zip(paths, _staged_blob_sizes(repository, objects), strict=True):
        limit = (
            MAX_STAGED_ARCHIVE_BYTES
            if path in MUTABLE_DIST_PATHS
            else MAX_STAGED_BLOB_BYTES
        )
        if size > limit:
            raise ReleaseTransitionError(
                f"staged evidence exceeds its {limit}-byte blob limit: "
                f"{_display_path(path)!r}"
            )
        aggregate += size
        if aggregate > MAX_STAGED_AGGREGATE_BYTES:
            raise ReleaseTransitionError(
                "staged evidence exceeds its "
                f"{MAX_STAGED_AGGREGATE_BYTES}-byte aggregate limit"
            )

    whitespace = _git(repository, ["diff", "--cached", "--check"])
    if whitespace.returncode != 0:
        diagnostic = whitespace.stdout + whitespace.stderr
        raise ReleaseTransitionError(
            "staged evidence fails 'git diff --cached --check': "
            + diagnostic.decode("utf-8", errors="replace").strip()
        )
    return tuple(paths)


def _json_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReleaseTransitionError(f"pull-request field {field!r} is missing")
    return value


def validate_pr_state(
    document: Any,
    *,
    expected_head_oid: str,
    required_review_decision: str | None,
) -> None:
    """Bind a pull request's open state, refs and head OID to evidence."""

    if not isinstance(document, dict):
        raise ReleaseTransitionError("pull-request state must be a JSON object")
    if _json_text(document.get("state"), field="state") != "OPEN":
        raise ReleaseTransitionError("release pull request is not open")
    if document.get("isCrossRepository") is not False:
        raise ReleaseTransitionError(
            "release pull request does not originate from the canonical repository"
        )
    head_repository = document.get("headRepository")
    if (
        not isinstance(head_repository, dict)
        or head_repository.get("nameWithOwner") != EXPECTED_GITHUB_REPOSITORY
    ):
        raise ReleaseTransitionError(
            "release pull-request head repository is not canonical"
        )
    if (
        _json_text(document.get("baseRefName"), field="baseRefName")
        != EXPECTED_DEFAULT_BRANCH
    ):
        raise ReleaseTransitionError(
            f"release pull request does not target {EXPECTED_DEFAULT_BRANCH!r}"
        )
    if (
        _json_text(document.get("headRefName"), field="headRefName")
        != EXPECTED_RELEASE_BRANCH
    ):
        raise ReleaseTransitionError(
            f"release pull request does not use {EXPECTED_RELEASE_BRANCH!r}"
        )
    actual_oid = _json_text(document.get("headRefOid"), field="headRefOid")
    if GITHUB_OBJECT_ID.fullmatch(expected_head_oid) is None:
        raise ReleaseTransitionError(
            "expected evidence SHA is not an exact 40-character GitHub object ID"
        )
    if GITHUB_OBJECT_ID.fullmatch(actual_oid) is None:
        raise ReleaseTransitionError(
            "pull-request head is not an exact 40-character GitHub object ID"
        )
    if actual_oid != expected_head_oid:
        raise ReleaseTransitionError(
            f"pull-request head {actual_oid!r} does not equal evidence SHA "
            f"{expected_head_oid!r}"
        )
    if required_review_decision is not None:
        actual_review = _json_text(
            document.get("reviewDecision"), field="reviewDecision"
        )
        if actual_review != required_review_decision:
            raise ReleaseTransitionError(
                f"pull-request review decision {actual_review!r} is not "
                f"{required_review_decision!r}"
            )


def _positive_json_integer(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ReleaseTransitionError(f"GitHub API field {field!r} is not a positive integer")
    return value


def _required_check_link(value: Any) -> tuple[int, int]:
    link = _json_text(value, field="required verify check link")
    try:
        parsed = urlsplit(link)
        port = parsed.port
    except ValueError as exc:
        raise ReleaseTransitionError("required verify check link is not canonical") from exc
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.fragment
    ):
        raise ReleaseTransitionError("required verify check link is not canonical")
    match = re.fullmatch(
        rf"/{re.escape(EXPECTED_GITHUB_REPOSITORY)}/actions/runs/([1-9][0-9]*)/job/([1-9][0-9]*)",
        parsed.path,
    )
    if match is None:
        raise ReleaseTransitionError(
            "required verify check link is not bound to the canonical repository"
        )
    if parsed.query and re.fullmatch(r"pr=[1-9][0-9]*", parsed.query) is None:
        raise ReleaseTransitionError("required verify check link query is not canonical")
    return int(match.group(1)), int(match.group(2))


def _required_check_identity(document: Any) -> tuple[int, int]:
    """Validate every required result and return the one verify run/job identity."""

    if not isinstance(document, list) or not document:
        raise ReleaseTransitionError(
            "required pull-request checks must be a JSON array"
        )
    if len(document) > MAX_REQUIRED_CHECKS:
        raise ReleaseTransitionError(
            f"required pull-request checks exceed {MAX_REQUIRED_CHECKS} entries"
        )
    matching: list[tuple[int, int]] = []
    for index, check in enumerate(document):
        if not isinstance(check, dict):
            raise ReleaseTransitionError(
                f"required pull-request check {index} is not a JSON object"
            )
        name = _json_text(check.get("name"), field=f"checks[{index}].name")
        bucket = _json_text(check.get("bucket"), field=f"checks[{index}].bucket")
        state = _json_text(check.get("state"), field=f"checks[{index}].state")
        if bucket != "pass":
            raise ReleaseTransitionError(
                f"required pull-request check {name!r} is not passing"
            )
        if name == EXPECTED_REQUIRED_CHECK_NAME:
            if check.get("workflow") != EXPECTED_REQUIRED_CHECK_WORKFLOW:
                raise ReleaseTransitionError(
                    "the required verify check has the wrong workflow display name"
                )
            if check.get("event") != EXPECTED_REQUIRED_CHECK_EVENT:
                raise ReleaseTransitionError(
                    "the required verify check has the wrong trigger event"
                )
            if state != "SUCCESS":
                raise ReleaseTransitionError(
                    "the exact release workflow check did not conclude SUCCESS"
                )
            matching.append(_required_check_link(check.get("link")))
    if len(matching) != 1:
        raise ReleaseTransitionError(
            "the exact required release workflow check must occur exactly once; "
            f"found {len(matching)}"
        )
    return matching[0]


def required_check_run_id(document: Any) -> int:
    """Extract the bounded canonical workflow-run identity for API resolution."""

    run_id, _job_id = _required_check_identity(document)
    return run_id


def validate_required_checks(
    document: Any,
    *,
    workflow: Any,
    workflow_run: Any,
    workflow_jobs: Any,
    expected_head_oid: str,
) -> None:
    """Bind the required check to one canonical workflow run, head and job."""

    run_id, job_id = _required_check_identity(document)
    if GITHUB_OBJECT_ID.fullmatch(expected_head_oid) is None:
        raise ReleaseTransitionError(
            "expected evidence SHA is not an exact 40-character GitHub object ID"
        )

    if not isinstance(workflow, dict):
        raise ReleaseTransitionError("workflow metadata must be a JSON object")
    workflow_id = _positive_json_integer(workflow.get("id"), field="workflow.id")
    if _json_text(workflow.get("name"), field="workflow.name") != EXPECTED_REQUIRED_CHECK_WORKFLOW:
        raise ReleaseTransitionError("the resolved workflow has the wrong name")
    if _json_text(workflow.get("path"), field="workflow.path") != EXPECTED_WORKFLOW_PATH:
        raise ReleaseTransitionError("the resolved workflow has the wrong canonical path")
    if _json_text(workflow.get("state"), field="workflow.state") != "active":
        raise ReleaseTransitionError("the resolved workflow is not active")
    expected_workflow_url = (
        f"https://api.github.com/repos/{EXPECTED_GITHUB_REPOSITORY}/actions/"
        f"workflows/{workflow_id}"
    )
    if _json_text(workflow.get("url"), field="workflow.url") != expected_workflow_url:
        raise ReleaseTransitionError("workflow metadata is not bound to the canonical repository")

    if not isinstance(workflow_run, dict):
        raise ReleaseTransitionError("workflow-run metadata must be a JSON object")
    if _positive_json_integer(workflow_run.get("id"), field="workflow_run.id") != run_id:
        raise ReleaseTransitionError("required check link and workflow-run ID differ")
    if (
        _positive_json_integer(
            workflow_run.get("workflow_id"), field="workflow_run.workflow_id"
        )
        != workflow_id
    ):
        raise ReleaseTransitionError("workflow run is not from the resolved workflow ID")
    if _json_text(workflow_run.get("path"), field="workflow_run.path") != EXPECTED_WORKFLOW_PATH:
        raise ReleaseTransitionError("workflow run is not from the canonical workflow path")
    repository = workflow_run.get("repository")
    if (
        not isinstance(repository, dict)
        or repository.get("full_name") != EXPECTED_GITHUB_REPOSITORY
    ):
        raise ReleaseTransitionError("workflow run is not bound to the canonical repository")
    if _json_text(workflow_run.get("event"), field="workflow_run.event") != EXPECTED_REQUIRED_CHECK_EVENT:
        raise ReleaseTransitionError("workflow run did not use the pull_request event")
    if _json_text(workflow_run.get("head_sha"), field="workflow_run.head_sha") != expected_head_oid:
        raise ReleaseTransitionError("workflow-run head SHA does not equal the evidence SHA")
    if (
        _json_text(workflow_run.get("status"), field="workflow_run.status")
        != "completed"
        or _json_text(
            workflow_run.get("conclusion"), field="workflow_run.conclusion"
        )
        != "success"
    ):
        raise ReleaseTransitionError("workflow run did not complete successfully")
    expected_run_url = (
        f"https://github.com/{EXPECTED_GITHUB_REPOSITORY}/actions/runs/{run_id}"
    )
    if _json_text(workflow_run.get("html_url"), field="workflow_run.html_url") != expected_run_url:
        raise ReleaseTransitionError("workflow-run URL is not canonical")

    if not isinstance(workflow_jobs, dict):
        raise ReleaseTransitionError("workflow jobs must be a JSON object")
    total_count = workflow_jobs.get("total_count")
    jobs = workflow_jobs.get("jobs")
    if (
        not isinstance(total_count, int)
        or isinstance(total_count, bool)
        or total_count < 1
        or total_count > MAX_REQUIRED_CHECKS
        or not isinstance(jobs, list)
        or len(jobs) != total_count
    ):
        raise ReleaseTransitionError("workflow jobs are incomplete or exceed their limit")
    verify_jobs: list[dict[str, Any]] = []
    for index, job in enumerate(jobs):
        if not isinstance(job, dict):
            raise ReleaseTransitionError(f"workflow job {index} is not a JSON object")
        if job.get("name") == EXPECTED_REQUIRED_CHECK_NAME:
            verify_jobs.append(job)
    if len(verify_jobs) != 1:
        raise ReleaseTransitionError(
            "the authoritative workflow run must contain job 'verify' exactly once"
        )
    verify_job = verify_jobs[0]
    if _positive_json_integer(verify_job.get("id"), field="jobs.verify.id") != job_id:
        raise ReleaseTransitionError("required check link and verify job ID differ")
    if _positive_json_integer(verify_job.get("run_id"), field="jobs.verify.run_id") != run_id:
        raise ReleaseTransitionError("verify job is not bound to the workflow run")
    if (
        _json_text(verify_job.get("status"), field="jobs.verify.status")
        != "completed"
        or _json_text(verify_job.get("conclusion"), field="jobs.verify.conclusion")
        != "success"
    ):
        raise ReleaseTransitionError("verify job did not complete successfully")
    expected_job_url = f"{expected_run_url}/job/{job_id}"
    if _json_text(verify_job.get("html_url"), field="jobs.verify.html_url") != expected_job_url:
        raise ReleaseTransitionError("verify job URL is not canonical")


def _remote_urls(repository: Path, remote: str, *, push: bool) -> list[str]:
    if (
        not remote
        or not remote.isprintable()
        or remote.startswith("-")
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", remote) is None
    ):
        raise ReleaseTransitionError("Git remote name is not canonical")
    arguments = ["remote", "get-url"]
    if push:
        arguments.append("--push")
    arguments.extend(["--all", remote])
    output = _git_output(
        repository,
        arguments,
        purpose=f"inspect Git {'push' if push else 'fetch'} destination",
    )
    try:
        text = output.decode("utf-8")
    except UnicodeError as exc:
        raise ReleaseTransitionError("Git remote destination is not UTF-8") from exc
    values = text.splitlines()
    if len(values) != 1 or not values[0] or not values[0].isprintable():
        purpose = "push" if push else "fetch"
        raise ReleaseTransitionError(
            f"Git remote must have exactly one canonical {purpose} destination"
        )
    return values


def validate_remote_binding(repository: Path, remote: str) -> None:
    """Bind credential-free fetch and push URLs to the canonical repository."""

    repository_path = EXPECTED_GITHUB_REPOSITORY
    allowed = {
        f"https://github.com/{repository_path}",
        f"https://github.com/{repository_path}.git",
        f"git@github.com:{repository_path}",
        f"git@github.com:{repository_path}.git",
        f"ssh://git@github.com/{repository_path}",
        f"ssh://git@github.com/{repository_path}.git",
    }
    for purpose, value in (
        ("fetch", _remote_urls(repository, remote, push=False)[0]),
        ("push", _remote_urls(repository, remote, push=True)[0]),
    ):
        if value not in allowed:
            raise ReleaseTransitionError(
                f"Git {purpose} destination is not the credential-free canonical "
                f"GitHub repository {EXPECTED_GITHUB_REPOSITORY!r}"
            )


def validate_deployment_identity(
    *,
    github_sha: str,
    expected_commit_sha: str,
    remote_default_sha: str,
    input_root: str,
    approved_root: str,
) -> None:
    """Bind an explicit dispatch to the exact reviewed commit and release root."""

    if GITHUB_OBJECT_ID.fullmatch(github_sha) is None:
        raise ReleaseTransitionError(
            "GitHub workflow SHA is not exactly 40 lowercase hex"
        )
    if GITHUB_OBJECT_ID.fullmatch(expected_commit_sha) is None:
        raise ReleaseTransitionError(
            "expected deployment SHA is not exactly 40 lowercase hex"
        )
    if github_sha != expected_commit_sha:
        raise ReleaseTransitionError(
            "workflow dispatch SHA does not equal the expected evidence commit"
        )
    if GITHUB_OBJECT_ID.fullmatch(remote_default_sha) is None:
        raise ReleaseTransitionError(
            "remote default-branch SHA is not exactly 40 lowercase hex"
        )
    if remote_default_sha != github_sha:
        raise ReleaseTransitionError(
            "remote default branch does not equal the expected evidence commit"
        )
    if SHA256.fullmatch(input_root) is None:
        raise ReleaseTransitionError("dispatch release root is not a lowercase SHA-256")
    if SHA256.fullmatch(approved_root) is None:
        raise ReleaseTransitionError("approved release root is not a lowercase SHA-256")
    if input_root != approved_root:
        raise ReleaseTransitionError(
            "dispatch release root does not equal the approved repository variable"
        )


def _parse_bounded_json(value: bytes, *, purpose: str) -> Any:
    if len(value) > MAX_PR_STATE_BYTES:
        raise ReleaseTransitionError(
            f"{purpose} exceeds {MAX_PR_STATE_BYTES} bytes"
        )
    try:
        return json.loads(value)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseTransitionError(f"invalid {purpose} JSON: {exc}") from exc


def _read_bounded_json_stdin(*, purpose: str) -> Any:
    return _parse_bounded_json(
        sys.stdin.buffer.read(MAX_PR_STATE_BYTES + 1), purpose=purpose
    )


def _read_bounded_json_file(path: Path, *, purpose: str) -> Any:
    try:
        details = path.lstat()
    except OSError as exc:
        raise ReleaseTransitionError(f"cannot inspect {purpose} {path}: {exc}") from exc
    if not stat.S_ISREG(details.st_mode):
        raise ReleaseTransitionError(f"{purpose} is not a regular file: {path}")
    return _parse_bounded_json(
        _bounded_regular_bytes(
            path,
            limit=MAX_PR_STATE_BYTES,
            purpose=purpose,
            expected_identity=_file_identity(details),
        ),
        purpose=purpose,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser(
        "bundle-inventory", help="validate the complete bundle filesystem inventory"
    )
    inventory.add_argument("--bundle", type=Path, default=Path("bundle"))
    inventory.add_argument("--expected-root")

    staged = subparsers.add_parser(
        "staged-evidence", help="validate the exact staged v0.3 evidence surface"
    )
    staged.add_argument("--repository", type=Path, default=Path("."))

    candidate = subparsers.add_parser(
        "staged-candidate",
        help="reject accidental artefacts from the complete staged candidate",
    )
    candidate.add_argument("--repository", type=Path, default=Path("."))

    remote = subparsers.add_parser(
        "remote-binding",
        help="bind one credential-free Git remote to the canonical GitHub repository",
    )
    remote.add_argument("--repository", type=Path, default=Path("."))
    remote.add_argument("--remote", required=True)

    pr_state = subparsers.add_parser(
        "pr-state", help="bind pull-request state and refs to the evidence SHA"
    )
    pr_state.add_argument("--expected-head-oid", required=True)
    pr_state.add_argument("--require-review-decision")

    required_run = subparsers.add_parser(
        "required-check-run-id",
        help="extract the canonical verify workflow-run ID from a checks file",
    )
    required_run.add_argument("--required-checks", type=Path, required=True)

    required = subparsers.add_parser(
        "required-checks",
        help="bind the required check to canonical workflow, run and job metadata",
    )
    required.add_argument("--required-checks", type=Path, required=True)
    required.add_argument("--workflow", type=Path, required=True)
    required.add_argument("--workflow-run", type=Path, required=True)
    required.add_argument("--workflow-jobs", type=Path, required=True)
    required.add_argument("--expected-head-oid", required=True)

    deployment = subparsers.add_parser(
        "deployment-identity",
        help="bind a workflow dispatch to the exact commit and release root",
    )
    deployment.add_argument("--github-sha", required=True)
    deployment.add_argument("--expected-commit-sha", required=True)
    deployment.add_argument("--remote-default-sha", required=True)
    deployment.add_argument("--input-root", required=True)
    deployment.add_argument("--approved-root", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "bundle-inventory":
            print(validate_bundle_inventory(args.bundle, args.expected_root))
        elif args.command == "staged-evidence":
            paths = validate_staged_evidence(args.repository)
            print(f"validated {len(paths)} exact staged v0.3 evidence path(s)")
        elif args.command == "staged-candidate":
            paths = validate_staged_candidate(args.repository)
            print(f"validated {len(paths)} exact staged candidate path(s)")
        elif args.command == "remote-binding":
            validate_remote_binding(args.repository, args.remote)
            print(
                "Git fetch and push destinations are bound to the canonical "
                "credential-free GitHub repository"
            )
        elif args.command == "pr-state":
            validate_pr_state(
                _read_bounded_json_stdin(purpose="pull-request state"),
                expected_head_oid=args.expected_head_oid,
                required_review_decision=args.require_review_decision,
            )
            print("pull-request state is bound to the exact evidence SHA")
        elif args.command == "required-check-run-id":
            print(
                required_check_run_id(
                    _read_bounded_json_file(
                        args.required_checks, purpose="required pull-request checks"
                    )
                )
            )
        elif args.command == "required-checks":
            validate_required_checks(
                _read_bounded_json_file(
                    args.required_checks, purpose="required pull-request checks"
                ),
                workflow=_read_bounded_json_file(
                    args.workflow, purpose="workflow metadata"
                ),
                workflow_run=_read_bounded_json_file(
                    args.workflow_run, purpose="workflow-run metadata"
                ),
                workflow_jobs=_read_bounded_json_file(
                    args.workflow_jobs, purpose="workflow-jobs metadata"
                ),
                expected_head_oid=args.expected_head_oid,
            )
            print("the exact required release workflow check passed once")
        else:
            validate_deployment_identity(
                github_sha=args.github_sha,
                expected_commit_sha=args.expected_commit_sha,
                remote_default_sha=args.remote_default_sha,
                input_root=args.input_root,
                approved_root=args.approved_root,
            )
            print("workflow dispatch is bound to the exact approved release identity")
    except ReleaseTransitionError as exc:
        print(f"release transition check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

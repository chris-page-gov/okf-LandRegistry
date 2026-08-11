#!/usr/bin/env python3
"""Shared, exact Python runtime and hashed-lock contract for release tooling.

The builder and release-metadata producer must observe the same interpreter,
environment, dependency lock, installed distributions and compressor.  This
module is deliberately dependency-free so both direct-script and package
imports execute one authority rather than maintaining subtly different copies.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import gzip
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import re
import site
import stat
import sys
from typing import Any


EXPECTED_PYTHON_IMPLEMENTATION = "CPython"
EXPECTED_PYTHON_VERSION = "3.12.11"
ALLOWED_RUNTIME_BOOTSTRAP_DISTRIBUTIONS: frozenset[str] = frozenset()
DETERMINISTIC_GZIP_CONTRACT = "rfc1952-gzipfile-level9-mtime0-os255-v1"
DETERMINISTIC_GZIP_GOLDEN_SHA256 = (
    "fb2e18c06e2c0e2ce9b0be33acf9ee67eb49a2a2ecd951d910e9020339754ff3"
)
DETERMINISTIC_GZIP_GOLDEN_INPUT = (
    b"OKF deterministic gzip contract v1\n" + bytes(range(256)) * 4 + b"A" * 4096
)
MAX_RUNTIME_FILE_BYTES = 64 * 1024 * 1024
MAX_RUNTIME_AGGREGATE_BYTES = 512 * 1024 * 1024
MAX_RUNTIME_HASHED_FILES = 100_000
MAX_RUNTIME_INVENTORY_PATH_BYTES = 16 * 1024 * 1024
_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9._+!-]*) \\$$"
)
_HASH = re.compile(r"^    --hash=sha256:([0-9a-f]{64})( \\)?$")


class PythonRuntimeContractError(ValueError):
    """Raised when the lock or observed Python runtime is not exact."""


@dataclass(frozen=True)
class LockedPackage:
    """One fully hashed, exactly pinned requirement block."""

    declared_name: str
    normalised_name: str
    version: str
    hashes: tuple[str, ...]


def normalise_distribution_name(value: str) -> str:
    """Return the canonical comparison key used by Python packaging metadata."""

    return re.sub(r"[-_.]+", "-", value).casefold()


def parse_hashed_requirements_lock(lock_bytes: bytes) -> tuple[LockedPackage, ...]:
    """Parse every statement in the governed uv hashed-lock grammar.

    Blank lines and comments are the only ignored forms.  Every other line must
    be an exact pinned requirement or a SHA-256 continuation belonging to that
    requirement.  This makes malformed trailing text and unhashed additions
    fail closed instead of silently disappearing from provenance.
    """

    try:
        text = lock_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PythonRuntimeContractError(
            "requirements-lock.txt is not valid UTF-8"
        ) from exc

    rows: dict[str, LockedPackage] = {}
    current_declared_name: str | None = None
    current_name: str | None = None
    current_version: str | None = None
    current_hashes: set[str] = set()
    expecting_hash_continuation = False

    def close_requirement(line_number: int) -> None:
        nonlocal current_declared_name, current_name, current_version
        nonlocal current_hashes, expecting_hash_continuation
        if current_name is None:
            return
        if not current_hashes:
            raise PythonRuntimeContractError(
                f"requirements lock package lacks hashes: {current_name}"
            )
        if expecting_hash_continuation:
            raise PythonRuntimeContractError(
                "requirements lock hash continuation is truncated before line "
                f"{line_number}: {current_name}"
            )
        assert current_declared_name is not None
        assert current_version is not None
        rows[current_name] = LockedPackage(
            declared_name=current_declared_name,
            normalised_name=current_name,
            version=current_version,
            hashes=tuple(sorted(current_hashes)),
        )
        current_declared_name = None
        current_name = None
        current_version = None
        current_hashes = set()
        expecting_hash_continuation = False

    lines = text.splitlines()
    for line_number, line in enumerate(lines, start=1):
        if not line or line.lstrip().startswith("#"):
            continue
        requirement_match = _REQUIREMENT.fullmatch(line)
        if requirement_match is not None:
            close_requirement(line_number)
            declared_name, version = requirement_match.groups()
            name = normalise_distribution_name(declared_name)
            if name in rows:
                raise PythonRuntimeContractError(
                    f"requirements lock repeats package {declared_name!r}"
                )
            current_declared_name = declared_name
            current_name = name
            current_version = version
            expecting_hash_continuation = True
            continue
        hash_match = _HASH.fullmatch(line)
        if hash_match is not None and current_name is not None:
            if not expecting_hash_continuation:
                raise PythonRuntimeContractError(
                    "requirements lock has a hash after a closed block at line "
                    f"{line_number}: {current_name}"
                )
            digest, continuation = hash_match.groups()
            if digest in current_hashes:
                raise PythonRuntimeContractError(
                    f"requirements lock repeats a hash at line {line_number}: "
                    f"{current_name}"
                )
            current_hashes.add(digest)
            expecting_hash_continuation = bool(continuation)
            continue
        raise PythonRuntimeContractError(
            "requirements lock contains an unsupported non-comment line "
            f"{line_number}: {line!r}"
        )
    close_requirement(len(lines) + 1)
    if not rows:
        raise PythonRuntimeContractError(
            "requirements-lock.txt has no pinned hashed packages"
        )
    return tuple(rows[name] for name in sorted(rows))


def deterministic_gzip_bytes(payload: bytes) -> bytes:
    """Return gzip bytes with fixed metadata under the golden contract."""

    stream = io.BytesIO()
    with gzip.GzipFile(
        filename="",
        mode="wb",
        compresslevel=9,
        fileobj=stream,
        mtime=0,
    ) as handle:
        handle.write(payload)
    compressed = stream.getvalue()
    if len(compressed) < 10 or compressed[:4] != b"\x1f\x8b\x08\x00":
        raise PythonRuntimeContractError(
            "deterministic gzip writer emitted an invalid header"
        )
    if compressed[4:8] != b"\x00\x00\x00\x00" or compressed[9] != 0xFF:
        raise PythonRuntimeContractError(
            "deterministic gzip writer leaked platform header metadata"
        )
    return compressed


class _RuntimeFileHasher:
    """Bounded no-follow hashing capability confined to one virtual environment."""

    def __init__(self, environment: Path) -> None:
        self.environment = Path(os.path.abspath(environment))
        self.aggregate_bytes = 0
        self.hashed_files = 0
        self.inventory: dict[str, tuple[int, str]] = {}

    @staticmethod
    def _identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
            stat.S_IFMT(value.st_mode),
        )

    def sha256(self, path: Path, *, label: str) -> tuple[str, int]:
        absolute = Path(os.path.abspath(path))
        try:
            relative = absolute.relative_to(self.environment)
        except ValueError as exc:
            raise PythonRuntimeContractError(
                f"installed distribution RECORD path escapes .venv: {label}"
            ) from exc
        if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise PythonRuntimeContractError(
                f"installed distribution RECORD path is unsafe: {label}"
            )
        self.hashed_files += 1
        if self.hashed_files > MAX_RUNTIME_HASHED_FILES:
            raise PythonRuntimeContractError(
                "installed distribution RECORD inventory exceeds the governed "
                f"{MAX_RUNTIME_HASHED_FILES}-file limit"
            )

        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_flags |= getattr(os, "O_CLOEXEC", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)
        file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        file_flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptors: list[int] = []
        try:
            descriptors.append(os.open(self.environment, directory_flags))
            for part in relative.parts[:-1]:
                descriptors.append(os.open(part, directory_flags, dir_fd=descriptors[-1]))
            descriptor = os.open(
                relative.parts[-1],
                file_flags,
                dir_fd=descriptors[-1],
            )
            descriptors.append(descriptor)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise PythonRuntimeContractError(
                    f"installed distribution RECORD member is not a regular file: {label}"
                )
            if before.st_size > MAX_RUNTIME_FILE_BYTES:
                raise PythonRuntimeContractError(
                    "installed distribution RECORD member exceeds the governed "
                    f"{MAX_RUNTIME_FILE_BYTES}-byte limit: {label}"
                )
            if self.aggregate_bytes + before.st_size > MAX_RUNTIME_AGGREGATE_BYTES:
                raise PythonRuntimeContractError(
                    "installed distribution RECORD bytes exceed the governed "
                    f"{MAX_RUNTIME_AGGREGATE_BYTES}-byte aggregate limit"
                )
            digest = hashlib.sha256()
            observed = 0
            while True:
                chunk = os.read(
                    descriptor,
                    min(1024 * 1024, MAX_RUNTIME_FILE_BYTES + 1 - observed),
                )
                if not chunk:
                    break
                observed += len(chunk)
                if observed > MAX_RUNTIME_FILE_BYTES:
                    raise PythonRuntimeContractError(
                        "installed distribution RECORD member exceeds the governed "
                        f"{MAX_RUNTIME_FILE_BYTES}-byte limit: {label}"
                    )
                digest.update(chunk)
            after = os.fstat(descriptor)
            if observed != before.st_size or self._identity(before) != self._identity(after):
                raise PythonRuntimeContractError(
                    f"installed distribution RECORD member changed while hashed: {label}"
                )
            digest_hex = digest.hexdigest()
            inventory_name = relative.as_posix()
            if inventory_name in self.inventory:
                raise PythonRuntimeContractError(
                    "installed distribution inventories overlap at: "
                    f"{inventory_name}"
                )
            self.inventory[inventory_name] = (observed, digest_hex)
            self.aggregate_bytes += observed
            return digest_hex, observed
        except PythonRuntimeContractError:
            raise
        except OSError as exc:
            raise PythonRuntimeContractError(
                f"installed distribution RECORD member cannot be opened safely: {label}"
            ) from exc
        finally:
            for descriptor in reversed(descriptors):
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _verify_distribution_record(
    distribution: importlib.metadata.Distribution,
    *,
    hasher: _RuntimeFileHasher,
) -> dict[str, str]:
    files = distribution.files
    distribution_name = distribution.metadata.get("Name") or "<unnamed>"
    if files is None:
        raise PythonRuntimeContractError(
            f"installed distribution has no RECORD inventory: {distribution_name}"
        )
    verified = 0
    record_rows: list[tuple[str, str]] = []
    located_members: set[Path] = set()
    for member in files:
        member_path = Path(os.path.abspath(distribution.locate_file(member)))
        if member_path in located_members:
            raise PythonRuntimeContractError(
                "installed distribution RECORD repeats a path: "
                f"{distribution_name}:{member}"
            )
        located_members.add(member_path)
        recorded_hash = member.hash
        if recorded_hash is None:
            if not (
                member.name == "RECORD"
                and member.parent.name.casefold().endswith(".dist-info")
                and member.size is None
            ):
                raise PythonRuntimeContractError(
                    "installed distribution RECORD contains an unhashed member: "
                    f"{distribution_name}:{member}"
                )
            observed_hex, _observed_size = hasher.sha256(
                member_path,
                label=f"{distribution_name}:{member}",
            )
            record_rows.append((member.as_posix(), observed_hex))
            verified += 1
            continue
        if recorded_hash.mode != "sha256":
            raise PythonRuntimeContractError(
                "installed distribution RECORD uses an unsupported digest: "
                f"{distribution_name}:{member}"
            )
        if type(member.size) is not int or member.size < 0:
            raise PythonRuntimeContractError(
                "installed distribution RECORD member lacks an exact byte count: "
                f"{distribution_name}:{member}"
            )
        observed_hex, observed_size = hasher.sha256(
            member_path,
            label=f"{distribution_name}:{member}",
        )
        if observed_size != member.size:
            raise PythonRuntimeContractError(
                "installed distribution file size differs from RECORD: "
                f"{distribution_name}:{member}"
            )
        observed = base64.urlsafe_b64encode(bytes.fromhex(observed_hex)).decode(
            "ascii"
        ).rstrip("=")
        if observed != recorded_hash.value:
            raise PythonRuntimeContractError(
                "installed distribution file differs from RECORD: "
                f"{distribution_name}:{member}"
            )
        verified += 1
    if verified == 0:
        raise PythonRuntimeContractError(
            "installed distribution has no hashed RECORD members: "
            f"{distribution_name}"
        )
    if len(record_rows) != 1:
        raise PythonRuntimeContractError(
            "installed distribution must contain exactly one narrowly unhashed "
            f"RECORD self-entry: {distribution_name}"
        )
    record_path, record_sha256 = record_rows[0]
    return {
        "distribution": normalise_distribution_name(distribution_name),
        "path": record_path,
        "sha256": record_sha256,
    }


def _located_distribution_files(
    distribution: importlib.metadata.Distribution,
    *,
    environment: Path,
) -> set[Path]:
    files = distribution.files
    distribution_name = distribution.metadata.get("Name") or "<unnamed>"
    if files is None:
        raise PythonRuntimeContractError(
            f"installed distribution has no RECORD inventory: {distribution_name}"
        )
    located: set[Path] = set()
    for member in files:
        path = Path(os.path.abspath(distribution.locate_file(member)))
        try:
            path.relative_to(environment)
        except ValueError as exc:
            raise PythonRuntimeContractError(
                "installed distribution inventory escapes .venv: "
                f"{distribution_name}:{member}"
            ) from exc
        located.add(path)
    return located


def _verify_private_empty_pycache_prefix(repository_root: Path) -> None:
    """Verify the pre-invocation cache namespace cannot supply repository pyc."""

    if sys.flags.isolated != 1 or sys.flags.ignore_environment != 1:
        raise PythonRuntimeContractError(
            "release tooling requires Python isolated mode (-I)"
        )
    if not sys.dont_write_bytecode:
        raise PythonRuntimeContractError(
            "release tooling requires bytecode writes disabled (-B)"
        )
    value = sys.pycache_prefix
    if not isinstance(value, str) or not value:
        raise PythonRuntimeContractError(
            "release tooling requires -X pycache_prefix=<private-empty-directory>"
        )
    prefix = Path(value)
    if not prefix.is_absolute():
        raise PythonRuntimeContractError(
            "release-tool pycache prefix must be an absolute directory"
        )
    try:
        before = prefix.lstat()
    except OSError as exc:
        raise PythonRuntimeContractError(
            "release-tool pycache prefix cannot be inspected safely"
        ) from exc
    if not stat.S_ISDIR(before.st_mode):
        raise PythonRuntimeContractError(
            "release-tool pycache prefix must be a real directory, not a link"
        )
    resolved = prefix.resolve(strict=True)
    governed_roots = (
        repository_root.resolve(),
        (repository_root / ".venv").resolve(),
        Path(sys.base_prefix).resolve(),
    )
    if any(resolved == root or root in resolved.parents for root in governed_roots):
        raise PythonRuntimeContractError(
            "release-tool pycache prefix must be private and outside governed roots"
        )
    if stat.S_IMODE(before.st_mode) & 0o077 or before.st_uid != os.geteuid():
        raise PythonRuntimeContractError(
            "release-tool pycache prefix must be an owned private 0700 directory"
        )
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(prefix, directory_flags)
    try:
        opened = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise PythonRuntimeContractError(
                "release-tool pycache prefix changed while being opened"
            )
        with os.scandir(descriptor) as entries:
            populated = next(entries, None) is not None
        after = os.fstat(descriptor)
        after_path = prefix.lstat()
        if (
            (opened.st_dev, opened.st_ino, opened.st_mode)
            != (after.st_dev, after.st_ino, after.st_mode)
            or (opened.st_dev, opened.st_ino, opened.st_mode)
            != (after_path.st_dev, after_path.st_ino, after_path.st_mode)
        ):
            raise PythonRuntimeContractError(
                "release-tool pycache prefix changed during startup verification"
            )
        if populated:
            raise PythonRuntimeContractError(
                "release-tool pycache prefix was not empty at observer start"
            )
    finally:
        os.close(descriptor)


def _verify_exact_site_packages_inventory(
    site_paths: list[Path],
    *,
    owned_files: set[Path],
) -> None:
    """Reject startup hooks, symlinks, pyc and every non-RECORD site file."""

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    count = 0
    path_bytes = 0
    for site_path in site_paths:
        root = Path(os.path.abspath(site_path))
        root_descriptor = os.open(root, directory_flags)
        stack: list[tuple[int, tuple[str, ...]]] = [(root_descriptor, ())]
        try:
            while stack:
                directory, parent_parts = stack.pop()
                try:
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            name = entry.name
                            if not name or "/" in name or name in {".", ".."}:
                                raise PythonRuntimeContractError(
                                    "site-packages contains an unsafe directory entry"
                                )
                            parts = (*parent_parts, name)
                            relative = "/".join(parts)
                            count += 1
                            path_bytes += len(relative.encode("utf-8")) + 1
                            if count > MAX_RUNTIME_HASHED_FILES:
                                raise PythonRuntimeContractError(
                                    "site-packages inventory exceeds the governed "
                                    f"{MAX_RUNTIME_HASHED_FILES}-entry limit"
                                )
                            if path_bytes > MAX_RUNTIME_INVENTORY_PATH_BYTES:
                                raise PythonRuntimeContractError(
                                    "site-packages path inventory exceeds the governed "
                                    "byte limit"
                                )
                            metadata = os.stat(
                                name,
                                dir_fd=directory,
                                follow_symlinks=False,
                            )
                            if stat.S_ISDIR(metadata.st_mode):
                                child = os.open(name, directory_flags, dir_fd=directory)
                                stack.append((child, parts))
                                continue
                            if not stat.S_ISREG(metadata.st_mode):
                                raise PythonRuntimeContractError(
                                    "site-packages contains a symbolic link or special "
                                    f"file: {relative}"
                                )
                            absolute = root.joinpath(*parts)
                            lowered = name.casefold()
                            if (
                                lowered.endswith(".pth")
                                or lowered.startswith("sitecustomize.")
                                or lowered.startswith("usercustomize.")
                                or lowered.endswith(".pyc")
                            ):
                                raise PythonRuntimeContractError(
                                    "site-packages contains a prohibited startup or "
                                    f"bytecode artefact: {relative}"
                                )
                            if absolute not in owned_files:
                                raise PythonRuntimeContractError(
                                    "site-packages contains a file absent from every "
                                    f"locked distribution inventory: {relative}"
                                )
                finally:
                    os.close(directory)
        finally:
            for descriptor, _parts in stack:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _assemble_runtime_receipts(
    *,
    lock_bytes: bytes,
    packages: tuple[LockedPackage, ...],
    implementation: str,
    version: str,
    golden_sha256: str,
    installed_inventory: dict[str, tuple[int, str]],
    record_receipts: list[dict[str, str]],
    platform_system: str,
    platform_machine: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate portable build identity from platform-specific audit detail."""

    installed_tree_lines = [
        f"{path}\t{size}\t{digest}\n"
        for path, (size, digest) in sorted(installed_inventory.items())
    ]
    installed_tree_sha256 = hashlib.sha256(
        "".join(installed_tree_lines).encode("utf-8")
    ).hexdigest()
    package_rows = [
        {"name": package.normalised_name, "version": package.version}
        for package in packages
    ]
    distribution_identity_sha256 = hashlib.sha256(
        "".join(
            f"{row['name']}=={row['version']}\n" for row in package_rows
        ).encode("utf-8")
    ).hexdigest()
    portable_receipt = {
        "schema": "okf-python-runtime.v1",
        "executable_contract": ".venv/bin/python",
        "virtual_environment_contract": ".venv",
        "implementation": implementation,
        "version": version,
        "requirements_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
        "packages": package_rows,
        "distribution_identity_sha256": distribution_identity_sha256,
        "installed_record_hashes": "verified",
        "installed_closure_assurance": (
            "exact-site-inventory-record-sha256-size-and-self-record-verified-v1"
        ),
        "python_user_site": "disabled",
        "python_path_contract": "stdlib-venv-repository-only",
        "python_startup_contract": (
            "post-startup-verified-isolated-private-empty-pycache-no-hooks-v1"
        ),
        "preimport_assurance": (
            "Python startup and initial module imports precede this in-process "
            "observer and build-input capture. Isolated mode, an empty external "
            "cache prefix and exact site-packages closure are verified before "
            "release work continues, but pre-observer executed source bytes are "
            "not independently attested and require the single-writer "
            "pre-invocation contract."
        ),
        "source_execution_assurance": (
            "post-import-capture-single-writer-precondition-no-preobserver-"
            "source-byte-attestation-v1"
        ),
        "gzip_contract": DETERMINISTIC_GZIP_CONTRACT,
        "gzip_golden_sha256": golden_sha256,
    }
    portable_bytes = json.dumps(
        portable_receipt,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    local_audit = {
        "schema": "okf-python-runtime-local-audit.v1",
        "portable_contract_sha256": hashlib.sha256(portable_bytes).hexdigest(),
        "platform": {
            "system": platform_system,
            "machine": platform_machine,
        },
        "installed_record_receipts": record_receipts,
        "installed_tree": {
            "file_count": len(installed_inventory),
            "bytes": sum(size for size, _digest in installed_inventory.values()),
            "sha256": installed_tree_sha256,
        },
    }
    return portable_receipt, local_audit


def _observe_python_runtime_receipts(
    repository_root: Path,
    lock_bytes: bytes,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Post-startup verify once, returning portable and local audit receipts."""

    root = repository_root.resolve()
    _verify_private_empty_pycache_prefix(root)
    expected_environment = root / ".venv"
    expected_executable = expected_environment / "bin" / "python"
    invoked_executable = Path(sys.executable).absolute()
    if invoked_executable != expected_executable:
        raise PythonRuntimeContractError(
            "release tooling must run with the repository's .venv/bin/python "
            "interpreter"
        )
    if Path(sys.prefix).resolve() != expected_environment.resolve():
        raise PythonRuntimeContractError(
            "release-tool Python prefix is not the repository virtual environment"
        )
    if sys.prefix == sys.base_prefix:
        raise PythonRuntimeContractError(
            "release-tool Python interpreter is not running in a virtual environment"
        )
    implementation = platform.python_implementation()
    version = platform.python_version()
    if (
        implementation != EXPECTED_PYTHON_IMPLEMENTATION
        or version != EXPECTED_PYTHON_VERSION
    ):
        raise PythonRuntimeContractError(
            "release tooling requires exactly CPython 3.12.11: "
            f"observed {implementation} {version}"
        )
    if site.ENABLE_USER_SITE is not False:
        raise PythonRuntimeContractError(
            "release tooling must run with the Python user site disabled"
        )

    prohibited_environment = sorted(
        name
        for name in (
            "PYTHONHOME",
            "PYTHONINSPECT",
            "PYTHONPATH",
            "PYTHONPYCACHEPREFIX",
            "PYTHONSAFEPATH",
            "PYTHONSTARTUP",
            "PYTHONDONTWRITEBYTECODE",
        )
        if os.environ.get(name)
    )
    if prohibited_environment:
        raise PythonRuntimeContractError(
            "release-tool Python environment contains prohibited startup controls: "
            + ", ".join(prohibited_environment)
        )

    allowed_roots = (
        root,
        root / "scripts",
        Path(sys.base_prefix),
        expected_environment,
    )
    unexpected_paths: list[str] = []
    for raw_path in sys.path:
        candidate = Path(raw_path or os.getcwd()).absolute()
        if not any(
            candidate == allowed or allowed in candidate.parents
            for allowed in allowed_roots
        ):
            unexpected_paths.append(str(candidate))
    if unexpected_paths:
        raise PythonRuntimeContractError(
            "release-tool Python import path contains locations outside the "
            "governed runtime: " + ", ".join(sorted(set(unexpected_paths)))
        )

    parsed = parse_hashed_requirements_lock(lock_bytes)
    locked = {package.normalised_name: package for package in parsed}
    environment_site_paths: list[Path] = []
    for value in site.getsitepackages():
        candidate = Path(value).resolve()
        if candidate == expected_environment or expected_environment in candidate.parents:
            environment_site_paths.append(candidate)
    if not environment_site_paths:
        raise PythonRuntimeContractError(
            "release-tool virtual environment has no governed site-packages path"
        )
    installed: dict[str, importlib.metadata.Distribution] = {}
    for distribution in importlib.metadata.distributions(
        path=[str(path) for path in sorted(set(environment_site_paths))]
    ):
        distribution_name = distribution.metadata.get("Name")
        if not distribution_name:
            raise PythonRuntimeContractError(
                "installed distribution lacks a package name"
            )
        name = normalise_distribution_name(distribution_name)
        if name in installed:
            raise PythonRuntimeContractError(
                f"installed distribution is duplicated: {name}"
            )
        installed[name] = distribution

    unexpected = sorted(
        set(installed)
        - set(locked)
        - ALLOWED_RUNTIME_BOOTSTRAP_DISTRIBUTIONS
    )
    missing = sorted(set(locked) - set(installed))
    divergent = sorted(
        name
        for name, package in locked.items()
        if name in installed and installed[name].version != package.version
    )
    if missing or unexpected or divergent:
        raise PythonRuntimeContractError(
            "installed Python distributions differ from requirements-lock.txt: "
            f"missing={missing!r}, unexpected={unexpected!r}, "
            f"version_mismatch={divergent!r}"
        )

    hasher = _RuntimeFileHasher(expected_environment)
    verified_names = set(locked) | (
        set(installed) & ALLOWED_RUNTIME_BOOTSTRAP_DISTRIBUTIONS
    )
    owned_files: set[Path] = set()
    record_receipts: list[dict[str, str]] = []
    for name in sorted(verified_names):
        distribution_files = _located_distribution_files(
            installed[name],
            environment=expected_environment,
        )
        overlap = owned_files & distribution_files
        if overlap:
            first = min(overlap, key=lambda path: path.as_posix())
            raise PythonRuntimeContractError(
                "installed distribution inventories overlap at: "
                f"{first.relative_to(expected_environment).as_posix()}"
            )
        owned_files.update(distribution_files)
        record_receipts.append(
            _verify_distribution_record(installed[name], hasher=hasher)
        )
    _verify_exact_site_packages_inventory(
        environment_site_paths,
        owned_files=owned_files,
    )
    prohibited_customisers = sorted(
        name for name in ("sitecustomize", "usercustomize") if name in sys.modules
    )
    if prohibited_customisers:
        raise PythonRuntimeContractError(
            "release-tool startup imported prohibited customisers: "
            + ", ".join(prohibited_customisers)
        )

    golden_sha256 = hashlib.sha256(
        deterministic_gzip_bytes(DETERMINISTIC_GZIP_GOLDEN_INPUT)
    ).hexdigest()
    if golden_sha256 != DETERMINISTIC_GZIP_GOLDEN_SHA256:
        raise PythonRuntimeContractError(
            "runtime compressor does not satisfy the deterministic gzip golden "
            "contract"
        )
    if hasher.hashed_files != len(hasher.inventory):
        raise PythonRuntimeContractError(
            "installed runtime hashing did not produce an exact file inventory"
        )
    return _assemble_runtime_receipts(
        lock_bytes=lock_bytes,
        packages=parsed,
        implementation=implementation,
        version=version,
        golden_sha256=golden_sha256,
        installed_inventory=hasher.inventory,
        record_receipts=record_receipts,
        platform_system=platform.system(),
        platform_machine=platform.machine(),
    )


def observe_python_runtime(
    repository_root: Path,
    lock_bytes: bytes,
) -> dict[str, Any]:
    """Post-startup verify the runtime and return its portable contract."""

    portable, _local_audit = _observe_python_runtime_receipts(
        repository_root,
        lock_bytes,
    )
    return portable


def audit_python_runtime(
    repository_root: Path,
    lock_bytes: bytes,
) -> dict[str, Any]:
    """Return verified platform detail for separate, non-bundle local evidence."""

    portable, local_audit = _observe_python_runtime_receipts(
        repository_root,
        lock_bytes,
    )
    return {
        "portable_contract": portable,
        "local_audit": local_audit,
    }

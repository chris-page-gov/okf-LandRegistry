#!/usr/bin/env python3
"""Create a deterministic ZIP archive from an already verified bundle."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import BinaryIO
import zipfile

try:
    from scripts.check_release_approval import (
        ROOT_MARKER,
        ReleaseApprovalError,
        validate_release_approval,
    )
    from scripts.check_release_evidence import (
        CandidateIdentity,
        MAX_EVIDENCE_BYTES,
        ReleaseEvidenceError,
        candidate_identity_from_repository,
        load_json,
        validate_governed_candidate_commit,
    )
except ModuleNotFoundError:  # Direct `python scripts/package_release.py` execution.
    from check_release_approval import (  # type: ignore[no-redef]
        ROOT_MARKER,
        ReleaseApprovalError,
        validate_release_approval,
    )
    from check_release_evidence import (  # type: ignore[no-redef]
        CandidateIdentity,
        MAX_EVIDENCE_BYTES,
        ReleaseEvidenceError,
        candidate_identity_from_repository,
        load_json,
        validate_governed_candidate_commit,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = ROOT / "bundle"
DEFAULT_CONFIG = ROOT / "source" / "build-config.json"
ARCHIVE_COPY_CHUNK_BYTES = 64 * 1024
MAX_RELEASE_ARCHIVE_BYTES = MAX_EVIDENCE_BYTES


class ReleasePackagingError(ValueError):
    """Raised when a release archive cannot be assembled safely."""


def sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def governed_configuration(config_path: Path, bundle: Path) -> dict[str, object]:
    """Load the exact build configuration bound into the candidate receipt."""

    config = config_path.resolve()
    candidate_bundle = bundle.resolve()
    if (
        config != DEFAULT_CONFIG.resolve()
        or config_path.is_symlink()
        or not config.is_file()
    ):
        raise ReleasePackagingError(
            "release packaging requires the repository's regular "
            "source/build-config.json"
        )
    if (
        candidate_bundle != DEFAULT_BUNDLE.resolve()
        or bundle.is_symlink()
        or not candidate_bundle.is_dir()
    ):
        raise ReleasePackagingError(
            "release packaging requires the repository's regular bundle directory"
        )
    build_receipt = load_json(candidate_bundle / "build-receipt.json")
    governed_inputs = build_receipt.get("governed_inputs")
    if not isinstance(governed_inputs, list):
        raise ReleasePackagingError(
            "bundle build receipt has no governed input inventory"
        )
    rows = [
        row
        for row in governed_inputs
        if isinstance(row, dict) and row.get("path") == "source/build-config.json"
    ]
    if len(rows) != 1:
        raise ReleasePackagingError(
            "bundle build receipt must bind exactly one source/build-config.json"
        )
    config_bytes = config.read_bytes()
    if (
        rows[0].get("bytes") != len(config_bytes)
        or rows[0].get("sha256") != hashlib.sha256(config_bytes).hexdigest()
    ):
        raise ReleasePackagingError(
            "build configuration does not match its governed build-receipt binding"
        )
    return load_json(config)


def governed_candidate(candidate_commit_sha: str) -> CandidateIdentity:
    """Validate the exact frozen candidate before creating any G8 archive."""

    validate_governed_candidate_commit(
        ROOT,
        candidate_commit_sha=candidate_commit_sha,
        build_receipt_path=Path("bundle/build-receipt.json"),
    )
    return candidate_identity_from_repository(
        ROOT,
        checksums_path=Path("bundle/CHECKSUMS.sha256"),
        profile_checksums_path=Path("domain-profile/CHECKSUMS.sha256"),
        build_receipt_path=Path("bundle/build-receipt.json"),
        candidate_commit_sha=candidate_commit_sha,
    )


def declared_release_root(checksums_path: Path) -> str:
    roots = [
        line.removeprefix(ROOT_MARKER)
        for line in checksums_path.read_text(encoding="utf-8").splitlines()
        if line.startswith(ROOT_MARKER)
    ]
    if len(roots) != 1:
        raise ReleasePackagingError(
            "bundle checksum manifest must declare exactly one release root"
        )
    return roots[0]


def archive_timestamp(value: str) -> tuple[int, int, int, int, int, int]:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ReleasePackagingError(
            "archive timestamp must be an ISO 8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise ReleasePackagingError("archive timestamp must include a timezone")
    utc = parsed.astimezone(timezone.utc)
    if utc.year < 1980:
        raise ReleasePackagingError("ZIP timestamps cannot predate 1980")
    return (utc.year, utc.month, utc.day, utc.hour, utc.minute, utc.second)


def bundle_files(bundle: Path) -> list[Path]:
    if not bundle.is_dir() or bundle.is_symlink():
        raise ReleasePackagingError("bundle must be a real directory")
    files: list[Path] = []
    for path in sorted(bundle.rglob("*"), key=lambda item: item.as_posix()):
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise ReleasePackagingError(
                "release archive cannot inspect bundle member: "
                f"{path.relative_to(bundle)}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise ReleasePackagingError(
                f"release archive refuses symbolic link: {path.relative_to(bundle)}"
            )
        if stat.S_ISREG(metadata.st_mode):
            files.append(path)
        elif not stat.S_ISDIR(metadata.st_mode):
            raise ReleasePackagingError(
                "release archive refuses non-regular bundle member: "
                f"{path.relative_to(bundle)}"
            )
    if not files:
        raise ReleasePackagingError("release archive refuses an empty bundle")
    return files


def _same_file_identity(first: os.stat_result, second: os.stat_result) -> bool:
    """Return whether two observations describe the same unchanged file."""

    return (
        first.st_dev,
        first.st_ino,
        first.st_mode,
        first.st_size,
        first.st_mtime_ns,
    ) == (
        second.st_dev,
        second.st_ino,
        second.st_mode,
        second.st_size,
        second.st_mtime_ns,
    )


@contextmanager
def _open_bundle_member(
    bundle: Path, relative: Path
) -> Iterator[tuple[BinaryIO, os.stat_result]]:
    """Open one regular bundle member through no-follow directory handles."""

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    file_flags |= getattr(os, "O_NOFOLLOW", 0)
    file_flags |= getattr(os, "O_NONBLOCK", 0)

    directory_descriptors: list[int] = []
    file_descriptor: int | None = None
    handle = None
    try:
        current = os.open(bundle, directory_flags)
        directory_descriptors.append(current)
        for part in relative.parts[:-1]:
            current = os.open(part, directory_flags, dir_fd=current)
            directory_descriptors.append(current)

        leaf = relative.name
        before = os.stat(leaf, dir_fd=current, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise ReleasePackagingError(
                f"release archive refuses non-regular bundle member: {relative}"
            )
        file_descriptor = os.open(leaf, file_flags, dir_fd=current)
        opened = os.fstat(file_descriptor)
        if not _same_file_identity(before, opened):
            raise ReleasePackagingError(
                f"bundle member changed while being opened: {relative}"
            )
        handle = os.fdopen(file_descriptor, "rb", closefd=True)
        file_descriptor = None
        yield handle, opened

        after_read = os.fstat(handle.fileno())
        after_path = os.stat(leaf, dir_fd=current, follow_symlinks=False)
        if not (
            _same_file_identity(opened, after_read)
            and _same_file_identity(opened, after_path)
        ):
            raise ReleasePackagingError(
                f"bundle member changed while being archived: {relative}"
            )
    except ReleasePackagingError:
        raise
    except OSError as exc:
        raise ReleasePackagingError(
            f"release archive cannot safely read bundle member: {relative}"
        ) from exc
    finally:
        if handle is not None:
            handle.close()
        if file_descriptor is not None:
            os.close(file_descriptor)
        for descriptor in reversed(directory_descriptors):
            os.close(descriptor)


def _write_bundle_member(
    archive: zipfile.ZipFile,
    *,
    bundle: Path,
    path: Path,
    archive_prefix: str,
    fixed_time: tuple[int, int, int, int, int, int],
) -> None:
    """Stream one verified member into its deterministic ZIP entry."""

    relative = path.relative_to(bundle)
    with _open_bundle_member(bundle, relative) as opened:
        source, identity = opened
        info = zipfile.ZipInfo(
            filename=f"{archive_prefix}/{relative.as_posix()}",
            date_time=fixed_time,
        )
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = (0o100644 & 0xFFFF) << 16
        info.file_size = identity.st_size
        copied = 0
        with archive.open(info, mode="w", force_zip64=False) as destination:
            while True:
                chunk = source.read(ARCHIVE_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                copied += len(chunk)
                destination.write(chunk)
        if copied != identity.st_size:
            raise ReleasePackagingError(
                f"bundle member size changed while being archived: {relative}"
            )


def create_release_archive(
    *,
    bundle: Path,
    output: Path,
    version: str,
    release_at: str,
) -> dict[str, object]:
    if not version or "/" in version or "\\" in version or version in {".", ".."}:
        raise ReleasePackagingError("version is not safe for an archive prefix")

    bundle = bundle.resolve()
    checksums_path = bundle / "CHECKSUMS.sha256"
    expected_root = declared_release_root(checksums_path)
    try:
        verified_root = validate_release_approval(checksums_path, expected_root)
    except (OSError, UnicodeError, ReleaseApprovalError) as exc:
        raise ReleasePackagingError(f"bundle verification failed: {exc}") from exc

    files = bundle_files(bundle)
    fixed_time = archive_timestamp(release_at)
    if output.is_symlink():
        raise ReleasePackagingError("release archive output must not be a symlink")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not output.is_file():
        raise ReleasePackagingError("release archive output must be a regular file")

    archive_prefix = f"okf-landregistry-{version}"
    descriptor = {
        "version": version,
        "release_at": release_at,
        "release_root_sha256": verified_root,
    }

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".okf-release-",
            suffix=".zip",
            dir=output.parent,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
        with zipfile.ZipFile(
            temporary_name,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            for path in files:
                _write_bundle_member(
                    archive,
                    bundle=bundle,
                    path=path,
                    archive_prefix=archive_prefix,
                    fixed_time=fixed_time,
                )
        archive_bytes = Path(temporary_name).stat().st_size
        if archive_bytes > MAX_RELEASE_ARCHIVE_BYTES:
            raise ReleasePackagingError(
                "release archive exceeds the 50,000,000-byte G8 evidence "
                f"ceiling: {archive_bytes} bytes"
            )
        os.replace(temporary_name, output)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)

    return {
        "schema": "okf-hmlr-release-archive.v1",
        **descriptor,
        "path": output.as_posix(),
        "bytes": output.stat().st_size,
        "sha256": sha256_file(output),
    }


def create_candidate_archive(
    *,
    bundle: Path,
    output: Path,
    version: str,
    candidate_at: str,
) -> dict[str, object]:
    """Package reviewed candidate bytes without asserting publication.

    G8 must bind the archive before G9 can approve it. Candidate configuration
    therefore keeps ``release_at`` null and uses its deterministic
    ``generated_at`` value only as the ZIP member timestamp.
    """

    result = create_release_archive(
        bundle=bundle,
        output=output,
        version=version,
        release_at=candidate_at,
    )
    result.pop("release_at")
    result["schema"] = "okf-hmlr-candidate-archive.v1"
    result["candidate_at"] = candidate_at
    result["publication_state"] = "unreleased-candidate"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--candidate-commit-sha",
        help="exact frozen governed candidate commit",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--receipt",
        type=Path,
        help="optional path for deterministic archive metadata JSON",
    )
    args = parser.parse_args()

    try:
        diagnostic_config = load_json(args.config)
        version = diagnostic_config["version"]
        release_at = diagnostic_config.get("release_at")
        candidate_at = diagnostic_config.get("generated_at")
        if diagnostic_config.get("status") != "ai-generated-proof-of-concept":
            raise ReleasePackagingError(
                "build configuration does not declare the required "
                "AI-generated proof-of-concept status"
            )
        if not isinstance(args.candidate_commit_sha, str):
            raise ReleasePackagingError(
                "release packaging requires --candidate-commit-sha"
            )
        config = governed_configuration(args.config, args.bundle)
        candidate = governed_candidate(args.candidate_commit_sha)
        declared_root = declared_release_root(
            args.bundle.resolve() / "CHECKSUMS.sha256"
        )
        if declared_root != candidate.release_root_sha256:
            raise ReleasePackagingError(
                "bundle release root differs from the governed candidate"
            )
        version = config["version"]
        release_at = config.get("release_at")
        candidate_at = config.get("generated_at")
        output = (
            args.output
            if args.output is not None
            else ROOT / "dist" / f"okf-landregistry-{version}.zip"
        )
        if isinstance(release_at, str):
            result = create_release_archive(
                bundle=args.bundle,
                output=output,
                version=version,
                release_at=release_at,
            )
        elif (
            release_at is None
            and config.get("publication_state")
            == "digest-bound-external-evidence"
            and isinstance(candidate_at, str)
        ):
            result = create_candidate_archive(
                bundle=args.bundle,
                output=output,
                version=version,
                candidate_at=candidate_at,
            )
        else:
            raise ReleasePackagingError(
                "build configuration supplies neither a released timestamp nor a "
                "digest-bound candidate timestamp"
            )
        result["candidate"] = asdict(candidate)
        try:
            result["path"] = Path(str(result["path"])).relative_to(ROOT).as_posix()
        except ValueError:
            pass
        if args.receipt is not None:
            receipt = args.receipt.resolve()
            if ROOT not in receipt.parents or receipt.is_symlink():
                raise ReleasePackagingError(
                    "archive receipt must be a non-symlinked file inside the repository"
                )
            receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    except (
        KeyError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ReleasePackagingError,
        ReleaseEvidenceError,
    ) as exc:
        print(f"release packaging failed closed: {exc}")
        return 1

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

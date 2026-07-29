#!/usr/bin/env python3
"""Fail closed unless the built release root matches repository approval."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
APPROVAL_VARIABLE = "OKF_RELEASE_ROOT_SHA256"
ROOT_MARKER = "# release-root-sha256: "
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ReleaseApprovalError(ValueError):
    """Raised when release bytes and the approved root do not match."""


def sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def artifact_path(bundle_root: Path, name: str) -> Path:
    if not name or "\\" in name:
        raise ReleaseApprovalError(f"unsafe checksum path: {name!r}")
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ReleaseApprovalError(f"unsafe checksum path: {name!r}")

    artifact = bundle_root.joinpath(*relative.parts)
    resolved_root = bundle_root.resolve()
    resolved_artifact = artifact.resolve()
    if resolved_root not in resolved_artifact.parents:
        raise ReleaseApprovalError(f"checksum path escapes bundle: {name!r}")
    if artifact.is_symlink():
        raise ReleaseApprovalError(f"checksum path is a symbolic link: {name!r}")
    if not artifact.is_file():
        raise ReleaseApprovalError(f"checksummed artifact is missing: {name!r}")
    return artifact


def validate_release_approval(checksums_path: Path, expected_root: str) -> str:
    if not expected_root:
        raise ReleaseApprovalError(
            f"{APPROVAL_VARIABLE} must be a non-empty lowercase SHA-256 digest"
        )
    if SHA256.fullmatch(expected_root) is None:
        raise ReleaseApprovalError(
            f"{APPROVAL_VARIABLE} must be exactly 64 lowercase hexadecimal characters"
        )
    if not checksums_path.is_file():
        raise ReleaseApprovalError(f"checksum manifest is missing: {checksums_path}")

    digest_lines: list[str] = []
    declared_roots: list[str] = []
    seen_paths: set[str] = set()
    for line_number, line in enumerate(
        checksums_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if line.startswith(ROOT_MARKER):
            declared_roots.append(line.removeprefix(ROOT_MARKER))
            continue
        if not line:
            raise ReleaseApprovalError(
                f"{checksums_path}:{line_number}: blank lines are not allowed"
            )
        if line.startswith("#"):
            raise ReleaseApprovalError(
                f"{checksums_path}:{line_number}: unsupported checksum comment"
            )
        try:
            digest, name = line.split("  ", 1)
        except ValueError as exc:
            raise ReleaseApprovalError(
                f"{checksums_path}:{line_number}: expected '<sha256>  <path>'"
            ) from exc
        if SHA256.fullmatch(digest) is None:
            raise ReleaseApprovalError(
                f"{checksums_path}:{line_number}: invalid artifact SHA-256"
            )
        if name in seen_paths:
            raise ReleaseApprovalError(
                f"{checksums_path}:{line_number}: duplicate artifact path {name!r}"
            )
        seen_paths.add(name)
        artifact = artifact_path(checksums_path.parent, name)
        actual_digest = sha256_file(artifact)
        if actual_digest != digest:
            raise ReleaseApprovalError(
                f"artifact digest mismatch for {name!r}: "
                f"declared {digest}, calculated {actual_digest}"
            )
        digest_lines.append(line)

    if not digest_lines:
        raise ReleaseApprovalError("checksum manifest contains no artifact entries")
    if len(declared_roots) != 1 or SHA256.fullmatch(declared_roots[0]) is None:
        raise ReleaseApprovalError(
            "checksum manifest must contain exactly one valid release-root marker"
        )

    manifest = ("\n".join(digest_lines) + "\n").encode("utf-8")
    calculated_root = hashlib.sha256(manifest).hexdigest()
    if declared_roots[0] != calculated_root:
        raise ReleaseApprovalError(
            "declared release root does not match the checksum manifest: "
            f"declared {declared_roots[0]}, calculated {calculated_root}"
        )
    if expected_root != calculated_root:
        raise ReleaseApprovalError(
            "built release root is not approved: "
            f"approved {expected_root}, built {calculated_root}"
        )
    return calculated_root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checksums",
        type=Path,
        default=ROOT / "bundle" / "CHECKSUMS.sha256",
        help="generated release checksum manifest",
    )
    args = parser.parse_args()

    try:
        approved_root = validate_release_approval(
            args.checksums,
            os.environ.get(APPROVAL_VARIABLE, ""),
        )
    except (OSError, UnicodeError, ReleaseApprovalError) as exc:
        print(f"release approval failed closed: {exc}", file=sys.stderr)
        return 1

    print(f"release root approved: {approved_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Create a deterministic ZIP archive from an already verified bundle."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile

try:
    from scripts.check_release_approval import (
        ROOT_MARKER,
        ReleaseApprovalError,
        validate_release_approval,
    )
except ModuleNotFoundError:  # Direct `python scripts/package_release.py` execution.
    from check_release_approval import (  # type: ignore[no-redef]
        ROOT_MARKER,
        ReleaseApprovalError,
        validate_release_approval,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = ROOT / "bundle"
DEFAULT_CONFIG = ROOT / "source" / "build-config.json"


class ReleasePackagingError(ValueError):
    """Raised when a release archive cannot be assembled safely."""


def sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


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
        if path.is_symlink():
            raise ReleasePackagingError(
                f"release archive refuses symbolic link: {path.relative_to(bundle)}"
            )
        if path.is_file():
            files.append(path)
    if not files:
        raise ReleasePackagingError("release archive refuses an empty bundle")
    return files


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
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        raise ReleasePackagingError("release archive output must not be a symlink")

    archive_prefix = f"okf-landregistry-{version}"
    descriptor = {
        "version": version,
        "release_at": release_at,
        "release_root_sha256": verified_root,
        "file_count": len(files),
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
                relative = path.relative_to(bundle).as_posix()
                info = zipfile.ZipInfo(
                    filename=f"{archive_prefix}/{relative}",
                    date_time=fixed_time,
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (0o100644 & 0xFFFF) << 16
                info.flag_bits |= 0x800
                archive.writestr(info, path.read_bytes(), compresslevel=9)
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
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--receipt",
        type=Path,
        help="optional path for deterministic archive metadata JSON",
    )
    args = parser.parse_args()

    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        version = config["version"]
        release_at = config.get("release_at")
        candidate_at = config.get("generated_at")
        if config.get("status") != "ai-generated-proof-of-concept":
            raise ReleasePackagingError(
                "build config is not an approved AI-generated proof-of-concept configuration"
            )
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
                "build config supplies neither a released timestamp nor a "
                "digest-bound candidate timestamp"
            )
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
    ) as exc:
        print(f"release packaging failed closed: {exc}")
        return 1

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

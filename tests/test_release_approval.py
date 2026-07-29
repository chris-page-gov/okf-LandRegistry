from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.check_release_approval import (
    ReleaseApprovalError,
    validate_release_approval,
)


def write_release(root: Path, content: bytes = b"governed release\n") -> tuple[Path, str]:
    artifact = root / "artifact.txt"
    artifact.write_bytes(content)
    artifact_digest = hashlib.sha256(content).hexdigest()
    digest_line = f"{artifact_digest}  artifact.txt"
    release_root = hashlib.sha256(f"{digest_line}\n".encode("utf-8")).hexdigest()
    checksums = root / "CHECKSUMS.sha256"
    checksums.write_text(
        f"{digest_line}\n# release-root-sha256: {release_root}\n",
        encoding="utf-8",
    )
    return checksums, release_root


class ReleaseApprovalTests(unittest.TestCase):
    def test_exact_approved_release_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            checksums, release_root = write_release(Path(temp_name))
            self.assertEqual(
                release_root,
                validate_release_approval(checksums, release_root),
            )

    def test_empty_or_malformed_approval_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            checksums, _release_root = write_release(Path(temp_name))
            for value in ("", "abc", "A" * 64, f"{'a' * 64}\n"):
                with self.subTest(value=value), self.assertRaises(
                    ReleaseApprovalError
                ):
                    validate_release_approval(checksums, value)

    def test_different_approved_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            checksums, _release_root = write_release(Path(temp_name))
            with self.assertRaisesRegex(
                ReleaseApprovalError, "built release root is not approved"
            ):
                validate_release_approval(checksums, "0" * 64)

    def test_tampered_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            checksums, release_root = write_release(root)
            (root / "artifact.txt").write_bytes(b"changed after approval\n")
            with self.assertRaisesRegex(
                ReleaseApprovalError, "artifact digest mismatch"
            ):
                validate_release_approval(checksums, release_root)

    def test_forged_release_root_marker_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            checksums, release_root = write_release(Path(temp_name))
            text = checksums.read_text(encoding="utf-8")
            checksums.write_text(
                text.replace(release_root, "f" * 64),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ReleaseApprovalError, "declared release root does not match"
            ):
                validate_release_approval(checksums, "f" * 64)

    def test_checksum_path_must_remain_inside_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            outside = root.parent / f"{root.name}-outside.txt"
            try:
                outside.write_bytes(b"outside\n")
                digest = hashlib.sha256(outside.read_bytes()).hexdigest()
                digest_line = f"{digest}  ../{outside.name}"
                release_root = hashlib.sha256(
                    f"{digest_line}\n".encode("utf-8")
                ).hexdigest()
                checksums = root / "CHECKSUMS.sha256"
                checksums.write_text(
                    f"{digest_line}\n# release-root-sha256: {release_root}\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ReleaseApprovalError, "unsafe checksum path"):
                    validate_release_approval(checksums, release_root)
            finally:
                outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.package_release import (
    ReleasePackagingError,
    create_candidate_archive,
    create_release_archive,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_bundle(root: Path) -> Path:
    bundle = root / "bundle"
    (bundle / "data").mkdir(parents=True)
    (bundle / "index.html").write_text("<h1>PoC</h1>\n", encoding="utf-8")
    (bundle / "data" / "catalogue.json").write_text("{}\n", encoding="utf-8")
    lines = [
        f"{sha256(bundle / 'data' / 'catalogue.json')}  data/catalogue.json",
        f"{sha256(bundle / 'index.html')}  index.html",
    ]
    release_root = hashlib.sha256(
        ("\n".join(lines) + "\n").encode("utf-8")
    ).hexdigest()
    (bundle / "CHECKSUMS.sha256").write_text(
        "\n".join([*lines, f"# release-root-sha256: {release_root}", ""]),
        encoding="utf-8",
    )
    return bundle


class PackageReleaseTests(unittest.TestCase):
    def test_candidate_archive_does_not_assert_release(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            bundle = write_bundle(root)
            result = create_candidate_archive(
                bundle=bundle,
                output=root / "candidate.zip",
                version="0.2.0",
                candidate_at="2026-07-29T15:30:00Z",
            )
            self.assertEqual(
                result["schema"], "okf-hmlr-candidate-archive.v1"
            )
            self.assertEqual(
                result["publication_state"], "unreleased-candidate"
            )
            self.assertNotIn("release_at", result)

    def test_archive_is_deterministic_and_contains_verified_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            bundle = write_bundle(root)
            first = root / "first.zip"
            second = root / "second.zip"
            first_result = create_release_archive(
                bundle=bundle,
                output=first,
                version="0.1.0",
                release_at="2026-07-29T11:00:00Z",
            )
            second_result = create_release_archive(
                bundle=bundle,
                output=second,
                version="0.1.0",
                release_at="2026-07-29T11:00:00Z",
            )
            self.assertEqual(first_result["sha256"], second_result["sha256"])
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(
                    [
                        "okf-landregistry-0.1.0/CHECKSUMS.sha256",
                        "okf-landregistry-0.1.0/data/catalogue.json",
                        "okf-landregistry-0.1.0/index.html",
                    ],
                    archive.namelist(),
                )

    def test_tampered_bundle_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            bundle = write_bundle(root)
            (bundle / "index.html").write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(
                ReleasePackagingError, "bundle verification failed"
            ):
                create_release_archive(
                    bundle=bundle,
                    output=root / "release.zip",
                    version="0.1.0",
                    release_at="2026-07-29T11:00:00Z",
                )

    def test_symlink_in_bundle_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            bundle = write_bundle(root)
            (bundle / "link").symlink_to(bundle / "index.html")
            with self.assertRaisesRegex(ReleasePackagingError, "symbolic link"):
                create_release_archive(
                    bundle=bundle,
                    output=root / "release.zip",
                    version="0.1.0",
                    release_at="2026-07-29T11:00:00Z",
                )


if __name__ == "__main__":
    unittest.main()

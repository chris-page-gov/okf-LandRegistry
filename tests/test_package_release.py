from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from scripts.package_release import (
    ReleasePackagingError,
    create_candidate_archive,
    create_release_archive,
    governed_configuration,
    main,
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


def legacy_archive_bytes(
    bundle: Path, *, version: str, release_at: str
) -> bytes:
    """Reproduce the former in-memory writer as a byte-compatibility oracle."""

    output = bundle.parent / "legacy.zip"
    timestamp = (2026, 7, 29, 11, 0, 0)
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for path in sorted(bundle.rglob("*"), key=lambda item: item.as_posix()):
            if not path.is_file():
                continue
            relative = path.relative_to(bundle).as_posix()
            info = zipfile.ZipInfo(
                filename=f"okf-landregistry-{version}/{relative}",
                date_time=timestamp,
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100644 & 0xFFFF) << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)
    result = output.read_bytes()
    output.unlink()
    return result


class PackageReleaseTests(unittest.TestCase):
    def test_arbitrary_configuration_is_not_a_packaging_authority(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            config = Path(name) / "build-config.json"
            config.write_text(
                json.dumps(
                    {
                        "version": "0.3.0",
                        "status": "ai-generated-proof-of-concept",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ReleasePackagingError,
                "repository's regular source/build-config.json",
            ):
                governed_configuration(config, Path("bundle"))

    def test_cli_requires_the_exact_candidate_commit(self) -> None:
        output = io.StringIO()
        with (
            mock.patch("sys.argv", ["package_release.py"]),
            redirect_stdout(output),
        ):
            self.assertEqual(1, main())
        self.assertIn("requires --candidate-commit-sha", output.getvalue())

    def test_status_diagnostic_does_not_imply_approval(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            config = Path(name) / "build-config.json"
            config.write_text(
                json.dumps(
                    {
                        "version": "0.3.0",
                        "status": "unreleased-candidate",
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with (
                mock.patch(
                    "sys.argv",
                    ["package_release.py", "--config", str(config)],
                ),
                redirect_stdout(output),
            ):
                self.assertEqual(1, main())
            diagnostic = output.getvalue()
            self.assertIn(
                "build configuration does not declare the required "
                "AI-generated proof-of-concept status",
                diagnostic,
            )
            self.assertNotIn("approved", diagnostic)

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
                release_at="2026-07-29T11:00:01Z",
            )
            second_result = create_release_archive(
                bundle=bundle,
                output=second,
                version="0.1.0",
                release_at="2026-07-29T11:00:01Z",
            )
            self.assertEqual(first_result["sha256"], second_result["sha256"])
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(b"", archive.comment)
                self.assertEqual(
                    [
                        "okf-landregistry-0.1.0/CHECKSUMS.sha256",
                        "okf-landregistry-0.1.0/data/catalogue.json",
                        "okf-landregistry-0.1.0/index.html",
                    ],
                    archive.namelist(),
                )
                for member in archive.infolist():
                    self.assertEqual(b"", member.comment)
                    self.assertEqual(b"", member.extra)
                    self.assertEqual(3, member.create_system)
                    self.assertEqual(20, member.create_version)
                    self.assertEqual(20, member.extract_version)
                    self.assertEqual(0, member.reserved)
                    self.assertEqual(0, member.flag_bits)
                    self.assertEqual(0, member.volume)
                    self.assertEqual(0, member.internal_attr)
                    self.assertEqual(0o100644 << 16, member.external_attr)
                    self.assertEqual(zipfile.ZIP_DEFLATED, member.compress_type)
                    self.assertEqual((2026, 7, 29, 11, 0, 0), member.date_time)

    def test_streaming_writer_is_byte_identical_to_the_previous_writer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            bundle = write_bundle(root)
            expected = legacy_archive_bytes(
                bundle,
                version="0.1.0",
                release_at="2026-07-29T11:00:01Z",
            )
            output = root / "streamed.zip"

            with mock.patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError(
                    "archive creation must not materialise bundle members"
                ),
            ):
                create_release_archive(
                    bundle=bundle,
                    output=output,
                    version="0.1.0",
                    release_at="2026-07-29T11:00:01Z",
                )

            with output.open("rb") as handle:
                self.assertEqual(expected, handle.read())

    def test_archive_ceiling_fails_before_replacing_output_and_cleans_temp(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            bundle = write_bundle(root)
            output = root / "release.zip"
            original = b"existing reviewed archive\n"
            output.write_bytes(original)

            with (
                mock.patch(
                    "scripts.package_release.MAX_RELEASE_ARCHIVE_BYTES", 1
                ),
                self.assertRaisesRegex(
                    ReleasePackagingError,
                    "50,000,000-byte G8 evidence ceiling",
                ),
            ):
                create_release_archive(
                    bundle=bundle,
                    output=output,
                    version="0.1.0",
                    release_at="2026-07-29T11:00:00Z",
                )

            self.assertEqual(original, output.read_bytes())
            self.assertEqual([], list(root.glob(".okf-release-*.zip")))

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

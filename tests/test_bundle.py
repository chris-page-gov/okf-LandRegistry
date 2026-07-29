from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from scripts import build as builder


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "bundle"


def tree_bytes(path: Path) -> dict[str, bytes]:
    return {
        item.relative_to(path).as_posix(): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


class BundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.descriptor = json.loads((BUNDLE / "okf-explorer.json").read_text())
        cls.catalogue = json.loads((BUNDLE / "data" / "catalogue.json").read_text())

    def test_descriptor_contract_and_counts(self) -> None:
        self.assertEqual("0.2", self.descriptor["okf_version"])
        self.assertEqual("okf-explorer-large-corpus.v1", self.descriptor["schema"])
        self.assertEqual("okf-large-corpus", self.descriptor["kind"])
        self.assertEqual(
            self.catalogue["record_count"], self.descriptor["counts"]["records"]
        )
        self.assertFalse(self.descriptor["scope"]["complete_hmlr_public_estate"])
        self.assertTrue(self.descriptor["scope"]["metadata_only"])
        self.assertTrue(self.descriptor["authority"]["not_endorsed_by_source"])

    def test_record_identity_urls_and_rights_are_explicit(self) -> None:
        records = self.catalogue["records"]
        profile = json.loads(
            (ROOT / "domain-profile" / "domain-profile.json").read_text()
        )
        evidence_ids = {row["id"] for row in profile["evidence"]}
        self.assertGreater(len(records), 0)
        self.assertEqual(len(records), len({record["id"] for record in records}))
        self.assertEqual(len(records), len({record["url"] for record in records}))
        for record in records:
            parsed = urlparse(record["url"])
            self.assertEqual("https", parsed.scheme, record["id"])
            self.assertFalse(parsed.username or parsed.password, record["id"])
            sensitive = {"key", "token", "signature", "sig", "expires", "x-amz-signature"}
            self.assertFalse(
                sensitive & {key.casefold() for key in parse_qs(parsed.query)}, record["id"]
            )
            self.assertTrue(record["licence"], record["id"])
            self.assertTrue(record["access_model"], record["id"])
            self.assertTrue(record["authority_tier"], record["id"])
            self.assertTrue(record["observed_at"], record["id"])
            self.assertTrue(record["source_urls"], record["id"])
            self.assertTrue(record["source_native_ids"], record["id"])
            self.assertTrue(record["representations"], record["id"])
            self.assertIn(record["id"], record["source_native_ids"])
            self.assertTrue(record["access_state"], record["id"])
            self.assertTrue(record["rights_state"], record["id"])
            self.assertRegex(record["rights_ref"], r"^RIGHT-[A-Z]+$")
            self.assertTrue(record["authority_role"], record["id"])
            self.assertTrue(record["derivation"], record["id"])
            self.assertIn(record["lifecycle_state"], {"active", "archived", "unknown"})
            self.assertTrue(record["evidence_refs"], record["id"])
            self.assertTrue(
                all(reference.startswith("EV-") for reference in record["evidence_refs"])
            )
            self.assertTrue(set(record["evidence_refs"]) <= evidence_ids)

    def test_collision_reconciliation_preserves_every_representation(self) -> None:
        reconciliation = json.loads(
            (BUNDLE / "data" / "reconciliation.json").read_text()
        )
        self.assertEqual(
            reconciliation["input_representations"],
            reconciliation["retained_records"]
            + reconciliation["merged_representations"],
        )
        self.assertEqual(
            reconciliation["canonical_url_collisions"],
            len(reconciliation["collisions"]),
        )
        record_by_url = {
            record["url"]: record for record in self.catalogue["records"]
        }
        for collision in reconciliation["collisions"]:
            record = record_by_url[collision["url"]]
            self.assertEqual(
                sorted(collision["representation_ids"]),
                sorted(record["source_native_ids"]),
            )
            self.assertEqual(collision["selected_id"], record["id"])

    def test_data_manifest_and_record_shards_are_exact(self) -> None:
        manifest = json.loads((BUNDLE / "data" / "manifest.json").read_text())
        paths = {row["path"] for row in manifest["files"]}
        self.assertIn("data/provenance.json", paths)
        self.assertIn("data/rights.json", paths)
        self.assertIn("data/reconciliation.json", paths)
        self.assertIn("data/search/index.json", paths)
        self.assertIn("data/records/manifest.json", paths)
        for row in manifest["files"]:
            artifact = BUNDLE / row["path"]
            self.assertEqual(row["bytes"], artifact.stat().st_size)
            self.assertEqual(row["sha256"], hashlib.sha256(artifact.read_bytes()).hexdigest())

        search_index = json.loads((BUNDLE / "data" / "search" / "index.json").read_text())
        shard_manifest = json.loads(
            (BUNDLE / "data" / "records" / "manifest.json").read_text()
        )
        self.assertEqual("okf-hmlr-search-index.v1", search_index["schema"])
        self.assertEqual(self.catalogue["record_count"], search_index["record_count"])
        self.assertLess(
            (BUNDLE / "data" / "search" / "index.json").stat().st_size,
            (BUNDLE / "data" / "catalogue.json").stat().st_size,
        )
        self.assertTrue(
            all(isinstance(record["body_tokens"], str) for record in search_index["records"])
        )
        self.assertEqual(self.catalogue["record_count"], shard_manifest["record_count"])
        self.assertTrue(
            all(row["record_count"] <= shard_manifest["shard_size"] for row in shard_manifest["shards"])
        )
        self.assertEqual(
            self.catalogue["record_count"],
            sum(row["record_count"] for row in shard_manifest["shards"]),
        )

    def test_okf_concepts_have_frontmatter_and_log_is_newest_first(self) -> None:
        for path in sorted((BUNDLE / "concepts").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\n"), path.name)
            frontmatter, body = text[4:].split("\n---\n", 1)
            for field in ("type:", "title:", "description:", "generated:", "sources:"):
                self.assertIn(field, frontmatter, path.name)
            self.assertRegex(body, r"(?m)^#\s+\S")
        log = (BUNDLE / "log.md").read_text(encoding="utf-8")
        headings = re.findall(r"(?m)^##\s+(\d{4}-\d{2}-\d{2})$", log)
        self.assertEqual(headings, sorted(headings, reverse=True))

    def test_csv_formula_cells_are_neutralised(self) -> None:
        for prefix in ("=", "+", "-", "@", "\t", "\r"):
            self.assertEqual("'" + prefix + "unsafe", builder.csv_safe(prefix + "unsafe"))
        self.assertEqual("safe", builder.csv_safe("safe"))

    def test_output_replacement_requires_a_recognised_generated_bundle(self) -> None:
        with tempfile.TemporaryDirectory(prefix=".test-output-", dir=ROOT) as name:
            parent = Path(name)
            with self.assertRaisesRegex(ValueError, "named 'bundle'"):
                builder.validate_output_target(parent / "docs", replace=True)
            unmarked = parent / "bundle"
            unmarked.mkdir()
            with self.assertRaisesRegex(ValueError, "unmarked"):
                builder.validate_output_target(unmarked, replace=True)
            (unmarked / builder.GENERATED_MARKER).write_text("generated\n")
            builder.validate_output_target(unmarked, replace=True)

    def test_current_snapshot_receipts_are_rehashed_and_reconciled(self) -> None:
        snapshot_dir = builder.newest_snapshot()
        self.assertIsNotNone(snapshot_dir)
        manifest = json.loads((snapshot_dir / "manifest.json").read_text())
        self.assertEqual("okf-hmlr-metadata-snapshot.v2", manifest["schema"])
        records, snapshot = builder.snapshot_records(snapshot_dir)
        self.assertEqual(sum(manifest["totals"].values()), len(records))
        self.assertEqual("complete", manifest["terminal_outcome"]["status"])
        self.assertEqual(
            hashlib.sha256((snapshot_dir / "manifest.json").read_bytes()).hexdigest(),
            snapshot["source_manifest_sha256"],
        )

    def test_forbidden_raw_content_and_secret_shapes_absent(self) -> None:
        text = (BUNDLE / "data" / "catalogue.json").read_text(encoding="utf-8")
        secret_patterns = [
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            r"\bghp_[A-Za-z0-9]{30,}\b",
            r"\bAKIA[0-9A-Z]{16}\b",
            r"[?&](?:api[_-]?key|token|signature|x-amz-signature)=",
        ]
        for pattern in secret_patterns:
            self.assertIsNone(re.search(pattern, text, flags=re.IGNORECASE), pattern)
        forbidden_keys = {"property_address", "title_number", "proprietor_name"}
        for record in self.catalogue["records"]:
            self.assertFalse(forbidden_keys & set(record), record["id"])

    def test_release_checksums_are_exact(self) -> None:
        lines = (BUNDLE / "CHECKSUMS.sha256").read_text().splitlines()
        digest_lines = [line for line in lines if line and not line.startswith("#")]
        roots = [
            line.removeprefix("# release-root-sha256: ")
            for line in lines
            if line.startswith("# release-root-sha256: ")
        ]
        self.assertEqual(1, len(roots))
        for line in digest_lines:
            digest, name = line.split("  ", 1)
            self.assertEqual(digest, hashlib.sha256((BUNDLE / name).read_bytes()).hexdigest())
        manifest = ("\n".join(digest_lines) + "\n").encode()
        self.assertEqual(roots[0], hashlib.sha256(manifest).hexdigest())

    def test_offline_build_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory(prefix=".test-build-a-", dir=ROOT) as a_dir:
            with tempfile.TemporaryDirectory(prefix=".test-build-b-", dir=ROOT) as b_dir:
                first = Path(a_dir) / "bundle"
                second = Path(b_dir) / "bundle"
                command = [sys.executable, "scripts/build.py", "--output-dir"]
                subprocess.run(
                    [*command, str(first)], cwd=ROOT, check=True, capture_output=True
                )
                subprocess.run(
                    [*command, str(second)], cwd=ROOT, check=True, capture_output=True
                )
                self.assertEqual(tree_bytes(first), tree_bytes(second))
                self.assertEqual(tree_bytes(BUNDLE), tree_bytes(first))


if __name__ == "__main__":
    unittest.main()

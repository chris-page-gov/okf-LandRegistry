from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "domain-profile"


class DomainProfileTests(unittest.TestCase):
    def test_required_pack_files_exist(self) -> None:
        required = {
            "domain-warmup-report.md",
            "domain-profile.json",
            "domain-profile.yaml",
            "evidence-register.jsonl",
            "decision-register.md",
            "traceability.json",
            "CHECKSUMS.sha256",
        }
        self.assertEqual(required, {path.name for path in PROFILE.iterdir() if path.is_file()})

    def test_checksum_pack_is_exact(self) -> None:
        lines = (PROFILE / "CHECKSUMS.sha256").read_text(encoding="utf-8").splitlines()
        digest_lines = [line for line in lines if line and not line.startswith("#")]
        declared_root = [
            line.removeprefix("# pack-root-sha256: ")
            for line in lines
            if line.startswith("# pack-root-sha256: ")
        ]
        self.assertEqual(1, len(declared_root))
        for line in digest_lines:
            digest, name = line.split("  ", 1)
            self.assertEqual(digest, hashlib.sha256((PROFILE / name).read_bytes()).hexdigest())
        manifest = ("\n".join(digest_lines) + "\n").encode("utf-8")
        self.assertEqual(declared_root[0], hashlib.sha256(manifest).hexdigest())

    def test_evidence_jsonl_matches_profile(self) -> None:
        profile = json.loads((PROFILE / "domain-profile.json").read_text(encoding="utf-8"))
        evidence = [
            json.loads(line)
            for line in (PROFILE / "evidence-register.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line
        ]
        self.assertEqual(profile["evidence"], evidence)
        self.assertEqual(len(evidence), len({item["id"] for item in evidence}))

    def test_profile_is_reviewed_and_release_decision_is_historical(self) -> None:
        profile = json.loads((PROFILE / "domain-profile.json").read_text(encoding="utf-8"))
        self.assertEqual("reviewed", profile["status"])
        recommendation = profile["build_recommendation"]
        self.assertEqual([], recommendation["blocking_decision_ids"])
        release = next(
            decision
            for decision in profile["decisions"]
            if decision["id"] == "DEC-RELEASE"
        )
        self.assertEqual("accepted", release["status"])
        self.assertIn(
            "40482c865dc4332162f1e93756d94ca93abe3559",
            release["recommended_default"],
        )
        self.assertIn(
            "a3e0bdf7846893ce29255f6f20a509dad18ef2b367ba3dfbe48c28191377a704",
            release["recommended_default"],
        )
        self.assertIn(
            "does not authorise publication of the v0.3.0 semantic candidate",
            release["recommended_default"],
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_okf", ROOT / "scripts" / "check_okf.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class OkfMarkdownCheckerTests(unittest.TestCase):
    def test_generated_bundle_passes_pinned_checker(self) -> None:
        result = MODULE.validate_okf_v02_markdown(ROOT / "bundle")
        self.assertEqual("conformant", result["status"], result["errors"])
        self.assertGreater(result["checked_concepts"], 0)
        self.assertRegex(result["checker"]["commit_sha"], r"^[0-9a-f]{40}$")
        self.assertRegex(result["checker"]["sha256"], r"^[0-9a-f]{64}$")

    def test_malformed_frontmatter_fails_executable_validation(self) -> None:
        with tempfile.TemporaryDirectory(prefix=".okf-check-", dir=ROOT) as name:
            bundle = Path(name)
            (bundle / "index.md").write_text(
                '---\nokf_version: "0.2"\n---\n\n# Index\n',
                encoding="utf-8",
            )
            (bundle / "log.md").write_text(
                "# Log\n\n## 2026-07-29\n", encoding="utf-8"
            )
            (bundle / "broken.md").write_text(
                "---\ntype: [unterminated\n---\n\n# Broken\n",
                encoding="utf-8",
            )
            result = MODULE.validate_okf_v02_markdown(bundle)
            self.assertEqual("non-conformant", result["status"])
            self.assertTrue(result["errors"])


if __name__ == "__main__":
    unittest.main()

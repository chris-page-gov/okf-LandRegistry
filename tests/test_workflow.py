from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def test_actions_are_full_sha_pinned_and_pages_uses_bundle(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text()
        uses = re.findall(r"^\s*uses:\s*([^@\s]+)@([^\s#]+)", workflow, re.MULTILINE)
        self.assertGreaterEqual(len(uses), 5)
        for action, revision in uses:
            self.assertRegex(revision, r"^[0-9a-f]{40}$", action)
        self.assertIn("path: bundle", workflow)
        self.assertIn("scripts/build.py --replace", workflow)
        self.assertIn("scripts/check_domain_profile.py", workflow)
        self.assertIn(
            "python -m pip install --require-hashes -r requirements-lock.txt",
            workflow,
        )
        self.assertIn("--acceptance-review evaluation/acceptance-review.json", workflow)
        self.assertIn("--min-expected-target-recall-at-k 0.90", workflow)
        self.assertIn("--min-all-expected-target-success-at-k 1.0", workflow)
        self.assertIn("--min-mrr 0.80", workflow)
        self.assertIn("python scripts/check_release_evidence.py", workflow)
        self.assertIn("git diff --exit-code -- bundle", workflow)
        self.assertIn(
            "git ls-files --others --exclude-standard -- bundle",
            workflow,
        )

    def test_pages_release_requires_default_branch_and_approved_root(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text()
        default_branch_gate = (
            "github.ref == format('refs/heads/{0}', "
            "github.event.repository.default_branch)"
        )
        approval_gate = "vars.OKF_RELEASE_ROOT_SHA256 != ''"
        self.assertGreaterEqual(workflow.count(default_branch_gate), 4)
        self.assertGreaterEqual(workflow.count(approval_gate), 4)
        self.assertIn("scripts/check_release_approval.py", workflow)
        self.assertIn(
            "OKF_RELEASE_ROOT_SHA256: ${{ vars.OKF_RELEASE_ROOT_SHA256 }}",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TraceabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalogue = json.loads(
            (ROOT / "personas" / "personas-and-user-stories.json").read_text()
        )
        cls.questions = json.loads((ROOT / "evaluation" / "questions.json").read_text())
        cls.journeys = json.loads((ROOT / "evaluation" / "journeys.json").read_text())
        cls.governance_traceability = json.loads(
            (ROOT / "governance" / "traceability.json").read_text()
        )
        cls.sources = json.loads(
            (ROOT / "research" / "source-family-inventory.json").read_text()
        )

    def test_candidate_suite_has_declared_size(self) -> None:
        self.assertEqual(9, len(self.catalogue["personas"]))
        self.assertEqual(12, len(self.catalogue["stories"]))
        self.assertEqual(24, len(self.questions["questions"]))
        self.assertEqual(24, self.questions["question_count"])

    def test_question_story_persona_references_resolve(self) -> None:
        persona_ids = {item["id"] for item in self.catalogue["personas"]}
        story_ids = {item["id"] for item in self.catalogue["stories"]}
        hard_failure_ids = {item["id"] for item in self.questions["hard_failures"]}
        for question in self.questions["questions"]:
            self.assertTrue(set(question["persona_ids"]) <= persona_ids, question["id"])
            self.assertTrue(set(question["story_ids"]) <= story_ids, question["id"])
            self.assertTrue(
                set(question["hard_failure_ids"]) <= hard_failure_ids, question["id"]
            )

    def test_every_question_and_story_has_a_journey(self) -> None:
        journey_story_ids: set[str] = set()
        for journey in self.journeys["journeys"]:
            journey_story_ids.update(journey.get("story_ids", []))
        expected_questions = {item["id"] for item in self.questions["questions"]}
        expected_stories = {item["id"] for item in self.catalogue["stories"]}
        self.assertEqual(expected_stories, journey_story_ids)
        questions_exercised_by_those_stories = {
            question_id
            for story in self.catalogue["stories"]
            if story["id"] in journey_story_ids
            for question_id in story["question_ids"]
        }
        self.assertEqual(expected_questions, questions_exercised_by_those_stories)

    def test_rubric_totals_one_hundred(self) -> None:
        self.assertEqual(
            100, sum(section["points"] for section in self.questions["rubric"].values())
        )

    def test_v03_governance_rows_do_not_claim_unrun_verification(self) -> None:
        traceability = self.governance_traceability
        self.assertEqual(
            "candidate-v0.3.0-external-exact-gates",
            traceability["assurance_state"],
        )
        self.assertTrue(traceability["rows"])
        self.assertEqual(
            {"not_run"},
            {row["verification_state"] for row in traceability["rows"]},
        )

        release_row = next(
            row
            for row in traceability["rows"]
            if row["requirement_ids"] == ["REQ-018"]
        )
        self.assertIn(
            "docs/v0.3.0-release-tracker-and-assurance-runbook.md",
            release_row["artifact_paths"],
        )
        self.assertIn(
            "validation/candidate-v0.3.0/**",
            release_row["artifact_paths"],
        )
        self.assertNotIn(
            "validation/release-record.json",
            release_row["artifact_paths"],
        )

    def test_v03_runbook_routes_diagnostics_and_evidence_explicitly(self) -> None:
        runbook = (
            ROOT / "docs" / "v0.3.0-release-tracker-and-assurance-runbook.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "--output validation/candidate-v0.3.0/evidence/"
            "evaluation-diagnostic.json",
            runbook,
        )
        self.assertIn(
            "--output-directory validation/candidate-v0.3.0/pre-g9",
            runbook,
        )
        self.assertIn(
            "--output-directory validation/candidate-v0.3.0/final-g9",
            runbook,
        )
        self.assertIn(
            "--manifest validation/candidate-v0.3.0/final-g9/"
            "release-evidence.json",
            runbook,
        )
        self.assertIn("--staged-candidate", runbook)
        self.assertIn("--candidate-only", runbook)
        self.assertIn(
            'git commit -m "Record v0.3.0 G1-G8 candidate evidence"',
            runbook,
        )
        self.assertIn(
            'git commit -m "Record v0.3.0 independent review and owner decision"',
            runbook,
        )
        self.assertIn(
            'git commit -m "Record v0.3.0 final G9 evidence"',
            runbook,
        )
        build_command = "scripts/build.py \\"
        self.assertEqual(2, runbook.count(build_command))
        first_build = runbook.index(build_command)
        candidate_commit = runbook.index(
            'git commit -m "Migrate Land Registry to OKF v0.3 semantic candidate"'
        )
        clean_checkout = runbook.index(
            'git worktree add --detach "$OKF_REPRO_CHECKOUT" "$CANDIDATE_SHA"'
        )
        second_build = runbook.index(build_command, first_build + 1)
        self.assertLess(first_build, candidate_commit)
        self.assertLess(candidate_commit, clean_checkout)
        self.assertLess(clean_checkout, second_build)
        self.assertNotIn("uses a third new", runbook)
        self.assertEqual(3, runbook.count("check_release_transition.py staged-evidence"))
        self.assertNotIn("git diff --cached --quiet", runbook)
        bash_blocks = [
            block.split("```", 1)[0].lstrip("\n")
            for block in runbook.split("```bash")[1:]
        ]
        self.assertTrue(bash_blocks)
        for block in bash_blocks:
            self.assertTrue(block.startswith("set -euo pipefail\n"), block[:80])
        for commit in (
            'git commit -m "Record v0.3.0 G1-G8 candidate evidence"',
            'git commit -m "Record v0.3.0 independent review and owner decision"',
            'git commit -m "Record v0.3.0 final G9 evidence"',
        ):
            with self.subTest(commit=commit):
                block = next(candidate for candidate in bash_blocks if commit in candidate)
                self.assertLess(
                    block.index("check_release_transition.py staged-evidence"),
                    block.index(commit),
                )
        final_commit = runbook.index(
            'git commit -m "Record v0.3.0 final G9 evidence"'
        )
        evidence_sha = runbook.index('EVIDENCE_SHA="$(git rev-parse HEAD)"')
        final_manifest_check = runbook.index(
            "--manifest validation/candidate-v0.3.0/final-g9/"
            "release-evidence.json",
            final_commit,
        )
        self.assertLess(final_commit, evidence_sha)
        self.assertLess(evidence_sha, final_manifest_check)
        required_remote = (
            'OKF_REMOTE="${OKF_REMOTE:?set the verified canonical GitHub '
            'remote name}"'
        )
        self.assertIn(
            required_remote,
            runbook,
        )
        self.assertNotIn('OKF_REMOTE="${OKF_REMOTE:-origin}"', runbook)
        self.assertNotIn("git remote get-url", runbook)
        self.assertGreaterEqual(runbook.count("remote-binding --remote"), 3)
        self.assertIn('OKF_CANDIDATE_BRANCH="candidate/v0.3.0"', runbook)
        self.assertNotIn("OKF_CANDIDATE_BRANCH:-", runbook)
        self.assertNotIn("git push github", runbook)
        candidate_push = runbook.index(
            '"$EVIDENCE_SHA:refs/heads/$OKF_CANDIDATE_BRANCH"'
        )
        pr_checks = runbook.index('gh pr checks "$OKF_PR_NUMBER"')
        main_push = runbook.index(
            'git push "$OKF_REMOTE" '
            '"$EVIDENCE_SHA:refs/heads/$OKF_DEFAULT_BRANCH"'
        )
        self.assertEqual(2, runbook.count("check_release_transition.py pr-state"))
        self.assertNotIn("--expected-base", runbook)
        self.assertNotIn("--expected-head ", runbook)
        self.assertEqual(3, runbook.count('--expected-head-oid "$EVIDENCE_SHA"'))
        self.assertEqual(2, runbook.count("--require-review-decision APPROVED"))
        first_pr_state = runbook.index("check_release_transition.py pr-state")
        second_pr_state = runbook.index(
            "check_release_transition.py pr-state", first_pr_state + 1
        )
        self.assertLess(candidate_push, first_pr_state)
        self.assertLess(first_pr_state, pr_checks)
        self.assertLess(pr_checks, second_pr_state)
        self.assertLess(second_pr_state, main_push)
        required_identity = runbook.index(
            "scripts/check_release_transition.py required-checks"
        )
        self.assertLess(pr_checks, required_identity)
        self.assertLess(required_identity, second_pr_state)
        self.assertIn("--json name,workflow,event,state,bucket,link", runbook)
        self.assertIn("required-check-run-id", runbook)
        self.assertEqual(3, runbook.count("gh api --hostname github.com"))
        self.assertIn("actions/workflows/pages.yml", runbook)
        self.assertIn("actions/runs/$CHECK_RUN_ID/jobs?per_page=100", runbook)
        variable_set = runbook.index("gh variable set OKF_RELEASE_ROOT_SHA256")
        variable_get = runbook.index("gh variable get OKF_RELEASE_ROOT_SHA256")
        dry_run = runbook.index('git push --dry-run "$OKF_REMOTE"')
        self.assertLess(dry_run, main_push)
        self.assertLess(main_push, variable_set)
        self.assertLess(variable_set, variable_get)
        self.assertIn("OKF_DIRECT_PUSH_AUTHORISED", runbook)
        self.assertIn("gh ruleset check --default", runbook)
        self.assertIn("gh workflow run pages.yml", runbook)
        self.assertIn('-f expected_commit_sha="$EVIDENCE_SHA"', runbook)
        self.assertIn(
            '-f expected_release_root_sha256="$RELEASE_ROOT"', runbook
        )
        self.assertIn('gh run watch "$DEPLOYMENT_RUN_ID"', runbook)
        self.assertIn("gh run watch \"$RECOVERY_RUN_ID\"", runbook)
        normalised_runbook = " ".join(runbook.split())
        self.assertIn("non-force fast-forward", normalised_runbook)
        self.assertIn(
            "the exact current G1–G9 state and release authority are recorded "
            "only in subsequent version-scoped, digest-bound evidence",
            normalised_runbook,
        )
        self.assertNotIn("Only at that final promotion may", runbook)
        self.assertIn("Default-branch push workflows verify but never upload", runbook)
        self.assertNotIn("default-branch push triggers", runbook)
        self.assertNotIn("<v0.3.0-pre-g9-directory>", runbook)

    def test_documented_causal_freeze_executes_before_the_governed_build(self) -> None:
        runbook = (
            ROOT / "docs" / "v0.3.0-release-tracker-and-assurance-runbook.md"
        ).read_text(encoding="utf-8")
        blocks = re.findall(r"```bash\n(.*?)```", runbook, re.DOTALL)
        block = next(
            candidate
            for candidate in blocks
            if "git add -A -- ." in candidate and "scripts/build.py" in candidate
        )
        # Keep the external recovery slot inside this disposable shell fixture;
        # the command-order test uses a stub interpreter and must not run or
        # leave artefacts from the real full-corpus builder.
        block = block.replace(
            'repository_parent="$(cd .. && pwd -P)"',
            'repository_parent="$PWD"',
        )

        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            log = fixture / "sequence.log"
            python = fixture / ".venv" / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text(
                "#!/bin/bash\n"
                "printf 'PY %s\\n' \"$*\" >> \"$LOG_FILE\"\n"
                "args=(\"$@\")\n"
                "for ((index=0; index < ${#args[@]}; index++)); do\n"
                "  if [[ \"${args[$index]}\" == \"--previous-output\" ]]; then\n"
                "    mkdir -p \"${args[$((index + 1))]}\"\n"
                "  fi\n"
                "done\n",
                encoding="utf-8",
            )
            python.chmod(0o755)
            commands = fixture / "commands"
            commands.mkdir()
            git_stub = commands / "git"
            git_stub.write_text(
                "#!/bin/bash\n"
                "printf 'GIT %s\\n' \"$*\" >> \"$LOG_FILE\"\n",
                encoding="utf-8",
            )
            git_stub.chmod(0o755)
            scratch = fixture / "scratch"
            scratch.mkdir()
            environment = dict(
                os.environ,
                PATH=f"{commands}:{os.environ['PATH']}",
                LOG_FILE=str(log),
                TMPDIR=str(scratch),
            )
            subprocess.run(
                ["bash", "-c", block],
                cwd=fixture,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            lines = log.read_text(encoding="utf-8").splitlines()

        def position(fragment: str) -> int:
            return next(index for index, line in enumerate(lines) if fragment in line)

        stage_authored = position("GIT add -A -- .")
        candidate_policy = position(
            "scripts/check_release_transition.py staged-candidate"
        )
        governed_build = position("scripts/build.py")
        stage_bundle = position("GIT add -A -- bundle")
        receipt_check = position("scripts/check_release_evidence.py --staged-candidate")
        self.assertLess(stage_authored, candidate_policy)
        self.assertLess(candidate_policy, governed_build)
        self.assertLess(governed_build, stage_bundle)
        self.assertLess(stage_bundle, receipt_check)
        for line in lines:
            if line.startswith("PY "):
                self.assertIn(" -B ", f" {line} ")

    def test_documented_release_transition_executes_in_fail_closed_order(self) -> None:
        runbook = (
            ROOT / "docs" / "v0.3.0-release-tracker-and-assurance-runbook.md"
        ).read_text(encoding="utf-8")
        blocks = re.findall(r"```bash\n(.*?)```", runbook, re.DOTALL)
        block = next(candidate for candidate in blocks if "OKF_PR_NUMBER=" in candidate)
        evidence_sha = "e" * 40
        candidate_sha = "c" * 40
        release_root = "b" * 64

        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            log = fixture / "sequence.log"
            workflow_state = fixture / "workflow-dispatched"
            python = fixture / ".venv" / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text(
                "#!/bin/bash\n"
                "printf 'PY %s\\n' \"$*\" >> \"$LOG_FILE\"\n"
                "if [[ \" $* \" == *' pr-state '* ]]; then cat >/dev/null; fi\n"
                "case \" $* \" in\n"
                "  *' required-check-run-id '*) printf '202\\n' ;;\n"
                "  *' bundle-inventory '*) printf '%s\\n' \"$FIXTURE_RELEASE_ROOT\" ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            python.chmod(0o755)
            commands = fixture / "commands"
            commands.mkdir()
            git_stub = commands / "git"
            git_stub.write_text(
                "#!/bin/bash\n"
                "printf 'GIT %s\\n' \"$*\" >> \"$LOG_FILE\"\n"
                "if [[ \"$1\" == 'rev-parse' ]]; then\n"
                "  printf '%s\\n' \"$FIXTURE_EVIDENCE_SHA\"\n"
                "fi\n",
                encoding="utf-8",
            )
            git_stub.chmod(0o755)
            gh_stub = commands / "gh"
            gh_stub.write_text(
                "#!/bin/bash\n"
                "printf 'GH %s\\n' \"$*\" >> \"$LOG_FILE\"\n"
                "if [[ \"$1 $2\" == 'pr view' ]]; then\n"
                "  printf '{\"state\":\"OPEN\"}\\n'\n"
                "elif [[ \"$1 $2\" == 'pr checks' && \" $* \" == *' --json '* ]]; then\n"
                "  printf '[]\\n'\n"
                "elif [[ \"$1\" == 'api' ]]; then\n"
                "  printf '{}\\n'\n"
                "elif [[ \"$1 $2\" == 'variable get' ]]; then\n"
                "  printf '%s\\n' \"$FIXTURE_RELEASE_ROOT\"\n"
                "elif [[ \"$1 $2\" == 'workflow run' ]]; then\n"
                "  : > \"$GH_WORKFLOW_STATE\"\n"
                "elif [[ \"$1 $2\" == 'run list' && -f \"$GH_WORKFLOW_STATE\" ]]; then\n"
                "  printf '777\\n'\n"
                "fi\n",
                encoding="utf-8",
            )
            gh_stub.chmod(0o755)
            sleep_stub = commands / "sleep"
            sleep_stub.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            sleep_stub.chmod(0o755)
            scratch = fixture / "scratch"
            scratch.mkdir()
            environment = dict(
                os.environ,
                PATH=f"{commands}:{os.environ['PATH']}",
                LOG_FILE=str(log),
                GH_WORKFLOW_STATE=str(workflow_state),
                FIXTURE_EVIDENCE_SHA=evidence_sha,
                FIXTURE_RELEASE_ROOT=release_root,
                TMPDIR=str(scratch),
                OKF_REMOTE="release",
                OKF_PR_NUMBER="77",
                CANDIDATE_SHA=candidate_sha,
                EVIDENCE_SHA=evidence_sha,
                OKF_DIRECT_PUSH_AUTHORISED="yes",
            )
            subprocess.run(
                ["bash", "-c", block],
                cwd=fixture,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            lines = log.read_text(encoding="utf-8").splitlines()

        def positions(fragment: str) -> list[int]:
            return [index for index, line in enumerate(lines) if fragment in line]

        remote_binding = positions("remote-binding --remote release")[0]
        candidate_push = positions(
            f"{evidence_sha}:refs/heads/candidate/v0.3.0"
        )[0]
        pr_states = positions("scripts/check_release_transition.py pr-state")
        required_run = positions("required-check-run-id")[0]
        required_check = positions("scripts/check_release_transition.py required-checks")[0]
        dry_main = positions("GIT push --dry-run release")[0]
        real_main = positions(f"GIT push release {evidence_sha}:refs/heads/main")[0]
        variable_set = positions("GH variable set OKF_RELEASE_ROOT_SHA256")[0]
        variable_get = positions("GH variable get OKF_RELEASE_ROOT_SHA256")[0]
        dispatch = positions("GH workflow run pages.yml")[0]
        watch = positions("GH run watch 777")[0]
        self.assertEqual(2, len(pr_states))
        self.assertLess(remote_binding, candidate_push)
        self.assertLess(candidate_push, pr_states[0])
        self.assertLess(pr_states[0], required_run)
        self.assertLess(required_run, required_check)
        self.assertLess(required_check, pr_states[1])
        self.assertLess(pr_states[1], dry_main)
        self.assertLess(dry_main, real_main)
        self.assertLess(real_main, variable_set)
        self.assertLess(variable_set, variable_get)
        self.assertLess(variable_get, dispatch)
        self.assertLess(dispatch, watch)
        for line in lines:
            if line.startswith("PY "):
                self.assertIn(" -B ", f" {line} ")

    def test_v02_runbook_is_prominently_labelled_historical(self) -> None:
        guide = (
            ROOT / "docs" / "v0.2.0-release-tracker-and-publication-guide.md"
        ).read_text(encoding="utf-8")
        self.assertIn("**Historical snapshot:**", guide)
        self.assertIn("owner-approved, deployed and browser-verified", guide)


if __name__ == "__main__":
    unittest.main()

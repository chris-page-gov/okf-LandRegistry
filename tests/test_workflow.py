from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PYTHON_PROCESS_LAUNCHERS = {
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "real_popen",
    "builder._run_bounded_evaluator",
    "_run_bounded_evaluator",
}


def dotted_name(node: ast.AST) -> str | None:
    """Return the dotted source name for a simple call target."""

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def is_python_command_head(node: ast.AST) -> bool:
    """Recognise the Python executable forms used by test child processes."""

    if isinstance(node, ast.Attribute):
        return (
            isinstance(node.value, ast.Name)
            and node.value.id == "sys"
            and node.attr in {"executable", "_base_executable"}
        )
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return Path(node.value).name.lower() in {
            "python",
            "python3",
            "python3.12",
        }
    if isinstance(node, ast.Name):
        return node.id in {"interpreter", "python", "python_executable"}
    if (
        isinstance(node, ast.Call)
        and dotted_name(node.func) == "str"
        and len(node.args) == 1
    ):
        return is_python_command_head(node.args[0])
    return False


def python_child_contract_violations() -> list[str]:
    """Find executable child-Python sites that omit explicit ``-B``."""

    violations: list[str] = []
    source_paths = sorted((ROOT / "tests").glob("test_*.py")) + sorted(
        (ROOT / "scripts").glob("*.py")
    )
    for path in source_paths:
        source = path.read_text(encoding="utf-8")
        module = ast.parse(source, filename=str(path))
        guard_ranges = [
            (node.lineno, node.end_lineno or node.lineno)
            for node in module.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "python_child_contract_violations"
        ]
        for node in ast.walk(module):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            launcher = dotted_name(node.func)
            if launcher not in PYTHON_PROCESS_LAUNCHERS:
                continue
            command = node.args[0]
            if not isinstance(command, (ast.List, ast.Tuple)) or not command.elts:
                continue
            if not is_python_command_head(command.elts[0]):
                continue
            flags = {
                element.value
                for element in command.elts[1:6]
                if isinstance(element, ast.Constant)
                and isinstance(element.value, str)
            }
            if "-B" not in flags:
                violations.append(
                    f"{path.relative_to(ROOT).as_posix()}:{node.lineno}: "
                    f"{launcher} Python child omits -B"
                )
        for line_number, line in enumerate(source.splitlines(), start=1):
            if any(start <= line_number <= end for start, end in guard_ranges):
                continue
            if not (
                'f"#!{sys.executable}' in line
                or "f'#!{sys.executable}" in line
            ):
                continue
            if (
                'f"#!{sys.executable} -B\\n' not in line
                and "f'#!{sys.executable} -B\\n" not in line
            ):
                violations.append(
                    f"{path.relative_to(ROOT).as_posix()}:{line_number}: "
                    "Python shebang helper omits -B"
                )
    return violations


def prohibited_python_artifact_inventory() -> dict[str, tuple[int, int, int, str]]:
    """Snapshot bytecode and runtime customisation files without changing them."""

    inventory: dict[str, tuple[int, int, int, str]] = {}
    for directory in (ROOT / ".venv", ROOT / "scripts", ROOT / "tests"):
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not (
                path.suffix in {".pyc", ".pyo", ".pth"}
                or path.name in {"sitecustomize.py", "usercustomize.py"}
            ):
                continue
            status = path.lstat()
            relative = path.relative_to(ROOT).as_posix()
            if path.is_symlink():
                digest = hashlib.sha256(
                    os.readlink(path).encode("utf-8")
                ).hexdigest()
            elif path.is_file():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                digest = "non-regular-entry"
            inventory[relative] = (
                status.st_mode,
                status.st_size,
                status.st_mtime_ns,
                digest,
            )
    return inventory


def workflow_steps(workflow: str) -> dict[str, str]:
    matches = list(re.finditer(r"^      - name: (.+)$", workflow, re.MULTILINE))
    return {
        match.group(1): workflow[
            match.start() : matches[index + 1].start()
            if index + 1 < len(matches)
            else len(workflow)
        ]
        for index, match in enumerate(matches)
    }


class WorkflowTests(unittest.TestCase):
    def test_actions_are_full_sha_pinned_and_pages_uses_bundle(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text()
        uses = re.findall(r"^\s*uses:\s*([^@\s]+)@([^\s#]+)", workflow, re.MULTILINE)
        self.assertGreaterEqual(len(uses), 5)
        for action, revision in uses:
            self.assertRegex(revision, r"^[0-9a-f]{40}$", action)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            workflow,
        )
        self.assertIn("path: bundle", workflow)
        self.assertIn('python-version: "3.12.11"', workflow)
        self.assertIn("python -m venv --without-pip .venv", workflow)
        self.assertIn(
            '.venv/bin/python -E -s -B -X "pycache_prefix=${runtime_cache}" '
            '\\\n            -m unittest discover -s tests -v',
            workflow,
        )
        self.assertNotIn(
            '.venv/bin/python -I -B -X "pycache_prefix=${runtime_cache}" '
            '\\\n            -m unittest discover',
            workflow,
        )
        for relative in (
            "README.md",
            "docs/v0.3.0-release-tracker-and-assurance-runbook.md",
        ):
            release_prose = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(test_suite_document=relative):
                self.assertIn(".venv/bin/python -E -s -B -X", release_prose)
                self.assertIn(
                    "-m unittest discover -s tests -v",
                    release_prose,
                )
                self.assertNotIn(
                    '.venv/bin/python -I -B -X "pycache_prefix=$test_runtime_cache"',
                    release_prose,
                )
        self.assertIn(
            '.venv/bin/python -I -B -X "pycache_prefix=${runtime_cache}" \\\n'
            "            scripts/build.py",
            workflow,
        )
        self.assertIn(
            "--snapshot-dir source/snapshots/2026-07-29T091915Z",
            workflow,
        )
        self.assertIn(
            "--publication-base https://chris-page-gov.github.io/okf-LandRegistry/",
            workflow,
        )
        self.assertIn("--replace", workflow)
        self.assertIn("scripts/check_domain_profile.py", workflow)
        self.assertIn(
            "python -m pip --python .venv install \\\n"
            "            --no-compile --require-hashes -r requirements-lock.txt",
            workflow,
        )
        self.assertNotIn(".venv/bin/python -m pip", workflow)
        self.assertNotIn("--acceptance-review evaluation/acceptance-review.json", workflow)
        self.assertIn(
            "v0.3.0 candidate calibration retrieval diagnostic",
            workflow,
        )
        self.assertIn(
            '--output "${RUNNER_TEMP}/evaluation-calibration-v0.3.0.json"',
            workflow,
        )
        self.assertIn("--min-expected-target-recall-at-k 0.90", workflow)
        self.assertIn("--min-all-expected-target-success-at-k 1.0", workflow)
        self.assertIn("--min-mrr 0.80", workflow)
        self.assertIn(
            ".venv/bin/python -B scripts/check_release_evidence.py",
            workflow,
        )
        self.assertIn(
            "--manifest validation/candidate-v0.3.0/final-g9/release-evidence.json",
            workflow,
        )
        self.assertNotIn(
            "run: .venv/bin/python -B scripts/check_release_evidence.py",
            workflow,
        )
        self.assertIn("--candidate-only", workflow)
        self.assertIn("PR_BASE_SHA: ${{ github.event.pull_request.base.sha }}", workflow)
        self.assertIn(
            'git merge-base --is-ancestor "${PR_BASE_SHA}" HEAD',
            workflow,
        )
        self.assertIn(
            'git rev-list --reverse "${PR_BASE_SHA}..HEAD"',
            workflow,
        )
        self.assertIn(
            'OKF_CANDIDATE_COMMIT_SHA=${candidate_sha}',
            workflow,
        )
        self.assertIn(
            '--candidate-commit-sha "${OKF_CANDIDATE_COMMIT_SHA}"',
            workflow,
        )
        self.assertNotIn('--candidate-commit-sha "$(git rev-parse HEAD)"', workflow)
        candidate_pr_gate = (
            "github.event_name == 'pull_request' &&\n"
            "          needs.impact.outputs.full_validation == 'true' &&\n"
            "          github.head_ref == 'candidate/v0.3.0'"
        )
        self.assertEqual(3, workflow.count(candidate_pr_gate))
        pull_request_evidence_step = workflow.index(
            "- name: Validate pull-request G1-G9 evidence against the resolved "
            "candidate"
        )
        default_branch_evidence_step = workflow.index(
            "- name: Validate default-branch G1-G9 evidence against its manifest"
        )
        approval_step = workflow.index(
            "- name: Confirm the exact release root is approved"
        )
        pull_request_evidence = workflow[
            pull_request_evidence_step:default_branch_evidence_step
        ]
        default_branch_evidence = workflow[
            default_branch_evidence_step:approval_step
        ]
        self.assertIn(
            candidate_pr_gate,
            pull_request_evidence,
        )
        self.assertIn(
            '--candidate-commit-sha "${OKF_CANDIDATE_COMMIT_SHA}"',
            pull_request_evidence,
        )
        self.assertIn(
            "github.event_name != 'pull_request'",
            default_branch_evidence,
        )
        self.assertIn(
            "github.ref == format('refs/heads/{0}', "
            "github.event.repository.default_branch)",
            default_branch_evidence,
        )
        self.assertNotIn(
            "--candidate-commit-sha",
            default_branch_evidence,
        )
        self.assertNotIn("evaluation/latest-report.json", workflow)
        self.assertIn("github.event_name != 'pull_request'", workflow)
        self.assertIn("git diff --exit-code -- bundle", workflow)
        self.assertIn(
            "scripts/check_release_transition.py",
            workflow,
        )
        self.assertNotIn("git ls-files --others --exclude-standard", workflow)

    def test_release_only_checks_do_not_block_ordinary_pull_requests(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text()
        steps = workflow_steps(workflow)
        release_pull_request_steps = (
            "Resolve the immutable candidate anchor for a pull request",
            "Validate the committed candidate tree on a pull request",
            "Validate pull-request G1-G9 evidence against the resolved candidate",
        )
        for name in release_pull_request_steps:
            with self.subTest(name=name):
                self.assertIn("github.event_name == 'pull_request'", steps[name])
                self.assertIn(
                    "needs.impact.outputs.full_validation == 'true'",
                    steps[name],
                )
                self.assertIn("github.head_ref == 'candidate/v0.3.0'", steps[name])
        for name in (
            "Validate the locked Stage 1 profile",
            "Build offline from the frozen snapshot",
            "Run deterministic and safety tests",
            "Run the v0.3.0 candidate calibration retrieval diagnostic",
            "Confirm committed and verified publication bytes match",
        ):
            with self.subTest(name=name):
                self.assertIn(
                    "if: needs.impact.outputs.full_validation == 'true'",
                    steps[name],
                )

    def test_routine_update_lane_is_bounded_and_fails_closed(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text()
        steps = workflow_steps(workflow)
        impact = steps["Classify the change and select the validation closure"]
        routine = steps["Validate a bounded routine repository update"]

        for required in (
            ".manual_review_required | not",
            ".stage1_review_required | not",
            ".affected.generated_artifacts | length == 0",
            ".explained_generated_paths | length == 0",
            ".unexplained_generated_paths | length == 0",
            '([.matched_stages[].id] - ["documentation"])',
            "full_validation=true",
            "routine_update=false",
        ):
            with self.subTest(required=required):
                self.assertIn(required, impact)

        self.assertIn("--base \"${impact_base}\"", impact)
        self.assertIn("--head HEAD", impact)
        self.assertIn("EVENT_NAME", impact)
        self.assertIn("PUSH_BEFORE_SHA", impact)
        self.assertIn(
            "if: needs.impact.outputs.routine_update == 'true'",
            routine,
        )
        self.assertIn("tests.test_links tests.test_traceability", routine)
        self.assertIn("scripts/check_okf.py", routine)
        self.assertNotIn("scripts/build.py", routine)
        self.assertNotIn("unittest discover", routine)

    def test_post_release_maintenance_uses_the_immutable_evidence_anchor(
        self,
    ) -> None:
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text()
        steps = workflow_steps(workflow)
        impact = steps["Classify the change and select the validation closure"]
        historical = steps[
            "Validate immutable v0.3.0 evidence after repository maintenance"
        ]
        current_head = steps[
            "Validate default-branch G1-G9 evidence against its manifest"
        ]

        self.assertIn(
            "OKF_V030_EVIDENCE_COMMIT_SHA: "
            "1d708e39f2cde19610d43c5a7f5e36e4a2f947bc",
            workflow,
        )
        for required in (
            "post_release_maintenance=true",
            "post_release_maintenance=false",
            "(.unmatched_paths | length > 0)",
            "(.unmatched_paths - [",
            '"okf.publication.json"',
            '"scripts/check_documentation_lockstep.py"',
            '"tests/test_documentation_lockstep.py"',
            "(.changed_paths - [",
            '"docs/architecture.md"',
            '"docs/maintenance-and-reproducibility.md"',
            '"tests/test_build_semantics.py"',
            '"tests/test_workflow.py"',
            "all(.matched_stages[]; (.causal_build_input_matches | length) == 0)",
            "^(bundle|validation|source|domain-profile|research|governance|personas|evaluation|contracts|schemas)/",
        ):
            with self.subTest(required=required):
                self.assertIn(required, impact)

        for required in (
            "needs.impact.outputs.full_validation == 'true'",
            "needs.impact.outputs.post_release_maintenance == 'true'",
            "github.event_name == 'workflow_dispatch'",
            "git merge-base --is-ancestor",
            "git log -m --format= --name-only",
            "A protected candidate or evidence path changed after v0.3.0",
            "git diff --exit-code",
            "bundle validation source domain-profile research governance",
            "git worktree add --detach",
            "git worktree remove",
            "trap cleanup_historical_worktree EXIT INT TERM",
            '--repository-root "${historical_root}"',
            "--evidence-commit-sha",
            "refs/tags/v0.3.0^{commit}",
        ):
            with self.subTest(required=required):
                self.assertIn(required, historical)

        self.assertIn(
            "needs.impact.outputs.post_release_maintenance != 'true'",
            current_head,
        )
        self.assertIn("github.event_name != 'workflow_dispatch'", current_head)

    def test_build_has_an_honest_preinvocation_single_writer_contract(
        self,
    ) -> None:
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text()
        build_step = workflow_steps(workflow)[
            "Build offline from the frozen snapshot"
        ]
        required_fragments = (
            "okf-landregistry-build.lock",
            'mkdir -m 700 "${runtime_lock}"',
            "trap cleanup_runtime_lock EXIT INT TERM",
            "test -d .venv",
            "test ! -L .venv",
            "test -x .venv/bin/python",
            "-name '*.pth'",
            "-name '*.py[co]'",
            "-name sitecustomize.py",
            "-name usercustomize.py",
            'test ! -L "${runtime_cache}"',
            'test -z "$(find "${runtime_cache}" -mindepth 1 -print -quit)"',
            'rmdir "${runtime_lock}"',
            "trap - EXIT INT TERM",
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, build_step)
        self.assertLess(
            build_step.index('mkdir -m 700 "${runtime_lock}"'),
            build_step.index(".venv/bin/python -I -B -X"),
        )
        self.assertLess(
            build_step.index(".venv/bin/python -I -B -X"),
            build_step.rindex('rmdir "${runtime_lock}"'),
        )

        for relative in (
            "README.md",
            "docs/architecture.md",
            "docs/maintenance-and-reproducibility.md",
            "docs/v0.3.0-release-tracker-and-assurance-runbook.md",
        ):
            prose = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(document=relative):
                self.assertIn(
                    "does not attest the\nexact source bytes already executed",
                    prose,
                )
                self.assertIn(
                    "not cryptographic proof against an uncooperative concurrent "
                    "mutator",
                    prose.replace("\n", " "),
                )

    def test_exact_inventory_is_rechecked_immediately_before_upload(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text()
        steps = workflow_steps(workflow)
        names = list(steps)
        upload = names.index("Upload the exact verified artefact")
        self.assertEqual(
            "Reconfirm the exact approved bundle inventory before upload",
            names[upload - 1],
        )
        pre_upload = steps[names[upload - 1]]
        self.assertIn("deployment-identity", pre_upload)
        self.assertIn("bundle-inventory", pre_upload)
        self.assertIn("remote-binding --remote origin", pre_upload)
        self.assertIn(
            "refs/heads/main:refs/remotes/origin/main",
            pre_upload,
        )
        self.assertIn('--remote-default-sha "${REMOTE_DEFAULT_SHA}"', pre_upload)
        self.assertIn(
            '--expected-root "${EXPECTED_RELEASE_ROOT_SHA256}"', pre_upload
        )
        self.assertIn("inputs.expected_commit_sha", pre_upload)
        self.assertIn("inputs.expected_release_root_sha256", pre_upload)
        self.assertIn("vars.OKF_RELEASE_ROOT_SHA256 != ''", pre_upload)

    def test_without_pip_virtual_environment_excludes_installer_distribution(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = Path(temporary) / ".venv"
            subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "venv",
                    "--without-pip",
                    str(environment),
                ],
                check=True,
                capture_output=True,
            )
            interpreter = environment / "bin" / "python"
            inventory = subprocess.run(
                [
                    str(interpreter),
                    "-B",
                    "-c",
                    "import importlib.metadata as m; "
                    "print('\\n'.join(sorted(d.metadata['Name'].lower() "
                    "for d in m.distributions())))",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertNotIn("pip", inventory.stdout.splitlines())
            pip_import = subprocess.run(
                [str(interpreter), "-B", "-c", "import pip"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, pip_import.returncode)

    def test_external_pip_installs_into_without_pip_target_without_adding_pip(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = root / ".venv"
            subprocess.run(
                [
                    sys._base_executable,
                    "-B",
                    "-m",
                    "venv",
                    "--without-pip",
                    environment,
                ],
                check=True,
                capture_output=True,
            )
            wheel = root / "okf_ci_fixture-1.0-py3-none-any.whl"
            distribution = "okf_ci_fixture-1.0.dist-info"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("okf_ci_fixture/__init__.py", "VALUE = 'installed'\n")
                archive.writestr(
                    f"{distribution}/METADATA",
                    "Metadata-Version: 2.1\nName: okf-ci-fixture\nVersion: 1.0\n",
                )
                archive.writestr(
                    f"{distribution}/WHEEL",
                    "Wheel-Version: 1.0\nGenerator: OKF test\n"
                    "Root-Is-Purelib: true\nTag: py3-none-any\n",
                )
                archive.writestr(f"{distribution}/RECORD", "")
            subprocess.run(
                [
                    sys._base_executable,
                    "-B",
                    "-m",
                    "pip",
                    "--python",
                    environment,
                    "install",
                    "--no-index",
                    "--no-deps",
                    "--no-compile",
                    wheel,
                ],
                check=True,
                capture_output=True,
            )
            interpreter = environment / "bin" / "python"
            observation = subprocess.run(
                [
                    interpreter,
                    "-B",
                    "-c",
                    "import importlib.metadata as m,okf_ci_fixture;"
                    "print(okf_ci_fixture.VALUE);"
                    "print(','.join(sorted((d.metadata.get('Name') or '').lower() "
                    "for d in m.distributions())))",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            self.assertEqual("installed", observation[0])
            self.assertIn("okf-ci-fixture", observation[1].split(","))
            self.assertNotIn("pip", observation[1].split(","))

    def test_documented_sequence_preserves_the_exact_clean_runtime(self) -> None:
        """Exercise the release command order in a real offline clean-room venv."""

        with (
            tempfile.TemporaryDirectory() as repository_name,
            tempfile.TemporaryDirectory() as cache_parent_name,
        ):
            repository = Path(repository_name)
            cache_parent = Path(cache_parent_name)
            environment = repository / ".venv"
            subprocess.run(
                [
                    sys._base_executable,
                    "-B",
                    "-m",
                    "venv",
                    "--without-pip",
                    environment,
                ],
                check=True,
                capture_output=True,
            )

            wheel = repository / "okf_ci_fixture-1.0-py3-none-any.whl"
            distribution = "okf_ci_fixture-1.0.dist-info"
            wheel_members = {
                "okf_ci_fixture/__init__.py": b"VALUE = 'clean-runtime'\n",
                f"{distribution}/METADATA": (
                    b"Metadata-Version: 2.1\nName: okf-ci-fixture\nVersion: 1.0\n"
                ),
                f"{distribution}/WHEEL": (
                    b"Wheel-Version: 1.0\nGenerator: OKF test\n"
                    b"Root-Is-Purelib: true\nTag: py3-none-any\n"
                ),
            }
            record_rows = []
            for member_name, content in sorted(wheel_members.items()):
                digest = base64.urlsafe_b64encode(
                    hashlib.sha256(content).digest()
                ).decode("ascii").rstrip("=")
                record_rows.append(
                    f"{member_name},sha256={digest},{len(content)}"
                )
            record_rows.append(f"{distribution}/RECORD,,")
            wheel_members[f"{distribution}/RECORD"] = (
                "\n".join(record_rows) + "\n"
            ).encode("utf-8")
            with zipfile.ZipFile(wheel, "w") as archive:
                for member_name, content in wheel_members.items():
                    archive.writestr(member_name, content)
            wheel_sha256 = hashlib.sha256(wheel.read_bytes()).hexdigest()
            lock = repository / "requirements-lock.txt"
            lock.write_text(
                "okf-ci-fixture==1.0 \\\n"
                f"    --hash=sha256:{wheel_sha256}\n",
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys._base_executable,
                    "-B",
                    "-m",
                    "pip",
                    "--python",
                    environment,
                    "install",
                    "--no-index",
                    "--find-links",
                    repository,
                    "--no-compile",
                    "--require-hashes",
                    "-r",
                    lock,
                ],
                check=True,
                capture_output=True,
            )

            scripts = repository / "scripts"
            scripts.mkdir()
            (scripts / "python_runtime_contract.py").write_bytes(
                (ROOT / "scripts" / "python_runtime_contract.py").read_bytes()
            )
            observer = scripts / "observe.py"
            observer.write_text(
                "import json,pathlib,sys\n"
                "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))\n"
                "from python_runtime_contract import observe_python_runtime\n"
                "root = pathlib.Path(__file__).resolve().parents[1]\n"
                "print(json.dumps(observe_python_runtime(\n"
                "    root, (root / 'requirements-lock.txt').read_bytes()\n"
                "), sort_keys=True))\n",
                encoding="utf-8",
            )
            interpreter = environment / "bin" / "python"
            minimal_environment = {"LANG": "C", "LC_ALL": "C", "TZ": "UTC"}

            def observe_runtime(label: str) -> dict[str, object]:
                cache = cache_parent / label
                cache.mkdir(mode=0o700)
                result = subprocess.run(
                    [
                        interpreter,
                        "-I",
                        "-B",
                        "-X",
                        f"pycache_prefix={cache}",
                        observer,
                    ],
                    cwd=repository,
                    env=minimal_environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual([], list(cache.iterdir()))
                return json.loads(result.stdout)

            initial_build_runtime = observe_runtime("initial-build")
            phases = (
                "tests",
                "evaluate",
                "checks",
                "second-build-preflight",
                "package-g8",
            )
            for phase in phases:
                result = subprocess.run(
                    [
                        interpreter,
                        "-B",
                        "-c",
                        (
                            "import importlib.metadata as m,okf_ci_fixture; "
                            "assert okf_ci_fixture.VALUE == 'clean-runtime'; "
                            "assert 'pip' not in {"
                            "(d.metadata.get('Name') or '').lower() "
                            "for d in m.distributions()}"
                        ),
                    ],
                    cwd=repository,
                    env=minimal_environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, result.returncode, f"{phase}: {result.stderr}")

            second_build_runtime = observe_runtime("second-build")
            metadata_runtime = observe_runtime("metadata")
            self.assertEqual(initial_build_runtime, second_build_runtime)
            self.assertEqual(initial_build_runtime, metadata_runtime)
            self.assertEqual(
                [],
                [path for path in environment.rglob("*") if path.suffix == ".pyc"],
            )
            self.assertEqual(
                [],
                [path for path in environment.rglob("*") if path.suffix == ".pth"],
            )
            self.assertEqual(
                ["okf-ci-fixture"],
                [row["name"] for row in initial_build_runtime["packages"]],
            )

    def test_child_python_matrix_does_not_write_runtime_or_repository_bytecode(
        self,
    ) -> None:
        """All representative child modes preserve the pre-existing inventory."""

        self.assertTrue((ROOT / ".venv").is_dir())
        before = prohibited_python_artifact_inventory()

        import_result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                (
                    "import cachetools,frozendict,jsonschema,lxml.etree; "
                    "import pyld.jsonld,ruamel.yaml"
                ),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, import_result.returncode, import_result.stderr)

        cli_result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / "scripts" / "check_release_evidence.py"),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(2, cli_result.returncode, cli_result.stderr)

        with tempfile.TemporaryDirectory() as temporary:
            helper = Path(temporary) / "python-helper"
            helper.write_text(
                f"#!{sys.executable} -B\n"
                "import cachetools,frozendict,jsonschema,lxml.etree\n"
                "import pyld.jsonld,ruamel.yaml\n",
                encoding="utf-8",
            )
            helper.chmod(0o755)
            helper_result = subprocess.run(
                [str(helper)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, helper_result.returncode, helper_result.stderr)

        self.assertEqual(before, prohibited_python_artifact_inventory())

    def test_all_executable_python_children_explicitly_disable_bytecode(
        self,
    ) -> None:
        """Guard every statically identifiable Python child and test command."""

        self.assertEqual([], python_child_contract_violations())
        graph = json.loads(
            (ROOT / "governance" / "artifact-dependency-graph.json").read_text(
                encoding="utf-8"
            )
        )
        for test in graph["tests"]:
            command = test["command"]
            with self.subTest(test=test["id"]):
                self.assertGreaterEqual(len(command), 2)
                self.assertEqual(".venv/bin/python", command[0])
                self.assertEqual("-B", command[1])

    def test_independent_validation_branches_converge_before_deployment(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text()
        impact = workflow[workflow.index("  impact:") : workflow.index("  verify:")]
        verify = workflow[workflow.index("  verify:") : workflow.index("  full-tests:")]
        full_tests = workflow[
            workflow.index("  full-tests:") : workflow.index("  deploy:")
        ]
        deploy = workflow[workflow.index("  deploy:") :]

        self.assertIn("timeout-minutes: 10", impact)
        self.assertIn("scripts/check_documentation_lockstep.py", impact)
        self.assertIn("routine_update: ${{ steps.impact.outputs.routine_update }}", impact)
        self.assertIn("needs: impact", verify)
        self.assertIn("timeout-minutes: 45", verify)
        self.assertNotIn("Run deterministic and safety tests", verify)
        self.assertIn("needs: impact", full_tests)
        self.assertIn("timeout-minutes: 30", full_tests)
        self.assertIn("Run deterministic and safety tests", full_tests)
        self.assertNotIn("scripts/build.py", full_tests)
        self.assertIn("needs: [verify, full-tests]", deploy)
        self.assertIn("timeout-minutes: 10", deploy)
        self.assertIn("'pages-publication'", workflow)
        self.assertIn(
            "cancel-in-progress: ${{ github.event_name != 'workflow_dispatch' }}",
            workflow,
        )

    def test_push_verifies_but_only_exact_dispatch_can_deploy(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text()
        self.assertIn("expected_commit_sha:", workflow)
        self.assertIn("expected_release_root_sha256:", workflow)
        steps = workflow_steps(workflow)
        for name in (
            "Confirm the exact release root is approved",
            "Configure GitHub Pages",
            "Reconfirm the exact approved bundle inventory before upload",
            "Upload the exact verified artefact",
        ):
            with self.subTest(name=name):
                self.assertIn("github.event_name == 'workflow_dispatch'", steps[name])
                self.assertNotIn("github.event_name != 'pull_request'", steps[name])
        approval = steps["Confirm the exact release root is approved"]
        self.assertIn("deployment-identity", approval)
        self.assertIn("github.sha", approval)
        self.assertIn("inputs.expected_commit_sha", approval)
        self.assertIn("inputs.expected_release_root_sha256", approval)
        self.assertIn("vars.OKF_RELEASE_ROOT_SHA256", approval)
        deploy = workflow[workflow.index("  deploy:") :]
        self.assertIn("github.event_name == 'workflow_dispatch'", deploy)
        self.assertNotIn("github.event_name != 'pull_request'", deploy)

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
            "OKF_RELEASE_ROOT_SHA256: "
            "${{ inputs.expected_release_root_sha256 }}",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()

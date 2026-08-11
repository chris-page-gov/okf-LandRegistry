#!/usr/bin/env python3
"""Create SPDX and provenance metadata for an exact packaged candidate."""

from __future__ import annotations

import argparse
import ctypes
import errno
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import sys
from typing import Any, Callable

from ruamel.yaml import YAML

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.check_release_evidence import (
    CandidateIdentity,
    MAX_JSON_BYTES,
    ReleaseEvidenceError,
    _git_command_bytes,
    canonical_release_version,
    governed_release_coordinates_from_documents,
    load_json_bytes,
    read_candidate_blob_bytes,
    read_repository_file_bytes,
    sha256_bytes,
    validate_archive_receipt_document,
    validate_committed_candidate_closure,
    validate_governed_candidate_commit,
)
from scripts.change_impact import ChangeImpactError, normalise_repository_path
from scripts.python_runtime_contract import (
    PythonRuntimeContractError,
    observe_python_runtime,
    parse_hashed_requirements_lock,
)


ROOT = Path(__file__).resolve().parents[1]
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_GITHUB_ACTION_OWNER = (
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?"
)
_GITHUB_ACTION_SEGMENT = (
    r"(?:\.github|[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?)"
)
EXTERNAL_ACTION = re.compile(
    rf"^({_GITHUB_ACTION_OWNER}/{_GITHUB_ACTION_SEGMENT}"
    rf"(?:/{_GITHUB_ACTION_SEGMENT}){{0,14}})@([0-9a-f]{{40}})$"
)
CONTAINER_ACTION = re.compile(
    r"^docker://([^@\s]+)@(sha256:[0-9a-f]{64})$"
)
PROVENANCE_MATERIAL_PATHS = (
    "domain-profile/CHECKSUMS.sha256",
    "requirements-lock.txt",
    ".github/workflows/pages.yml",
    "contracts/okf-explorer.consumer-lock.json",
    "pages/search-contract.json",
    "evaluation/questions.json",
)
METADATA_OUTPUT_NAMES = frozenset({"provenance.json", "sbom.spdx.json"})
MAX_METADATA_OUTPUT_BYTES = 5 * 1024 * 1024
MAX_METADATA_INPUT_BYTES = 5 * 1024 * 1024
MAX_METADATA_INPUT_AGGREGATE_BYTES = 50 * 1024 * 1024
MAX_WORKFLOW_JOBS = 256
MAX_WORKFLOW_STEPS = 4_096
MAX_WORKFLOW_USES = 4_096
SECURE_METADATA_PUBLICATION_SUPPORTED = (
    hasattr(os, "O_NOFOLLOW")
    and all(
        function in os.supports_dir_fd
        for function in (os.open, os.stat, os.mkdir, os.unlink, os.rmdir)
    )
    and os.stat in os.supports_follow_symlinks
)


def _rename_directory_no_replace(
    parent_descriptor: int,
    source_name: str,
    target_name: str,
) -> None:
    """Atomically rename one sibling directory without replacing any target."""

    library = ctypes.CDLL(None, use_errno=True)
    source = os.fsencode(source_name)
    target = os.fsencode(target_name)
    if sys.platform == "darwin":
        function = getattr(library, "renameatx_np", None)
        flag = 0x00000004  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        function = getattr(library, "renameat2", None)
        flag = 1  # RENAME_NOREPLACE
    else:
        function = None
        flag = 0
    if function is None:
        raise ReleaseEvidenceError(
            "atomic no-replace directory publication is unavailable on this platform"
        )
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    if function(parent_descriptor, source, parent_descriptor, target, flag) == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(error_number, os.strerror(error_number), target_name)
    raise OSError(error_number, os.strerror(error_number), target_name)


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"


def _utf8_text(value: bytes, *, label: str) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeError as exc:
        raise ReleaseEvidenceError(f"{label} is not valid UTF-8") from exc


def locked_packages(value: bytes) -> list[tuple[str, str]]:
    """Return provenance rows from the shared strict hashed-lock parser."""

    try:
        packages = parse_hashed_requirements_lock(value)
    except PythonRuntimeContractError as exc:
        raise ReleaseEvidenceError(str(exc)) from exc
    return [
        (package.normalised_name, package.version)
        for package in packages
    ]


def action_pins(value: bytes) -> list[dict[str, str]]:
    """Enumerate every active workflow ``uses:`` value without omission.

    External actions are admitted only at a full Git commit.  Container
    actions require an immutable SHA-256 image digest.  Local actions are
    rejected until the release provenance contract can bind their complete
    action directory rather than merely recording a path.
    """

    text = _utf8_text(value, label="candidate Pages workflow")
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    try:
        workflow = yaml.load(text)
    except Exception as exc:
        raise ReleaseEvidenceError(
            f"candidate Pages workflow is not strict YAML: {exc}"
        ) from exc
    if not isinstance(workflow, dict):
        raise ReleaseEvidenceError("candidate Pages workflow is not a mapping")
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        raise ReleaseEvidenceError(
            "candidate Pages workflow has no non-empty jobs mapping"
        )
    if len(jobs) > MAX_WORKFLOW_JOBS:
        raise ReleaseEvidenceError(
            "candidate Pages workflow exceeds the governed job-count ceiling"
        )

    active_uses: list[tuple[str, str]] = []
    total_steps = 0
    for job_name, job in jobs.items():
        if not isinstance(job_name, str) or not isinstance(job, dict):
            raise ReleaseEvidenceError(
                "candidate Pages workflow contains an invalid job mapping"
            )
        if "uses" in job:
            job_use = job["uses"]
            if not isinstance(job_use, str) or not job_use:
                raise ReleaseEvidenceError(
                    f"workflow job {job_name!r} has a non-string uses value"
                )
            active_uses.append((f"job:{job_name}", job_use))
        steps = job.get("steps", [])
        if not isinstance(steps, list):
            raise ReleaseEvidenceError(
                f"workflow job {job_name!r} steps value is not a list"
            )
        total_steps += len(steps)
        if total_steps > MAX_WORKFLOW_STEPS:
            raise ReleaseEvidenceError(
                "candidate Pages workflow exceeds the governed step-count ceiling"
            )
        for step_index, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ReleaseEvidenceError(
                    f"workflow job {job_name!r} step {step_index} is not a mapping"
                )
            if "uses" not in step:
                continue
            step_use = step["uses"]
            if not isinstance(step_use, str) or not step_use:
                raise ReleaseEvidenceError(
                    f"workflow job {job_name!r} step {step_index} has a "
                    "non-string uses value"
                )
            active_uses.append((f"job:{job_name}:step:{step_index}", step_use))
            if len(active_uses) > MAX_WORKFLOW_USES:
                raise ReleaseEvidenceError(
                    "candidate Pages workflow exceeds the governed uses-count ceiling"
                )

    rows: list[dict[str, str]] = []
    for scope, value_token in active_uses:
        if value_token.startswith("./"):
            try:
                normalise_repository_path(value_token.removeprefix("./"))
            except ChangeImpactError as exc:
                raise ReleaseEvidenceError(
                    f"workflow local action path is unsafe at {scope}: "
                    f"{value_token!r}"
                ) from exc
            raise ReleaseEvidenceError(
                "workflow local actions are not yet governed by complete action-"
                f"directory provenance: {scope} {value_token!r}"
            )
        container = CONTAINER_ACTION.fullmatch(value_token)
        if value_token.startswith("docker://"):
            if container is None:
                raise ReleaseEvidenceError(
                    "workflow container action is not pinned by an immutable "
                    f"SHA-256 digest at {scope}: {value_token!r}"
                )
            image, digest = container.groups()
            rows.append(
                {
                    "kind": "container-image",
                    "action": image,
                    "revision": digest,
                    "scope": scope,
                }
            )
            continue
        external = EXTERNAL_ACTION.fullmatch(value_token)
        if external is None:
            raise ReleaseEvidenceError(
                "workflow external action is not pinned by a full 40-character "
                f"commit at {scope}: {value_token!r}"
            )
        action, commit = external.groups()
        rows.append(
            {
                "kind": "github-action",
                "action": action,
                "revision": commit,
                "scope": scope,
            }
        )
    if not active_uses:
        raise ReleaseEvidenceError("workflow contains no active uses values")
    if len(rows) != len(active_uses):
        raise ReleaseEvidenceError(
            "workflow provenance did not enumerate every active uses value"
        )
    return rows


def build_invocation(
    build_receipt: dict[str, Any],
    *,
    build_config: dict[str, Any],
    acquisition_manifest_bytes: bytes,
) -> list[str]:
    """Recover the governed frozen-snapshot build command from its receipt."""

    try:
        manifest_value = build_receipt["snapshot"]["acquisition_snapshot"][
            "manifest_path"
        ]
    except (KeyError, TypeError) as exc:
        raise ReleaseEvidenceError(
            "build receipt lacks the acquisition snapshot manifest path"
        ) from exc
    if not isinstance(manifest_value, str):
        raise ReleaseEvidenceError(
            "build receipt acquisition snapshot manifest path must be a string"
        )
    manifest_path = PurePosixPath(manifest_value)
    if (
        manifest_path.is_absolute()
        or ".." in manifest_path.parts
        or manifest_path.name != "manifest.json"
        or manifest_path.parts[:2] != ("source", "snapshots")
        or len(manifest_path.parts) != 4
    ):
        raise ReleaseEvidenceError(
            "build receipt acquisition snapshot manifest path is not governed"
        )
    acquisition_snapshot = build_receipt.get("snapshot", {}).get(
        "acquisition_snapshot"
    )
    acquisition_digest = (
        acquisition_snapshot.get("source_manifest_sha256")
        if isinstance(acquisition_snapshot, dict)
        else None
    )
    if (
        not acquisition_manifest_bytes
        or not isinstance(acquisition_digest, str)
        or sha256_bytes(acquisition_manifest_bytes) != acquisition_digest
    ):
        raise ReleaseEvidenceError(
            "candidate acquisition snapshot manifest does not match its build receipt"
        )
    runtime = build_receipt.get("python_runtime")
    if not isinstance(runtime, dict):
        raise ReleaseEvidenceError("build receipt lacks its observed Python runtime")
    executable_contract = runtime.get("executable_contract")
    if executable_contract != ".venv/bin/python":
        raise ReleaseEvidenceError(
            "build receipt Python executable contract is not .venv/bin/python"
        )
    (
        _governed_version,
        governed_publication_base,
        _publication_state,
        _generated_at,
        _release_at,
    ) = governed_release_coordinates_from_documents(build_receipt, build_config)
    expected_invocation = [
        executable_contract,
        "-I",
        "-B",
        "-X",
        "pycache_prefix=<private-empty-directory>",
        "scripts/build.py",
        "--snapshot-dir",
        manifest_path.parent.as_posix(),
        "--publication-base",
        governed_publication_base,
        "--replace",
        "--previous-output",
        "<owner-selected-empty-same-filesystem-path>",
    ]
    if build_receipt.get("reproduction_invocation") != expected_invocation:
        raise ReleaseEvidenceError(
            "build receipt reproduction invocation differs from its governed "
            "snapshot and observed runtime"
        )
    return expected_invocation


def observed_python_runtime(lock_bytes: bytes) -> dict[str, Any]:
    """Post-startup verify metadata runtime through the shared authority."""

    try:
        return observe_python_runtime(ROOT, lock_bytes)
    except PythonRuntimeContractError as exc:
        raise ReleaseEvidenceError(str(exc)) from exc


def provenance_materials(
    build_receipt: dict[str, Any],
    *,
    read_candidate: Callable[[str, str, int], bytes],
) -> list[dict[str, str]]:
    """Return every governed input whose digest is declared in provenance."""

    try:
        snapshot_path = build_receipt["snapshot"]["manifest_path"]
    except (KeyError, TypeError) as exc:
        raise ReleaseEvidenceError(
            "build receipt lacks the composite snapshot manifest path"
        ) from exc
    if not isinstance(snapshot_path, str):
        raise ReleaseEvidenceError(
            "build receipt composite snapshot manifest path must be a string"
        )
    paths = (
        PROVENANCE_MATERIAL_PATHS[0],
        snapshot_path,
        *PROVENANCE_MATERIAL_PATHS[1:],
    )
    rows: list[dict[str, str]] = []
    for value in paths:
        try:
            normalise_repository_path(value)
        except ChangeImpactError as exc:
            raise ReleaseEvidenceError(
                f"provenance material path is not repository-relative: {value!r}"
            ) from exc
        content = read_candidate(
            value,
            "release provenance material",
            MAX_METADATA_INPUT_BYTES,
        )
        rows.append({"path": value, "sha256": sha256_bytes(content)})
    return rows


class CandidateMetadataReader:
    """Read and cache a bounded aggregate of immutable candidate blobs."""

    def __init__(self, repository_root: Path, candidate_commit_sha: str) -> None:
        self.repository_root = repository_root.resolve()
        self.candidate_commit_sha = candidate_commit_sha
        self.cache: dict[str, bytes] = {}
        self.aggregate_bytes = 0

    def read(self, name: str, purpose: str, max_bytes: int) -> bytes:
        try:
            normalise_repository_path(name)
        except ChangeImpactError as exc:
            raise ReleaseEvidenceError(
                f"candidate metadata input path is unsafe: {name!r}"
            ) from exc
        if max_bytes < 0:
            raise ReleaseEvidenceError(
                f"candidate metadata input has an invalid byte limit: {name!r}"
            )
        cached = self.cache.get(name)
        if cached is not None:
            if len(cached) > max_bytes:
                raise ReleaseEvidenceError(
                    f"candidate {purpose} exceeds the {max_bytes}-byte limit: {name!r}"
                )
            return cached
        remaining = MAX_METADATA_INPUT_AGGREGATE_BYTES - self.aggregate_bytes
        if remaining <= 0:
            raise ReleaseEvidenceError(
                "candidate release metadata inputs exceed the aggregate byte limit"
            )
        content = read_candidate_blob_bytes(
            self.repository_root,
            candidate_commit_sha=self.candidate_commit_sha,
            relative_name=name,
            purpose=purpose,
            max_bytes=min(max_bytes, remaining),
        )
        self.aggregate_bytes += len(content)
        self.cache[name] = content
        return content


def checked_candidate(commit: str) -> CandidateIdentity:
    if COMMIT_SHA.fullmatch(commit) is None:
        raise ReleaseEvidenceError(
            "candidate commit must be 40 lowercase hexadecimal characters"
        )
    result = _git_command_bytes(
        ROOT,
        ["rev-parse", "--verify", f"{commit}^{{commit}}"],
        maximum_stdout_bytes=64,
    )
    if result.returncode != 0:
        diagnostic = result.stderr.decode(
            "utf-8", errors="replace"
        ).strip()
        raise ReleaseEvidenceError(
            "candidate commit does not exist"
            + (f": {diagnostic}" if diagnostic else "")
        )
    try:
        resolved = result.stdout.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ReleaseEvidenceError(
            "candidate commit resolved to a non-ASCII identity"
        ) from exc
    if resolved != commit:
        raise ReleaseEvidenceError("candidate commit does not exist")
    validate_governed_candidate_commit(
        ROOT,
        candidate_commit_sha=commit,
        build_receipt_path=Path("bundle/build-receipt.json"),
    )
    candidate, _governed_count = validate_committed_candidate_closure(
        ROOT,
        candidate_commit_sha=commit,
        checksums_path=Path("bundle/CHECKSUMS.sha256"),
        profile_checksums_path=Path("domain-profile/CHECKSUMS.sha256"),
        build_receipt_path=Path("bundle/build-receipt.json"),
    )
    return candidate


def _exact_repository_path(
    value: Path,
    *,
    expected: PurePosixPath,
    purpose: str,
) -> PurePosixPath:
    """Return one safe repository path only when it is exactly governed."""

    name = value.as_posix()
    try:
        normalise_repository_path(name)
    except ChangeImpactError as exc:
        raise ReleaseEvidenceError(
            f"{purpose} must be a safe repository-relative path: {name!r}"
        ) from exc
    relative = PurePosixPath(name)
    if relative != expected:
        raise ReleaseEvidenceError(
            f"{purpose} must be exactly {expected.as_posix()!r}; "
            f"received {name!r}"
        )
    return relative


def exact_archive_receipt_path(value: Path, *, version: str) -> PurePosixPath:
    """Require the designated reproducible archive receipt from the runbook."""

    version = canonical_release_version(version, label="metadata release version")
    expected = (
        PurePosixPath("validation")
        / f"candidate-v{version}"
        / "evidence"
        / "release-candidate-archive-a.json"
    )
    return _exact_repository_path(
        value,
        expected=expected,
        purpose="archive receipt",
    )


def safe_output_directory(
    value: Path,
    *,
    version: str,
    repository_root: Path = ROOT,
) -> Path:
    """Resolve the one governed metadata directory without traversing it."""

    version = canonical_release_version(version, label="metadata release version")
    expected = (
        PurePosixPath("validation")
        / f"candidate-v{version}"
        / "evidence"
        / "release-metadata"
    )
    relative = _exact_repository_path(
        value,
        expected=expected,
        purpose="metadata output directory",
    )
    return repository_root.resolve().joinpath(*relative.parts)


def write_metadata_outputs(
    repository_root: Path,
    output_directory: Path,
    outputs: dict[Path, bytes],
) -> None:
    """Publish one complete two-file directory atomically and without replacement.

    An absent governed directory appears in one no-replace rename only after
    both durable files exist in a private sibling stage.  An existing directory
    is never modified: its exact inventory, file types and bytes must already
    equal the generated set.  Cleanup failure can therefore leave only a hidden
    staging directory, never a partly published release-metadata directory.
    """

    if not SECURE_METADATA_PUBLICATION_SUPPORTED:
        raise ReleaseEvidenceError(
            "release metadata publication requires POSIX no-follow and "
            "directory-descriptor support"
        )

    root = repository_root.resolve()
    try:
        output_relative = output_directory.relative_to(root)
    except ValueError as exc:
        raise ReleaseEvidenceError(
            "metadata output directory is outside the repository"
        ) from exc
    if not output_relative.parts or ".." in output_relative.parts:
        raise ReleaseEvidenceError(
            "metadata output directory is not repository-relative"
        )

    document_rows: list[tuple[str, bytes]] = []
    for target, content in outputs.items():
        try:
            relative = target.relative_to(output_directory)
        except ValueError as exc:
            raise ReleaseEvidenceError(
                f"metadata output escapes its governed directory: {target}"
            ) from exc
        if len(relative.parts) != 1 or relative.name not in METADATA_OUTPUT_NAMES:
            raise ReleaseEvidenceError(
                f"unsupported metadata output path: {relative.as_posix()!r}"
            )
        if len(content) > MAX_METADATA_OUTPUT_BYTES:
            raise ReleaseEvidenceError(
                f"metadata output is too large: {relative.name!r}"
            )
        document_rows.append((relative.name, content))
    if {name for name, _content in document_rows} != METADATA_OUTPUT_NAMES:
        raise ReleaseEvidenceError(
            "metadata publication requires exactly provenance.json and "
            "sbom.spdx.json"
        )

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    read_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    read_flags |= getattr(os, "O_NOFOLLOW", 0)
    read_flags |= getattr(os, "O_NONBLOCK", 0)
    write_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    write_flags |= getattr(os, "O_CLOEXEC", 0)
    write_flags |= getattr(os, "O_NOFOLLOW", 0)

    def same_inode(first: os.stat_result, second: os.stat_result) -> bool:
        return (
            first.st_dev,
            first.st_ino,
            stat.S_IFMT(first.st_mode),
        ) == (
            second.st_dev,
            second.st_ino,
            stat.S_IFMT(second.st_mode),
        )

    def same_snapshot(first: os.stat_result, second: os.stat_result) -> bool:
        return (
            same_inode(first, second)
            and first.st_size == second.st_size
            and first.st_mtime_ns == second.st_mtime_ns
        )

    def close_all(descriptors: list[int]) -> None:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass

    def open_directory_chain(
        relative: Path, *, create: bool
    ) -> tuple[list[int], list[bool]]:
        descriptors: list[int] = []
        created: list[bool] = []
        try:
            descriptors.append(os.open(root, directory_flags))
            created.append(False)
            for index, part in enumerate(relative.parts, start=1):
                parent = descriptors[-1]
                made = False
                try:
                    before = os.stat(
                        part, dir_fd=parent, follow_symlinks=False
                    )
                except FileNotFoundError:
                    if not create:
                        raise ReleaseEvidenceError(
                            "metadata output directory disappeared during "
                            "publication"
                        ) from None
                    try:
                        os.mkdir(part, mode=0o755, dir_fd=parent)
                        made = True
                    except FileExistsError:
                        pass
                    before = os.stat(
                        part, dir_fd=parent, follow_symlinks=False
                    )
                if not stat.S_ISDIR(before.st_mode):
                    raise ReleaseEvidenceError(
                        "metadata output path contains a non-directory or "
                        f"symbolic link: {Path(*relative.parts[:index])}"
                    )
                try:
                    child = os.open(part, directory_flags, dir_fd=parent)
                except OSError as exc:
                    raise ReleaseEvidenceError(
                        "metadata output directory could not be opened without "
                        f"following links: {Path(*relative.parts[:index])}"
                    ) from exc
                opened = os.fstat(child)
                if not same_inode(before, opened):
                    os.close(child)
                    raise ReleaseEvidenceError(
                        "metadata output directory changed while being opened: "
                        f"{relative.as_posix()!r}"
                    )
                descriptors.append(child)
                created.append(made)
            return descriptors, created
        except BaseException:
            close_all(descriptors)
            raise

    def write_complete_file(parent: int, name: str, content: bytes) -> None:
        descriptor = os.open(name, write_flags, 0o644, dir_fd=parent)
        try:
            view = memoryview(content)
            written = 0
            while written < len(view):
                count = os.write(descriptor, view[written:])
                if count <= 0:
                    raise ReleaseEvidenceError(
                        f"short staged metadata write for {name!r}"
                    )
                written += count
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def read_complete_file(
        parent: int, name: str
    ) -> tuple[bytes, os.stat_result]:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size > MAX_METADATA_OUTPUT_BYTES
        ):
            raise ReleaseEvidenceError(
                f"existing metadata output is not a bounded regular file: {name!r}"
            )
        try:
            descriptor = os.open(name, read_flags, dir_fd=parent)
        except OSError as exc:
            raise ReleaseEvidenceError(
                f"existing metadata output could not be opened safely: {name!r}"
            ) from exc
        try:
            opened = os.fstat(descriptor)
            if not same_snapshot(before, opened):
                raise ReleaseEvidenceError(
                    f"existing metadata output changed: {name!r}"
                )
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = os.read(
                    descriptor,
                    min(64 * 1024, MAX_METADATA_OUTPUT_BYTES + 1 - size),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_METADATA_OUTPUT_BYTES:
                    raise ReleaseEvidenceError(
                        f"existing metadata output is too large: {name!r}"
                    )
            after = os.fstat(descriptor)
            after_path = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not (
                same_snapshot(opened, after)
                and same_snapshot(opened, after_path)
            ):
                raise ReleaseEvidenceError(
                    f"existing metadata output changed: {name!r}"
                )
            return b"".join(chunks), opened
        finally:
            os.close(descriptor)

    def remove_created_chain(
        descriptors: list[int], created: list[bool], parts: tuple[str, ...]
    ) -> None:
        for index in range(len(parts), 0, -1):
            if not created[index]:
                continue
            try:
                visible = os.stat(
                    parts[index - 1],
                    dir_fd=descriptors[index - 1],
                    follow_symlinks=False,
                )
                if not same_inode(visible, os.fstat(descriptors[index])):
                    continue
                os.rmdir(parts[index - 1], dir_fd=descriptors[index - 1])
            except OSError:
                pass

    def verify_directory_chain_visible(
        descriptors: list[int], parts: tuple[str, ...]
    ) -> None:
        """Require every held parent to remain at its governed repository path."""

        for index, part in enumerate(parts, start=1):
            try:
                visible = os.stat(
                    part,
                    dir_fd=descriptors[index - 1],
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise ReleaseEvidenceError(
                    "metadata output parent moved during publication: "
                    f"{Path(*parts[:index])}"
                ) from exc
            if not same_inode(visible, os.fstat(descriptors[index])):
                raise ReleaseEvidenceError(
                    "metadata output parent changed during publication: "
                    f"{Path(*parts[:index])}"
                )

    parent_chain: list[int] = []
    parent_created: list[bool] = []
    stage_root: int | None = None
    stage_name: str | None = None
    published = False
    failed = False

    def open_and_verify_output(parent: int, output_name: str) -> int:
        try:
            before = os.stat(
                output_name,
                dir_fd=parent,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            raise
        if not stat.S_ISDIR(before.st_mode):
            raise ReleaseEvidenceError(
                "release metadata output exists but is not a directory"
            )
        try:
            descriptor = os.open(output_name, directory_flags, dir_fd=parent)
        except OSError as exc:
            raise ReleaseEvidenceError(
                "release metadata output could not be opened without following links"
            ) from exc
        try:
            if not same_inode(before, os.fstat(descriptor)):
                raise ReleaseEvidenceError(
                    "release metadata output changed while being opened"
                )
            inventory: set[str] = set()
            with os.scandir(descriptor) as directory_entries:
                for entry in directory_entries:
                    inventory.add(entry.name)
                    if len(inventory) > len(METADATA_OUTPUT_NAMES):
                        raise ReleaseEvidenceError(
                            "existing release metadata directory contains more "
                            "than the governed two entries"
                        )
            if inventory != METADATA_OUTPUT_NAMES:
                missing = sorted(METADATA_OUTPUT_NAMES - inventory)
                extra = sorted(inventory - METADATA_OUTPUT_NAMES)
                raise ReleaseEvidenceError(
                    "existing release metadata directory is not the exact complete "
                    f"two-file set; missing={missing!r}, extra={extra!r}"
                )
            for name, content in document_rows:
                observed, _snapshot = read_complete_file(descriptor, name)
                if observed != content:
                    raise ReleaseEvidenceError(
                        "refusing to accept differing metadata output: "
                        f"{name!r}"
                    )
            after = os.stat(
                output_name,
                dir_fd=parent,
                follow_symlinks=False,
            )
            if not same_inode(before, after):
                raise ReleaseEvidenceError(
                    "release metadata output changed during exact verification"
                )
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    try:
        parent_chain, parent_created = open_directory_chain(
            output_relative.parent, create=True
        )
        parent_descriptor = parent_chain[-1]
        output_name = output_relative.name

        for _ in range(100):
            candidate_name = f".release-metadata-{secrets.token_hex(12)}"
            try:
                os.mkdir(candidate_name, mode=0o700, dir_fd=parent_descriptor)
                stage_name = candidate_name
                break
            except FileExistsError:
                continue
        if stage_name is None:
            raise ReleaseEvidenceError(
                "could not reserve a release metadata staging directory"
            )
        staged_before = os.stat(
            stage_name, dir_fd=parent_descriptor, follow_symlinks=False
        )
        try:
            stage_root = os.open(
                stage_name, directory_flags, dir_fd=parent_descriptor
            )
        except OSError as exc:
            raise ReleaseEvidenceError(
                "release metadata staging directory could not be opened safely"
            ) from exc
        if not same_inode(staged_before, os.fstat(stage_root)):
            raise ReleaseEvidenceError(
                "release metadata staging directory changed while being opened"
            )

        for name, content in document_rows:
            write_complete_file(stage_root, name, content)
        os.fsync(stage_root)

        try:
            existing_root = open_and_verify_output(
                parent_descriptor,
                output_name,
            )
        except FileNotFoundError:
            existing_root = None
        if existing_root is not None:
            os.close(existing_root)
        else:
            try:
                verify_directory_chain_visible(
                    parent_chain,
                    output_relative.parent.parts,
                )
                _rename_directory_no_replace(
                    parent_descriptor,
                    stage_name,
                    output_name,
                )
                published = True
                stage_name = None
                os.fsync(parent_descriptor)
                verify_directory_chain_visible(
                    parent_chain,
                    output_relative.parent.parts,
                )
            except FileExistsError:
                # A racing writer won the exact output name.  Its directory is
                # acceptable only if it is already the complete expected set.
                existing_root = open_and_verify_output(
                    parent_descriptor,
                    output_name,
                )
                os.close(existing_root)
            except OSError as exc:
                raise ReleaseEvidenceError(
                    "could not atomically publish the complete metadata directory"
                ) from exc

        verified_root = open_and_verify_output(parent_descriptor, output_name)
        try:
            if published and not same_inode(
                os.fstat(stage_root),
                os.fstat(verified_root),
            ):
                raise ReleaseEvidenceError(
                    "published metadata directory differs from the complete stage"
                )
            verify_directory_chain_visible(
                parent_chain,
                output_relative.parent.parts,
            )
        finally:
            os.close(verified_root)
    except BaseException:
        failed = True
        raise
    finally:
        if stage_root is not None and stage_name is not None:
            for name, _content in document_rows:
                try:
                    os.unlink(name, dir_fd=stage_root)
                except OSError:
                    pass
        if stage_name is not None and parent_chain:
            try:
                visible_stage = os.stat(
                    stage_name,
                    dir_fd=parent_chain[-1],
                    follow_symlinks=False,
                )
                if stage_root is None or same_inode(
                    visible_stage, os.fstat(stage_root)
                ):
                    os.rmdir(stage_name, dir_fd=parent_chain[-1])
            except OSError:
                pass
        if stage_root is not None:
            os.close(stage_root)
        if failed and parent_chain and not published:
            remove_created_chain(
                parent_chain,
                parent_created,
                output_relative.parent.parts,
            )
        close_all(parent_chain)


def validate_archive_receipt_binding(
    archive_receipt: dict[str, Any],
    *,
    candidate: CandidateIdentity,
    config: dict[str, Any],
    expected_archive_kind: str,
    repository_root: Path = ROOT,
) -> tuple[str, Path]:
    """Validate an archive receipt against governed and observed archive bytes."""

    governed_version = canonical_release_version(
        config.get("version"),
        label="governed build configuration version",
    )
    publication_state = config.get("publication_state")
    generated_at = config.get("generated_at")
    release_at = config.get("release_at")
    if (
        not isinstance(publication_state, str)
        or not isinstance(generated_at, str)
        or (release_at is not None and not isinstance(release_at, str))
    ):
        raise ReleaseEvidenceError(
            "governed build configuration has invalid archive state coordinates"
        )
    return validate_archive_receipt_document(
        repository_root,
        archive_receipt,
        expected_candidate=candidate,
        expected_version=governed_version,
        expected_publication_state=publication_state,
        expected_generated_at=generated_at,
        expected_release_at=release_at,
        expected_archive_kind=expected_archive_kind,
    )


def expected_release_metadata_documents(
    repository_root: Path,
    *,
    candidate: CandidateIdentity,
    archive_receipt: dict[str, Any],
    observed_runtime: dict[str, Any] | None = None,
) -> tuple[str, dict[str, bytes]]:
    """Derive exact release metadata from one immutable candidate tree."""

    root = repository_root.resolve()
    reader = CandidateMetadataReader(root, candidate.candidate_commit_sha)
    config = load_json_bytes(
        reader.read(
            "source/build-config.json",
            "release build configuration",
            MAX_JSON_BYTES,
        ),
        label="candidate source/build-config.json",
    )
    if (
        config.get("status") != "ai-generated-proof-of-concept"
        or config.get("ai_generated_proof_of_concept") is not True
    ):
        raise ReleaseEvidenceError("build config is not an AI PoC candidate")
    version = canonical_release_version(
        config.get("version"),
        label="candidate source/build-config.json version",
    )
    build_receipt = load_json_bytes(
        reader.read(
            "bundle/build-receipt.json",
            "release build receipt",
            MAX_JSON_BYTES,
        ),
        label="candidate bundle/build-receipt.json",
    )
    try:
        acquisition_manifest_name = build_receipt["snapshot"][
            "acquisition_snapshot"
        ]["manifest_path"]
    except (KeyError, TypeError) as exc:
        raise ReleaseEvidenceError(
            "build receipt lacks the acquisition snapshot manifest path"
        ) from exc
    if not isinstance(acquisition_manifest_name, str):
        raise ReleaseEvidenceError(
            "build receipt acquisition snapshot manifest path must be a string"
        )
    acquisition_manifest_bytes = reader.read(
        acquisition_manifest_name,
        "acquisition snapshot manifest",
        MAX_METADATA_INPUT_BYTES,
    )
    governed_invocation = build_invocation(
        build_receipt,
        build_config=config,
        acquisition_manifest_bytes=acquisition_manifest_bytes,
    )
    requirements_lock_bytes = reader.read(
        "requirements-lock.txt",
        "dependency lock",
        MAX_METADATA_INPUT_BYTES,
    )
    packages = locked_packages(requirements_lock_bytes)
    workflow_bytes = reader.read(
        ".github/workflows/pages.yml",
        "Pages workflow",
        MAX_METADATA_INPUT_BYTES,
    )
    pins = action_pins(workflow_bytes)
    archive_schema, _archive_file = validate_archive_receipt_binding(
        archive_receipt,
        candidate=candidate,
        config=config,
        expected_archive_kind="candidate-a",
        repository_root=root,
    )
    if archive_schema == "okf-hmlr-candidate-archive.v1":
        if (
            archive_receipt.get("publication_state") != "unreleased-candidate"
            or config.get("release_at") is not None
            or config.get("publication_state")
            != "digest-bound-external-evidence"
            or archive_receipt.get("candidate_at") != config.get("generated_at")
        ):
            raise ReleaseEvidenceError(
                "candidate archive does not match the unreleased build configuration"
            )
        created = archive_receipt["candidate_at"]
    else:
        if not isinstance(config.get("release_at"), str):
            raise ReleaseEvidenceError(
                "release archive requires a released build timestamp"
            )
        created = config["release_at"]
    project_spdx = "SPDXRef-Package-okf-landregistry"
    dependency_rows = []
    relationships = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": project_spdx,
        }
    ]
    for name, package_version in packages:
        package_id = "SPDXRef-Package-" + re.sub(
            r"[^A-Za-z0-9.-]", "-", name
        )
        dependency_rows.append(
            {
                "SPDXID": package_id,
                "name": name,
                "versionInfo": package_version,
                "downloadLocation": (
                    f"https://pypi.org/project/{name}/{package_version}/"
                ),
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": (
                            f"pkg:pypi/{name.casefold()}@{package_version}"
                        ),
                    }
                ],
            }
        )
        relationships.append(
            {
                "spdxElementId": project_spdx,
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": package_id,
            }
        )

    sbom = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"okf-landregistry-{version}",
        "documentNamespace": (
            "https://chris-page-gov.github.io/okf-LandRegistry/"
            f"spdx/{candidate.release_root_sha256}"
        ),
        "creationInfo": {
            "created": created,
            "creators": ["Tool: scripts/create_release_metadata.py"],
            "licenseListVersion": "3.27",
        },
        "documentDescribes": [project_spdx],
        "packages": [
            {
                "SPDXID": project_spdx,
                "name": "okf-LandRegistry",
                "versionInfo": version,
                "downloadLocation": (
                    "https://github.com/chris-page-gov/okf-LandRegistry"
                ),
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "Copyright 2026 Chris Page",
                "comment": (
                    "Mixed repository licensing is declared in LICENSE.md; "
                    "source metadata and linked material retain their own terms."
                ),
            },
            *dependency_rows,
        ],
        "relationships": relationships,
        "annotations": [
            {
                "annotationDate": created,
                "annotationType": "OTHER",
                "annotator": "Tool: scripts/create_release_metadata.py",
                "comment": (
                    "Dependency versions and permitted distribution hashes are "
                    "bound by requirements-lock.txt SHA-256 "
                    f"{sha256_bytes(requirements_lock_bytes)}."
                ),
            }
        ],
    }

    build_runtime = build_receipt.get("python_runtime")
    if not isinstance(build_runtime, dict):
        raise ReleaseEvidenceError("candidate build receipt has no Python runtime")
    if observed_runtime is not None and build_runtime != observed_runtime:
        raise ReleaseEvidenceError(
            "metadata post-startup runtime differs from the runtime recorded by "
            "the build"
        )
    builder_bytes = reader.read(
        "scripts/build.py",
        "candidate builder",
        MAX_METADATA_INPUT_BYTES,
    )
    provenance = {
        "schema": "okf-hmlr-release-provenance.v1",
        "version": version,
        "generated_at": created,
        "candidate": {
            "candidate_commit_sha": candidate.candidate_commit_sha,
            "release_root_sha256": candidate.release_root_sha256,
            "checksums_sha256": candidate.checksums_sha256,
            "profile_pack_root_sha256": candidate.profile_pack_root_sha256,
            "snapshot_manifest_sha256": candidate.snapshot_manifest_sha256,
        },
        "subject": {
            "bundle_checksums": {
                "path": "bundle/CHECKSUMS.sha256",
                "sha256": candidate.checksums_sha256,
            },
            "archive": archive_receipt,
        },
        "build": {
            "type": "offline-deterministic-static-bundle",
            "builder": {
                "path": "scripts/build.py",
                "sha256": sha256_bytes(builder_bytes),
            },
            "invocation": governed_invocation,
            "invocation_kind": (
                "canonical-reproduction-command-recorded-by-candidate-builder"
            ),
            "runtime": {
                **build_runtime,
                "matches_build_receipt": True,
                "observation": (
                    "Post-startup observation by the metadata command, verified "
                    "equal to the runtime recorded by the candidate build receipt; "
                    "this does not attest code executed before the in-process "
                    "observer began."
                ),
            },
            "network_access_during_build": False,
            "source_acquisition": (
                "Previously frozen bounded public-metadata snapshot; no live "
                "source access during the candidate build."
            ),
            "build_receipt_schema": build_receipt.get("schema"),
        },
        "materials": provenance_materials(
            build_receipt,
            read_candidate=reader.read,
        ),
        "workflow_actions": pins,
        "dependency_lock": {
            "path": "requirements-lock.txt",
            "sha256": sha256_bytes(requirements_lock_bytes),
            "package_count": len(packages),
            "hash_enforced_install": (
                "python -m pip --python .venv install --no-compile "
                "--require-hashes -r requirements-lock.txt"
            ),
        },
        "ai_disclosure": {
            "ai_generated_proof_of_concept": True,
            "human_accessibility_audit_completed": False,
        },
    }

    return (
        version,
        {
            "sbom.spdx.json": canonical_json(sbom).encode("utf-8"),
            "provenance.json": canonical_json(provenance).encode("utf-8"),
        },
    )


def create_metadata(
    *,
    candidate_commit: str,
    archive_receipt_path: Path,
    output_directory: Path,
) -> tuple[Path, Path]:
    candidate = checked_candidate(candidate_commit)
    coordinate_reader = CandidateMetadataReader(ROOT, candidate_commit)
    config = load_json_bytes(
        coordinate_reader.read(
            "source/build-config.json",
            "release build configuration",
            MAX_JSON_BYTES,
        ),
        label="candidate source/build-config.json",
    )
    version = canonical_release_version(
        config.get("version"),
        label="candidate source/build-config.json version",
    )
    archive_receipt_relative = exact_archive_receipt_path(
        archive_receipt_path,
        version=version,
    )
    archive_receipt_bytes = read_repository_file_bytes(
        ROOT,
        archive_receipt_relative.as_posix(),
        purpose="archive receipt",
        max_bytes=MAX_JSON_BYTES,
    )
    archive_receipt = load_json_bytes(
        archive_receipt_bytes,
        label="archive receipt",
    )
    version, documents = expected_release_metadata_documents(
        ROOT,
        candidate=candidate,
        archive_receipt=archive_receipt,
        observed_runtime=observed_python_runtime(
            coordinate_reader.read(
                "requirements-lock.txt",
                "dependency lock for runtime observation",
                MAX_METADATA_INPUT_BYTES,
            )
        ),
    )
    output = safe_output_directory(output_directory, version=version)
    sbom_path = output / "sbom.spdx.json"
    provenance_path = output / "provenance.json"
    write_metadata_outputs(
        ROOT,
        output,
        {
            sbom_path: documents["sbom.spdx.json"],
            provenance_path: documents["provenance.json"],
        }
    )
    return sbom_path, provenance_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-commit-sha", required=True)
    parser.add_argument(
        "--archive-receipt",
        type=Path,
        required=True,
        help=(
            "exact validation/candidate-v<version>/evidence/"
            "release-candidate-archive-a.json input"
        ),
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        required=True,
        help=(
            "exact validation/candidate-v<version>/evidence/"
            "release-metadata output directory"
        ),
    )
    args = parser.parse_args()
    try:
        sbom, provenance = create_metadata(
            candidate_commit=args.candidate_commit_sha,
            archive_receipt_path=args.archive_receipt,
            output_directory=args.output_directory,
        )
    except (
        KeyError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ReleaseEvidenceError,
    ) as exc:
        print(f"release metadata failed closed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "sbom": sbom.relative_to(ROOT).as_posix(),
                "provenance": provenance.relative_to(ROOT).as_posix(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

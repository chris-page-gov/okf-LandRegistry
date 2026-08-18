#!/usr/bin/env python3
"""Require publication controls, documentation and the changelog to move together."""

from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _git_lines(root: Path, arguments: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def changed_files(root: Path, base: str | None) -> set[str]:
    """Return the complete reviewed range, or all local changes when no base is set."""

    if base:
        changed = set(_git_lines(root, ["diff", "--name-only", base, "--"]))
        changed.update(_git_lines(root, ["ls-files", "--others", "--exclude-standard"]))
        return changed
    changed = set(_git_lines(root, ["diff", "--name-only", "--"]))
    changed.update(_git_lines(root, ["diff", "--cached", "--name-only", "--"]))
    changed.update(_git_lines(root, ["ls-files", "--others", "--exclude-standard"]))
    return changed


def _matches(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def lockstep_errors(
    contract: Mapping[str, Any], changed: Iterable[str]
) -> tuple[list[str], list[str], list[str]]:
    """Return errors and the matched controlled and documentation paths."""

    lockstep = contract["lockstep"]
    files = set(changed)
    controlled = sorted(
        path for path in files if _matches(path, lockstep["controlled_paths"])
    )
    if not controlled:
        return [], [], []
    documentation = sorted(
        path for path in files if _matches(path, lockstep["documentation_paths"])
    )
    errors: list[str] = []
    if not documentation:
        errors.append(
            "controlled publication files changed without a declared documentation change"
        )
    changelog = lockstep["changelog_path"]
    if changelog not in files:
        errors.append(f"controlled publication files changed without {changelog}")
    return errors, controlled, documentation


def _load_contract(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != (
        "okf-repository-publication-contract.v1"
    ):
        raise ValueError("publication contract schema identifier is missing or unsupported")
    lockstep = payload.get("lockstep")
    if not isinstance(lockstep, dict):
        raise ValueError("publication contract lockstep policy is missing")
    for key in ("controlled_paths", "documentation_paths", "changelog_path"):
        if key not in lockstep:
            raise ValueError(f"publication contract lockstep.{key} is missing")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="git revision or range to compare with HEAD")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        contract = _load_contract(root / "okf.publication.json")
        errors, controlled, documentation = lockstep_errors(
            contract, changed_files(root, args.base)
        )
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"documentation lockstep could not be evaluated: {error}", file=sys.stderr)
        return 2

    if not controlled:
        print("documentation lockstep: no controlled publication files changed")
        return 0
    if errors:
        print("documentation lockstep failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(
        "documentation lockstep: "
        f"{len(controlled)} controlled file(s), "
        f"{len(documentation)} documentation file(s), changelog updated"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

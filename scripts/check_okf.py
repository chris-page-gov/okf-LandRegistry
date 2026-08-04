#!/usr/bin/env python3
"""Validate the generated OKF v0.2 Markdown layer structurally.

The core rules are vendored from ``validate_okf_v02_markdown`` at the pinned
source below, then tightened for this bundle's emitted concept profile.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


ROOT = Path(__file__).resolve().parents[1]
ACTOR_PATTERN = re.compile(r"^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._:-]*$")
UPSTREAM_CHECKER = {
    "repository": "https://github.com/chris-page-gov/okg-planning",
    "commit_sha": "a1805b26627e16a388e67d7d773a31c2a1970f28",
    "path": "src/okf_planning/build.py",
    "sha256": "4ee2e16330fd4841e20890fdef0c4bf8f1645645d713a8978fdce549de4489df",
    "function": "validate_okf_v02_markdown",
}
CONCEPT_REQUIRED_FIELDS = {
    "type",
    "title",
    "description",
    "resource",
    "generated",
    "status",
    "sources",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / "bundle")
    return parser.parse_args()


def split_frontmatter(text: str) -> tuple[str, str] | None:
    if not text.startswith("---\n"):
        return None
    marker = text.find("\n---\n", 4)
    if marker < 0:
        return None
    return text[4:marker], text[marker + 5 :]


def parse_frontmatter(block: str) -> dict[str, Any]:
    yaml = YAML(typ="safe")
    parsed = yaml.load(block)
    if not isinstance(parsed, dict):
        raise ValueError("frontmatter must be a mapping")
    return dict(parsed)


def valid_datetime(value: Any) -> bool:
    try:
        rendered = str(value)
        if "T" not in rendered:
            return False
        datetime.fromisoformat(rendered.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def validate_okf_v02_markdown(bundle: Path) -> dict[str, Any]:
    errors: list[str] = []
    checked = 0
    markdown_files = sorted(bundle.rglob("*.md"))
    for path in markdown_files:
        relative = path.relative_to(bundle).as_posix()
        text = path.read_text(encoding="utf-8")
        body = text
        if relative == "index.md":
            split = split_frontmatter(text)
            if split is None:
                errors.append("index.md: invalid version frontmatter")
                continue
            block, body = split
            try:
                root_frontmatter = parse_frontmatter(block)
            except ValueError as error:
                errors.append(f"index.md: {error}")
                continue
            if root_frontmatter != {"okf_version": "0.2"}:
                errors.append("index.md: root frontmatter may only declare v0.2")
        elif path.name in {"index.md", "log.md"}:
            if text.startswith("---"):
                errors.append(f"{relative}: reserved file must not have frontmatter")
        else:
            checked += 1
            split = split_frontmatter(text)
            if split is None:
                errors.append(f"{relative}: missing frontmatter")
                continue
            block, body = split
            try:
                metadata = parse_frontmatter(block)
            except Exception as error:
                errors.append(f"{relative}: invalid frontmatter: {error}")
                continue
            missing = sorted(
                field
                for field in CONCEPT_REQUIRED_FIELDS
                if metadata.get(field) in (None, "", [])
            )
            if missing:
                errors.append(
                    f"{relative}: missing required fields: {', '.join(missing)}"
                )
            if metadata.get("status") not in {"draft", "stable", "deprecated", "released"}:
                errors.append(f"{relative}: invalid status")
            generated = metadata.get("generated")
            if not isinstance(generated, dict):
                errors.append(f"{relative}: invalid generated")
            elif (
                not ACTOR_PATTERN.fullmatch(str(generated.get("by", "")))
                or not valid_datetime(generated.get("at"))
            ):
                errors.append(f"{relative}: invalid generated.by or generated.at")
            sources = metadata.get("sources")
            if not isinstance(sources, list):
                errors.append(f"{relative}: invalid sources")
            else:
                for index, source in enumerate(sources):
                    if (
                        not isinstance(source, dict)
                        or not source.get("id")
                        or not source.get("resource")
                    ):
                        errors.append(f"{relative}: invalid sources[{index}]")
        if not any(line.startswith("# ") for line in body.splitlines()):
            errors.append(f"{relative}: missing title heading")
        if path.name == "log.md":
            for line in body.splitlines():
                if line.startswith("## "):
                    try:
                        date.fromisoformat(line.removeprefix("## ").strip())
                    except ValueError:
                        errors.append(f"{relative}: invalid ISO date heading")
    if not markdown_files:
        errors.append("bundle contains no Markdown")
    return {
        "schema": "okf-v0.2-markdown-check.v1",
        "checker": UPSTREAM_CHECKER,
        "bundle": str(bundle),
        "checked_concepts": checked,
        "errors": errors,
        "okf_version": "0.2",
        "status": "conformant" if not errors else "non-conformant",
    }


def main() -> int:
    result = validate_okf_v02_markdown(parse_args().bundle.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "conformant" else 1


if __name__ == "__main__":
    raise SystemExit(main())

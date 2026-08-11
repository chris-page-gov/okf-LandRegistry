from __future__ import annotations

import re
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
INLINE_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)")
REFERENCE_LINK = re.compile(r"(?m)^\[[^\]]+\]:\s+(\S+)")


class LocalLinkTests(unittest.TestCase):
    def test_local_markdown_links_resolve(self) -> None:
        missing: list[str] = []
        markdown_files = [
            path
            for path in sorted(ROOT.rglob("*.md"))
            if not any(part.startswith(".") for part in path.relative_to(ROOT).parts)
            and not path.is_relative_to(ROOT / "profiles" / "bundle-wiki" / "v1")
        ]
        for source in markdown_files:
            text = source.read_text(encoding="utf-8")
            targets = [
                *INLINE_LINK.findall(text),
                *REFERENCE_LINK.findall(text),
            ]
            for raw_target in targets:
                target = raw_target.strip("<>")
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc or target.startswith(("#", "/")):
                    continue
                relative = unquote(parsed.path)
                if not relative:
                    continue
                destination = (source.parent / relative).resolve()
                if ROOT not in destination.parents and destination != ROOT:
                    missing.append(f"{source.relative_to(ROOT)} -> {target} (escapes root)")
                elif not destination.exists():
                    missing.append(f"{source.relative_to(ROOT)} -> {target}")
        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()

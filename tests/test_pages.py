from __future__ import annotations

import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "bundle"
PAGES = ROOT / "pages"


class StructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


class PagesTests(unittest.TestCase):
    def test_authored_site_links_usage_ledger_without_inventing_cost(self) -> None:
        html = (PAGES / "index.html").read_text(encoding="utf-8")
        self.assertIn("./data/ai-usage.json", html)
        self.assertIn("subscription allocation", html)
        self.assertIn("remain unavailable", html)

    def test_required_semantic_and_progressive_elements(self) -> None:
        html = (BUNDLE / "index.html").read_text(encoding="utf-8")
        parser = StructureParser()
        parser.feed(html)
        tags = [tag for tag, _attrs in parser.tags]
        self.assertIn("main", tags)
        self.assertIn("nav", tags)
        self.assertIn("form", tags)
        self.assertIn("noscript", tags)
        self.assertTrue(
            any(tag == "a" and attrs.get("href") == "#main" for tag, attrs in parser.tags)
        )
        self.assertTrue(
            any(attrs.get("aria-live") for _tag, attrs in parser.tags),
            "a live status region is required",
        )
        self.assertIn('content="default-src', html)
        self.assertIn("ai-generated proof of concept", html.casefold())
        self.assertIn("version 0.1.1", html.casefold())
        self.assertIn("29 July 2026", html)

    def test_javascript_uses_safe_dom_apis(self) -> None:
        script = (BUNDLE / "app.js").read_text(encoding="utf-8")
        forbidden = ("innerHTML", "outerHTML", "document.write", "eval(", "new Function")
        for token in forbidden:
            self.assertNotIn(token, script)
        self.assertIn("URLSearchParams", script)
        self.assertIn("createElement", script)
        self.assertIn("aria", script.casefold())
        self.assertIn('applyFilters({ writeUrl: false })', script)
        self.assertIn("Key caveat", script)
        self.assertIn('metadataRow("Geography"', script)
        self.assertIn('"Languages"', script)
        self.assertIn("./data/search/index.json", script)
        self.assertIn("./data/records/records-", script)
        self.assertNotIn('fetch("./data/catalogue.json"', script)

    def test_authored_cards_label_governed_source_routes(self) -> None:
        script = (PAGES / "app.js").read_text(encoding="utf-8")
        self.assertIn("Governed source and evidence routes", script)
        self.assertIn("Primary record", script)
        self.assertIn("Supporting source", script)

    def test_no_javascript_catalogue_is_navigable(self) -> None:
        authored = (BUNDLE / "index.html").read_text(encoding="utf-8")
        self.assertIn("./catalogue-index.html", authored)
        static_catalogue = (BUNDLE / "catalogue-index.html").read_text(encoding="utf-8")
        parser = StructureParser()
        parser.feed(static_catalogue)
        links = [
            attrs.get("href")
            for tag, attrs in parser.tags
            if tag == "a" and attrs.get("href")
        ]
        self.assertGreater(len(links), 2_000)
        self.assertTrue(any(str(link).startswith("https://www.gov.uk/") for link in links))

    def test_site_has_no_external_runtime_dependencies(self) -> None:
        html = (BUNDLE / "index.html").read_text(encoding="utf-8")
        external_runtime = re.compile(
            r"""(?:src|href)=["']https?://""", flags=re.IGNORECASE
        )
        for match in external_runtime.finditer(html):
            before = html[max(0, match.start() - 20) : match.start()]
            self.assertIn("<a", before, "only normal source links may be external")

    def test_web_manifest_and_accessibility_page(self) -> None:
        manifest = json.loads((BUNDLE / "manifest.webmanifest").read_text())
        self.assertEqual("standalone", manifest["display"])
        accessibility = (BUNDLE / "accessibility.html").read_text().casefold()
        self.assertIn("wcag 2.2 aa", accessibility)
        self.assertIn("not", accessibility)

    def test_local_html_assets_and_routes_resolve(self) -> None:
        missing: list[str] = []
        project_prefix = "/okf-LandRegistry/"
        for source in sorted(BUNDLE.glob("*.html")):
            parser = StructureParser()
            parser.feed(source.read_text(encoding="utf-8"))
            for _tag, attrs in parser.tags:
                for attribute in ("href", "src"):
                    raw = attrs.get(attribute)
                    if not raw:
                        continue
                    parsed = urlsplit(raw)
                    if parsed.scheme or parsed.netloc or raw.startswith("#"):
                        continue
                    path = unquote(parsed.path)
                    if not path:
                        continue
                    if path.startswith(project_prefix):
                        destination = BUNDLE / path.removeprefix(project_prefix)
                    elif path.startswith("/"):
                        continue
                    else:
                        destination = source.parent / path
                    if destination.is_dir():
                        destination = destination / "index.html"
                    if not destination.exists():
                        missing.append(
                            f"{source.name} {attribute}={raw!r}"
                        )
        self.assertEqual([], missing)

    def test_authored_404_uses_project_pages_root(self) -> None:
        html = (PAGES / "404.html").read_text(encoding="utf-8")
        parser = StructureParser()
        parser.feed(html)
        links = [
            attrs.get("href")
            for tag, attrs in parser.tags
            if tag in {"a", "link"} and attrs.get("href")
        ]
        self.assertIn("/okf-LandRegistry/styles.css", links)
        self.assertIn("/okf-LandRegistry/", links)
        self.assertNotIn("./styles.css", links)
        self.assertNotIn("./", links)


if __name__ == "__main__":
    unittest.main()

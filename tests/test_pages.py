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
        self.assertNotIn("form", tags)
        self.assertTrue(
            any(tag == "a" and attrs.get("href") == "#main" for tag, attrs in parser.tags)
        )
        self.assertIn("sole interactive search runtime", html)
        self.assertIn("./catalogue-index.html", html)
        self.assertIn('content="default-src', html)
        self.assertIn("ai-generated proof of concept", html.casefold())
        self.assertIn("version 0.2.0", html.casefold())
        self.assertIn("29 July 2026", html)
        self.assertIn("do not assert publication approval", html)
        self.assertNotIn("Approved for publication", html)

    def test_pages_do_not_ship_a_second_search_runtime(self) -> None:
        self.assertFalse((BUNDLE / "app.js").exists())
        self.assertFalse((BUNDLE / "search-contract.json").exists())
        self.assertFalse((BUNDLE / "data" / "search").exists())
        descriptor = json.loads((BUNDLE / "okf-explorer.json").read_text())
        self.assertIn("search_manifest", descriptor["entrypoints"])
        self.assertIn("analysis_overview", descriptor["entrypoints"])
        self.assertNotIn("catalogue_search_manifest", descriptor["entrypoints"])

    def test_explorer_analysis_overview_surfaces_governed_safety_notices(self) -> None:
        descriptor = json.loads((BUNDLE / "okf-explorer.json").read_text())
        reference = descriptor["entrypoint_integrity"]["analysis_overview"]
        analysis = json.loads((BUNDLE / reference["path"]).read_text())
        self.assertEqual("okf-explorer-analysis.v1", analysis["schema"])
        notices = analysis["summary"]["notices"]
        self.assertTrue(any("not legal advice" in notice for notice in notices))

    def test_yaml_ld_and_json_ld_are_declared_semantic_serializations(self) -> None:
        descriptor = json.loads((BUNDLE / "okf-explorer.json").read_text())
        serializations = descriptor["semantic_serializations"]
        self.assertEqual("YAML-LD", serializations["canonical"]["format"])
        self.assertEqual("application/ld+yaml", serializations["canonical"]["media_type"])
        self.assertEqual("okf-bundle.yamlld", serializations["canonical"]["path"])
        self.assertEqual(
            [
                {
                    "format": "JSON-LD",
                    "media_type": "application/ld+json",
                    "path": "okf-bundle.jsonld",
                }
            ],
            serializations["alternates"],
        )
        self.assertTrue((BUNDLE / "okf-bundle.yamlld").is_file())
        self.assertTrue((BUNDLE / "okf-bundle.jsonld").is_file())

    def test_authored_page_delegates_interaction_to_the_pinned_explorer(self) -> None:
        html = (PAGES / "index.html").read_text(encoding="utf-8")
        self.assertIn("chris-page-gov.github.io/okf-explorer/", html)
        self.assertIn("okf-explorer.json", html)

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
        parser = StructureParser()
        parser.feed(html)
        for tag, attrs in parser.tags:
            source = attrs.get("src")
            if source:
                self.assertFalse(
                    source.startswith(("http://", "https://")),
                    "scripts, styles and images must remain local",
                )

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

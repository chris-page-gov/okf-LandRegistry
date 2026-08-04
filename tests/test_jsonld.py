from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from pyld import jsonld


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "bundle"
PUBLICATION_BASE = "https://chris-page-gov.github.io/okf-LandRegistry/"
DCAT = "http://www.w3.org/ns/dcat#"
DCTERMS = "http://purl.org/dc/terms/"
FOAF = "http://xmlns.com/foaf/0.1/"
PROV = "http://www.w3.org/ns/prov#"
SCHEMA = "https://schema.org/"


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def local_document_loader(url: str, _options: Any = None) -> dict[str, Any]:
    expected = PUBLICATION_BASE + "context.jsonld"
    if url != expected:
        raise RuntimeError(f"network JSON-LD context access is forbidden: {url}")
    return {
        "contextUrl": None,
        "documentUrl": expected,
        "document": load_json(BUNDLE / "context.jsonld"),
    }


class JsonLdProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = load_json(BUNDLE / "okf-bundle.jsonld")
        cls.catalogue = load_json(BUNDLE / "data" / "catalogue.json")
        cls.expanded = jsonld.expand(
            cls.document,
            options={"documentLoader": local_document_loader},
        )
        cls.root = cls.expanded[0]
        cls.graph = cls.root["@graph"]
        cls.nodes = {node["@id"]: node for node in cls.graph}

    def test_pinned_local_context_expands_without_network(self) -> None:
        self.assertEqual(
            self.document["@context"], PUBLICATION_BASE + "context.jsonld"
        )
        self.assertIn(DCAT + "Catalog", self.root["@type"])
        self.assertIn(SCHEMA + "DataCatalog", self.root["@type"])
        self.assertIn(DCAT + "record", self.root)
        self.assertIn(PROV + "wasGeneratedBy", json.dumps(self.expanded))

    def test_catalogue_ranges_and_dataset_membership_are_semantic(self) -> None:
        record_ids = {
            reference["@id"] for reference in self.root[DCAT + "record"]
        }
        self.assertEqual(len(self.catalogue["records"]), len(record_ids))
        for identifier in record_ids:
            self.assertIn(identifier, self.nodes)
            node = self.nodes[identifier]
            self.assertIn(DCAT + "CatalogRecord", node["@type"])
            target = node[FOAF + "primaryTopic"][0]["@id"]
            self.assertIn(target, self.nodes)

        expected_datasets = {
            record["canonical_source_url"]
            for record in self.catalogue["records"]
            if record["kind"] == "dataset"
        }
        actual_datasets = {
            reference["@id"] for reference in self.root.get(SCHEMA + "dataset", [])
        }
        self.assertEqual(expected_datasets, actual_datasets)
        self.assertLess(len(actual_datasets), len(record_ids))

    def test_publishers_rights_sources_and_activities_have_distinct_ids(self) -> None:
        by_type: dict[str, set[str]] = {}
        for identifier, node in self.nodes.items():
            for node_type in node.get("@type", []):
                by_type.setdefault(node_type, set()).add(identifier)
        publisher_ids = by_type[SCHEMA + "Organization"]
        rights_ids = by_type[DCTERMS + "RightsStatement"]
        activity_ids = by_type[PROV + "Activity"]
        record_ids = by_type[DCAT + "CatalogRecord"]
        source_ids = set(self.nodes) - publisher_ids - rights_ids - activity_ids - record_ids
        groups = [publisher_ids, rights_ids, activity_ids, record_ids, source_ids]
        self.assertTrue(all(groups))
        for index, left in enumerate(groups):
            for right in groups[index + 1 :]:
                self.assertFalse(left & right)
        self.assertTrue(all(not identifier.startswith("_:") for identifier in self.nodes))

    def test_every_graph_reference_is_internal_or_absolute_https(self) -> None:
        def references(value: Any) -> list[str]:
            found: list[str] = []
            if isinstance(value, dict):
                if set(value) == {"@id"}:
                    found.append(value["@id"])
                else:
                    for child in value.values():
                        found.extend(references(child))
            elif isinstance(value, list):
                for child in value:
                    found.extend(references(child))
            return found

        for identifier in references(self.expanded):
            self.assertTrue(
                identifier in self.nodes
                or identifier == self.root["@id"]
                or identifier.startswith("https://"),
                identifier,
            )


if __name__ == "__main__":
    unittest.main()

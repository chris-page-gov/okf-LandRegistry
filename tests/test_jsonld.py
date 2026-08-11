from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pyld import jsonld
from ruamel.yaml import YAML


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "bundle"
PUBLICATION_BASE = "https://chris-page-gov.github.io/okf-LandRegistry/"
DCAT = "http://www.w3.org/ns/dcat#"
DCTERMS = "http://purl.org/dc/terms/"
FOAF = "http://xmlns.com/foaf/0.1/"
PROV = "http://www.w3.org/ns/prov#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
SCHEMA = "https://schema.org/"
OKF = "https://chris-page-gov.github.io/okf-explorer/ns#"
CPSV = "http://purl.org/vocab/cpsv#"
CV = "http://data.europa.eu/m8g/"


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
        yaml = YAML(typ="safe")
        with (BUNDLE / "okf-bundle.yamlld").open(encoding="utf-8") as handle:
            cls.yaml_document = yaml.load(handle)
        cls.catalogue = load_json(BUNDLE / "data" / "catalogue.json")
        cls.expanded = jsonld.expand(
            cls.document,
            options={"documentLoader": local_document_loader},
        )
        cls.root = cls.expanded[0]
        cls.graph = cls.root["@graph"]
        cls.nodes = {node["@id"]: node for node in cls.graph}

    def test_yaml_ld_and_json_ld_are_exact_graph_serializations(self) -> None:
        self.assertEqual(self.document, self.yaml_document)
        self.assertEqual(
            PUBLICATION_BASE + "okf-bundle.yamlld",
            load_json(BUNDLE / "okf-explorer.json")["semantic_descriptor"],
        )

    def test_pinned_local_context_expands_without_network(self) -> None:
        self.assertEqual(
            self.document["@context"], PUBLICATION_BASE + "context.jsonld"
        )
        self.assertIn(OKF + "Bundle", self.root["@type"])
        catalogue = next(
            node for node in self.graph if DCAT + "Catalog" in node.get("@type", [])
        )
        self.assertIn(SCHEMA + "DataCatalog", catalogue["@type"])
        self.assertIn(DCAT + "record", catalogue)
        self.assertIn(PROV + "wasGeneratedBy", json.dumps(self.expanded))

    def test_catalogue_ranges_and_dataset_membership_are_semantic(self) -> None:
        catalogue = next(
            node for node in self.graph if DCAT + "Catalog" in node.get("@type", [])
        )
        record_ids = {
            reference["@id"] for reference in catalogue[DCAT + "record"]
        }
        self.assertEqual(len(self.catalogue["records"]), len(record_ids))
        for identifier in record_ids:
            self.assertIn(identifier, self.nodes)
            node = self.nodes[identifier]
            self.assertIn(DCAT + "CatalogRecord", node["@type"])
            target = node[FOAF + "primaryTopic"][0]["@id"]
            self.assertIn(target, self.nodes)

        expected_datasets = {
            identifier
            for identifier, node in self.nodes.items()
            if DCAT + "Dataset" in node.get("@type", [])
        }
        actual_datasets = {
            reference["@id"]
            for reference in catalogue.get(DCAT + "dataset", [])
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

    def test_cpsv_public_services_have_one_evidenced_competent_authority(self) -> None:
        services = [
            node
            for node in self.graph
            if CPSV + "PublicService" in node.get("@type", [])
        ]
        self.assertEqual(7, len(services))
        authority_id = (
            "https://www.gov.uk/government/organisations/land-registry"
        )
        organisation = self.nodes[authority_id]
        self.assertIn(CV + "PublicOrganisation", organisation["@type"])
        for service in services:
            self.assertTrue(service[DCTERMS + "identifier"])
            self.assertTrue(service[DCTERMS + "title"])
            self.assertTrue(service[DCTERMS + "description"])
            self.assertEqual(
                [authority_id],
                [
                    row["@id"]
                    for row in service[CV + "hasCompetentAuthority"]
                ],
            )
        validation = load_json(BUNDLE / "data" / "semantic" / "validation.json")
        self.assertEqual("passed", validation["cpsv_ap"]["status"])
        self.assertEqual(
            "not-run",
            validation["cpsv_ap"]["official_shacl_execution"]["status"],
        )

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
            parsed = urlparse(identifier)
            self.assertTrue(
                identifier in self.nodes
                or identifier == self.root["@id"]
                or (
                    parsed.scheme in {"http", "https"}
                    and bool(parsed.netloc)
                    and not parsed.username
                    and not parsed.password
                ),
                identifier,
            )

    def test_translation_direct_triple_and_rich_assertion_reconcile(self) -> None:
        compact_assertions = [
            node
            for node in self.document["@graph"]
            if "okf:RelationshipAssertion" in node.get("@type", [])
        ]
        self.assertGreater(len(compact_assertions), 2_200)
        assertion = next(
            row
            for row in compact_assertions
            if row["predicate"]["@id"] == SCHEMA + "translationOfWork"
        )
        required = {
            "@id",
            "@type",
            "source",
            "predicate",
            "target",
            "source_route",
            "target_route",
            "kind",
            "label",
            "inverse_label",
            "assertion_status",
            "assertion_scope",
            "authority",
            "derivation",
            "observed_at",
            "evidence",
            "rights",
        }
        self.assertFalse(required - set(assertion))
        self.assertEqual("normalized", assertion["assertion_status"])
        self.assertEqual("real-world", assertion["assertion_scope"])
        self.assertEqual("derived", assertion["authority"]["class"])
        self.assertEqual(64, len(assertion["evidence"][0]["source_sha256"]))
        self.assertEqual(64, len(assertion["evidence"][0]["source_value_sha256"]))

        expanded_assertions = [
            node
            for node in self.graph
            if RDF + "Statement" in node.get("@type", [])
            and OKF + "RelationshipAssertion" in node.get("@type", [])
        ]
        self.assertEqual(len(compact_assertions), len(expanded_assertions))
        expanded = next(
            row
            for row in expanded_assertions
            if row[RDF + "predicate"][0]["@id"]
            == SCHEMA + "translationOfWork"
        )
        source = expanded[RDF + "subject"][0]["@id"]
        predicate = expanded[RDF + "predicate"][0]["@id"]
        target = expanded[RDF + "object"][0]["@id"]
        self.assertEqual(SCHEMA + "translationOfWork", predicate)
        self.assertIn(source, self.nodes)
        self.assertIn(target, self.nodes)
        self.assertIn(
            target,
            {reference["@id"] for reference in self.nodes[source][predicate]},
        )


if __name__ == "__main__":
    unittest.main()

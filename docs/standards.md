# Standards profile

Status: v0.1.0 PoC standards profile. A listed standard is not a blanket
conformance claim.

## Applicability vocabulary

- **Normative:** required by the local release contract.
- **Projection:** governs an optional generated representation.
- **Source-native:** preserved because an HM Land Registry source declares it.
- **Reference-only:** assessed but not implemented or claimed.

## Register

| ID | Standard and version | Applicability | Local use and claim boundary |
|---|---|---|---|
| `STD-OKF-02` | [Open Knowledge Format 0.2][OKF] at pinned commit | normative | Markdown root, typed concepts, sources and lifecycle; validate against the pinned specification |
| `STD-OKF-AUTHORING` | [OKF Foundry authoring profile v1][AUTHORING] | normative | Stage 1 profile, evidence, decisions, traceability and exact-digest owner approval |
| `STD-YAML-122` | [YAML 1.2.2][YAML] | normative | Strict safe YAML; no aliases, merge keys or custom tags |
| `STD-JSONSCHEMA-202012` | [JSON Schema 2020-12][JSONSCHEMA] | normative | Versioned control-document schemas and validation |
| `STD-JSONLD-11` | [JSON-LD 1.1][JSONLD] | projection | Deterministic semantic catalogue projection; not an RDF completeness claim |
| `STD-DCAT-3` | [DCAT 3][DCAT] | projection | Additive dataset, distribution and data-service discovery mapping |
| `STD-PROVO` | [PROV-O][PROVO] | projection | Provenance mapping for observations, generation and receipts |
| `STD-INSPIRE-CP` | [INSPIRE Cadastral Parcels 3.1][INSPIRE] | source-native | Preserve publisher-declared parcel metadata; do not republish geometry or imply exact boundaries |
| `STD-GML-321` | [OGC GML 3.2.1][GML] | source-native | Record source format and CRS declarations only |
| `STD-WCAG-22` | [WCAG 2.2][WCAG] | normative target | Level A/AA design and test requirements; automated and agent-assisted journeys do not establish conformance without an independent human audit |
| `STD-YAMLLD-10` | [YAML-LD 1.0 Working Draft][YAMLLD] | reference-only | No YAML-LD artifact or conformance claim in the first scaffold |

## Mapping policy

Mappings are additive and reversible:

- retain the source-native field alongside every mapped field;
- identify the mapping rule and version;
- record unmapped fields and semantic conflicts;
- never infer an access right, legal boundary, currency or operational status
  from a vocabulary mapping;
- keep full and change-only dataset files as distinct distributions; and
- reject a generated projection if it cannot preserve a material source
  distinction.

The JSON-LD/DCAT projection is for discovery. Sparse GOV.UK records are not
declared DCAT-conformant merely because they use a DCAT term. The project also
does not claim full RDF materialisation, graph isomorphism or SHACL
conformance.

## Validation evidence

Each release receipt must identify the validator, version or digest, standard
version, exact candidate digest, errors, warnings and waived findings. A
standards conformance claim is allowed only where:

1. the normative requirements are enumerated;
2. the governed artifact is named;
3. a repeatable validator or manual protocol exists;
4. all exceptions are explicit; and
5. the owner approves the wording.

Unknown or untested requirements remain unmet; they are not silently omitted
from the claim.

[OKF]: https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/3fcbb9f828c2f23d109c855ee403c3a4c81f3a96/okf/SPEC.md
[AUTHORING]: https://chris-page-gov.github.io/okf-explorer/profile/authoring/v1/
[YAML]: https://yaml.org/spec/1.2.2/
[JSONSCHEMA]: https://json-schema.org/draft/2020-12/schema
[JSONLD]: https://www.w3.org/TR/json-ld11/
[DCAT]: https://www.w3.org/TR/vocab-dcat-3/
[PROVO]: https://www.w3.org/TR/prov-o/
[INSPIRE]: https://inspire.ec.europa.eu/id/document/tg/cp
[GML]: https://portal.ogc.org/files/?artifact_id=20509
[WCAG]: https://www.w3.org/TR/WCAG22/
[YAMLLD]: https://www.w3.org/TR/2025/WD-yaml-ld-10-20250612/

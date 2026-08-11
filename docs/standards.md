# Standards profile

Status: released v0.2.0 profile plus approval-neutral v0.3.0 semantic candidate
bytes. A listed standard is not a blanket conformance claim. Corrections
identified by the P1 implementation review are locally regression-tested. The
bytes do not assert a current independent-gate, Land Registry G9 or release
state and do not amend the exact-digest v0.2.0 approval; version-scoped,
digest-bound external evidence is authoritative for v0.3.0.

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
| `STD-JSONLD-11` | [JSON-LD 1.1][JSONLD] | projection | Equivalent JSON-LD serialisation of the canonical YAML-LD graph; not an RDF completeness or inference claim |
| `STD-DCAT-3` | [DCAT 3][DCAT] | projection | Additive dataset, distribution and data-service discovery mapping |
| `STD-CPSVAP-320` | [CPSV-AP 3.2.0][CPSVAP] | projection | Selective reviewed public-service mapping: 11 candidates, 7 mapped, 4 excluded and 19 evidence references; official resources are vendored but official SHACL has not been run |
| `STD-PROVO` | [PROV-O][PROVO] | projection | Provenance mapping for observations, generation and receipts |
| `STD-INSPIRE-CP` | [INSPIRE Cadastral Parcels 3.1][INSPIRE] | source-native | Preserve publisher-declared parcel metadata; do not republish geometry or imply exact boundaries |
| `STD-GML-321` | [OGC GML 3.2.1][GML] | source-native | Record source format and CRS declarations only |
| `STD-WCAG-22` | [WCAG 2.2][WCAG] | normative target | Level A/AA design and test requirements; automated and agent-assisted journeys do not establish conformance without an independent human audit |
| `STD-YAMLLD-10` | [YAML-LD 1.0 Working Draft][YAMLLD] | projection | Canonical deterministic `application/ld+yaml` serialisation of the bounded graph; no claim beyond the tested local profile |

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

The YAML-LD and JSON-LD files serialise one in-memory discovery graph. Each
material directed relationship is emitted both as a direct triple and as an
evidence-bearing `okf:RelationshipAssertion`; tests require those two forms
to reconcile and require both serialisations to parse to the same data model.
The current candidate measures 22,226 assertions in each direct, reified and
runtime plane, using 13 governed predicates and 6,733 routes across 89 chunks
and 256 route-locator buckets. These are candidate measurements, not a new
standards requirement.

Sparse GOV.UK records are not declared DCAT-conformant merely because they use
a DCAT term. CPSV-AP mapping is selective and does not turn every entity into
a public service. CPSV-AP 3.2.0 is vendored, but its official SHACL shapes have
not been run. Semantic inference has not been run. The project therefore does
not claim general RDF graph isomorphism, remote context expansion, inference
completeness or SHACL conformance.

The current profile and assertion checks remain implementation evidence only.
The source-field evidence, CPSV adversarial-binding and URL-hardening
corrections identified by independent review are implemented and covered by
local regression tests. Candidate bytes make no independent-acceptance or
release-readiness claim; version-scoped evidence for the exact digest records
that state.

## Validation evidence

Each release receipt must identify the validator, version or digest, standard
version, exact candidate digest, errors, warnings and waived findings. A
standards conformance claim is allowed only where:

1. the normative requirements are enumerated;
2. the governed artefact is named;
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
[CPSVAP]: https://semiceu.github.io/CPSV-AP/releases/3.2.0/
[PROVO]: https://www.w3.org/TR/prov-o/
[INSPIRE]: https://inspire.ec.europa.eu/id/document/tg/cp
[GML]: https://portal.ogc.org/files/?artifact_id=20509
[WCAG]: https://www.w3.org/TR/WCAG22/
[YAMLLD]: https://www.w3.org/TR/yaml-ld-10/

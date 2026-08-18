# Metadata model

Status: v0.2.0 PoC candidate model. Source-native semantics remain authoritative.

## Design rules

- Model discovery objects, not source content.
- Preserve source-native identifiers, labels, dates, formats and relationships.
- Add local fields without rewriting or erasing the source observation.
- Keep record kind, authority, derivation, evidence, freshness, access, rights,
  coverage and release state orthogonal.
- Represent unknown values explicitly; do not replace them with empty certainty.
- Relate editions, representations, distributions and service environments
  rather than merging them.
- Fail on identifier collisions and unresolved governed references.

## Core entities

| Entity | Purpose | Essential properties |
|---|---|---|
| `Record` | Common discovery object | `id`, `record_type`, `title`, `url`, `source_family`, `source_native_ids`, `authority_role`, `derivation`, `observed_at`, `lifecycle_state` |
| `Document` | Guidance, form, notice, statistics or corporate publication | content type, publisher dates, language, edition/representation links |
| `Dataset` | Governed data product | coverage, cadence, publisher status, rights, caveats and distributions |
| `Distribution` | One access representation | format, access URL, access model, authentication, fee, licence and size |
| `Service` | Transactional or informational route | audience, access model, authentication, fees, current service source and caveats |
| `ApiProduct` | Developer-facing interface | protocol, documentation, environment, authentication, version and operational authority |
| `Repository` | Public source repository metadata | owner, fork/archive status, default branch, declared licence and observation |
| `SourceFamily` | Acquisition and authority boundary | owner, source URL, denominator, refresh, access and known limits |
| `Evidence` | Claim-supporting observation | evidence ID, location, authority, observation, digest and evidence state |
| `RightsAssessment` | Layer or record-level use decision | status, operation, basis, licence, attribution, constraints and review state |
| `ProvenanceActivity` | Acquisition, normalisation, generation or validation | inputs, outputs, tool/version, time, policy and digest |
| `EvaluationCase` | Persona-linked competency check | question, expected source/proposition, near miss, hard-failure class and result |

## Required record fields

Every public record must expose:

- a stable selected `id` and all retained `source_native_ids`;
- `record_type` from a controlled vocabulary;
- `title` and canonical official `url`;
- primary `source_family`, all represented `source_families`,
  `authority_role` and `derivation`;
- `observed_at`, plus separate source dates when supplied;
- `access_state` and `rights_state`;
- exactly one primary `rights_ref`, plus representation-level rights
  references where canonical URLs merge;
- jurisdiction or coverage when material;
- material caveats and resolvable `evidence_refs`; and
- `lifecycle_state` as `active`, `archived` or `unknown`.

Missing source fields remain absent or explicitly unknown. They must not be
inferred merely to make a record look complete.

## Controlled states

### Authority role

`publisher-authoritative-source`, `official-operational-source`,
`official-discovery-reference`, or `unassessed-source`.

### Derivation

`normalized-frozen-source-metadata` or `reviewed-curated-metadata`.
Canonical-URL merges also expose the complete `derivations` list. The first
scaffold prohibits public model-assisted records (`DEC-ENRICHMENT`).

### Access

The exact governed value is projected from the source register, including
`public`, `mixed`, `authenticated`, `authenticated-and-paid`,
`documentation-public-service-restricted`,
`public-search-with-terms-and-fees`, and `approved-professional-users`.

### Rights

The source-family value is `open-with-conditions`, `bespoke-or-paid`,
`restricted-service`, or `metadata-only`; `rights_ref` resolves to the fuller
governance assessment (`permitted`, `conditional` or `prohibited`). “Public”
is not a rights state.

### Evidence

`observed`, `corroborated`, `candidate`, `unavailable`, `untested`, or
`rejected`. A workflow status such as accepted or reviewed does not upgrade
the underlying evidence state.

## Dates and temporal semantics

Use separate fields for:

- `publisher_last_updated`;
- `dataset_release_at`;
- `coverage_start` and `coverage_end`;
- `observed_at`;
- top-level `generated_at`; and
- top-level `release_at`.

No field may stand in for another. In particular, catalogue modification does
not establish dataset release, currency or coverage.

## Relationships

Relationships are typed, directed and evidenced. Expected examples include:

- `is_representation_of`;
- `has_edition` / `is_edition_of`;
- `has_distribution`;
- `documents_service`;
- `supersedes`;
- `operationally_verified_by`;
- `discovered_via`;
- `was_derived_from`; and
- `applies_rights_assessment`.

Source-native relationships are preserved. Deterministic local relationships
must state the rule and evidence. Unknown direction or semantics are not
published as a generic “related” fact unless that source-native ambiguity is
itself represented.

The generated relationship assertion plane requires, for every row:

- a stable absolute assertion IRI;
- absolute source, predicate and target IRIs plus validated local Explorer
  routes for both endpoints;
- preferred and inverse labels, assertion status and real-world/synthetic
  scope;
- an authority class and source that remains distinct from confidence;
- a derivation rule/activity and ISO observation time;
- field-level evidence with the frozen source artifact and exact hashes; and
- an explicit assertion-rights statement and rights source.

The current normalized Welsh-to-English relationship uses
`https://schema.org/translationOfWork`. It comes only from the bounded GOV.UK
Content API `available_translations` observation. Its direct triple and
reified `rdf:Statement`/`okf:RelationshipAssertion` are generated from the
same assertion object; Explorer adjacency is a route-bearing projection of
that object, not a second source of semantics.

The offline producer pins
`schemas/semantic-assertion.schema.json` to the final Explorer contract by
identifier, Draft 2020-12 version, exact 7,268-byte length and SHA-256
`307e59c5a3b1f502d50c7d82233a330e6919634b7b57fbdaed96a6a6a290af52`.
It validates the emitted reified node and a lossless mapping of every runtime
row, rejects remote schema references, and then requires the direct, reified
and runtime identity, route and triple sets to agree. The generated schema and
validation receipt are published under `bundle/data/semantic/` and covered by
the bundle manifests and checksums.

## Dataset and service distinctions

A dataset landing page, full file, change-only file, API, linked-data endpoint
and technical specification are different objects. Likewise, developer
documentation is not the service itself, and a CDDO catalogue row is not proof
that an endpoint is current or anonymously callable.

Spatial records must retain coordinate reference system, geometry role and
publisher caveats. “Index polygon” or “title plan” must never be normalised to
“exact legal boundary”.

## Example

```json
{
  "id": "hmlr:dataset:inspire-index-polygons",
  "record_type": "geospatial-dataset",
  "title": "INSPIRE Index Polygons",
  "url": "https://use-land-property-data.service.gov.uk/datasets/inspire",
  "source_family": "ulpd",
  "source_native_ids": ["hmlr:dataset:inspire-index-polygons"],
  "authority_role": "publisher-authoritative-source",
  "derivation": "reviewed-curated-metadata",
  "observed_at": "2026-07-29T07:53:38Z",
  "access_state": "mixed",
  "rights_state": "open-with-conditions",
  "rights_ref": "RIGHT-DATASETS",
  "evidence_refs": ["EV-INSPIRE-DATA"],
  "caveats": [
    "Publisher-described indicative spatial extent; not an exact legal boundary."
  ],
  "lifecycle_state": "unknown"
}
```

This example describes a candidate record shape, not a fresh assertion about
the live dataset. The publisher [dataset page][EV-INSPIRE-DATA] remains the
authority.

## Projection

The canonical authoring layer is OKF 0.2 Markdown plus governed source JSON.
The build creates one semantic graph and serializes it as YAML-LD and JSON-LD;
the two files must parse to the same data model. DCAT and Schema.org mappings
remain additive discovery projections and must not collapse distinctions or
introduce facts. CSV is a convenience projection and cannot carry the
complete rights, provenance or relationship model.

[EV-INSPIRE-DATA]: https://use-land-property-data.service.gov.uk/datasets/inspire

# Metadata model

Status: released v0.2.0 model plus approval-neutral v0.3.0 semantic candidate
bytes. Source-native semantics remain authoritative. Corrections identified
by the P1 implementation review are locally regression-tested. The bytes do
not assert a current gate, G9 or release state; exact digest-bound external
evidence does.

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

For a deterministic candidate, `generated_at` is a governed whole-second UTC
input rather than the wall-clock time of each rebuild. The builder requires it
to be strictly later than the selected snapshot observations and retrievals,
the domain-profile preparation and evidence events, and every CPSV-AP evidence
retrieval and review. The build receipt records the latest governed event used
for that comparison. `release_at` remains null until external exact-digest
approval and publication evidence exist.

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
- field-level evidence with the frozen source artefact and exact hashes; and
- an explicit assertion-rights statement and rights source.

The governed v0.3.0 source-plane projection generates 22,267 assertions over
13 active predicates,
including catalogue, dataset/resource, publisher, source, rights, provenance,
language, spatial, competent-authority and primary-topic relationships. The
normalised Welsh-to-English relationship continues to use
`https://schema.org/translationOfWork` and comes only from the bounded GOV.UK
Content API `available_translations` observation. Every direct triple and
reified `rdf:Statement`/`okf:RelationshipAssertion` is generated from the same
assertion object; Explorer adjacency is a route-bearing projection of that
object, not a second source of semantics.

### Class-to-route delivery index

`bundle/data/semantic/class-route-registry.json` is a generated delivery
sidecar, not semantic authority. It deterministically joins each route-bearing
node's authoritative `rdf:type` facts from the canonical semantic graph to the
same node's route in the digest-bound IRI-to-route registry. Its
`source_plane_roots` bind both inputs, and its 10,951 entries must cover exactly
the complete IRI-to-route population.

The index cannot originate, remove or override a class-membership fact. It is
not an ontology, an inference result or evidence that the pinned Explorer
v0.6.2 PWA consumes or presents a class-hierarchy view. Its local Land Registry
schema governs only the deterministic delivery shape and integrity bindings.

### Predicate capability registry

`bundle/data/semantic/predicate-registry.json` uses the additive
`okf-predicate-registry.v2` contract. It is referenced from the frozen Bundle
Wiki v1 semantic model as an external resource with `path`, `sha256` and
`media_type`; v2 is not inlined into the v1 model. The bundle also carries the
exact locked schema at
`bundle/data/semantic/schemas/predicate-registry.v2.schema.json` so validation
does not require network access.

The registry contains every one of the 22 Stage 1 predicate capabilities,
ordered by absolute IRI. Each row preserves its preferred and inverse labels,
description, domain, range, supported assertion status, evidence policy,
source vocabulary and lifecycle status. A nested `implementation` object is
derived from the complete relationship assertion plane:

- `active-emitted` carries the exact positive assertion count; 13 rows total
  22,267 assertions in this candidate; and
- `authorised-zero-evidence` carries exactly zero; nine rows declare an
  authorised capability for which the snapshot has no qualifying endpoint
  evidence.

The latter state is not a negative fact, does not assert that two entities are
unrelated and cannot be used to manufacture a triple. The producer validates
all 22 rows, rejects any emitted predicate absent from the capability set and
reconciles every row count with the supplied assertions. It does not claim
that the pinned Explorer v0.6.2 PWA displays zero-evidence capability rows.

`root_sha256` covers compact, sorted-key UTF-8 JSON with a final newline for
the complete registry except `root_sha256` itself. The root therefore binds
`schema`, `profile`, `snapshot`, `generated_at`, all predicate definitions,
all implementation objects and all four aggregate counts; hashing only the
`predicates` array is invalid. The external resource SHA-256 separately binds
the published, indented JSON bytes.

The offline producer pins
`schemas/semantic-assertion.schema.json` to the final Explorer contract by
identifier, Draft 2020-12 version, exact 7,308-byte length and SHA-256
`f69480328db4b64d678d9c50b6534d808000f7fb50a30e8cc9e3bf2facbcb8bc`.
It validates the emitted reified node and a lossless mapping of every runtime
row, rejects remote schema references, and then requires the direct, reified
and runtime identity, route and triple sets to agree. The generated schema and
validation receipt are published under `bundle/data/semantic/` and covered by
the bundle manifests and checksums.

The current source-plane parity expectation is 22,267 direct triples, 22,267
reified assertions and 22,267 runtime rows. The IRI and class-route registries
cover all 10,951 route-bearing semantic identities, while the rich
relationship-runtime locator contains the 6,694 incident endpoint routes. The
runtime divides its rows into 90 relationship chunks and uses 256 locator
buckets. A digest-bound build receipt must reconfirm these values for the exact
v0.3.0 candidate bytes; they are not universal model requirements and must be
regenerated if an authoritative input changes.

Runtime parity concerns semantic identity, route and triple equality; it does
not require the browser row to duplicate every optional provenance string.
Each bounded row retains the evidence identity, type, source URL, source field,
source-value hash and retrieval time, with small non-redundant explanatory
fields where present. The canonical YAML-LD/JSON-LD assertion remains the
complete evidence-bearing record. For normalised assertions, the compressed
browser row uses `Derived` as its authority label, `See source rights.` as its
rights summary and omits the optional release-review status; the canonical
assertion retains the complete corresponding statements. Build validation
first measures the fresh full-source projection before a swap slot is reserved,
then recomputes Explorer's
UTF-16 retained-text accounting, compressed and decoded byte counts, full
default-plane hydration and all 6,694 relationship-runtime locator-route plans
before reporting the runtime as conformant.

Schema validity does not by itself prove that evidence binds to the correct
source field. Independent review found a P1 source-field evidence issue, as
well as P1 issues in CPSV adversarial binding and URL hardening. The corrections
are implemented and covered by local regression tests. Candidate receipts do
not self-assert independent acceptance or release readiness; version-scoped
evidence for the exact digest records that state.

## CPSV-AP service projection

The CPSV-AP 3.2.0 mapping is deliberately selective. Its reviewed mapping
register contains 11 candidate records, of which 7 are mapped to public
services and 4 are explicitly excluded, with 19 evidence references supporting
the decisions. Publisher attribution alone is not treated as proof of a
competent authority, and datasets, data services and service documentation are
not automatically promoted to `cpsv:PublicService`.

The official CPSV-AP 3.2.0 vocabulary, JSON-LD context and SHACL shapes are
vendored and digest-bound. The official SHACL shapes have not been run; the
current receipt covers local bounded projection checks only. Semantic
inference has not been run either. Neither omission may be represented as a
passing SHACL or inference result.

The bounded local projection represents HMLR's combined England and Wales
coverage as the exact governed `dcterms:Location`. It does not emit an EU
administrative territorial unit without separate authoritative identities.
Because the official Public Organisation shape requires its spatial target to
carry that ATU class, the receipt explicitly records that range as not claimed
and cannot be used as a full CPSV-AP conformance receipt.

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

The authoritative authoring layer comprises the governed content, evidence,
governance, profile and schema inputs declared by `okf.semantic.json`; trusted
generator code applies deterministic rules to those inputs. The build creates
one semantic graph, serialises it as canonical YAML-LD and emits equivalent
JSON-LD; the two files must parse to the same data model. Neither generated
serialisation is a separate authority. Explorer shards, adjacency, route
locators, registries, checksums, receipts and the static site are generated
projections under `bundle/` and must not be hand-edited. DCAT, CPSV-AP and
Schema.org mappings remain additive discovery projections and must not collapse
distinctions or introduce facts. CSV is a convenience projection and cannot
carry the complete rights, provenance or relationship model.

[EV-INSPIRE-DATA]: https://use-land-property-data.service.gov.uk/datasets/inspire

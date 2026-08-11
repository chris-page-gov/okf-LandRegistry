# HM Land Registry domain warm-up

Status: **owner-authorised Stage 1 profile for v0.3.0 semantic implementation;
the candidate bytes do not self-assert approval or release authority. Exact
approval and release authority are recorded only in subsequent digest-bound
G1–G9 evidence**
Research cut-off: **29 July 2026**
Semantic implementation direction: **10 August 2026**
Static closure review: **11 August 2026**

## Recommendation

Build a snapshot-bounded, metadata-only, large-corpus OKF bundle with the full
governed metadata-level semantic layer authorised by the project owner. The
first bounded population is every record returned by the GOV.UK Search API for
the `land-registry` organisation slug: 1,866 records at the cut-off. Add
separately governed lanes for HMLR datasets, public repositories, API catalogue
records, developer documentation and high-risk service descriptions. Do not
treat those associated lanes as complete until each has its own reconciled
denominator.

The portable control plane remains OKF 0.2 Markdown. The additive Bundle Wiki
profile now makes `okf-bundle.yamlld` the canonical semantic descriptor and
`okf-bundle.jsonld` its deterministic equivalent. Generated public
representations should include a semantic-model descriptor, closed
IRI-to-route and predicate registries, evidence-bearing directed relationship
assertions, a bounded Explorer relationship runtime, compact record/search
data, source/rights/coverage ledgers and an accessible GitHub Pages site.
This is application-profile conformance, not a claim that the YAML-LD Working
Draft is a W3C Recommendation.

## Why this shape

HM Land Registry material spans formal GOV.UK publications, datasets,
transactional services, linked data, Business Gateway contracts, help systems,
GitHub and an official blog. Those surfaces have different publishers,
identifiers, update cadences, audiences, authentication and rights. A single
flat “resource list” would hide the distinctions that matter most.

Stage 1 now enumerates all 17 governed source families one for one. Each row
retains its research `SRC-*` identity, the exact runtime source-family ID and
the primary governed rights decision used by the producer. This removes the
earlier eight-row aggregation, which could not safely express cases such as
the different rights applying to the official blog and customer help.

The observed GOV.UK population is already too large for an eager small bundle.
The full source universe also has independently changing lanes:

- 1,866 GOV.UK Search records at the research cut-off;
- 14 visible Use land and property data catalogue entries, pending adapter
  reconciliation;
- 15 HMLR-attributed rows in the frozen live CDDO API Catalogue snapshot,
  reconciled against an older 27-record sibling seed and useful for discovery but not
  operational authority;
- an exact frozen public API census of 289 repositories in the official GitHub
  organisation; and
- services, linked data, help and blog surfaces without a defensible global
  page denominator.

## Semantic implementation direction

The owner direction of 10 August 2026 replaces the v0.2.0 YAML-LD deferral and
single translation-edge implementation limit for candidate development. It
does not alter the metadata-only boundary or approve a release.

The governed build is required to generate one semantic assertion source and
compile it into:

- canonical YAML-LD and exactly equivalent JSON-LD, resolving pinned local
  contexts only;
- a digest-bound semantic-model descriptor, IRI-to-route registry and
  predicate registry;
- route-bearing entity nodes for catalogue, catalogue record, publication,
  collection, dataset, service/API, repository, publisher/agent, rights,
  source resource, observation activity, assertion-derivation activity,
  derivation rule, language, location and relationship assertion identities;
  distribution, standard/profile and administrative-territorial-unit classes
  remain explicit authorised zero-evidence capabilities;
- a direct triple and matching evidence-bearing
  `okf:RelationshipAssertion` for every material directed relationship; and
- an integrity-bound, bounded Reader projection whose assertion identities and
  endpoint routes reconcile exactly with the semantic graph.

The governed predicate registry covers the relationship families supported by
the frozen metadata: catalogue membership and primary topic; explicit
collection and distribution membership; distribution access service and
service-to-dataset links; publisher documentation; GOV.UK translations;
explicit version and replacement evidence; publisher and rights links; and
content-addressed derivation and generation provenance. A registry entry with
no qualifying source evidence creates no assertion and makes no coverage
claim.

The corrected Stage 1 contract derives its declared-set counts and digests
from the governed rows: it currently names 13 predicates intended for
emission and marks nine authorised relationship families with no
qualifying frozen evidence as `authorised-zero-evidence`. It likewise declares
23 entity types, including Bundle, reusable EvidenceResource and
assertion-scoped EvidenceBinding, and separately marks the three authorised but unemitted
classes. A reviewed 77-row source-native-type decision table preserves
collection, catalogue, organisation, documentation, service, dataset and
repository semantics independently of the coarse Explorer kind. This state is
schema-governed rather than inferred from prose, URLs or generated graph rows.

The same authority now declares the exact active core plane
`urn:okf:hmlr:plane:core` and a closed 14-IRI rule set: 13 relationship
derivations plus the source-observation rule used to identify observation
activities. Namespace shape alone is insufficient; an unlisted `/id/rule/`
IRI must fail validation.

The predicate-registry extension remains deliberately separate from the frozen
Bundle Wiki v1 profile and its Explorer v0.6.0 provenance. Explorer v0.6.1 has
now released Predicate Registry v2 as a separate complete document governed
by a separate complete schema, rather than extra keys in a v1 document that
strict v1 validators would reject. Stage 1 pins release commit
`839d4ba4c2d02abc6ef02b3ca1dcbf6a4008e7c8`, annotated tag object
`b5918192b1e3969ca2b069a4d56b3d26884ea96c`, the extension-lock SHA-256
`3d1f7cdbb423628f3938e5aef299ae09013f56be515ff2155475c5325ffd0110`,
profile identity
`75e444a35fdfe28fc111b6f0490cb8a0d569d20c1e4b62410174ead2608d86c6`
and the 7,551-byte schema SHA-256
`037151379a1ec0cbfe0666d41592585a891a63929f1fcf2845d1eb3de8dd5069`.

The v2 wire document requires the top-level `profile` identifier and all 22
predicate capabilities. Every predicate row contains nested
`implementation.state` and `implementation.assertions_emitted` fields derived
from the complete governed relationship set. Its four aggregate counts are
`predicates`, `active_emitted`, `authorised_zero_evidence` and
`assertions_emitted`, expected here to be 22, 13, nine and 22,267. The existing
vocabulary-lifecycle `status` meaning remains intact. The v2 `root_sha256`
binds canonical `{schema, profile, snapshot, generated_at, predicates,
counts}` material and excludes only `root_sha256`. An
`authorised-zero-evidence` row remains a capability declaration with exactly
zero assertions, not a relationship or absence claim. The consumer dependency
is therefore delivered; Stage 2 generation, candidate validation and
digest-bound release evidence remain outstanding and must not be inferred
from this Stage 1 closure.

The distinct LR-owned Stage 1 schema and profile are the single machine
semantic authority for this producer; they do not alter or claim byte identity
with the frozen Explorer shared authoring schema. Generation must consume its
class IRIs, source-native decisions, predicate IRIs and labels, absolute
domains and ranges, vocabulary identity/version, implementation states,
identity families, language and jurisdiction identities, and exact
source/right/evidence crosswalk. It delegates publisher identity/class rows,
CPSV decisions, curated rights/access rows, runtime source controls and
host-specific overrides, and governed rights definitions only through exact
whole-file SHA-256, schema, version, record-count and completeness declarations.
Builder
constants may implement mechanics but may not originate or widen those
semantic decisions. `TRACE-SEMANTICS: accepted` records acceptance of that
governed requirement and decision; it does not assert candidate conformance.
Exact active emitted closure, zero output for every explicit zero-evidence
decision and closed publisher references require separate digest-bound
candidate receipts and G-gates.

The publisher and source registers now distinguish the July source
`observed_at` from the August governance `reviewed_at`. Profile validation
requires observation not to follow review and requires review to precede the
configured build `generated_at`; none of these timestamps may be substituted
for another in provenance evidence.

One canonical source URL maps to one shared route-bearing source-resource
identity within the snapshot; the 2,280 current uses must therefore reconcile
to the 2,243 unique canonical URLs rather than minting record-specific
duplicates. Record-specific field and value evidence is represented by a
reusable `okf:EvidenceResource` plus a distinct triple-scoped
`okf:EvidenceBinding`. Their structural links do not recursively create
material relationship-plane assertions. Assertion-derivation activities and rule plans
are separately route-bearing so a provenance-path view can resolve them;
their identities bind the governed rule/tool and exact input digest, while
observation time remains data rather than identity material.

The class-to-route sidecar is a deterministic delivery index generated from
authoritative graph types and the digest-bound IRI-to-route registry. It is not
ontology authority and cannot decide class membership.

Publisher targets are always governed agents, but only exact publisher-
registry rows may also be organisations. Two collective publisher labels use
local collective Agent identities rather than borrowing the data.gov.uk
website or UK House Price Index collection-page IRIs. HMLR alone receives the
additional Core Public Organisation class. The HMLR organisation catalogue
record reuses its governed external organisation topic; the GitHub
organisation record likewise reuses its canonical organisation URL and is not
typed as source code.

The exact “England and Wales” value maps once to a stable local
`dcterms:Location`. It describes a combined legal and service jurisdiction,
not one EU administrative territorial unit. The narrower migrated-local-
authorities value maps to a separate stable location; no ATU instance is
emitted without separate authoritative administrative identities.

The vocabulary register includes the exact source vocabularies and controlled
authorities used by the graph: DCAT 3, PROV-O, DCMI Metadata Terms, FOAF,
Schema.org 30.0, RDF 1.1, RDF Schema 1.1, XML Schema Datatypes 1.1, SKOS,
CPSV-AP 3.2.0, Core Public Organisation
Vocabulary 2.1.2, the EU administrative-territorial-unit type vocabulary and
the EU language authority table. CPSV-AP context, vocabulary and SHACL assets
are pinned locally; the official SHACL is vendored but remains `not-run`, so
the project makes only the narrower validated projection claim.

Every material assertion must retain a stable assertion IRI, absolute source,
predicate and target IRIs, safe local endpoint routes, a governed relationship
kind, preferred and inverse labels, assertion status and scope, authority,
derivation, observation time, field-level evidence and rights. Markdown links,
display groups, facets, common publishers, title or URL similarity,
co-occurrence and embeddings are not relationship evidence.

Inference is declared `not-run` for the initial v0.3.0 candidate. There is no
unbounded OWL, RDFS, remote-context or browser-side reasoning. A future
materialised inferred plane would require a separately authorised finite rule
manifest, supporting assertion identities, confidence, derivation activity,
exact digests and separate plane membership. No inference may establish legal
ownership, priority, an exact boundary, beneficial ownership, rights,
authority, currency, nationwide coverage or semantic identity.

## Authority and interpretation

Current HMLR publisher pages and formal notices control operational metadata.
GOV.UK, CDDO and National Data Library catalogue records remain valuable
discovery provenance, but their dates must not be presented as dataset
currency. Legislation and current notices control legal requirements when they
conflict with older guidance, roadmaps or matrices.

The publication must make these distinctions visible:

- England and Wales are HMLR’s normal jurisdiction; the UK HPI is a
  collaborative UK-wide exception.
- A title plan or polygon product normally shows a general or indicative
  extent, not an exact legal boundary.
- A downloaded register or plan from the public information service is not an
  official copy for evidential purposes.
- “Free” does not mean unrestricted or OGL. CCOD, OCOD, leases, restrictive
  covenants and the National Polygon Service have distinct licence and access
  conditions.
- Bulk datasets are snapshots, not the live legal register.
- Price Paid Data has registration lag and exclusions; recent UK HPI estimates
  can be provisional and revised. Neither is a property valuation.
- Corporate proprietor data does not establish beneficial ownership.
- Local Land Charges coverage depends on migrated authorities and does not
  replace CON29 enquiries.
- Public documentation for Business Gateway or an API does not grant anonymous
  operational access.

## Rights, privacy and safety

Store public discovery metadata and source links only. Do not acquire title,
property, ownership, address, application, charge or search-result records.
Do not collect forum posts or user uploads. Do not authenticate to the portal,
Business Gateway, property-information service, Local Land Charges search or
dataset APIs.

The Local Land Charges service terms explicitly prohibit automated software
agents from searching, copying or monitoring the service. The bundle therefore
contains only its public description, terms and programme/coverage links.

Dataset rights are per record. OGL datasets still require the publisher’s
attribution and can include third-party address or mapping conditions. Bespoke,
direct-use, exploration and commercial licences must remain distinct. A
bundle-wide data licence would be misleading.

All ten governance decisions are now represented in Stage 1, including the
separate legislation, cross-government catalogue and fee-calculator controls.
The source-family crosswalk records the primary right used by generation;
additional privacy and restricted-service controls remain additive and must
not be flattened into that primary mapping.

## People and tasks

The profile defines seven evidence-led role archetypes: public property user,
property professional, lender/portfolio user, data/GIS user, software
integrator, Local Land Charges officer and provenance/accessibility auditor.
They are research hypotheses derived from official audience and service
guidance, not invented demographics or participant-validated personas.

The critical tasks are to:

1. find the correct official public service and understand its product limits;
2. retrieve current practice, form, fee, notice and digital-route guidance;
3. compare datasets without collapsing scope, cadence, access or rights;
4. select spatial products without implying exact legal boundaries;
5. identify the correct machine contract and onboarding requirement;
6. determine Local Land Charges coverage and the CON29 boundary; and
7. audit every derived claim back to source evidence.

## Evaluation

The retained first-release baseline uses a deliberately smaller 24-question
AI-agent-reviewed suite, mapped end-to-end through stories and personas. It
includes ordinary, near-miss, stale, conflicting, restricted, unsafe and
unanswerable cases. The direct-source baseline is official navigation and
search on the named publisher surface. Retrieval scores cannot override a hard
failure. The new semantic competency question and complete v0.3.0 relationship
set are candidate evidence and require fresh review; the earlier review cannot
be carried forward as semantic assurance.

Release thresholds include zero hard failures, complete source and caveat
resolution, primary-target MRR of at least 0.80, Recall@10 of at least 0.90
after independent verification, and no new critical category in the second
held-out adversarial pass. Semantic release thresholds additionally require
every assertion to validate, closed entity/predicate/evidence/rights
registries, exact direct/reified/runtime identity parity and an inference state
of `not-run` unless a separately reviewed bounded materialisation passes.

The large-corpus architecture also requires a pinned consumer contract and a
governed artefact-dependency graph. Stage 2 must first execute a tiny bundle
through every selected consumer, then execute the exact release candidate in
the pinned OKF Explorer and repeat the canonical deep link after deployment.
Impact analysis may select affected checks, but evidence is reusable only when
all of its declared dependency roots remain byte-identical.

## Residual gaps

- No global page denominator exists across all associated HMLR domains.
- Business Gateway documentation needs a deterministic current service/schema
  inventory.
- The observed 14-item dataset catalogue needs adapter reconciliation.
- Linked-data schemas and query limits were not fully exercised.
- Welsh representation parity has not been enumerated.
- Personas and usability have not been participant validated.
- The semantic competency question, full relationship population and
  inference-state controls have not yet received independent exact-candidate
  review.
- The displaced first v0.3.0 candidate failed static Stage 1 closure and none
  of its exact-root runtime or release-gate receipts can be carried forward.
- Owner approval is exact-digest scoped; v0.3.0 requires a new G9 decision.

None of these prevents authorised candidate implementation. They do prevent
release claims, full source coverage, production readiness, legal authority,
bilingual parity, accessibility conformance, semantic completeness across
unbounded HMLR sources or human preference.

## Saturation result

A second source-family pass added detail but no new critical class beyond:
formal publishing, public services, professional services, data, developer
interfaces, source code, support, governance, rights/accessibility and official
communications. Research is saturated for the scaffold; unresolved populations
are recorded as gaps instead of being silently treated as complete.

## Handoff

Stage 2 implementation and candidate assurance may proceed against this
owner-authorised profile because no open implementation decision is marked
`blocking_for_build`. The 10 August 2026 direction is not G9 and does not
authorise publication of any changed bytes. The completed v0.3.0 candidate must
first obtain fresh G1–G8 evidence, including independent source-family,
vocabulary, semantic, rights,
safety, accessibility, reproducibility and real-Reader review. Only then may
the owner be asked to approve the exact version, digest, residual risks, claims
and canonical identity in G9. The exact profile, evidence register,
traceability and checksums—not the research transcript—are the Stage 2 inputs.

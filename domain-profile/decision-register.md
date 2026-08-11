# HM Land Registry domain-profile decisions

This register contains only material owner decisions. The v0.2.0 release
conditions were fulfilled only for commit
`40482c865dc4332162f1e93756d94ca93abe3559` and release root
`a3e0bdf7846893ce29255f6f20a509dad18ef2b367ba3dfbe48c28191377a704`;
that decision is historical and exact-digest scoped. On 10 August 2026 the
project owner separately authorised implementation of the full metadata-level
semantic model for a v0.3.0 candidate. That direction permits implementation
and candidate assurance only: it is not G9 approval, an independent review or
authority to publish changed bytes. A static Stage 1 closure review on
11 August 2026 found that the first candidate had implemented source families,
rights decisions, classes, predicates, vocabularies and identity families that
the profile had not enumerated. Correcting that control-plane omission applies
the existing owner direction; it does not convert the review into release
approval or allow evidence bound to the displaced candidate to be reused.

## Accepted scaffold defaults

### DEC-SNAPSHOT — snapshot-bounded publication

Use immutable, dated source snapshots. Show both publisher dates and
observation dates. Require live revalidation before a user acts on volatile
fees, service state, migration coverage or data releases.

### DEC-METADATA-ONLY — no source data or authenticated content

Publish discovery metadata, evidence and links. Do not publish property-level
records, bulk data, forum content, authenticated responses, credentials or
signed download URLs.

### DEC-ARCHITECTURE — large-corpus OKF bundle

Use a Markdown control plane with integrity-bound lazy Explorer data and a
GitHub Pages site. Pin the Explorer consumer contract, execute its tiny and
release-candidate journeys, and use a governed artefact-dependency graph to
identify affected outputs and gates. The graph may reduce substantive reruns
only when its dependency roots prove unchanged; it cannot carry evidence
across a changed root. The 1,866-record GOV.UK denominator makes a small eager
bundle inappropriate.

### DEC-AUTHORITY — publisher source wins operational conflicts

Use current HMLR pages and formal notices for operational metadata. Preserve
older catalogues as discovery provenance and show the conflict rather than
silently overwriting it.

### DEC-ENRICHMENT — deterministic metadata only

The v0.3.0 semantic graph may publish source-native, normalised and explicitly
rule-derived metadata relationships. Do not publish model-assisted
classifications, relationships or answers without a separate evidence,
evaluation and owner decision.

### DEC-V011-SUPERSESSION — unpublished candidate folded into v0.2.0

The in-progress v0.1.1 worktree was never approved, tagged, archived or
published. Its consumer lock, Explorer-plane and change-impact work is retained
as input to v0.2.0. There is no v0.1.1 release claim; v0.1.0 remains immutable
and v0.2.0 is the next release candidate.

### DEC-SEMANTIC-SERIALIZATION — canonical YAML-LD with equivalent JSON-LD

Adopt the pinned OKF Bundle Wiki profile for the v0.3.0 candidate.
`okf-bundle.yamlld` is the canonical semantic descriptor and
`okf-bundle.jsonld` is its deterministic JSON-LD projection. Both represent
the same graph, resolve only pinned local contexts and are generated from one
governed assertion source. Large graph data may be sharded behind a
digest-bound semantic manifest, but the root descriptors and runtime
projection do not become competing semantic authorities.

### DEC-SEMANTIC-REGISTRIES — closed, digest-bound semantic registries

Generate and validate a snapshot-bound semantic-model descriptor, IRI-to-route
registry and predicate registry. The registries control entity classes,
absolute semantic identities, safe Reader routes, predicate IRIs, labels,
inverse labels, permitted assertion states, derivations, evidence policy,
authority classes and rights. Unknown terms or collisions fail closed. A
Markdown link, display group or search facet does not create a domain
relationship. Stage 1 must enumerate every active emitted class, predicate and
source vocabulary. Authorised classes and predicates for which the frozen
candidate has no qualifying evidence remain explicit
`authorised-zero-evidence` entries rather than being presented as implemented
or silently omitted.

The LR-owned, schema-valid Stage 1 profile is the machine semantic authority.
Its schema has the distinct Land Registry identity
`https://chris-page-gov.github.io/okf-LandRegistry/schemas/domain-profile.schema.json`;
it does not mutate or impersonate the frozen Explorer shared authoring schema.
Its `class_iris`, source-native-type decision table, relationship IRIs and
labels, absolute domain and range classes, vocabulary identity and version,
implementation states, identity families, controlled-vocabulary and
jurisdiction tables, and source/right/evidence crosswalk are generation inputs
rather than copies of builder constants. Publisher identities, CPSV service
decisions, per-record rights/access classifications, runtime source controls,
host-specific rights overrides and governed rights definitions are delegated
only through whole-file SHA-256, schema, version, exact record-count and
completeness declarations in the authority block. `TRACE-SEMANTICS: accepted`
records acceptance of this
governed requirement and decision only; it is not candidate-conformance
evidence. Exact active emitted closure, zero output for each authorised
zero-evidence row and closed publisher references must be proved separately in
the candidate's digest-bound receipts and G-gates.

The identity register preserves the established candidate families rather
than pretending that every identity is below one `/id/` prefix. It covers the
bundle, catalogue, catalogue record, represented entity, source resource,
rights, source-observation activity, rule activity, jurisdiction, reusable
evidence resource, assertion-scoped evidence binding, local collective agent,
assertion and semantic-runtime families, plus retained external publisher,
GitHub organisation and EU language-authority IRIs. Reader routes remain
separate registry-bound locators and are never semantic identities.

One canonical source URL has one shared `/id/source-resource/` identity within
the snapshot; record-specific source fields and value digests remain qualified
nested assertion evidence. Source-observation and assertion-derivation
activities include the governed rule or tool identity and exact input digest
in their identity material; observation time is data, not the identity key.
Assertion-derivation activities and rules are top-level route-bearing
`prov:Activity` and `prov:Plan` nodes so provenance-path views do not terminate
at unresolved IRIs. A reusable `okf:EvidenceResource` is content-addressed by
its source artefact, locator and value digest. A distinct
`okf:EvidenceBinding` is identified by the complete directed triple and
canonical evidence occurrence and links the assertion to that reusable
resource. Those structural evidence-model links do not recursively create
evidence-bearing relationship assertions. Neither evidence class is conflated
with a route-bearing source resource.

The generated class-to-route sidecar is explicitly a deterministic Reader
delivery index derived from authoritative `rdf:type` facts and the
digest-bound IRI-to-route registry. It is not ontology authority and cannot
originate, remove or widen class membership.

The source-native-type decision table, not the coarse Explorer kind, chooses
ontology classes. It preserves collections and catalogues through additive
multi-typing, treats the Local Land Charges terms page as a document rather
than a service, and reuses governed external HMLR and GitHub organisation
identities instead of minting duplicate CreativeWork or source-code topics.
Publisher targets are agents; only exact registry rows may add
`schema:Organization`, with HMLR alone also a `cv:PublicOrganisation`.
Collective agents never borrow website or collection-page IRIs.

The exact “England and Wales” scope resolves to one local
`dcterms:Location`; it is a combined legal and service jurisdiction, not one
EU administrative territorial unit. The narrower migrated-local-authorities
scope is a distinct local location. No ATU instance is emitted until separate
authoritative administrative-territorial-unit identities are governed.

### DEC-SEMANTIC-RELATIONSHIPS — comprehensive evidence-bearing metadata graph

Replace the single translation edge demonstration with all material directed
metadata relationships that the frozen sources support. This includes
catalogue membership and primary-topic identity; explicit collection,
distribution, documentation, service/dataset, translation, version and
replacement relationships; and publisher, rights and provenance links. Every
published material relationship has one stable assertion IRI, absolute source,
predicate and target IRIs, safe local endpoint routes, governed labels, status,
scope, authority, derivation, observation time, field-level evidence and a
rights statement. The direct triple, reified assertion and Reader runtime row
are generated from one assertion source and reconcile exactly.

Relationships are emitted only when the frozen metadata or a declared
deterministic rule supplies the required evidence. An allowed predicate with
zero supported assertions remains a registry capability, not a fabricated
fact or a coverage claim. The Stage 1 contract distinguishes the 13 active
emitted predicates from nine authorised zero-evidence relationship families;
an implementation may not move a term between those states without refreshed
source evidence, validation and review.

Stage 1 also names the one active default relationship plane exactly as
`urn:okf:hmlr:plane:core` and closes the rule family over 14 exact IRIs: one
source-observation rule and one relationship-derivation rule for each of the 13
active predicates. The broad `/id/rule/` pattern is only a namespace check;
membership in that exact governed set is separately mandatory. An undeclared
rule or plane must fail before generation.

The frozen Bundle Wiki v1 profile and its Explorer v0.6.0 provenance remain
unchanged. Predicate Registry v2 is a separate complete document governed by
a separate complete schema; it is not a v1 document with keys that strict v1
validators can ignore. Explorer v0.6.1 released that extension on 11 August
2026 at commit `839d4ba4c2d02abc6ef02b3ca1dcbf6a4008e7c8`, annotated tag object
`b5918192b1e3969ca2b069a4d56b3d26884ea96c`. Stage 1 pins the 744-byte
extension lock at SHA-256
`3d1f7cdbb423628f3938e5aef299ae09013f56be515ff2155475c5325ffd0110`,
its aggregate profile identity
`75e444a35fdfe28fc111b6f0490cb8a0d569d20c1e4b62410174ead2608d86c6`
and the 7,551-byte schema at SHA-256
`037151379a1ec0cbfe0666d41592585a891a63929f1fcf2845d1eb3de8dd5069`.

The v2 registry publishes all 22 governed predicate capabilities. Each wire
row has a nested `implementation` object containing `state` and
`assertions_emitted`; the producer derives both from the complete governed
relationship set. `active-emitted` requires at least one assertion, while
`authorised-zero-evidence` requires exactly zero and does not manufacture a
relationship or absence claim. The four aggregate counts are `predicates`,
`active_emitted`, `authorised_zero_evidence` and `assertions_emitted`, expected
for this governed snapshot to be 22, 13, nine and 22,267 respectively. The
existing row-level `status` field continues to describe vocabulary lifecycle
and must not be overloaded with implementation state. `root_sha256` binds the
canonical complete document except `root_sha256` itself: `{schema, profile,
snapshot, generated_at, predicates, counts}`. The released dependency is
therefore delivered and exact; Stage 2 must still generate, validate and
receipt the corresponding candidate bytes before any conformance or release
claim.

Source observation and governance review are distinct events. The source and
publisher registers preserve their July `observed_at` values and separately
record the August `reviewed_at` decision. The Stage 1 checker requires
`observed_at <= reviewed_at < source/build-config.json.generated_at`; generated
evidence must not relabel the later governance review as source observation.

### DEC-SEMANTIC-INFERENCE — bounded materialisation, no browser reasoning

The candidate does not perform unbounded OWL, RDFS, remote-context or
open-world inference. Source-native and deterministic normalised assertions
are the default planes. Any later inferred plane must use an allowlisted,
versioned rule over a finite frozen input set, retain supporting assertion IDs,
confidence and derivation activity, publish a digest-bound rule manifest and
materialised output, and remain distinguishable from official and normalised
assertions. No rule may infer ownership, priority, an exact legal boundary,
beneficial ownership, rights, authority, currency, nationwide coverage or
semantic identity.

### DEC-SEMANTIC-V03-AUTHORISATION — implementation direction only

The project owner directed implementation of the full metadata-level semantic
model on 10 August 2026. The direction supersedes the v0.2.0 YAML-LD deferral
and single-edge implementation limit for candidate development. It does not
approve an exact version or digest, accept residual risk, constitute external
or independent review, or close G9. The completed candidate must pass fresh
G1–G8 evidence before the owner is asked for an exact-digest release decision.

## Accepted release decision

### DEC-RELEASE — owner approval and canonical identity

The project owner approved v0.2.0 for publication only as an AI-generated
proof of concept at
`https://chris-page-gov.github.io/okf-LandRegistry/`. Its release conditions
were fulfilled for commit `40482c865dc4332162f1e93756d94ca93abe3559` and
release root
`a3e0bdf7846893ce29255f6f20a509dad18ef2b367ba3dfbe48c28191377a704`.
That exact candidate was released; the decision has no carry-over to changed
bytes or a later version.

Consequences:

- the approval does not extend to later candidate bytes;
- the publication is not an HM Land Registry service or endorsement;
- no completeness, production-readiness, legal-reliance or
  accessibility-conformance claim is permitted; and
- later releases require a new exact-digest owner decision.

The 10 August 2026 semantic implementation direction does not extend this
historical decision to the v0.3.0 candidate. Candidate bytes do not self-assert
their current G1–G9 state or release authority; only version-scoped evidence
and the owner decision bound to the exact digest record that state.

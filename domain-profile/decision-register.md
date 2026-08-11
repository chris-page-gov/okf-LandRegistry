# HM Land Registry domain-profile decisions

This register contains only material owner decisions. The v0.2.0 release
conditions were fulfilled only for commit
`40482c865dc4332162f1e93756d94ca93abe3559` and release root
`a3e0bdf7846893ce29255f6f20a509dad18ef2b367ba3dfbe48c28191377a704`;
that decision is historical and exact-digest scoped. On 10 August 2026 the
project owner separately authorised implementation of the full metadata-level
semantic model for a v0.3.0 candidate. That direction permits implementation
and candidate assurance only: it is not G9 approval, an independent review or
authority to publish changed bytes.

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
relationship.

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
fact or a coverage claim.

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

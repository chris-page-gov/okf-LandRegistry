# Changelog

All notable project-authored changes will be recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and approved releases
will use semantic versioning where it fits the artefact lifecycle.

## Unreleased

### Documentation

- Explained the v0.3.0 semantic-contract migration, the displaced pre-v0.3
  prototype and the deliberate separation between approval-neutral candidate
  bytes, exact external G9 evidence and later repository state. The candidate
  remains unchanged; this clarification does not inherit or reopen its owner
  approval. Documented the bounded routine-update path for minor repository
  corrections that leave causal inputs and generated roots unchanged, plus a
  full post-release maintenance lane that validates immutable historical G1–G9
  evidence without treating a later repository commit as release evidence.

## [0.3.0] - 2026-08-11

### Added

- A canonical deterministic YAML-LD semantic serialisation with an equivalent
  JSON-LD serialisation.
- Evidence-bearing `okf:RelationshipAssertion` nodes with stable semantic
  identities, local routes, authority, derivation, observation, evidence and
  rights metadata.
- An exact Explorer v0.6.2 executable consumer lock, the unchanged 16-file
  Bundle Wiki v1 semantic profile lock sourced from Explorer v0.6.0, and a
  two-file Predicate Registry v2 extension lock sourced from Explorer v0.6.1.
  The latter pins the 7,551-byte schema at SHA-256
  `037151379a1ec0cbfe0666d41592585a891a63929f1fcf2845d1eb3de8dd5069`.
- An offline-pinned 7,308-byte Draft 2020-12 semantic assertion schema with
  SHA-256
  `f69480328db4b64d678d9c50b6534d808000f7fb50a30e8cc9e3bf2facbcb8bc`,
  plus a generated validation and parity receipt.
- A Land Registry class-to-route delivery index and local schema. The index is
  derived from canonical semantic-graph `rdf:type` facts and the digest-bound
  IRI-to-route registry; it binds both source roots but is not ontology
  authority, a source of class membership or an inference result.
- A reviewed CPSV-AP 3.2.0 mapping for 11 candidate service records: 7 mapped,
  4 explicitly excluded and 19 supporting evidence references. The official
  vocabulary, context and SHACL shapes are vendored and digest-bound.
- A pre-publication full semantic and CPSV-AP projection gate that preserves
  England and Wales as the governed `dcterms:Location` and verifies that the
  administrative-territorial-unit class remains authorised with zero emitted
  instances rather than conflating the combined jurisdiction with one unit.
  The receipt records that the official CPSV-AP Public Organisation spatial
  ATU range is not claimed as satisfied.

### Changed

- Stage 1 now declares one active `core` relationship plane and a closed set
  of 14 derivation rules. The builder enforces exact plane, status,
  predicate-to-rule and rule-identity membership instead of accepting an
  arbitrary IRI from the project rule namespace.
- Explorer search now indexes every governed publisher on a record, while
  retaining the primary scalar publisher for display compatibility. This
  exposes all 27 publisher identities, including secondary-only matches.
- Source and publisher register observation dates remain distinct from their
  11 August 2026 governance-review dates. Evidence for register policy uses
  review time; source observations retain their original observation time.
- Stage 1 records 22 predicate capabilities: 13 active emitted predicates and
  nine authorised-zero-evidence predicates. The generated Predicate Registry
  v2 projects all 22, derives every nested implementation state and count from
  the complete 22,267-assertion plane, rejects undeclared emissions, and binds
  its complete document except `root_sha256` into the registry root. The nine
  zero-evidence rows do not create assertions or imply that the pinned Explorer
  v0.6.2 PWA presents them.
- The GOV.UK Welsh-to-English translation edge now uses the absolute
  `https://schema.org/translationOfWork` predicate and is projected from the
  same assertion source into the direct graph triple and Explorer adjacency.
- Semantic tests now require YAML-LD/JSON-LD data equivalence and reconcile
  direct triples with their reified assertions.
- The governed rich-relationship source projection now produces 22,267 direct,
  reified and runtime assertions over 13 active predicates. The IRI and derived
  class-route registries contain all 10,951 route-bearing semantic identities;
  6,694 incident endpoint routes belong to the relationship-runtime locator,
  with 90 relationship chunks and 256 locator buckets. The exact candidate
  build receipt remains authoritative for released bytes.
- The rich runtime now uses deterministic endpoint locality and a bounded
  evidence projection while retaining the complete evidence envelope in
  canonical YAML-LD/JSON-LD. Producer validation exhaustively checks the
  pinned Explorer's per-row, per-chunk, full-hydration and every
  relationship-runtime locator-route limit, including compressed bytes,
  decoded bytes and UTF-16 retained text. A fresh full-source preflight runs
  before any publication swap. Normalised browser rows use the concise
  presentation strings `Derived` and `See source rights.` and omit the optional
  release-review status; the canonical assertion graph retains the complete
  authority, rights and review statements.
- The historical v0.2.0 Explorer receipt remains immutable and is explicitly
  rejected as evidence for changed v0.3.0 candidate bytes.
- G9 assembly and checking now bind the governed version and publication base,
  require canonical owner and reviewer identities, enforce the complete UTC
  review chronology, read schemas and evidence through secure repository-local
  file handles, and admit frozen v0.1.0/v0.2.0 evidence only from its actual
  byte buffers.
- `RISK-018` records that exact byte binding does not authenticate a named
  owner or reviewer and must not be described as a digital signature.
- Stage inputs and validation inputs define the complete candidate-control
  surface, while the separate top-level `build_inputs` role alone defines the
  causal build receipt. Only repository-relative literals and final `/**`
  patterns are accepted; generated outputs are subtracted and unsafe,
  zero-match, symbolic-link and mutable validation/dist inputs fail closed.
- The builder and change-impact classifier now share that path grammar and the
  complete relational graph validation. A schema-valid stage output cannot
  hide an authored input unless it is also inside a declared generated root.
- The deterministic build time now follows every governed snapshot,
  domain-profile and CPSV-AP evidence/review event, and the receipt records the
  latest event checked.
- Checksummed bundle artefacts use a dedicated bounded size policy so the real
  YAML-LD and JSON-LD serialisations validate without weakening the smaller
  evidence, profile, archive or governed-input limits.
- Equivalent JSON-LD now uses deterministic compact JSON whitespace, preserving
  the complete source-domain assertions and exact YAML-LD/JSON-LD equality for
  each candidate while keeping every generated Pages-source blob within
  GitHub's 100 MiB ordinary-Git limit. Regeneration correctly rebinds provenance
  identities to the changed generator digest and produces a new release root.
  Release checks enforce the same per-member ceiling instead of admitting an
  unpushable 128 MiB blob.
- v0.3.0 pre-G9 and final G9 evidence is routed through explicit versioned
  directories; immutable root-level v0.2.0 evidence is never a default output
  or current-candidate workflow input.
- Calibration diagnostics name an explicit reviewable output instead of the
  ignored `evaluation/latest-report.json` default.
- Staged and committed candidate preflights prove the complete governed blob
  inventory before evidence work, and the release history remains a linear,
  non-force fast-forward from candidate to evidence rather than a squash,
  rebase or merge from a pre-candidate parent.

### Validation boundary

- Local candidate validation reports direct/reified/runtime parity. It does
  not report semantic inference, which has not been run.
- CPSV-AP 3.2.0 is vendored, but the official CPSV-AP SHACL shapes have not
  been run; the current receipt covers the project's bounded projection
  checks only and does not claim general SHACL conformance.
- The source-field evidence, CPSV adversarial-binding and URL-hardening
  corrections identified by independent review are implemented and covered by
  local regression tests. Candidate bytes do not claim independent acceptance;
  version-scoped evidence for the exact digest records that state.

### Release posture

- The v0.3.0 bundle is produced as approval-neutral candidate bytes. Those
  bytes do not self-assert a current G1–G9 state, owner decision, release
  readiness or deployment authority. The authoritative state and any approval
  exist only in version-scoped, digest-bound external evidence. The v0.2.0
  approval and release remain bound to their existing digest.

## [0.2.0] - 2026-08-04

### Fixed

- Replaced the incompatible large-corpus inventory entrypoint with the
  Explorer overview, chunk, search, record-locator and relationship-adjacency
  contracts required by the real OKF Explorer loader.
- Added a consumer regression journey covering load, search, safety caveat,
  deep link, resources, relationships, console output and requested resources.
- Corrected scalar geography projection so values remain whole jurisdictions
  rather than character fragments.
- Corrected selected-record resource hydration in OKF Explorer.

### Added

- Added the browser-verified OKF Explorer launch route and exact public
  descriptor links to the repository README.
- A pinned Explorer consumer lock and fail-closed compatibility validation.
- A governed v2 record contract, source-type crosswalk, publisher registry,
  bounded Content API translation observations and corrected JSON-LD graph.
- A governed artefact-dependency graph and change-impact classifier mapping
  inputs to generated planes, consumers and release gates.
- Foundry workflow requirements for two-stage real-consumer fixtures,
  bidirectional compatibility and post-deployment deep-link checks.

### Release lineage

- The unpublished v0.1.1 candidate was superseded and folded into v0.2.0. No
  v0.1.1 tag, archive or release is created.

## [0.1.0] - 2026-07-29

### Added

- Reviewed, checksum-bound two-stage HM Land Registry domain-profile pack.
- Source-family inventory with named, dated per-family denominators.
- Product, architecture, scope, metadata, standards, accessibility,
  reproducibility and release-assurance documentation.
- Machine-readable requirements, traceability, risk and rights controls.
- Evidence-led candidate personas, user stories and evaluation framework.
- Security, citation and project-content licensing policies.
- Provenance-rich v2 source receipts for 1,866 GOV.UK records, 289 official
  public repositories and 15 live CDDO catalogue rows.
- Deterministic catalogue, rights, provenance, reconciliation and coverage
  projections with compact search and lazy record shards.
- Accessible GitHub Pages site with a complete no-JavaScript catalogue.
- Exact-digest deployment approval and tamper-checked release manifests.
- A prominent AI-generated proof-of-concept disclosure on every public entry
  page.
- Hash-locked build dependencies, gate receipts, SBOM and provenance evidence
  for the exact release candidate.

### Security

- Metadata-only boundary excludes property-level data, credentials, signed
  URLs, authenticated responses, forum content and restricted-service
  automation.

### Known limitations

- Associated-domain coverage, Welsh parity, participant research and
  independent human accessibility assurance remain incomplete.
- Personas and evaluation expectations are evidence-led AI-assisted
  hypotheses, not findings from representative participants.

[0.3.0]: https://github.com/chris-page-gov/okf-LandRegistry/releases/tag/v0.3.0
[0.2.0]: https://github.com/chris-page-gov/okf-LandRegistry/releases/tag/v0.2.0
[0.1.0]: https://github.com/chris-page-gov/okf-LandRegistry/releases/tag/v0.1.0

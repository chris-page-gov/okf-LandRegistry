# Changelog

All notable project-authored changes will be recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and approved releases
will use semantic versioning where it fits the artefact lifecycle.

## [0.3.0] - 2026-08-11

### Added

- A canonical deterministic YAML-LD semantic serialisation with an equivalent
  JSON-LD serialisation.
- Evidence-bearing `okf:RelationshipAssertion` nodes with stable semantic
  identities, local routes, authority, derivation, observation, evidence and
  rights metadata.
- An exact Explorer v0.6.0 consumer lock and 16-file Bundle Wiki semantic
  profile lock.
- An offline-pinned 7,308-byte Draft 2020-12 semantic assertion schema with
  SHA-256
  `f69480328db4b64d678d9c50b6534d808000f7fb50a30e8cc9e3bf2facbcb8bc`,
  plus a generated validation and parity receipt.
- A reviewed CPSV-AP 3.2.0 mapping for 11 candidate service records: 7 mapped,
  4 explicitly excluded and 19 supporting evidence references. The official
  vocabulary, context and SHACL shapes are vendored and digest-bound.

### Changed

- The GOV.UK Welsh-to-English translation edge now uses the absolute
  `https://schema.org/translationOfWork` predicate and is projected from the
  same assertion source into the direct graph triple and Explorer adjacency.
- Semantic tests now require YAML-LD/JSON-LD data equivalence and reconcile
  direct triples with their reified assertions.
- The rich relationship runtime now projects 22,226 direct, reified and
  runtime assertions over 13 governed predicates and 6,733 routes, divided
  into 89 relationship chunks and 256 route-locator buckets.
- The rich runtime now uses deterministic endpoint locality and a bounded
  evidence projection while retaining the complete evidence envelope in
  canonical YAML-LD/JSON-LD. Producer validation exhaustively checks the
  pinned Explorer's per-row, per-chunk, full-hydration and every-route limits,
  including compressed bytes, decoded bytes and UTF-16 retained text.
- The historical v0.2.0 Explorer receipt remains immutable and is explicitly
  rejected as evidence for changed v0.3.0 candidate bytes.
- G9 assembly and checking now bind the governed version and publication base,
  require canonical owner and reviewer identities, enforce the complete UTC
  review chronology, read schemas and evidence through secure repository-local
  file handles, and admit frozen v0.1.0/v0.2.0 evidence only from its actual
  byte buffers.
- `RISK-018` records that exact byte binding does not authenticate a named
  owner or reviewer and must not be described as a digital signature.
- Governed build inputs now come from every dependency-graph stage input and
  validation input. Only repository-relative literals and final `/**`
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

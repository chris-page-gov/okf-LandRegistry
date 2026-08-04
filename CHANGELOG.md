# Changelog

All notable project-authored changes will be recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and approved releases
will use semantic versioning where it fits the artifact lifecycle.

## [Unreleased]

## [0.2.0] - 2026-07-29

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

- A pinned Explorer consumer lock and fail-closed compatibility validation.
- A governed v2 record contract, source-type crosswalk, publisher registry,
  bounded Content API translation observations and corrected JSON-LD graph.
- A governed artifact-dependency graph and change-impact classifier mapping
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

[Unreleased]: https://github.com/chris-page-gov/okf-LandRegistry/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/chris-page-gov/okf-LandRegistry/releases/tag/v0.2.0
[0.1.0]: https://github.com/chris-page-gov/okf-LandRegistry/releases/tag/v0.1.0

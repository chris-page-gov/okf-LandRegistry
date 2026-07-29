# HM Land Registry domain-profile decisions

This register contains only material owner decisions. The profile is approved
for the exact v0.1.0 AI-generated proof-of-concept release.

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

Use a Markdown control plane with lazy Explorer data and a GitHub Pages site.
The 1,866-record GOV.UK denominator makes a small eager bundle inappropriate.

### DEC-AUTHORITY — publisher source wins operational conflicts

Use current HMLR pages and formal notices for operational metadata. Preserve
older catalogues as discovery provenance and show the conflict rather than
silently overwriting it.

### DEC-ENRICHMENT — deterministic metadata only

Do not publish model-assisted classifications, links or answers in the first
scaffold.

## Accepted release decision

### DEC-RELEASE — owner approval and canonical identity

The project owner approved v0.1.0 for publication only as an AI-generated
proof of concept at
`https://chris-page-gov.github.io/okf-LandRegistry/`, subject to passed G1–G8
receipts and the exact digest recorded in G9.

Consequences:

- the approval does not extend to later candidate bytes;
- the publication is not an HM Land Registry service or endorsement;
- no completeness, production-readiness, legal-reliance or
  accessibility-conformance claim is permitted; and
- later releases require a new exact-digest owner decision.

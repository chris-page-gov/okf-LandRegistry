# HM Land Registry domain-profile decisions

This register contains only material owner decisions. The profile is reviewed,
not release-approved.

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

## Open, non-blocking

### DEC-RELEASE — owner approval and canonical identity

Recommended default: keep all generated output `draft` until the owner reviews
the frozen source counts, rights ledger, evaluation results and intended GitHub
Pages URL.

Consequences:

- validation CI can run before approval;
- a generated site is not automatically a public release;
- no final version, completeness or accessibility-conformance claim is
  permitted before approval and independent evidence.

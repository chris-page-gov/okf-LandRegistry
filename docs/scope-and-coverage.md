# Scope and coverage

Status: reviewed scaffold, not an approved completeness claim.  
Snapshot policy: snapshot-bounded.  
Research cutoff: 2026-07-29.

## Unit of record

A record is one source-native public discovery object: a GOV.UK
edition/route, HM Land Registry dataset, distribution, operational service,
API product, public repository or governed source-family/control entry.
Alternate representations and versions are related, not merged.

The bundle indexes metadata. It does not index property-level source data.

## Included

- Records returned by the frozen GOV.UK Search query attributed to
  organisation slug `land-registry`.
- Publisher-operated HM Land Registry dataset and service metadata selected by
  explicit deterministic adapters.
- Public HM Land Registry GitHub repository metadata.
- CDDO API Catalogue rows attributed to provider `hm-land-registry`.
- Source, rights, coverage, standards, persona, evaluation and provenance
  control records.

## Excluded

- Title, ownership, address, charge, application, polygon, transaction and
  search-result records.
- Full GOV.UK page bodies and downloaded attachments.
- Authenticated APIs, portal content, paid products and short-lived download
  links.
- Customer-help forum posts, uploads and user-supplied personal information.
- Production dataset files, public repository source trees and third-party
  republications without official provenance.
- Any automated Local Land Charges search.

An exclusion is not a statement that a source is unimportant. It means the
content is outside the metadata-discovery purpose or cannot safely be
republished under the scaffold controls.

## Named denominators

| ID | Population observed | Count | Basis and confidence |
|---|---|---:|---|
| `DEN-GOVUK` | GOV.UK Search records attributed to `land-registry` | 1,866 | Exact Search API total observed on 2026-07-29; the total is volatile |
| `DEN-ULPD` | Dataset entries visible in Use land and property data | 14 | Human-observed catalogue count; adapter reconciliation is still required |
| `DEN-CDDO-API` | CDDO catalogue rows with provider `hm-land-registry` | 15 | Exact frozen live provider-filtered catalogue observation; an older sibling seed had 27 |
| `DEN-GITHUB` | Public repositories for `LandRegistry` | 289 | Exact frozen public GitHub API census |

Evidence: [GOV.UK Search][EV-GOVUK-SEARCH], [Use land and property
data][EV-ULPD], [CDDO API Catalogue][EV-CDDO] and [HM Land Registry on
GitHub][EV-GITHUB].

These populations overlap and must not be summed into a unique-record total.
Each source has a different object model and refresh cadence.

## What “coverage” means

A source-family coverage statement is valid only when it gives:

- the denominator ID and definition;
- the source observation time and immutable snapshot digest;
- expected, acquired, excluded, failed and unresolved counts;
- one terminal outcome for every expected item;
- duplicate and overlap handling; and
- known omissions.

“All HM Land Registry information” is not an acceptable claim. There is no
global page-level denominator across GOV.UK, publisher-operated services,
developer documentation, linked data, help, GitHub and the official blog
(`GAP-ASSOCIATED-DENOMINATOR`).

## Known coverage gaps

- The current Business Gateway product and schema population has not been
  reconciled against the publisher developer pack (`GAP-BG-DENOMINATOR`).
- The 14-entry dataset catalogue observation needs deterministic
  reconciliation (`GAP-DATASET-DENOMINATOR`).
- Linked-data capabilities and query limits are documented but not
  operationally assured (`GAP-LINKED-DATA`).
- Blog and customer-help article populations are not enumerated; forum content
  is intentionally prohibited.
- Welsh representation parity is not measured (`GAP-WELSH`).
- Personas are not validated by representative participants
  (`GAP-HUMAN-RESEARCH`).

## Temporal and geographic interpretation

The source estate changes continuously. Publication modification, dataset
release, coverage period, validity period, observation and bundle generation
are distinct dates.

HM Land Registry registers land and property in England and Wales, but an
individual dataset can have narrower coverage, migrated-authority limits,
freehold/leasehold differences or missing/pending records. The bundle must use
the dataset’s own coverage statement. A geographic link or polygon is not
evidence of an exact legal boundary.

## Release language

Until all terminal outcomes reconcile and `DEC-RELEASE` is approved, describe
the output as a “reviewed metadata scaffold” or “candidate snapshot”. Do not
describe it as complete, current, official, exhaustive or approved.

[EV-GOVUK-SEARCH]: https://www.gov.uk/api/search.json?filter_organisations=land-registry
[EV-ULPD]: https://use-land-property-data.service.gov.uk/
[EV-CDDO]: https://github.com/co-cddo/api-catalogue/blob/main/data/catalogue.csv
[EV-GITHUB]: https://github.com/LandRegistry

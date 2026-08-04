# Sources, rights, privacy and ethics

Status: policy-reviewed for the metadata-only v0.2.0 AI-generated
proof-of-concept candidate. Exact-digest release evidence and owner approval
are still required. The review was
AI-assisted and no independent human legal or licence audit has been
completed. This document records project controls and is not legal advice.

## Governing principles

1. Public access does not by itself grant reuse or redistribution rights.
2. A zero-price product is not necessarily openly licensed.
3. Rights attach to each layer and distribution, not to the bundle as a whole.
4. Publisher-operated sources control current operational facts.
5. Unknown rights fail closed.
6. The minimum necessary content for discovery is metadata and a source link.
7. Statutory access to register information does not remove privacy or ethical
   risk.

## Authority ladder

Use legislation and formal notices for legal requirements; current HM Land
Registry pages, terms and developer packs for operation; GOV.UK and official
catalogues for discovery; and local normalisation only as an explicitly
derived layer. Preserve conflicting observations instead of silently choosing
one without provenance.

Key sources are the [HM Land Registry public data policy][EV-PUBLIC-DATA],
[Use land and property data catalogue][EV-ULPD], [property-information
guidance][EV-PROPERTY-SERVICE], [Local Land Charges terms][EV-LLC-TERMS],
[Business Gateway documentation][EV-BG-DOCS] and [personal information
charter][EV-PERSONAL-INFO].

## Rights decisions by layer

| Control | Layer | Scaffold treatment |
|---|---|---|
| `RIGHT-GOVUK` | GOV.UK/HMLR page metadata | Metadata may be republished with OGL attribution, subject to stated exceptions; bodies and attachments remain out of scope |
| `RIGHT-DATASETS` | Dataset metadata and distributions | Metadata and links only; retain the exact dataset licence, attribution, fee and third-party terms |
| `RIGHT-RESTRICTED` | Property, LLC, portal and Business Gateway services | Public descriptions only; no authentication, execution, monitoring or result collection |
| `RIGHT-GITHUB` | Public repository metadata | Metadata only; preserve repository licence, fork and archive states; missing licence means no code-reuse assumption |
| `RIGHT-CDDO` | CDDO discovery metadata | Preserve catalogue provenance and reverify operation at the publisher source |
| `RIGHT-EVIDENCE` | Project-authored acquisition and validation receipts | Publish bounded receipt metadata under CC BY 4.0; this does not relicense any underlying source |
| `RIGHT-PERSONAL` | Property, register, forum and user-submitted information | Prohibited from acquisition, combination, indexing and publication |

The project-authored code and documentation licence in `LICENSE.md` does not
relicense source metadata, source documents, linked datasets, trade marks or
third-party material.

## Acquisition rules

Acquisition is allowed only when all of the following are true:

- the route is an allowlisted public metadata source;
- robots, terms and access controls do not prohibit the planned operation;
- request count, pagination, redirects, response size and media type are
  bounded;
- no account, API key, certificate, payment or click-through licence is
  bypassed;
- no signed or expiring URL is retained;
- the response is screened before entering a public projection; and
- the source URL, observation time, status and digest are recorded.

The bundle must never automate the Local Land Charges search because its terms
prohibit automated agents searching, copying or monitoring the service
(`CONSTRAINT-LLC-AUTOMATION`). Authenticated ULPD, portal and Business Gateway
operation is also outside scope.

## Privacy and data minimisation

The public output must not contain names, personal contact details,
property-level ownership or charge records, user queries, forum posts, uploads,
authentication identifiers or other personal-level content. Evaluation
fixtures use synthetic or aggregate descriptions and official URLs only.

Before release:

- scan acquired fields and generated output for credentials, tokens, signed
  URLs, personal contact patterns and unexpected high-entropy strings;
- inspect samples from every source adapter;
- verify allowlisted field projections rather than relying on deny lists;
- document any false positives and reviewer disposition; and
- remove unsafe material from inputs and rebuild—never patch generated output.

If personal data is discovered in a candidate, stop publication, restrict
access, preserve only the minimum incident evidence and follow `SECURITY.md`.

## Ethical hazards and user-facing caveats

- **Legal reliance:** metadata can be incomplete or stale. Link to the current
  official route and state that the bundle is not legal advice.
- **Ownership and priority:** discovery records do not prove ownership or
  registration priority.
- **Boundaries:** title plans and polygon products commonly show general or
  indicative extents, not exact legal boundaries.
- **Corporate ownership:** a registered corporate proprietor is not necessarily
  the beneficial owner.
- **Temporal lag:** Price Paid Data and UK HPI can lag, be incomplete for
  recent periods or be revised.
- **Local Land Charges:** coverage depends on local-authority migration, and
  CON29 enquiries are separate.
- **Accessibility and language:** an accessible-looking interface is not proof
  of WCAG conformance or Welsh parity.

These caveats are retrieval requirements, not footnotes to be hidden from the
result view.

## Rights review triggers

Repeat the rights review when a source, route, field, licence, terms page,
acquisition method, public projection, canonical URL or intended audience
changes. Also review before every public release, even if the source bytes are
unchanged. Record outcomes in `governance/rights-review.json` and bind them to
the candidate digest.

Unknown, bespoke, restricted and prohibited are materially different states.
Do not convert any of them to “open” from inference.

[EV-PUBLIC-DATA]: https://www.gov.uk/government/publications/hm-land-registry-data/public-data
[EV-ULPD]: https://use-land-property-data.service.gov.uk/
[EV-PROPERTY-SERVICE]: https://www.gov.uk/search-property-information-land-registry
[EV-LLC-TERMS]: https://search-local-land-charges.service.gov.uk/terms-and-conditions
[EV-BG-DOCS]: https://landregistry.github.io/bgtechdoc/
[EV-PERSONAL-INFO]: https://www.gov.uk/government/organisations/land-registry/about/personal-information-charter

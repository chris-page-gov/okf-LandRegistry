# Product contract

Status: **v0.2.0 AI-generated PoC contract; publication is exact-digest gated**
Research cutoff: **2026-07-29**  
Decision authority: project owner (`DEC-RELEASE`)

## Purpose

This OKF Bundle is an independent, metadata-only discovery layer for public
HM Land Registry material. It helps people find the publisher-operated route
for guidance, forms, services, datasets, APIs, developer resources and public
repositories, while keeping authority, freshness, access and reuse conditions
visible.

Version 0.2.0 was generated with AI assistance and is reviewed through
digest-bound automated, real-consumer and independent-agent evidence gates.
Publication is allowed only when `DEC-RELEASE` closes for the exact candidate
digest. Any approval is for a proof of concept, not for production or legal
reliance.

The bundle is not an HM Land Registry service and is not endorsed by HM Land
Registry. It does not provide legal advice, determine ownership or priority,
or establish an exact legal boundary. Users must follow and, where the matter
is consequential or time-sensitive, recheck the linked official source.

## Intended outcomes

- A member of the public can reach the correct official service without
  mistaking an ordinary download for an official copy.
- A conveyancer or other professional can find the current practice guide,
  form, fee or digital route and see when it was observed.
- A data user can compare source datasets without collapsing cadence,
  coverage, access or licence differences.
- A developer can distinguish discovery metadata from publisher-operated
  technical documentation and authorised service operation.
- An auditor can trace a displayed assertion to a source, observation,
  derivation, rights state and validation receipt.

The first-release user evidence is hypothesis-led. Personas and stories are
not presented as participant-validated research (`GAP-HUMAN-RESEARCH`).

## Product boundary

The public bundle may contain:

- source-native public metadata and stable official links;
- deterministic normalisations of that metadata;
- source-family, coverage, rights and provenance records;
- documentation, evaluation fixtures and validation receipts; and
- a progressively enhanced static discovery site for GitHub Pages.

It must not contain:

- title-register, title-plan, search-result or bulk property records;
- personal-level property, address, ownership or forum content;
- authenticated responses, credentials, certificates or signed URLs;
- copied production datasets or source-code trees;
- automated searches of restricted services; or
- model-generated legal, ownership, priority or boundary conclusions.

These boundaries implement `DEC-METADATA-ONLY` and the rights controls
`RIGHT-RESTRICTED` and `RIGHT-PERSONAL`.

## Source authority and truth

The bundle is a finding aid, not the authority. It uses this order when claims
conflict:

1. current legislation and formal notices for legal requirements;
2. the current publisher-operated HM Land Registry page, service terms,
   dataset page or developer pack for operation;
3. GOV.UK metadata for publication discovery;
4. official cross-government catalogues for discovery provenance; and
5. deterministic local normalisation.

An older catalogue record is preserved as provenance but must not override a
current publisher page (`DEC-AUTHORITY`). A catalogue modification date is not
a dataset release date, coverage date or validity date.

Primary evidence includes the [HM Land Registry organisation page][EV-HMLR-ORG],
[public data policy][EV-PUBLIC-DATA], [Use land and property data
catalogue][EV-ULPD], [Business Gateway developer documentation][EV-BG-DOCS]
and [Local Land Charges terms][EV-LLC-TERMS].

## Quality attributes

The product contract requires:

- **traceability:** every material proposition resolves to evidence;
- **honest coverage:** completeness is claimed only against a named, dated
  denominator;
- **determinism:** the same frozen inputs and toolchain produce byte-identical
  generated output;
- **fail-closed safety:** unknown rights, identifier collisions, unsafe URLs,
  malformed inputs and missing required evidence stop a candidate release;
- **accessibility:** the site targets WCAG 2.2 Level AA and works for core
  discovery without JavaScript, but makes no conformance claim before audit;
- **durability:** all public output is static and usable from GitHub Pages or
  as downloaded files; and
- **bounded performance:** search and rendering do not require loading bulk
  source data or making live service calls.

## Release acceptance

A release is acceptable only when all gates in
[`release-assurance.md`](release-assurance.md) pass against the same
digest-bound candidate. In particular:

- domain-profile JSON/YAML equivalence and checksums pass;
- source denominators and terminal acquisition outcomes reconcile;
- rights, secrets, personal-data and restricted-service checks have no hard
  failures;
- all required provenance, coverage and caveat fields are present;
- the independently reviewed evaluation meets its declared thresholds;
- accessibility checks and documented manual journeys pass;
- a clean rebuild is byte-identical; and
- the project owner closes `DEC-RELEASE` for a named version and canonical URL.

For v0.2.0 the owner decision can approve only the exact digest recorded in the
release evidence; until that record closes, the candidate is not approved.
“Complete”, “production-ready”, “accessible” and “official” still must not
describe the bundle: the bounded per-family coverage, PoC status and lack of
human accessibility or participant research remain explicit.

[EV-HMLR-ORG]: https://www.gov.uk/government/organisations/land-registry
[EV-PUBLIC-DATA]: https://www.gov.uk/government/publications/hm-land-registry-data/public-data
[EV-ULPD]: https://use-land-property-data.service.gov.uk/
[EV-BG-DOCS]: https://landregistry.github.io/bgtechdoc/
[EV-LLC-TERMS]: https://search-local-land-charges.service.gov.uk/terms-and-conditions

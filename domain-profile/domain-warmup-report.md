# HM Land Registry domain warm-up

Status: **approved profile for the v0.1.0 AI-generated PoC**
Research cut-off: **29 July 2026**

## Recommendation

Build a snapshot-bounded, metadata-only, large-corpus OKF bundle. The first
bounded population is every record returned by the GOV.UK Search API for the
`land-registry` organisation slug: 1,866 records at the cut-off. Add separately
governed lanes for HMLR datasets, public repositories, API catalogue records,
developer documentation and high-risk service descriptions. Do not treat those
associated lanes as complete until each has its own reconciled denominator.

The portable control plane should be OKF 0.2 Markdown. Generated public
representations should include an Explorer large-corpus descriptor, compact
record/search data, source/rights/coverage ledgers, a deliberately additive
JSON-LD projection and an accessible GitHub Pages site. Full YAML-LD/RDF/SHACL
conformance is not justified for the first scaffold.

## Why this shape

HM Land Registry material spans formal GOV.UK publications, datasets,
transactional services, linked data, Business Gateway contracts, help systems,
GitHub and an official blog. Those surfaces have different publishers,
identifiers, update cadences, audiences, authentication and rights. A single
flat “resource list” would hide the distinctions that matter most.

The observed GOV.UK population is already too large for an eager small bundle.
The full source universe also has independently changing lanes:

- 1,866 GOV.UK Search records at the research cut-off;
- 14 visible Use land and property data catalogue entries, pending adapter
  reconciliation;
- 15 HMLR-attributed rows in the frozen live CDDO API Catalogue snapshot,
  reconciled against an older 27-record sibling seed and useful for discovery but not
  operational authority;
- an exact frozen public API census of 289 repositories in the official GitHub
  organisation; and
- services, linked data, help and blog surfaces without a defensible global
  page denominator.

## Authority and interpretation

Current HMLR publisher pages and formal notices control operational metadata.
GOV.UK, CDDO and National Data Library catalogue records remain valuable
discovery provenance, but their dates must not be presented as dataset
currency. Legislation and current notices control legal requirements when they
conflict with older guidance, roadmaps or matrices.

The publication must make these distinctions visible:

- England and Wales are HMLR’s normal jurisdiction; the UK HPI is a
  collaborative UK-wide exception.
- A title plan or polygon product normally shows a general or indicative
  extent, not an exact legal boundary.
- A downloaded register or plan from the public information service is not an
  official copy for evidential purposes.
- “Free” does not mean unrestricted or OGL. CCOD, OCOD, leases, restrictive
  covenants and the National Polygon Service have distinct licence and access
  conditions.
- Bulk datasets are snapshots, not the live legal register.
- Price Paid Data has registration lag and exclusions; recent UK HPI estimates
  can be provisional and revised. Neither is a property valuation.
- Corporate proprietor data does not establish beneficial ownership.
- Local Land Charges coverage depends on migrated authorities and does not
  replace CON29 enquiries.
- Public documentation for Business Gateway or an API does not grant anonymous
  operational access.

## Rights, privacy and safety

Store public discovery metadata and source links only. Do not acquire title,
property, ownership, address, application, charge or search-result records.
Do not collect forum posts or user uploads. Do not authenticate to the portal,
Business Gateway, property-information service, Local Land Charges search or
dataset APIs.

The Local Land Charges service terms explicitly prohibit automated software
agents from searching, copying or monitoring the service. The bundle therefore
contains only its public description, terms and programme/coverage links.

Dataset rights are per record. OGL datasets still require the publisher’s
attribution and can include third-party address or mapping conditions. Bespoke,
direct-use, exploration and commercial licences must remain distinct. A
bundle-wide data licence would be misleading.

## People and tasks

The profile defines seven evidence-led role archetypes: public property user,
property professional, lender/portfolio user, data/GIS user, software
integrator, Local Land Charges officer and provenance/accessibility auditor.
They are research hypotheses derived from official audience and service
guidance, not invented demographics or participant-validated personas.

The critical tasks are to:

1. find the correct official public service and understand its product limits;
2. retrieve current practice, form, fee, notice and digital-route guidance;
3. compare datasets without collapsing scope, cadence, access or rights;
4. select spatial products without implying exact legal boundaries;
5. identify the correct machine contract and onboarding requirement;
6. determine Local Land Charges coverage and the CON29 boundary; and
7. audit every derived claim back to source evidence.

## Evaluation

The first release uses a deliberately smaller 24-question AI-agent-reviewed suite,
mapped end-to-end through stories and personas. It includes ordinary,
near-miss, stale, conflicting, restricted, unsafe and unanswerable cases. The
direct-source baseline is official navigation and search on the named
publisher surface. Retrieval scores cannot override a hard failure.

Release thresholds include zero hard failures, complete source and caveat
resolution, primary-target MRR of at least 0.80, Recall@10 of at least 0.90
after independent verification, and no new critical category in the second
held-out adversarial pass.

## Residual gaps

- No global page denominator exists across all associated HMLR domains.
- Business Gateway documentation needs a deterministic current service/schema
  inventory.
- The observed 14-item dataset catalogue needs adapter reconciliation.
- Linked-data schemas and query limits were not fully exercised.
- Welsh representation parity has not been enumerated.
- Personas and usability have not been participant validated.
- Owner approval is exact-digest scoped; every later version requires a new G9
  decision.

None of these prevents the clearly labelled v0.1.0 PoC. They do prevent claims
of full source coverage, production readiness, legal authority, bilingual
parity, accessibility conformance or human preference.

## Saturation result

A second source-family pass added detail but no new critical class beyond:
formal publishing, public services, professional services, data, developer
interfaces, source code, support, governance, rights/accessibility and official
communications. Research is saturated for the scaffold; unresolved populations
are recorded as gaps instead of being silently treated as complete.

## Handoff

The build may proceed against this approved profile because no open decision is
marked `blocking_for_build`. Publication is limited to the exact v0.1.0 digest
accepted by `DEC-RELEASE` and recorded in G9. The exact profile, evidence
register, traceability and checksums—not the research transcript—are the Stage
2 inputs.

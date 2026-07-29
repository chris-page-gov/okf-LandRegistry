# Personas And User Stories

Status: v0.1.0 evidence-led hypotheses, independently agent-reviewed at the
research cut-off of 29 July 2026.

These personas are evidence-led task hypotheses for the HM Land Registry OKF
bundle. They are behavioural roles, not demographic profiles, and they do not
claim that completed user research represents everyone who performs the task.
The machine-readable source is
[`../personas/personas-and-user-stories.json`](../personas/personas-and-user-stories.json).

## Evidence Boundary

A role appears here only where an official source exposes a corresponding
public task, professional workflow, dataset, API, access boundary or assurance
need. No age, gender, ethnicity, disability, income, family status, location or
technical skill is inferred.

Accessibility and Welsh-language needs are cross-cutting publication
requirements. They are not assumptions about a person's identity. The
accessible-service and Wales-focused personas make those needs testable rather
than leaving them as unowned overlays.

Expected answers in the evaluation suite were independently agent-reviewed
against their named official sources and frozen snapshot for v0.1.0. They are
bounded acceptance expectations, not verified legal answers or
participant-validated findings.

## Persona Hypotheses

| ID | Task-based role | Primary need | Main failure to prevent |
|---|---|---|---|
| `LR-P01` | Public property-information user | Find the right official property, boundary or Local Land Charges route and understand what it can establish. | Treating an online copy as proof, a title-plan line as exact, or HMLR as a UK-wide register. |
| `LR-P02` | Conveyancing practitioner | Resolve current practice guidance, official evidence and authorised professional services. | Using superseded or unofficial guidance, missing affected titles, or conflating boundary types. |
| `LR-P03` | Lending and valuation evidence user | Separate registered-title evidence, charges, historic transactions and derived indicators. | Treating a price-paid value as current valuation or a catalogue date as currency. |
| `LR-P04` | Data and GIS practitioner | Assess format, spatial meaning, coverage, vintage, access and licence before reuse. | Presenting indicative geometry as a legal boundary or assuming uniform access and licensing. |
| `LR-P05` | API and data-integration engineer | Find the publisher contract and authentication boundary without exposing credentials. | Treating catalogue metadata as an operational contract or automating a restricted service. |
| `LR-P06` | Local-authority land and planning user | Understand Local Land Charges migration, official-search evidence and spatial discovery data. | Claiming complete authority coverage or conflating charges, planning, titles and polygons. |
| `LR-P07` | Provenance and licensing reviewer | Verify canonical source, authority, derivation, licence, access and observation time. | Inheriting OGL incorrectly or presenting normalized/catalogue metadata as official. |
| `LR-P08` | Accessible-service user and evaluator | Complete search, filtering and evidence inspection using keyboard and assistive technology. | A visual-only task, obscured focus, unnamed controls or inaccessible source formats. |
| `LR-P09` | Wales-focused bilingual service user | Discover Welsh or bilingual services and retain source-native language metadata. | Suppressing Welsh availability, losing language tags or misrepresenting bilingual registers. |

## User Stories And Question Coverage

| Story | User outcome | Personas | Candidate questions |
|---|---|---|---|
| `LR-S01` — orient to scope and authority | Identify HMLR's England-and-Wales remit and the canonical official source. | `LR-P01`, `LR-P07` | `LR-Q001` |
| `LR-S02` — choose the right property evidence | Distinguish title summary, online copy, title plan and official copy. | `LR-P01`, `LR-P02`, `LR-P03` | `LR-Q002`–`003` |
| `LR-S03` — interpret boundaries safely | Keep general, determined and indicative geometry distinct. | `LR-P01`, `LR-P02`, `LR-P04`, `LR-P06` | `LR-Q004`, `005`, `012` |
| `LR-S04` — follow an authorised conveyancing route | Recover via index-map or professional guidance without invoking a restricted service. | `LR-P02`, `LR-P05` | `LR-Q006`–`007` |
| `LR-S05` — separate lending evidence and market data | Keep title evidence, price-paid data and temporal semantics distinct. | `LR-P02`, `LR-P03`, `LR-P07` | `LR-Q008`–`010` |
| `LR-S06` — assess a dataset for reuse | Filter by technical, spatial and language metadata and inspect the specification. | `LR-P04`, `LR-P06`, `LR-P09` | `LR-Q011`, `014` |
| `LR-S07` — understand Local Land Charges coverage | Keep authority migration, official search, title and spatial discovery concepts distinct. | `LR-P01`, `LR-P02`, `LR-P04`, `LR-P06`, `LR-P07`, `LR-P09` | `LR-Q013`, `019`, `020` |
| `LR-S08` — integrate within the access boundary | Distinguish public docs, authenticated data APIs and restricted professional services. | `LR-P02`, `LR-P05`, `LR-P07` | `LR-Q015`–`017` |
| `LR-S09` — refresh reproducibly | Model complete and change-only monthly releases without inventing currency. | `LR-P04`, `LR-P05`, `LR-P07` | `LR-Q018` |
| `LR-S10` — verify licence layers | Expose dataset-specific licence, attribution and third-party address rights. | `LR-P03`, `LR-P05`, `LR-P07` | `LR-Q021`–`022` |
| `LR-S11` — complete discovery accessibly | Use search, facets, results and evidence links with keyboard and assistive technology. | `LR-P05`, `LR-P08` | `LR-Q023` |
| `LR-S12` — discover Welsh services | Find Welsh availability, bilingual-register context and language-aware labels. | `LR-P01`, `LR-P08`, `LR-P09` | `LR-Q024` |

Every question appears in at least one story and every story has at least one
persona. The same IDs are repeated in
[`../evaluation/questions.json`](../evaluation/questions.json) and
[`../evaluation/journeys.json`](../evaluation/journeys.json), allowing static
validation to reject orphaned coverage.

## Primary Evidence Routes

The source-family IDs come from
[`../research/source-family-inventory.json`](../research/source-family-inventory.json).
Important direct evidence includes:

- [About HM Land Registry](https://www.gov.uk/government/organisations/land-registry/about);
- [search the register](https://www.gov.uk/get-information-about-property-and-land/search-the-register);
- [boundary practice guidance](https://www.gov.uk/government/publications/hm-land-registry-plans-boundaries-pg40s3);
- [Use land and property data](https://use-land-property-data.service.gov.uk/);
- [API access information](https://use-land-property-data.service.gov.uk/api-information);
- [Local Land Charges search](https://www.gov.uk/search-local-land-charges);
- [Use land and property data accessibility statement](https://use-land-property-data.service.gov.uk/accessibility-statement); and
- [HMLR Welsh Language Scheme](https://www.gov.uk/government/organisations/land-registry/about/welsh-language-scheme).

Discovery catalogues can help find these sources, but publisher-operated HMLR
pages remain the authority for current access, licence, technical and release
claims.

## Review Rules

- Promote no persona or expected proposition without independent evidence.
- Preserve unresolved tasks as explicit gaps rather than adding invented
  behaviour.
- Treat hard failures as release blockers; an average score cannot compensate
  for a wrong legal boundary, licence, authority or access statement.
- Re-run traceability checks whenever a persona, story or question ID changes.
- Update the machine and human files in the same change.

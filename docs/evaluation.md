# Evaluation And Quality

Status: 24-question first-release candidate suite, research cut-off
29 July 2026.

The evaluation scaffold tests whether a static HM Land Registry OKF
publication helps people find official evidence while preserving legal,
licensing, access, temporal, provenance, accessibility and language
boundaries. It does not test the correctness of the underlying land register,
provide legal advice or authorize access to a transactional service.

Machine-readable assets:

- [`../evaluation/questions.json`](../evaluation/questions.json) — 24 candidate
  competency and retrieval questions;
- [`../evaluation/journeys.json`](../evaluation/journeys.json) — 12 static
  search/filter/detail journeys using
  `okf-explorer-interaction-suite.v1`; and
- [`../personas/personas-and-user-stories.json`](../personas/personas-and-user-stories.json)
  — explicit question → story → persona traceability.

Expected propositions are candidates, not verified gold answers. Each must be
checked against its named source at the release cut-off and independently
reviewed before promotion.

## First-Release Coverage

| Area | Questions | Stories | Primary personas |
|---|---|---|---|
| Public scope and property evidence | `LR-Q001`–`004` | `LR-S01`–`003` | `LR-P01`, `LR-P02`, `LR-P03` |
| Conveyancing guidance and authorised routes | `LR-Q005`–`007` | `LR-S03`, `LR-S04` | `LR-P02`, `LR-P05` |
| Lending, price and temporal evidence | `LR-Q008`–`010` | `LR-S05` | `LR-P03`, `LR-P07` |
| Data and GIS reuse | `LR-Q011`–`014` | `LR-S03`, `LR-S06`, `LR-S07` | `LR-P04`, `LR-P06`, `LR-P09` |
| APIs, authentication and refresh | `LR-Q015`–`018` | `LR-S08`, `LR-S09` | `LR-P05`, `LR-P07` |
| Local Land Charges and authority coverage | `LR-Q019`–`020` | `LR-S07` | `LR-P01`, `LR-P06`, `LR-P09` |
| Licence and third-party rights | `LR-Q021`–`022` | `LR-S10` | `LR-P03`, `LR-P05`, `LR-P07` |
| Accessibility and Welsh-language overlays | `LR-Q023`–`024` | `LR-S11`, `LR-S12` | `LR-P08`, `LR-P09` |

This is a documented smaller first-release acceptance suite. It must not be
described as the Explorer reference harness's mature 100-question suite. Before
using a runner that hard-codes exactly 100 questions, either expand this suite
with independently evidenced cases or version the runner to accept the
documented bounded suite. Do not pad it with invented questions.

## Direct-Source Baseline

For every question:

1. open each `expected_sources` canonical URL;
2. record the official page, dataset or contract identity and observation time;
3. verify the candidate expected propositions and near-miss rule;
4. compare the direct-source result with the static bundle result; and
5. record disagreement, inaccessible evidence or source drift rather than
   silently changing the expected answer.

The baseline never creates an account, pays for a document, submits a search or
transaction, invokes an authenticated API, signs in to the portal or Business
Gateway, or stores credentials and signed links.

## Hard-Failure Gates

Any applicable hard failure makes the release result fail regardless of its
average score.

| ID | Failure |
|---|---|
| `HF-LEGAL-BOUNDARY` | General or indicative geometry is presented as an exact legal boundary or definitive title extent. |
| `HF-LICENCE-ACCESS` | Open/free reuse is claimed despite a fee, account, bespoke licence, third-party right or other restriction. |
| `HF-DATE-CURRENCY` | A catalogue, observation or bundle date is presented as dataset release, registration or legal currency. |
| `HF-AUTHORITY` | Derived, normalized, catalogue or unofficial material is presented as an official HMLR assertion. |
| `HF-RESTRICTED-AUTOMATION` | The publication invokes or appears to authorize automated restricted-service access or exposes a credential. |
| `HF-COVERAGE` | Completeness is claimed without a denominator, as-of date and exclusions. |
| `HF-ACCESSIBILITY` | A critical task is not keyboard operable or lacks accessible names, focus, status or a non-visual equivalent. |
| `HF-WELSH-LANGUAGE` | Welsh availability or content is erased, merged without language metadata or falsely described. |

## Metrics And Rubric

The additive rubric totals 100 points:

- **Retrieval — 35:** expected-source recall at 5, first relevant rank, facet
  completion, zero-result recovery and durable query/filter state.
- **Display — 25:** expected proposition, legal/spatial limitation,
  licence/access and distinct temporal labels are visible in plain language.
- **Accessibility — 20:** keyboard completion, accessible names, visible and
  unobscured focus, announced status, 400% zoom and Welsh language metadata.
- **Provenance and safety — 20:** canonical-source resolution, material-claim
  citation coverage, correct authority/derivation labels, temporal
  traceability, no restricted invocation and zero hard failures.

Report metrics overall and by persona/story stratum. A strong average must not
hide failure for conveyancing, local-authority, accessibility or Welsh-language
tasks.

## Static Interaction Journeys

The journeys require only static bundle data, search, facets, title sorting,
record detail and public source links.

| Journey | Task |
|---|---|
| `LR-J01` | Orient to England-and-Wales scope and restore search/filter state with Back and Forward. |
| `LR-J02` | Distinguish title information and official-copy evidence. |
| `LR-J03` | Inspect the general-boundaries limitation. |
| `LR-J04` | Recover from an incomplete address search via index-map guidance. |
| `LR-J05` | Separate registered-charge evidence from historic price data. |
| `LR-J06` | Find GML/INSPIRE data while retaining its indicative status. |
| `LR-J07` | Inspect Local Land Charges authority-migration coverage. |
| `LR-J08` | Inspect API documentation without crossing authentication boundaries. |
| `LR-J09` | Find complete and change-only monthly distributions. |
| `LR-J10` | Inspect OGL, attribution and third-party rights. |
| `LR-J11` | Find accessibility evidence through keyboard-operable controls. |
| `LR-J12` | Discover Welsh and bilingual-service evidence. |

No journey signs in, searches a restricted service, submits a transaction or
makes an authenticated API request.

## Validation

Validate JSON syntax:

```sh
python3 -m json.tool personas/personas-and-user-stories.json > /dev/null
python3 -m json.tool evaluation/questions.json > /dev/null
python3 -m json.tool evaluation/journeys.json > /dev/null
```

When this repository is checked out beside `okf-explorer`, validate the
journey schema, action vocabulary and complete question/story/persona
traceability without launching a browser:

```sh
node ../okf-explorer/scripts/evaluate_okf_explorer.mjs \
  --no-browser \
  --journeys-only \
  --journeys evaluation/journeys.json
```

Browser execution becomes a release gate only after the static site publishes
the facet keys and values declared in `evaluation/journeys.json`. Record
screenshots or traces only with reproducible viewport, bundle digest, route and
capture context; conversational screenshots are not a durable baseline.

## Promotion Criteria

The candidate suite can be marked reviewed only when:

- all 24 questions resolve to a story and persona;
- every expected proposition has direct-source and independent review;
- every canonical URL and source-family ID resolves;
- critical journeys pass locally and against the deployed Pages build;
- accessibility combines automated checks with keyboard and manual inspection;
- no hard failure occurs; and
- results name the exact bundle snapshot and research cut-off.

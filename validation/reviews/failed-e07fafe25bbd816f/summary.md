# Claude independent review summary — OKF Land Registry v0.2.0

## Decisions

| Task | Decision |
|---|---|
| **Task A — renewed Stage 1 review** | **`fail`** |
| **Task B — Land Registry G5 independent evaluation** | **`fail`** |

Both tasks were completed in full. Neither is `not_run`: every Stage 1 scope item was
reviewed, all 24 questions were independently verified across all six required
dimensions, and eight reviewer-owned held-out cases were actually executed against the
frozen candidate. `held-out-execution-request.json` is therefore not required and has
not been produced.

Neither decision is an owner decision. No Land Registry G9 approval has been created,
no deployment variable has been set, no publication has been authorised, and no public
or Pages URL has been visited or verified.

---

## Blocking findings

Ordered by severity. Three of the five require a rebuild; the fourth changes the
governed evaluation-suite digest.

### 1. Fifteen restricted Business Gateway endpoints are published as public with unknown licence — *hard failure observed*

All 15 records whose `url` and `canonical_source_url` are live
`businessgateway.landregistry.gov.uk` B2B endpoints are published with:

- `access_state: "public"`
- `rights_state: "metadata-only"`, `rights_ref: "RIGHT-CDDO"`
- `licence_state: "unknown"`, `licence: null`, `authentication: null`, `access_model: null`
- **no** restricted-automation caveat on any of the 15
- production Poll Request Service URLs and `bgtest.landregistry.gov.uk` test-endpoint
  substitutions in 15 of 15 descriptions, including *"automate the collection of
  correspondence and responses"*

The same bundle handles its other two restricted hosts **correctly**:
`search-local-land-charges.service.gov.uk` is `public-search-with-terms-and-fees` /
`restricted-service` / `RIGHT-RESTRICTED` with explicit anti-automation caveats, and
`propertyalert.landregistry.gov.uk` is `authenticated-and-paid` / `restricted-service`.
`LR-Q015` labels the authenticated ULPD API correctly too. The vocabulary exists and was
simply not applied to this one family — this is a curation failure, not a modelling gap.

Under the project's own definitions this is an observed hard failure on two counts:
`HF-RESTRICTED-AUTOMATION` and `HF-LICENCE-ACCESS`. `release-assurance.md` separately
lists *"a restricted service called or described as an anonymous public API"* as
publication-blocking.

**The 24-question suite cannot detect this.** The string `businessgateway` appears zero
times in `evaluation/questions.json`, so all 15 records lie outside every declared
expected and forbidden target. It surfaced only in the held-out pass — twice, on two
different queries and two different record pairs:

- **HO-004** `"who owns this house find current owner name proprietor address"` →
  **rank 1** was *Online Owner Verification*, advertising real-time ownership
  verification, historical name matching from 2005 and other-legal-owner flagging.
- **HO-008** `"official copy proof of ownership instant download certificate no fee"` →
  **ranks 3 and 4** were the two *Official Copy Document Availability* endpoints,
  against a query explicitly demanding no fee.

No endpoint was contacted. The finding rests entirely on published record metadata.

### 2. The candidate bundle claims a publication approval that has not been given

`bundle/index.html` line 72: **"Approved for publication as an AI-generated proof of
concept."**

Contradicted by every governing control:

- `decision-register.md` `DEC-RELEASE`: *"Publication remains blocked until G1–G8 pass
  and the owner approves the exact digest recorded in G9."*
- `product-contract.md`: *"until that record closes, the candidate is not approved."*
- `REVIEW-MANIFEST.json`: `release_authorized: false`, `decision_status: "not_requested"`
- `RISK-014` — *"CI deployment is mistaken for owner-approved release"* — is carried as
  `controlled-candidate-v0.2.0`. This claim is that exact risk realised inside the
  artifact the control was meant to protect.

The string exists only in the generated bundle, so it is inside the release root.

### 3. The near-miss control cannot fail for any question

All 24 questions declare exactly one `must_not_retrieve` target and all 24 are the
identical URL `https://www.ros.gov.uk/`. That URL appears in **zero of 2,203 records**;
the catalogue spans 13 hosts and none is `ros.gov.uk`.

`evaluate.py` enforces `must_not_retrieve_pass_rate == 1.0` as a hard gate, and
`validate_question_contract` checks only that the list is non-empty and well-formed. The
rate is therefore mathematically forced to 1.00 regardless of retrieval behaviour,
tokenisation, ranking weights or bundle content — and it could not have detected the two
hard failures above.

This is why `near_miss_rule_verified` is `false` on all 24 rows. The *narrative*
`near_miss_rule` text was assessed individually and is sound in every case; the
*executable* control that the gate measures discriminates nothing.

### 4. The rights review governing and published by this candidate is stamped to v0.1.0

`governance/rights-review.json` declares `review_state: "approved-for-v0.1.0-ai-poc"`,
`release_approved: true`, `decision_scope: "Metadata-only v0.1.0 AI-generated proof of
concept"`, and an open item requiring receipts to *"bind the exact v0.1.0 digest"*. This
is projected into the public bundle at `bundle/data/rights.json`.

`sources-rights-and-ethics.md` requires the review to be repeated before every public
release even when source bytes are unchanged, and G3 requires an exact-candidate receipt.
The v0.2.0 candidate has no exact-candidate rights approval, and publishes a prior
version's `release_approved: true` as its own rights state.

Content-level scans found nothing prohibited, so this is an evidence-binding and
misleading-flag defect, not leaked data.

### 5. Two of the four G5 pass criteria have no executed evidentiary basis

`release-assurance.md` requires source and caveat coverage of 1.00.

- **No catalogue record references any `CAV-*` ID** — zero occurrences of all eight
  across the entire catalogue — so caveat coverage cannot be checked mechanically.
- `evaluate.py`'s `validate_acceptance_review` measures neither. It returns hard-coded
  `source_resolution_coverage: 1.0`, `caveat_coverage: 1.0` and `hard_failure_count: 0`
  taken from the reviewer's own assertions, then the report presents them as metrics.
- **0 of 24** Explorer calibration journeys assert that `runtime_expected_source_url`
  appears; **0 of 24** assert any required caveat — even though both fields are declared
  on every journey. The assertions are title-in-body, `q` URL param, search-manifest
  requested, console clean.

I verified caveat coverage semantically for all 24 questions and found it substantively
strong. The finding is that the *control* is unmeasured, not that the caveats are absent.

---

## Warnings

- **Evaluator defaults sit far below governed thresholds.** `k=5`,
  `min_expected_source_success_at_k=0.50`, `min_mrr=0.0`, all-targets `0.0`, against
  required MRR ≥ 0.80 and Recall@10 ≥ 0.90. A default run passes at half the governed
  bar. This candidate clears the real thresholds comfortably, so it is a control
  weakness, not a scoring dispute.
- **Language vocabulary is split.** Two records use BCP-47 (`['cy']`, `['en']`), three
  use display names (`['English','Welsh']`), and a sixth Welsh Language Scheme route is
  `language_state: "unknown"` with an empty array. The consumer lock declares `language`
  as a required filter key, so a Welsh filter returns different sets depending on
  encoding. Sits inside the accepted `RISK-012` / `GAP-WELSH` gap, so recorded as a
  warning — but it weakens the very control `LR-Q014` and `LR-Q024` depend on.
- **Seven governed documents still carry v0.1.0 status lines**, including
  `scope-and-coverage.md`, whose "Release language" clause grants permitted-claim wording
  to *"Version 0.1.0"* only — leaving the v0.2.0 permitted-claim rule textually
  ungoverned by the document that defines it.
- **The scored substrate is not the shipped index.** `bundle/data/search/index.json` does
  not exist, so both the calibration baseline and my harness fell back to
  `data/catalogue.json`, while the Explorer consumes `data/explorer/search/` shards.
  Self-reported as `catalogue-fallback`, so disclosed — but the deterministic numbers do
  not characterise the ranking users will experience.
- **`bundle/data/rights.json` omits `field_semantics`**, the key that explains why
  `publication_allowed: false` can legitimately sit beside a published record. A reader
  of the public projection alone would reasonably conclude the bundle breached its own
  control. The design is sound; only the disambiguating field is missing.
- **Landing-page counters are JavaScript-filled** (`—` without JS) against the stated
  no-JS core-discovery constraint. Cosmetic: the no-JS catalogue index does state 2,203.
- **Ranking noise on narrow queries.** HO-006 returned three unrelated Business Gateway
  SOAP endpoints in the top 6 for a Welsh-language query, and the `.cy` glossary — the
  most relevant Welsh artifact — did not appear at all.
- **`LR-Q009`'s `CAV-SOURCE-AUTHORITY` is the thinnest caveat match** in the suite,
  satisfied indirectly rather than by an explicit record caveat.
- **No declared consumer compatibility window.** The lock pins Explorer 0.5.7 exactly
  with full digest coverage but states no supported range — a point, not an interval.

---

## What is genuinely strong

Stated plainly, because a `fail` should not obscure it:

- **Denominators reconcile exactly.** 2,227 input representations − 24 canonical-URL
  merges = 2,203 retained, errors 0, excluded 0. By family: govuk-search 1,842 + 24
  merged = **1,866 = DEN-GOVUK** exactly; github 289 + 1 org = 290; ulpd 14 + 1 = 15;
  cddo 15 + 1 = 16. `complete_for_govuk_hmlr_filter_at_snapshot: true` is evidenced and
  correctly bounded, with `complete_hmlr_public_estate: false` stated.
- **Content-level privacy and secret hygiene are clean.** Independent scan of all 2,203
  records: zero credentials, tokens, private keys, signed-URL parameters, emails,
  postcodes, title-number patterns, plaintext `http://` URLs or traversal sequences; zero
  property-level records; zero ID or canonical-URL collisions; every record carries
  access, rights, rights_ref, evidence_refs, observed_at and at least one caveat.
- **Retrieval genuinely exceeds the governed thresholds** (recomputed independently, not
  copied from the calibration): **MRR 0.9167**, first-target success@5 and @10 = **1.00**,
  all-expected-targets@10 = **1.00**, micro recall **39/39**, zero expected targets
  missing from the catalogue.
- **Machine-checkable closure holds in both directions.** Type-to-kind crosswalk is total
  (82 mappings, 0 unmapped record types, 0 kind disagreements); all 20 requirements and
  17 risks are referenced by the dependency graph and none is dangling; all 24 questions
  resolve to declared personas and stories with no persona or story unreferenced.
- **The dependency graph is genuinely fail-closed** — `unknown_change_policy:
  all-gates-and-manual-review`, direct edits to `bundle/` never an accepted correction
  path, evidence never carried across a changed root — and the exact-candidate
  change-impact run agrees with this review existing: `stage1_review_required: true`,
  `release_approval_required: true`, 0 unexplained generated paths.
- **Limitations disclosure is candid and specific**, with named gap IDs, and the AI usage
  ledger declines to estimate unavailable cost fields rather than inventing them.

The safety *design* here is better than most. The failures are places where the design
was not applied uniformly (finding 1), where a control was wired to something inert
(finding 3), or where an assertion stood in for a measurement (finding 5).

---

## Held-out adversarial pass — 8 executed, 6 pass, 2 fail

Run from the packet root: `python3 reviewer-search.py --query "<QUERY>" --k 6`.
All eight queries were composed for this review. None is copied from the suite, the
calibration journeys, or any earlier review — the earlier Opus review and the v0.1.0
acceptance decision were absent by design and were neither consulted nor reconstructed.

| ID | Risk class | Result |
|---|---|---|
| HO-001 | Legal / exact-boundary misuse | **pass** — every result contradicted the exact-boundary premise |
| HO-002 | Rights, licence, access inference | **pass** — *"Free access is not the same as open redistribution"*; £20k product surfaced against a "free" query |
| HO-003 | Restricted-service automation | **pass** — LLC terms at ranks 1–2 with the explicit prohibition |
| HO-004 | Personal data + restricted automation | **fail** — restricted BG ownership-verification endpoint at rank 1 |
| HO-005 | Coverage / currency overstatement | **pass** — exclusions explicit, no completeness claim |
| HO-006 | Welsh-language distinction | **pass** (precision not accepted) |
| HO-007 | Accessibility conformance overstatement | **pass** — refused to convert partial compliance into certification |
| HO-008 | Source authority / fee inference | **fail** — fee-bearing official-copy endpoints as public, unknown licence |

One new critical category remains open (findings 1 / EF-002).

No account was created, no payment made, no authenticated API called, no restricted
service searched or automated, and no credential, certificate, session or signed link
handled or stored.

---

## Exact identities reviewed

All verified by independent rehash before review began; **all match**, so the review
proceeded rather than stopping.

| Item | Value | Status |
|---|---|---|
| Version | `0.2.0` | ✅ |
| Governed candidate commit | `9955823144e05cad295dc51ac0558d2f9174a464` | ✅ declared |
| Bundle release root | `e07fafe25bbd816f790f6a604aa7e99ab529dda51ec7d106e94d2e57485a217d` | ✅ declared by `bundle/CHECKSUMS.sha256` |
| Stage 1 profile-pack root | `47f0a5c1a89c78cdeda8e57623db46036753de752766588874fa5835a36a0d95` | ✅ declared by `domain-profile/CHECKSUMS.sha256` |
| Evaluation-suite SHA-256 | `c6d00626c50d3a50b29e0576ca529e3b0110eb270606feebeb3cde232b7dad9b` | ✅ rehashed |
| Bundle member files | 147 of 147 | ✅ rehash OK |
| Profile member files | 6 of 6 | ✅ rehash OK |
| `bundle/CHECKSUMS.sha256` | `3d3f8a80b18104125ad5b38dcebc8d43e7c528a2085e00e3670fed213ce304e7` | ✅ |
| `domain-profile/CHECKSUMS.sha256` | `4dd7c27b2fa4943b4197b9564cf10dd03411d5f980f256936e60f3739fd0d261` | ✅ |
| Consumer lock | `b03bdbb401e1621efc97d156bfc75cc30f3f75b03e5226cce14adaa5e9fc8bea` (Explorer 0.5.7 @ `afd940b6…`) | ✅ |
| Dependency graph / requirements / risks | `be83ae07…` / `5ec8d101…` / `106b43dd…` | ✅ |
| Record count | 2,203 | ✅ reconciles |
| Prospective owner | `Chris Page-PoC` — **not** the reviewer | — |

One structural caveat: both roots are read from a trailing comment marker in their
CHECKSUMS file. The packet does not publish the root-derivation algorithm, so I verified
every member digest and both manifest digests but could not independently recompute the
roots from the member list.

---

## Reviewer disclosure

- **Identity:** Claude Opus 5 (model ID `claude-opus-5`), Anthropic, operating as an
  autonomous review agent in the Claude Code CLI harness.
- **Kind:** `AI-agent`
- **Roles:** independent Stage 1 reviewer; independent evaluation reviewer.
- **Reviewed at:** `2026-07-30T03:16:03Z` (UTC)
- **Independence:** I did not implement any part of this candidate — not the acquisition
  adapters, normalisation or build pipeline, domain profile, source register, curated
  records, schemas, search contract, ranking function, Explorer consumer or lock,
  evaluation suite, or any validator. I am independent of the retrieval implementation
  under test.
- **Scope separation:** My inputs were the frozen review packet, checksum manifests I
  rehashed myself, and read-only execution of the packet's own `reviewer-search.py`
  against the frozen bundle. The calibration report was treated as *implementation
  evidence*; its judgements were not copied, and every rank and metric quoted here was
  recomputed independently. No earlier Opus review, no v0.1.0 acceptance decision and no
  owner intention was used. I hold no owner, release or deployment authority.

**Limitations.** This is an AI-agent review. It is **not** independent human HM Land
Registry assurance, human land-registration domain expertise, legal advice, a licence or
IP audit, a privacy or data-protection assessment, a security assessment or penetration
test, an accessibility audit or conformance assertion, or participant/user research.
`RISK-015` remains a residual risk that this review does not discharge. No live official
source was fetched, so source drift since the 2026-07-29 cutoff is unmeasured. No clean
rebuild was attempted, so G7 byte-identity is unverified here. Accessibility was assessed
from declared statements, metadata and static markup only — no assistive-technology,
keyboard, zoom or contrast testing. Welsh translation quality was not evaluated and no
bilingual-parity conclusion is offered.

---

## What this review does not say

Missing evidence has not been converted into a recommendation anywhere in these outputs.

- A `fail` on Task A or Task B is **not** a statement that the candidate is unsafe to
  develop further, nor that the four blocking findings are unfixable. Three require a
  rebuild; one changes the suite digest.
- Nothing here approves publication, and nothing here should be read as an owner
  decision. Under the project's own rule that direct edits to `bundle/` are never an
  accepted correction path, a **new candidate digest** must be produced and both reviews
  rerun against it.
- No public URL was verified. The planned Pages route remains undeployed, and no bundle
  URL should be shared from this packet.

## Files produced

1. `stage1-review-v0.2.0.json` — Task A, `status`/`outcome` = `fail`
2. `evaluation-acceptance-review-v0.2.0.json` — Task B, `status` = `fail`, exactly 24
   `question_reviews` with caveat and hard-failure arrays bound programmatically to the
   frozen suite, plus 8 held-out adversarial rows
3. `claude-independent-review-summary.md` — this file

`held-out-execution-request.json` is **not** produced: the held-out queries were executed.

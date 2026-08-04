# Claude independent review summary — OKF Land Registry v0.2.0 (remediated candidate `50506bff…`)

## Decisions

| Task | Decision |
|---|---|
| **Task A — renewed Stage 1 review** | **`fail`** |
| **Task B — Land Registry G5 independent evaluation** | **`fail`** |

Both decisions rest on **one** narrow, mechanically fixable evidence-binding defect.
Neither is a recurrence of any earlier failure, and neither is a safety finding.

All four prior blocking findings and all nine prior warnings are **closed**, verified
against primary evidence rather than accepted from the remediation matrix. Eight fresh
adversarial cases were executed and all eight pass. No hard failure was observed
anywhere. No new critical category is open.

`held-out-execution-request-remediated.json` is **not** produced: the held-out queries
were executed.

---

## Blocking finding

### The exact-bundle runtime receipt required by formal G5 does not bind the shipped bundle

`scripts/evaluate.py bundle_tree_identity()` manifests every file under `bundle/`,
**including `CHECKSUMS.sha256`**. Recomputed against this packet:

| | value |
|---|---|
| Declared by both runtime receipts and `REVIEW-MANIFEST.json` | `b10d6d996561ff7ea08fe495c2b4e5f66273d3325b1b68378d3ca5f470e6ec79` |
| Recomputed by this reviewer | `5fc13c6300fbf057261efbf57a5e76a52de4a5ca3f589bc873be2cc80db967b4` |
| File count | **148 in both** |
| Total bytes | **31,525,576 in both** |

Running the formal gate exits 1 and writes no report:

```text
evaluation acceptance review failed closed: runtime receipt does not bind the exact bundle tree
```

**Diagnosis.** All 147 content files are provably identical to what the builder
recorded — I recovered the root-derivation algorithm and reproduced the declared release
root exactly (see below). With file count and total bytes also identical, the only file
that can differ is `CHECKSUMS.sha256`, at its same length of 14,661 bytes. That is
consistent with the 64-character release-root marker being rewritten *after* the browser
run, since swapping one hex value for another preserves length exactly. I tested the
prior root `e07fafe2…`, the `dafdc945` label and other candidates; none reproduces
`b10d6d99…`, so the difference is **bounded to that one file, not identified**.

**Root cause — a circularity worth fixing permanently.** The tree includes
`CHECKSUMS.sha256`, while `CHECKSUMS.sha256` itself embeds the release root derived from
the other 147 files. Any regeneration of that marker after a runtime run invalidates the
binding even though no content changed, so the gate is inherently fragile to the ordinary
build-then-stamp sequence.

**Impact — evidentiary, not substantive.** Neither receipt records a single request for
`CHECKSUMS.sha256`, the string appears zero times in either receipt, and neither
`okf-explorer.json` nor the Explorer data-plane manifest references it. The 26 search and
6 product journeys therefore observed behaviour faithful to the shipped content. But
remediation row 12 introduced "formal G5 now requires a passed exact-bundle runtime
receipt" *precisely* to close the prior finding that source and caveat coverage were
asserted rather than measured — and that control cannot be exercised against these bytes.
`release-assurance.md` states that a receipt from a different digest is not evidence for
the candidate, and that "pending" and "not run" are not passes. Certifying G5 on evidence
the project's own validator rejects would repeat the error the prior review identified.

**Fix (no content file changes).** Re-run the locked-Explorer search and product journeys
against the final stamped bundle and replace both receipts and the declared
`bundle_tree_sha256`; or exclude `CHECKSUMS.sha256` from `bundle_tree_identity()`, which
also removes the circularity, then regenerate the receipts.

---

## Prior findings — all closed

Each row was re-derived from primary evidence. The matrix was treated as a navigation
aid, not proof.

| Prior finding | Verdict | How I verified it |
|---|---|---|
| 15 Business Gateway endpoints presented as public/anonymous | **closed** | All 15 now `approved-professional-users` / `restricted-service` / `RIGHT-RESTRICTED`, with `authentication` and `access_model` populated, all carrying `CAV-NO-RESTRICTED-AUTOMATION` and "Restricted Business Gateway service: do not authenticate, call, search, monitor or automate it…". Catalogue-wide counts of `bgtest.landregistry.gov.uk`, `Poll Request Service` and `automate the collection` are all **zero**. Re-tested adversarially by RV-003. |
| Landing page claimed approval before G9 | **closed** | Now reads "These bytes do not assert publication approval." Tree-wide sweep finds "Approved for publication" only in `tests/test_pages.py` (a regression guard) and the preserved historical review. |
| All 24 negatives targeted the absent `ros.gov.uk` | **closed** | 24 present, question-specific, rank-bounded targets; 22 distinct; none overlaps an expected target; **21 of 24 reachable** by the ranking function; none violated. Qualified by warning EW-001 below. |
| v0.1.0 rights approval projected into v0.2.0 | **closed** | `review_state: policy-reviewed-for-v0.2.0-candidate`, `release_approved: false`, plus a `release_authority` statement. No `0.1.0` string anywhere in the generated bundle. |
| Source/caveat metrics asserted, journeys didn't select records or assert caveats | **closed** | 26 journeys now click the expected record, assert its canonical URL as an `href`, and assert each required caveat ID. Verified programmatically: **24/24** questions have every required caveat asserted and their expected URL asserted; all 26 journeys passed. Qualified by EW-002. |
| Evaluator defaults below governed thresholds | **closed** | Now `k=10`, source success `1.0`, target recall `0.90`, MRR `0.80`, all-target `1.0`. |
| Mixed language vocabulary | **closed** | Catalogue language values are now exclusively `cy` and `en`; unknown stays explicit as "Not stated by the source." |
| v0.1.0 documentation wording | **closed** | All twelve `docs/` files carry v0.2.0 wording. |
| Public rights projection omitted field semantics | **closed** | `field_semantics` and `release_authority` are both in `bundle/data/rights.json`. |
| No-JavaScript counters showed dashes | **closed** | Initial HTML contains `2,203` and `15` literally. |
| Compatibility window not stated | **closed** | `compatibility_window`: `exact-version-only`, min = max = `0.5.7`, with rationale. |

**On the Q20 split, which the matrix asked me to judge:** it is semantically correct, not
merely test-satisfying. LR-Q020's own second proposition is that the search service,
charge record, title record and indicative spatial dataset are *distinct entities*, and
both URLs are declared expected sources. `CAV-BOUNDARY-NOT-CONCLUSION` properly belongs to
the spatial dataset — requiring the service record to display it would have been the exact
conflation the question exists to prevent. The preserved failing receipt
`…-dafdc945.json` is authentic: 24 of 25 passed, with the exact error
`.right-panel .context-note does not include CAV-BOUNDARY-NOT-CONCLUSION`.

---

## Warnings

- **EW-001 / W-001 — negative-control bounds appear fitted to observed output.** For
  **20 of 24** questions, `max_rank` equals exactly the negative's observed rank minus one.
  The rule implied by several `reason` strings (the negative must not outrank the expected
  source) explains only **3 of 24**, and is contradicted by LR-Q011 (bound 2, expected
  targets at 1 and 10), LR-Q003 (bound 4, expected at 1 and 9) and LR-Q017 (bound 2,
  expected at 1, 2 and 4). The controls therefore pass by construction on this bundle,
  though they are live against regression. Three negatives (LR-Q008, LR-Q016, LR-Q021)
  never rank so cannot fire; LR-Q015's ranks 24 against a bound of 10. The *choice* of
  target is a genuine near miss in every case — the weakness is the derivation of the bound.
- **EW-002 / W-002 — two thirds of caveat assertions use universal IDs.**
  `CAV-BOUNDED-COVERAGE`, `CAV-DATE-SEPARATION`, `CAV-RIGHTS-AND-ACCESS` and
  `CAV-SOURCE-AUTHORITY` are on **all 2,203 records**. 32 of 48 required assertions name a
  universal ID, and **10 of 24** questions have entirely universal requirements. Mitigated
  because each journey independently asserts the expected canonical URL, so record
  selection doesn't depend on caveat discrimination.
- **EW-003 / W-003 — a caveat can satisfy its assertion as a bare identifier.** 2,196 of
  2,203 records declare more caveat IDs than prose caveats; the `context_note` appends a
  "Caveat controls: …" trailer, so a governed caveat can appear to a user as an opaque token.
- **W-004 / EW-005 — Welsh material remains hard to retrieve.** RV-006 (Welsh forms and
  correspondence) returned one record, and the bundle's only Welsh-language artifact
  (`Geirfa o dermau cyfreithiol…`, `languages: ['cy']`) did not surface. Handling is
  correct; discovery for persona LR-P09 is weak.
- **EW-004 / W-005 — the lexical diagnostic still scores a different substrate.**
  `data/search/index.json` is absent so scoring falls back to `data/catalogue.json`. Now
  correctly labelled non-formal with the Explorer receipt as formal evidence.
- **W-006 — one restricted record points at a non-restricted assessment.** The ULPD API v1
  record is `restricted-service` but `rights_ref: RIGHT-DATASETS`. No safety consequence.
- **W-007 / EW-005 — recall and ordering observations.** RV-002 returned one record for a
  third-party-rights query; RV-008 ranked adverse-possession guidance above the directly
  responsive corrective. **Correction:** I initially logged these as *precision* failures.
  That was wrong — every returned record was relevant and correctly caveated. The
  deficiency is recall and ordering, and the outputs record it as such.

---

## Held-out adversarial pass — 8 executed, 8 pass

All eight composed for this review; none copied or paraphrased from the earlier set.

| ID | Risk class | Result |
|---|---|---|
| RV-001 | Determined-boundary dispute resolution | **pass** — PG40 rank 1; a *new* topical caveat now appears on boundary records |
| RV-002 | UPRN/AddressBase commercial redistribution | **pass** — £20k price, indicative-not-legal, UPRN linkage qualifier |
| RV-003 | Business Gateway credentials + production endpoint | **pass** — direct re-test of the prior hard failure; remediation held |
| RV-004 | Bankruptcy / insolvency name search | **pass** — no personal data; restricted endpoint correctly labelled |
| RV-005 | Statistical revision and finality | **pass** — "provisional and revised", release vs reference month |
| RV-006 | Welsh forms and correspondence | **pass** (recall weak — W-004) |
| RV-007 | Alternative accessible formats | **pass** — candid about its own PDF limits, no conformance claim |
| RV-008 | Absence-of-evidence about unregistered land | **pass** (ordering weak — W-007) |

---

## A verification result worth recording

I recovered the **root-derivation algorithm**, which the prior review could only take on
trust from a comment marker:

> `sha256` over `"\n".join(f"{sha256(file_bytes)}  {relative_posix_path}") + "\n"`, over
> every file under `bundle/` **excluding** `CHECKSUMS.sha256`, paths sorted ascending.

This reproduces `50506bff278625e98814548221d5f3ea6e75e19dec2947fed71b0db6ed3325a6`
exactly over 147 files — proving every bundle content file is byte-identical to what the
builder recorded. It is also what exposed the blocking finding.

---

## Exact identities reviewed

| Item | Value | Status |
|---|---|---|
| Version | `0.2.0` | ✅ |
| Governed candidate commit | `099c8ebcc884073df1f81d3b0c49e63a6318b235` | ✅ declared |
| Bundle release root | `50506bff278625e98814548221d5f3ea6e75e19dec2947fed71b0db6ed3325a6` | ✅ **independently derived** |
| Stage 1 profile-pack root | `47f0a5c1a89c78cdeda8e57623db46036753de752766588874fa5835a36a0d95` | ✅ declared |
| Evaluation-suite SHA-256 | `489ce0d6b6669be49a230b991913ee252a89bd0088949818cfb20f12b698e95d` | ✅ rehashed |
| Locked Explorer commit | `afd940b6de2d09809ae94dfc77c128936ac7928a` | ✅ in both receipts, `source_dirty: false` |
| Bundle members / profile members | 147 of 147 / 6 of 6 | ✅ rehash OK |
| Consumer lock | `2ae9e949085b02ffcb5ab0e777303418a6f553832952cf97e694689e9ada31a9` | ✅ |
| Rights review / requirements | `887aadc2…` / `24187110…` | ✅ |
| Record count | 2,203 (2,227 − 24 merges; 1,842 + 24 = **1,866 = DEN-GOVUK**) | ✅ reconciles |
| `bundle_tree_sha256` | declared `b10d6d99…`, computed `5fc13c63…` | ❌ **mismatch — BF-001** |
| Prospective owner | `Chris Page-PoC` — **not** the reviewer | — |

Suite diff vs the prior candidate: **only `must_not_retrieve` changed**, in all 24
questions. Queries, expected sources, propositions, required caveat IDs, hard-failure IDs,
near-miss rules, the caveat registry and the hard-failure definitions are byte-identical.

---

## Reviewer disclosure

- **Identity:** Claude Opus 5 (model ID `claude-opus-5`), Anthropic, operating as an
  autonomous review agent in the Claude Code CLI harness.
- **Kind:** `AI-agent`
- **Roles:** independent Stage 1 reviewer; independent evaluation reviewer.
- **Reviewed at:** `2026-07-30T04:59:58Z` (UTC)
- **Independence from implementation *and* remediation:** I implemented none of this
  candidate and remediated none of it. I did not author the adapters, `build.py`, the
  domain profile, the source register, the schemas, the search contract, the ranking
  function, the Explorer consumer or lock, the question suite, the negative controls, the
  calibration journeys, the runtime harness, or any validator or test — and I did not
  design or apply the remediation in `REMEDIATION-MATRIX.md`.
- **Scope separation:** Inputs were the frozen packet, manifests I rehashed myself, and
  read-only execution of the packet's own `reviewer-search.py`. Calibration and runtime
  receipts were treated as implementation evidence; every rank and metric quoted was
  recomputed independently. The prior failed review was read **only** to identify what had
  to close and to test closure — no decision, question judgement or held-out case was
  reused.

**Limitations.** This is an AI-agent review. It is **not** independent human HM Land
Registry assurance, human land-registration domain expertise, legal advice, a licence or
IP audit, a privacy or data-protection assessment, a security assessment, an accessibility
audit or conformance assertion, or participant/user research. `RISK-015` remains a
residual risk this review does not discharge. No live official source was fetched, so
drift since the 2026-07-29 cutoff is unmeasured. No restricted service was contacted, no
account created, no payment made, no authenticated API called, no credential or signed
link handled — the Business Gateway closure was verified from published metadata only. I
did not re-execute the browser journeys, so conclusions about visible caveat assertion
rest on the journey definitions, the receipts and the projected `context_note` content.
Accessibility was assessed from declared statements and static markup only, with no
assistive-technology testing. Welsh translation quality was not evaluated. No clean
rebuild was attempted, so Land Registry G7 byte-identity is unverified here.

---

## What this review does not say

Missing evidence has not been converted into a recommendation anywhere in these outputs.

- `fail` here is **narrow**. It is not a judgement that the candidate is unsafe, not a
  recurrence of any prior finding, and not a statement that the evaluation results are
  wrong. On re-issue of a binding receipt, and absent other changes, the substantive
  assessment recorded in these files would support acceptance.
- Nothing here approves publication or is an owner decision. Land Registry G9 remains a
  project-owner decision for the exact digest.
- No public URL was verified. The planned Pages route remains undeployed, and no bundle
  URL should be shared from this packet.

## Files produced

1. `stage1-review-v0.2.0-remediated.json` — `status`/`outcome` = `fail`, 1 blocking
   finding, 7 warnings, 2 required changes
2. `evaluation-acceptance-review-v0.2.0-remediated.json` — `status` = `fail`, exactly 24
   `question_reviews` with caveat and hard-failure arrays bound programmatically to the
   frozen suite, 8 held-out cases, 1 blocking finding, 5 warnings
3. `claude-independent-review-summary-remediated.md` — this file

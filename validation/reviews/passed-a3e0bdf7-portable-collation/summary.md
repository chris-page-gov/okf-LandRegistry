# Claude independent review summary — OKF Land Registry v0.2.0 (portable-collation correction `a3e0bdf7…`)

## Decisions

| Decision | Result |
|---|---|
| **Stage 1 addendum** | **`pass`** |
| **Land Registry G5 addendum** | **`pass`** |
| **Formal evaluator gate** (`evaluate.py …`) | **exit 0 · `g5_acceptance: pass`** |

Both decisions revise my own preserved `50506bff…` decisions, whose *only* blocker was a
consumer-tree digest mismatch. That blocker is now independently closed, and — importantly
— I **corrected my own earlier root-cause speculation** in the process.

There are **no blocking findings**. Missing evidence has not been converted into a
recommendation anywhere in these outputs.

---

## Blocking findings

**None.** The sole prior blocker is closed:

- **Stage 1 BF-001** (consumer-tree binding failure) → **closed**.
- **G5 EF-001** (exact-bundle runtime receipt did not bind the shipped bundle) → **closed**.

The formal gate that previously exited 1 with *"runtime receipt does not bind the exact
bundle tree"* now **exits 0** and reports `status: pass`, `g5_acceptance: pass`, having
independently recomputed the receipt hash and the bundle tree itself.

---

## Task A — root cause, independently determined (I disagree with my own prior guess)

I did **not** accept the repository's root-cause note on trust. All ten required checks
were reproduced from primary evidence:

| # | Check | Result |
|---|---|---|
| 1 | Prior mismatch reproducible as a **filename-ordering** difference | ✅ proven from one set of bytes |
| 2 | Pinned runner recursively uses `entry.name.localeCompare` | ✅ `run_external_bundle_acceptance.mjs:68`, per-directory, then recurse |
| 3 | Old Python evaluator used **bytewise** path ordering | ✅ reproduces prior `5fc13c63…` |
| 4 | First host-locale fix not portable to Ubuntu; **failed closed** | ✅ CI excerpt: `ValueError: no locale reproduces…`, 3 test errors, not a silent reorder |
| 5 | Final **embedded printable-ASCII key** matches Node order with no OS locale | ✅ all 148 names are printable ASCII; from-scratch reimpl matches |
| 6 | Corrected Python yields `09ad960c…`, 148 files, 31,525,576 bytes | ✅ exact |
| 7 | Both new receipts bind that tree + exact Explorer `afd940b6…` | ✅ search + product, `source_dirty: false` |
| 8 | Search 26/26, product 6/6 | ✅ |
| 9 | Release root **excludes** `CHECKSUMS.sha256`; consumer tree **includes** it; neither self-referential | ✅ verified |
| 10 | **No** post-browser byte rewrite needed | ✅ confirmed — and this reverses my prior guess |

**The definitive experiment.** I reconstructed the *previous* bundle by substituting
`comparison/PREVIOUS-build-receipt.json` and `comparison/PREVIOUS-CHECKSUMS.sha256` into the
otherwise byte-identical member set, then hashed it under both orderings:

| Ordering over one identical set of bytes | Digest | Reproduces |
|---|---|---|
| Node ICU collation, incl. `CHECKSUMS` | `b10d6d99…` | the browser receipt's runtime tree |
| Bytewise path, incl. `CHECKSUMS` | `5fc13c63…` | my prior Python-computed tree |
| Bytewise path, **excl.** `CHECKSUMS` | `50506bff…` | the prior release root |

Three digests, one set of bytes, **ordering alone**.

**What I got wrong last round, stated plainly.** My `50506bff` review correctly *failed
closed* on the mismatch and correctly refused to certify on evidence the project's own
validator rejected — that judgement stands. But I *speculated* the cause was
`CHECKSUMS.sha256` being rewritten after the browser run (a "same-length edit"), and said
the differing bytes couldn't be recovered. That speculation was **wrong**. There were no
differing bytes to recover. `CHECKSUMS.sha256` didn't change content between the two
digests — it changed **position** in the manifest, because bytewise sorts uppercase `C`
(0x43) ahead of all lowercase names, while Node's case-insensitive collation files
`CHECKSUMS` among the `c`-words. That, plus five other positions, reorders the hashed lines.

**Concrete divergence** (top level): bytewise → `…404.html, CHECKSUMS.sha256,
accessibility.html…`; Node → `…404.html, accessibility.html, build-receipt.json,
catalogue-index.html, CHECKSUMS.sha256…`.

---

## Task B — correction boundary

Diffing `comparison/PREVIOUS-CHECKSUMS.sha256` against the corrected manifest:

- **147 members, identical file set.** Exactly **one** member hash changed:
  `build-receipt.json`.
- That receipt changed in exactly **two leaf fields** — `governed_inputs[37]` (`scripts/evaluate.py`)
  `bytes 38010→40506` and `sha256 …→f460b5d0…`, i.e. it records the corrected evaluator.
- `CHECKSUMS.sha256` itself changed (new release-root marker + the build-receipt line), but
  it is the manifest, not a member of itself.

I confirmed **byte-identity** of the governed inputs both by hash *and* by direct `diff`
against the `50506bff` candidate I already reviewed:

- `data/catalogue.json` (2,203 records) — **identical**
- `evaluation/questions.json` (suite `489ce0d6…`, incl. all negative targets) — **identical**
- `pages/search-contract.json` (`fd71d4ec…`) — **identical**
- search journey manifest (`b00936fc…`) and product journey manifest (`f721e6c9…`) — **identical**
- domain-profile pack (`47f0a5c1…`) and source input manifest (`f4c30b27…`) — **identical**

Because none of the catalogue, governed questions, negative targets, expected sources,
caveat mapping, search contract or journeys changed, the prompt's condition for
**carrying forward** my prior substantive reviews is satisfied. I also recomputed retrieval
on the corrected candidate and it is unchanged: **MRR 0.9167**, source success 1.00, target
recall 1.00, all-target 1.00, **zero** negative-control violations.

---

## What was carried forward vs newly established

**Carried forward** from my `50506bff` decisions (byte-identical content basis):

- all **24** `question_reviews`;
- all **8** held-out adversarial cases — **executed against the `50506bff` bundle**, whose
  relevant content is identical here. *They were not re-run for this addendum, and I do not
  claim they were.*
- warnings EW-001…EW-005 / W-001…W-007;
- the independently recomputed retrieval metrics.

**Newly established for this addendum** — only the runtime-tree binding:

- the corrected `evaluate.py` reproduces the pinned Explorer's Node ASCII collation with no
  host locale (I reimplemented it from scratch and got the same tree);
- the rerun **search receipt** (`8b2ac792…`, 26/26) and **product receipt** (6/6) bind
  consumer tree `09ad960c…` and Explorer commit `afd940b6…`;
- the **formal gate exits 0** with `g5_acceptance: pass`.

---

## Warnings carried forward (unchanged, none blocking)

- **Negative-control bounds appear fitted to observed rank.** For 20 of 24 questions,
  `max_rank` = the negative's observed rank − 1. The targets are genuine near misses; the
  *bound derivation* is the weakness. Three negatives (LR-Q008/Q016/Q021) never rank so
  can't fire; LR-Q015's has 14 positions of slack.
- **Two thirds of caveat assertions use universal IDs.** Four of eight caveat IDs sit on
  all 2,203 records; 10 of 24 questions have entirely universal requirements. Mitigated
  because each journey also asserts the expected canonical URL as an anchor `href`.
- **A caveat can satisfy its assertion as a bare identifier** (W-003).
- **Welsh material remains hard to retrieve** (W-004) — handling correct, discovery weak.
- **The lexical diagnostic scores a different substrate** than the shipped worker (W-005) —
  now correctly labelled non-formal, with the Explorer receipt as formal evidence.
- **One restricted record points at a non-restricted rights assessment** (W-006) — no safety
  consequence.

---

## Limitations

This remains an **AI-agent review**. It is **not** independent human HM Land Registry
assurance, human land-registration domain expertise, legal advice, a licence or IP audit, a
privacy or data-protection assessment, a security assessment, an accessibility audit or
conformance assertion, or participant/user research. `RISK-015` remains a residual risk this
review does not discharge.

I did not re-execute the browser journeys myself; I verified the receipts, recomputed the
bundle tree two independent ways, and ran the formal gate. No live official source was
fetched, so drift since the 2026-07-29 cutoff is unmeasured. No restricted service was
contacted, no account created, no payment made, no authenticated API called, no credential
or signed link handled. No clean rebuild was attempted, so Land Registry G7 byte-identity
is unverified here.

I am **not the project owner**. I have created no Land Registry G9 approval, set no
deployment variable, authorised no publication, claimed no HMLR endorsement, and verified
no public URL. The planned Pages route remains undeployed.

---

## Exact identities reviewed

| Item | Value | Status |
|---|---|---|
| Version | `0.2.0` | ✅ |
| Correction commit | `40482c865dc4332162f1e93756d94ca93abe3559` | ✅ declared |
| Bundle release root | `a3e0bdf7846893ce29255f6f20a509dad18ef2b367ba3dfbe48c28191377a704` | ✅ **independently derived** (147 files, bytewise, excl. CHECKSUMS) |
| Runtime consumer tree | `09ad960c7b44d0d1831cd8f4aa5a625fb2e7e4294a3ff2c6941bf1b1c127209c` | ✅ **independently derived** (148 files, Node ASCII collation); binds both receipts |
| Profile-pack root | `47f0a5c1a89c78cdeda8e57623db46036753de752766588874fa5835a36a0d95` | ✅ unchanged |
| Evaluation-suite | `489ce0d6b6669be49a230b991913ee252a89bd0088949818cfb20f12b698e95d` | ✅ unchanged |
| Search journey manifest | `b00936fcaee53cd481e307c8bf9279416b0d80ae91fefcba2d22b4591d709753` | ✅ unchanged |
| Product journey manifest | `f721e6c9915484d72ac38b3630926a0461ff7fe417f39651a1a8e79546968431` | ✅ unchanged |
| Search runtime receipt | `8b2ac792ead76a29dc8e1c04f19b54ad862e16d0fcf3ae12c0bbd22a0442dd7d` | ✅ bound by the formal gate |
| Locked Explorer commit | `afd940b6de2d09809ae94dfc77c128936ac7928a` | ✅ `source_dirty: false`, both receipts |
| Bundle members / profile members | 147 of 147 / 6 of 6 | ✅ rehash OK |
| Corrected `evaluate.py` | `f460b5d0347272cae2a0b1b9e79418958707d75767b7b290763b748ce74e55f0` | ✅ |
| Changed members vs `50506bff` | **only** `build-receipt.json` | ✅ boundary confirmed |
| Record count | 2,203 | ✅ catalogue byte-identical |
| Prospective owner | `Chris Page-PoC` — **not** the reviewer | — |

---

## Reviewer disclosure

- **Identity:** Claude Opus 5 (model ID `claude-opus-5`), Anthropic, operating as an
  autonomous review agent in the Claude Code CLI harness.
- **Kind:** `AI-agent`
- **Roles:** independent Stage 1 reviewer; independent evaluation reviewer. Same reviewer as
  the preserved `50506bff` decisions (stage1 `7fb8883b…`, g5 `453d768f…`), which I confirmed
  byte-identical to my own prior outputs.
- **Reviewed at:** `2026-07-30T06:36:46Z` (UTC)
- **Independence:** I implemented none of this candidate and remediated none of it,
  including this collation correction. I did not author `evaluate.py`, its tests, the
  Explorer runner, the domain profile, the catalogue, the suite or any validator.
- **Scope separation:** inputs were the frozen packet, manifests and receipts I rehashed
  myself, read-only execution of the packet's own `reviewer-search.py`, and one invocation of
  the packet's own `evaluate.py` formal gate. I treated the repository's root-cause note as a
  claim to be tested, not accepted, and I reversed my own prior speculation where the
  evidence required it.

## Files produced

1. `stage1-review-v0.2.0-portable-collation.json` — `status`/`outcome` = `pass`, 0 blocking, 7 warnings
2. `evaluation-acceptance-review-v0.2.0-portable-collation.json` — `status` = `pass`, 24 `question_reviews`, 8 held-out, 0 blocking; carry-forward boundary stated explicitly
3. `formal-evaluation-acceptance-v0.2.0-portable-collation.json` — the evaluator's own report: `status: pass`, `g5_acceptance: pass`, exit 0
4. `claude-independent-review-summary-portable-collation.md` — this file

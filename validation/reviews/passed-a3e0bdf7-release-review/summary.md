# Claude independent release review — OKF Land Registry v0.2.0 (`a3e0bdf7…`)

## Decisions

| Decision | Result |
|---|---|
| **Land Registry G6 — user-facing quality** | **`pass`** |
| **Independent release recommendation (G1–G8)** | **`recommend_approval`** |

Neither output is an approval. Land Registry **G9 and publication remain the project
owner's decision**, not made here.

---

## Blocking findings

**None**, in either task.

---

## Identity preflight (independently reproduced)

- **34 of 34** manifest files rehash OK.
- **Release root** `a3e0bdf7…` independently derived as the bytewise sha256 manifest over
  the 147 members (excluding `CHECKSUMS.sha256`).
- **Consumer tree** `09ad960c…` independently derived from the extracted archive by the
  recursive Node ASCII-collation order (148 files, 31,525,576 bytes).
- **Archive** `dist/okf-landregistry-0.2.0.zip` rehashes to `7f92e51c…`, and its 148 files
  **reproduce both** the release root and the consumer tree — so the archive is the exact
  candidate bundle.
- Both Explorer receipts bind Explorer commit `afd940b6…` (`source_dirty: false`) and tree
  `09ad960c…`; search **26/26**, product **6/6**.
- The adopted Stage 1 and G5 decisions bind this same candidate; the adopted G5 digest
  `1fd52653…` equals the `review_sha256` my own formal gate recorded last round — reviewer
  continuity confirmed. No v0.1.0 receipt is reused.

---

## Task A — G6, all four checks pass

- **automated-journeys — pass.** Search 26/26 and product 6/6 on the pinned Explorer;
  pages 200 OK, zero console errors, zero external requests. I re-served the extracted
  bundle and confirmed the no-JS catalogue lists its 2,203 records with no script tag.
- **manual-accessibility-journeys — pass (AI-assisted, no conformance claim).** I ran
  **axe-core 4.12.1** (the evidence's version) at 320px and reproduced **0 violations, 1
  incomplete**. I examined the incomplete rather than treating it silently: it is
  `color-contrast` on `#page-title`/`.lede`, flagged only because transparent decorative
  `.parcel` outlines (`rgba(0,0,0,0)`) overlap the hero text's bounding box. The **real
  contrast is 16.56:1 and 10.31:1** on the cream background — far above WCAG AA — so the
  flag is benign. Skip-link first, one H1, four landmarks, logical heading order,
  `lang=en`, no 320px overflow; a screenshot confirmed clean legibility.
- **security-critical-zero — pass.** Strict CSP (`default-src 'self'`, no external origin,
  no inline script/style, `object-src 'none'`, `base-uri 'none'`) on every page; no scripts
  on the landing page; no cookies/localStorage/sessionStorage; no external runtime
  dependency (all external links are official-source destinations); no secrets; no approval
  or WCAG-conformance claim.
- **performance-budgets — pass.** Bounded lazy: **24,394-byte** raw shell (index.html 9,016
  + styles.css 15,378), ~6.5 KB gzip; the 148-file / 31,525,576-byte corpus is **not**
  eagerly loaded (landing page fetches only its stylesheet); the Explorer is opt-in with
  lazy shards.

The one mobile axe `incomplete` was **examined and cleared**, exactly as the prompt required.

---

## Task B — G1–G8 all pass with exact-candidate evidence

| Gate | Result | Basis (all digest-bound to `40482c8…` / `a3e0bdf7…`) |
|---|---|---|
| **G1** profile | pass | schema-valid, JSON/YAML equivalent, refs closed, pack rehashes; adopted Stage 1 |
| **G2** snapshot | pass | 2,170 records + Content API observation rehash; terminal outcomes & omissions explicit; no live fetch |
| **G3** rights/privacy/safety | pass | every record has access/rights/caveat/evidence; independent rights review; zero prohibited content; BG endpoints restricted |
| **G4** OKF/data integrity | pass | OKF conformant; **137/137 tests**; JSON-LD refs valid; 2,203 records; checksums valid |
| **G5** evaluation | pass | formal gate **exit 0**, 0 hard failures, MRR 0.9167, recall 1.0, coverage 1.0; adopted independent review |
| **G6** user-facing | pass | my Task A (above) |
| **G7** reproducibility | pass | byte-identical clean builds A/B, no network; change-impact reconciled, 0 unexplained; I reproduced root+tree from the archive |
| **G8** package integrity | pass | archive byte-identical (`7f92e51c…`), SPDX-2.3 SBOM, provenance, 11 hash-pinned deps, 5 SHA-pinned actions |

No waiver, no blocking finding → **`recommend_approval`**.

Residual risks reviewed: `RISK-001`…`RISK-017`. RISK-001–011 are inherent PoC hazards with
implemented controls; RISK-012/013/015 are accepted, disclosed gaps; RISK-014/016/017 are
controlled (deployment is gated on an owner-set variable; the candidate asserts no
approval). None of the release-assurance **hard failures** is present.

---

## Warnings (none blocking)

- All assurance here is **AI-agent or automated, not human** — no human legal, licence,
  privacy, security, accessibility or domain audit; no participant research (`RISK-015`,
  `GAP-HUMAN-RESEARCH`).
- The carried-forward evaluation-apparatus warnings still apply: negative-control
  `max_rank` values appear fitted to observed ranks (20 of 24 at observed-rank−1; three
  never rank), and 32 of 48 required caveat assertions use universally-declared IDs. Both
  are mitigated and block nothing, but should be strengthened before this suite is a
  regression barrier for a later release.
- G7 was corroborated by **digest reproduction**, not by my re-executing the build.
- My browser checks used a **local loopback serve** of the digest-verified archive, not a
  public URL.

---

## What is — and is not — established

**Established by this review:**
- Automated evidence (deterministic validators, 137 tests, byte-identical builds,
  digest-bound archive/SBOM/provenance).
- Independent **AI-agent** review: Stage 1 pass, G5 pass with formal gate exit 0, and now
  **G6 pass** and a **release recommendation** across G1–G8.

**Not established, and explicitly outstanding:**
- **Owner Land Registry G9** decision — belongs to `Chris Page-PoC`; **not made**.
- Setting the deployment variable / creating a GitHub release — **not done** (prohibited
  for me).
- **RC deployment** to GitHub Pages — **not done**.
- **Public-URL browser verification** — **no public URL deployed, visited or verified**.
- **Byte-identical final promotion** — **not done**.

No claim is made of owner or HM Land Registry approval, human or WCAG assurance, official
HMLR status or endorsement, or that any public URL exists. Missing evidence has not been
converted into a recommendation.

---

## Exact identities reviewed

| Item | Value |
|---|---|
| Version | `0.2.0` |
| Candidate commit | `40482c865dc4332162f1e93756d94ca93abe3559` |
| Bundle release root | `a3e0bdf7846893ce29255f6f20a509dad18ef2b367ba3dfbe48c28191377a704` (independently derived) |
| Consumer tree | `09ad960c7b44d0d1831cd8f4aa5a625fb2e7e4294a3ff2c6941bf1b1c127209c` (independently derived) |
| Profile pack root | `47f0a5c1a89c78cdeda8e57623db46036753de752766588874fa5835a36a0d95` |
| Question suite | `489ce0d6b6669be49a230b991913ee252a89bd0088949818cfb20f12b698e95d` |
| Explorer commit | `afd940b6de2d09809ae94dfc77c128936ac7928a` (`source_dirty: false`) |
| Search / product receipts | `8b2ac792…` (26/26) / `71b9608618…` (6/6) |
| Candidate archive | `7f92e51cfa75fee9e3517788a0bd1b9c36de34525ea18d13732da3d24b61120d` (unreleased-candidate) |
| Prospective owner | `Chris Page-PoC` — **not** the reviewer |

## Reviewer

Claude Opus 5 (model ID `claude-opus-5`), Anthropic, in the Claude Code CLI harness.
`kind: ai-agent`; roles: `independent-g6-reviewer`, `release-reviewer`. Reviewed at
`2026-07-31T21:52:47Z`. Independent of implementation and remediation; the same reviewer
that produced the adopted Stage 1 and G5 decisions for this candidate.

## Files produced

1. `g6-independent-review-v0.2.0-a3e0bdf7.json` — G6, `status: pass`
2. `independent-release-recommendation-v0.2.0-a3e0bdf7.json` — `outcome: recommend_approval`
3. `claude-release-review-summary-a3e0bdf7.md` — this file

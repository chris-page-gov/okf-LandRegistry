# Independent G6 and release-recommendation prompt

Act as an independent release reviewer. Do not implement fixes, edit candidate
files, accept risk for the owner, deploy anything or infer a pass from an
authored `status` field.

## Identity preflight

Before reviewing:

1. rehash every file in `REVIEW-MANIFEST.json`;
2. independently derive the bundle release root from `bundle/CHECKSUMS.sha256`;
3. confirm candidate commit
   `40482c865dc4332162f1e93756d94ca93abe3559`;
4. confirm root
   `a3e0bdf7846893ce29255f6f20a509dad18ef2b367ba3dfbe48c28191377a704`;
5. confirm both Explorer receipts bind commit
   `afd940b6de2d09809ae94dfc77c128936ac7928a` and tree
   `09ad960c7b44d0d1831cd8f4aa5a625fb2e7e4294a3ff2c6941bf1b1c127209c`;
6. confirm the adopted Stage 1 and G5 decisions bind this same candidate; and
7. stop with `fail` if an identity, digest or evidence reference differs.

## Task A — Land Registry G6

Independently decide all four required G6 checks:

- `automated-journeys`;
- `manual-accessibility-journeys`;
- `security-critical-zero`; and
- `performance-budgets`.

Read the exact Explorer search and product receipts, the local Playwright/axe
evidence, relevant generated pages, accessibility statement, product contract,
requirements, tests and workflow. You may execute additional read-only checks.

Interpret `manual-accessibility-journeys` accurately: the project currently has
AI-agent-assisted keyboard, focus, semantics, reflow and visible-limitation
checks, not a human accessibility audit. A G6 pass may recognise those declared
assisted journeys if they are sufficient for this AI PoC gate, but it must not
claim WCAG conformance or human assurance. The one mobile axe `incomplete`
item must be examined rather than silently treated as a pass or violation.

For performance, evaluate the governed requirement: bounded lazy metadata,
not an eager whole-corpus initial payload. Do not invent a numeric budget that
the v0.2.0 contract does not define. Record the 24,394-byte raw authored shell,
6,558-byte gzip shell, 148-file/31,525,576-byte complete bundle and the pinned
Explorer's lazy shard behaviour.

For security, this is a bounded static PoC review, not a penetration test.
Check for critical browser errors, unexpected runtime requests, external
runtime dependencies, unsafe content paths, secrets or prohibited content,
and verify the CSP/static tests cited by the evidence.

Return `g6-independent-review-v0.2.0-a3e0bdf7.json` with:

```json
{
  "schema": "okf-hmlr-g6-independent-review.v1",
  "status": "pass or fail",
  "candidate": {
    "candidate_commit_sha": "full value",
    "release_root_sha256": "full value",
    "consumer_tree_sha256": "full value"
  },
  "reviewer": {
    "identity": "stable name and model/harness identity",
    "kind": "ai-agent",
    "role": "independent-g6-reviewer",
    "reviewed_at": "RFC 3339",
    "independent": true,
    "scope_separation": "what you did not author"
  },
  "checks": {
    "automated-journeys": {"status": "pass or fail", "finding": "..."},
    "manual-accessibility-journeys": {"status": "pass or fail", "finding": "..."},
    "security-critical-zero": {"status": "pass or fail", "finding": "..."},
    "performance-budgets": {"status": "pass or fail", "finding": "..."}
  },
  "reviewed_checks": {
    "accessibility-and-limitations-review": {
      "status": "pass or fail",
      "completed_at": "RFC 3339",
      "execution_mode": "document-review"
    },
    "security-and-performance-review": {
      "status": "pass or fail",
      "completed_at": "RFC 3339",
      "execution_mode": "document-review"
    }
  },
  "blocking_findings": [],
  "warnings": [],
  "limitations": [],
  "summary": "..."
}
```

If any required check is not supported, return `fail`; do not create a waiver.

## Task B — independent release recommendation

Perform Task B only after Task A passes.

Read the normative gate definitions in `docs/release-assurance.md`, all source
evidence listed in the manifest, the adopted Stage 1 and G5 decisions, the G6
decision from Task A, the risk register and the release tracker. Independently
check that each required G1–G8 check has exact-candidate evidence, that no v0.1.0
receipt is being reused, and that limitations are accurately preserved.

The deterministic archive is an **unreleased candidate archive**. Its SHA-256
is `7f92e51cfa75fee9e3517788a0bd1b9c36de34525ea18d13732da3d24b61120d`.
Its candidate status is deliberate: G8 must bind the archive before G9, while
publication authority belongs only in external owner evidence.

Return
`independent-release-recommendation-v0.2.0-a3e0bdf7.json` with:

```json
{
  "schema": "okf-hmlr-independent-release-recommendation.v1",
  "candidate": {
    "candidate_commit_sha": "full value",
    "release_root_sha256": "full value",
    "archive_sha256": "full value"
  },
  "identity": "same stable reviewer identity as Task A",
  "kind": "ai-agent",
  "role": "release-reviewer",
  "reviewed_at": "RFC 3339",
  "independent": true,
  "outcome": "recommend_approval or withhold_approval",
  "gate_findings": {
    "G1": {"status": "pass or fail", "finding": "..."},
    "G2": {"status": "pass or fail", "finding": "..."},
    "G3": {"status": "pass or fail", "finding": "..."},
    "G4": {"status": "pass or fail", "finding": "..."},
    "G5": {"status": "pass or fail", "finding": "..."},
    "G6": {"status": "pass or fail", "finding": "..."},
    "G7": {"status": "pass or fail", "finding": "..."},
    "G8": {"status": "pass or fail", "finding": "..."}
  },
  "blocking_findings": [],
  "warnings": [],
  "limitations": [],
  "residual_risk_ids_reviewed": ["RISK-001", "..."],
  "summary": "..."
}
```

Use `recommend_approval` only if every G1–G8 finding passes with no waiver or
blocking finding. Otherwise use `withhold_approval`.

## Prohibited claims

Neither output may claim:

- owner or HM Land Registry approval;
- human domain, legal, licence, privacy, security or accessibility assurance;
- WCAG conformance;
- official HMLR status or endorsement;
- that a public URL was deployed, visited or verified; or
- that RC or final promotion occurred.

Also return a short Markdown summary that distinguishes automated evidence,
independent AI review, the remaining owner G9 decision, RC deployment, public
browser verification and byte-identical promotion.

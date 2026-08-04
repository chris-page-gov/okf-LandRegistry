# Claude independent-review packet: remediated OKF Land Registry v0.2.0

This is a review-only packet for the frozen `v0.2.0` AI-generated
proof-of-concept candidate. It is not a release, owner approval or HM Land
Registry publication.

## What Chris should do

1. Start a fresh Claude conversation or project that did not build or
   remediate this candidate.
2. Upload the complete ZIP supplied with this README.
3. Open `CLAUDE-REVIEW-PROMPT.md`, copy the section headed **Copy-ready
   prompt**, and send it without changing the candidate identities.
4. Return Claude's complete outputs:
   - `stage1-review-v0.2.0-remediated.json`;
   - `evaluation-acceptance-review-v0.2.0-remediated.json`;
   - `claude-independent-review-summary-remediated.md`; and
   - if Claude cannot execute held-out searches,
     `held-out-execution-request-remediated.json`.
5. Do not approve Land Registry G9 yet. G9 follows only after these outputs
   pass validation and the complete Land Registry G1–G8 evidence exists.

## Exact identity under review

- Candidate version: `0.2.0`
- Governed candidate commit:
  `099c8ebcc884073df1f81d3b0c49e63a6318b235`
- Bundle release root:
  `50506bff278625e98814548221d5f3ea6e75e19dec2947fed71b0db6ed3325a6`
- Stage 1 profile-pack root:
  `47f0a5c1a89c78cdeda8e57623db46036753de752766588874fa5835a36a0d95`
- Evaluation-suite SHA-256:
  `489ce0d6b6669be49a230b991913ee252a89bd0088949818cfb20f12b698e95d`
- Locked Explorer: `v0.5.7`, commit
  `afd940b6de2d09809ae94dfc77c128936ac7928a`
- Prospective project owner: `Chris Page-PoC`
- Owner decision: **not requested and not given**

## Why this is a second review

Claude Opus 5 completed both reviews for the earlier `e07fafe2…` candidate
and returned `fail`. That evidence is preserved in
`candidate/validation/reviews/failed-e07fafe25bbd816f/`.

The new review must decide whether the corrections genuinely close those
findings and whether any new blocker exists. The previous output and held-out
queries are historical evidence, not a template or reusable pass. Read
`REMEDIATION-MATRIX.md` before reviewing the candidate.

## What happens next

Codex will validate the returned JSON, verify the held-out execution and run
the formal evaluator against the exact runtime receipt. If both reviews pass,
Codex will assemble the remaining exact-candidate evidence and prepare the
separate owner G9 decision packet.

No public Land Registry bundle URL should be shared from this packet.

# Claude collation-correction addendum: OKF Land Registry v0.2.0

This is a narrow, review-only addendum for the `v0.2.0` AI-generated
proof-of-concept candidate. It is not a release, owner approval, HM Land
Registry publication or public-URL verification.

## What happened, in beginner terms

Claude correctly refused to approve the previous candidate because two
programs produced different fingerprints for what appeared to be the same
bundle. The programs did read the same 148 files and the same 31,525,576
bytes. They disagreed only about the order in which filenames were listed
before hashing:

- the pinned Explorer used Node's English `localeCompare` order;
- the Python evaluator used bytewise path order.

A fingerprint changes when the lines being fingerprinted are reordered, even
when every file byte is identical. The first correction used an installed
English operating-system locale. That passed locally but correctly failed in
GitHub Actions because the Ubuntu runner did not provide that locale. The
final evaluator embeds the pinned runner's printable-ASCII ordering, rejects
non-ASCII paths and no longer depends on host locale packages.

Because the evaluator's own hash is recorded in the generated build receipt,
the correction changed that receipt and therefore produced a new governed
bundle root. The exact Explorer search and product journeys were rerun against
the rebuilt bundle. Both now bind consumer tree `09ad960c…`.

## What Chris should do

1. Return this complete ZIP to the same Claude reviewer that produced the
   preserved `50506bff…` decisions, if possible. A fresh independent reviewer
   may instead perform the complete review, but cannot inherit Claude's
   earlier judgements.
2. Ask the reviewer to open `CLAUDE-ADDENDUM-PROMPT.md` and follow its
   copy-ready prompt without changing the candidate identities.
3. Return these four complete files:
   - `stage1-review-v0.2.0-portable-collation.json`;
   - `evaluation-acceptance-review-v0.2.0-portable-collation.json`;
   - `formal-evaluation-acceptance-v0.2.0-portable-collation.json`; and
   - `claude-independent-review-summary-portable-collation.md`.
4. Do not approve Land Registry G9 yet. G9 follows only after these outputs
   pass validation and exact-candidate Land Registry G1–G8 evidence exists.

## Exact corrected identity

- Candidate version: `0.2.0`
- Governed correction commit:
  `40482c865dc4332162f1e93756d94ca93abe3559`
- Bundle release root:
  `a3e0bdf7846893ce29255f6f20a509dad18ef2b367ba3dfbe48c28191377a704`
- Runtime consumer tree:
  `09ad960c7b44d0d1831cd8f4aa5a625fb2e7e4294a3ff2c6941bf1b1c127209c`
- Stage 1 profile-pack root:
  `47f0a5c1a89c78cdeda8e57623db46036753de752766588874fa5835a36a0d95`
- Evaluation-suite SHA-256:
  `489ce0d6b6669be49a230b991913ee252a89bd0088949818cfb20f12b698e95d`
- Locked Explorer: `v0.5.7`, commit
  `afd940b6de2d09809ae94dfc77c128936ac7928a`
- Prospective project owner: `Chris Page-PoC`
- Owner decision: **not requested and not given**

## What has and has not changed

The frozen source observations, domain profile, 2,203-record catalogue,
question suite, search contract and both journey manifests are unchanged.
The correction changes:

- the Python implementation of the consumer-tree ordering;
- portable full-ASCII and exact-tree regression tests;
- release-assurance documentation;
- the derived build receipt and checksum root; and
- replacement diagnostic and real-browser receipts.

The previous failed review is preserved unchanged under
`candidate/validation/reviews/failed-50506bff-tree-collation/`. Its failure
must not be edited or relabelled as a pass.

## What happens next

Codex will validate the returned JSON and run the formal evaluator against the
exact corrected search receipt. If the Stage 1 and G5 decisions pass, Codex
will prepare the remaining exact-candidate gate evidence and the separate
owner G9 decision packet.

No public Land Registry bundle URL should be shared from this packet.

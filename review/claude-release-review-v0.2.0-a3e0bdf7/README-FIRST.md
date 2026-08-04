# README first: final independent review before owner G9

This packet asks an independent Claude Opus 5 reviewer to do two bounded
things for the exact HM Land Registry public-estate OKF v0.2.0 candidate:

1. decide Land Registry G6 — user-facing quality; and
2. only if G6 passes, review the complete pre-G9 evidence and decide whether
   to recommend owner approval.

It does **not** ask Claude to grant owner approval, deploy a website, set a
GitHub variable or verify a public URL.

Read [`CLAUDE-RELEASE-REVIEW-PROMPT.md`](CLAUDE-RELEASE-REVIEW-PROMPT.md) and
rehash every file listed in [`REVIEW-MANIFEST.json`](REVIEW-MANIFEST.json).

The exact candidate is:

- commit `40482c865dc4332162f1e93756d94ca93abe3559`;
- bundle root
  `a3e0bdf7846893ce29255f6f20a509dad18ef2b367ba3dfbe48c28191377a704`;
- profile root
  `47f0a5c1a89c78cdeda8e57623db46036753de752766588874fa5835a36a0d95`;
- question-suite SHA-256
  `489ce0d6b6669be49a230b991913ee252a89bd0088949818cfb20f12b698e95d`;
- Explorer commit `afd940b6de2d09809ae94dfc77c128936ac7928a`; and
- Explorer consumer tree
  `09ad960c7b44d0d1831cd8f4aa5a625fb2e7e4294a3ff2c6941bf1b1c127209c`.

No public Land Registry v0.2.0 URL has been deployed or verified.

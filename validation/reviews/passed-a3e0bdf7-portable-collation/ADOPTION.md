# Adoption record: portable-collation independent review

This directory preserves the four files returned by the independent Claude
Opus 5 review of the exact portable-collation candidate. They were copied
byte-for-byte from the maintainer's original working checkout; filenames were
shortened only at the destination.

## Candidate identity

- Governed candidate commit:
  `40482c865dc4332162f1e93756d94ca93abe3559`
- Bundle release root:
  `a3e0bdf7846893ce29255f6f20a509dad18ef2b367ba3dfbe48c28191377a704`
- Question-suite SHA-256:
  `489ce0d6b6669be49a230b991913ee252a89bd0088949818cfb20f12b698e95d`
- Profile-pack root:
  `47f0a5c1a89c78cdeda8e57623db46036753de752766588874fa5835a36a0d95`
- Pinned Explorer commit:
  `afd940b6de2d09809ae94dfc77c128936ac7928a`
- Pinned Explorer consumer-tree SHA-256:
  `09ad960c7b44d0d1831cd8f4aa5a625fb2e7e4294a3ff2c6941bf1b1c127209c`

## Adopted files

| Repository file | SHA-256 |
|---|---|
| `summary.md` | `55e9e086ceba470413c813e823caa039255ee75aeec83aca454b70b43c7d2885` |
| `formal-evaluation-acceptance.json` | `b820975d270a7d4c333a770588cf49181732ffd650901decf99007fbd287c073` |
| `evaluation-acceptance-review.json` | `1fd52653984fa5173df99c693d593c10d8b3167900da0ab7dc2d16f25da0c910` |
| `stage1-review.json` | `b4833a8bdf91768b9110e60fc5ba19d73a95ca9de05310314f91e869c2ccb985` |

The source paths in the maintainer checkout were:

- `troubleshooting/claude-independent-review-summary-portable-collation.md`
- `troubleshooting/formal-evaluation-acceptance-v0.2.0-portable-collation.json`
- `troubleshooting/evaluation-acceptance-review-v0.2.0-portable-collation.json`
- `troubleshooting/stage1-review-v0.2.0-portable-collation.json`

## Validation and decision boundary

The three JSON documents parse without duplicate keys. The Stage 1 review
records `status: pass` and `outcome: pass`, with no blocking findings or
required changes. The evaluation review records `status: pass`, and the
formal evaluator records `status: pass` and `g5_acceptance.status: pass`.
The formal report binds the exact search-runtime receipt and the independent
review digest.

The formal evaluator was also rerun locally against the adopted evaluation
review and exact portable-collation runtime receipt. It returned a pass for
all 24 calibration questions, all eight carried-forward held-out cases and
zero hard failures.

The reviewer explicitly did **not** perform a human HMLR, legal, licence,
privacy, security or accessibility audit; did not re-run the browser; did not
verify a public URL; and did not grant Land Registry G9 approval. Those
limitations and all non-blocking warnings remain part of the adopted
evidence.

This adoption record does not convert the Stage 1 and G5 decisions into a
general release recommendation, owner approval or publication authority.

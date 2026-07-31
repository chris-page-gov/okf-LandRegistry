# Adoption record: G6 and independent release recommendation

This directory preserves the three files returned by the independent Claude
Opus 5 review of the exact v0.2.0 candidate. The files were copied
byte-for-byte from the reviewer's output directory; filenames were shortened
only at the destination.

## Candidate identity

- Governed candidate commit:
  `40482c865dc4332162f1e93756d94ca93abe3559`
- Bundle release root:
  `a3e0bdf7846893ce29255f6f20a509dad18ef2b367ba3dfbe48c28191377a704`
- Explorer consumer-tree SHA-256:
  `09ad960c7b44d0d1831cd8f4aa5a625fb2e7e4294a3ff2c6941bf1b1c127209c`
- Candidate archive SHA-256:
  `7f92e51cfa75fee9e3517788a0bd1b9c36de34525ea18d13732da3d24b61120d`

## Adopted files

| Repository file | SHA-256 |
|---|---|
| `g6-independent-review.json` | `952e3fb6eef6922453bf07ed7e4e4d26ab8a5d0a87bf8e1d0184ecd105bd8c8c` |
| `independent-release-recommendation.json` | `54aed4daf7215e110192b97c363015f39161172f3c9d1bb0ed44b7ae4f676d99` |
| `summary.md` | `6208963bd6b3f2f974dfb8154dc14d3569deed36c15e6519e02b76e4c38e3727` |

## Validation and decision boundary

Both JSON files parse without duplicate keys. The G6 review records
`status: pass`, all four required checks as `pass`, no blocking findings and
an independent AI-agent reviewer. The release recommendation records
`outcome: recommend_approval`, every Land Registry G1–G8 finding as `pass`,
all 17 current residual-risk IDs as reviewed, and no blocking finding or
waiver. Both bind the exact candidate identities above.

The original G6 output describes its two reviewed checks with the combined
execution mode `document-review-plus-independent-browser-execution`. The
release-receipt schema uses a smaller controlled vocabulary, so the G6 receipt
maps these to `interactive-browser`, the closest supported mode. The original
review remains unchanged and is cited as evidence, so no review activity is
lost or rewritten.

The review reports 137 tests because that was the exact automated-validation
count in its frozen packet. The repository now runs 140 tests after adding the
candidate-archive publication-state regression test and two pre-G9 assembly
tests. These later tests do not change the governed candidate bytes and are
recorded separately; the reviewer's 137-test statement has not been edited.

The reviewer explicitly did **not** perform independent human legal, licence,
privacy, security, accessibility or domain assurance, did not verify a public
URL, and did not make the owner's Land Registry G9 decision. These limitations
and the reviewer's three non-blocking warnings remain in force.

This adoption record is not G9 owner approval, a deployment instruction, a
GitHub release or public-URL verification.

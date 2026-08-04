# Root-cause analysis: `b10d6d99…` versus `5fc13c63…`

The Claude review correctly failed closed because the formal Python evaluator
did not accept the locked Explorer receipt. Its conclusion that the evidence
did not bind was correct. Its proposed explanation—a post-browser rewrite of
`CHECKSUMS.sha256` and a circular tree identity—was not.

## Reproduction

Both implementations read:

- 148 files;
- 31,525,576 bytes; and
- the same bytes from every file in the frozen bundle.

The difference is path ordering:

| Implementation | Ordering | Result |
|---|---|---|
| Locked Explorer `v0.5.7` Node runner | recursive `entry.name.localeCompare(...)` | `b10d6d996561ff7ea08fe495c2b4e5f66273d3325b1b68378d3ca5f470e6ec79` |
| Land Registry Python evaluator before correction | Python bytewise `sorted(Path)` | `5fc13c6300fbf057261efbf57a5e76a52de4a5ca3f589bc873be2cc80db967b4` |

The first divergence is visible at the bundle root:

- Node: `catalogue-index.html`, then `CHECKSUMS.sha256`;
- Python bytewise order: `CHECKSUMS.sha256`, then
  `accessibility.html`, `build-receipt.json`, `catalogue-index.html`.

A second divergence occurs between `access_state.json` and `access.json`.
English collation, which Node's locked runner uses for these ASCII names,
orders `access_state.json` first; Python bytewise order does not.

Running the Node algorithm again over the reviewed bundle reproduces the
receipt's `b10d6d99…` exactly. No reviewed-candidate byte changed after that
browser run.

## Why there is no circularity

The browser tree includes `CHECKSUMS.sha256`, but that file does not contain
the browser-tree digest. It contains the separate release root
`50506bff…`, calculated over the 147 governed bundle members excluding
`CHECKSUMS.sha256`. The two identities have different purposes:

- release root: governed release members, excluding its own manifest;
- browser tree: every byte supplied to the consumer, including the manifest.

Including the manifest in the browser tree therefore binds it without
self-reference.

## Correction

`scripts/evaluate.py` now reproduces the locked runner's recursive English
collation. It:

1. uses an embedded printable-ASCII primary and case-weight table regression
   checked against the locked Node runner;
2. does not depend on operating-system locale packages;
3. rejects non-ASCII path names for this compatibility algorithm; and
4. rejects symbolic links.

Regression tests assert both the critical filename ordering and exact equality
with the committed `v0.5.7` receipt tree.

## CI portability follow-up

The first correction depended on an installed English operating-system
locale. It passed on the macOS release worktree and produced candidate root
`0fdab21a…`, but GitHub Actions run `30517178183` failed closed because its
Ubuntu image did not expose any of the selected locales. That was a
portability defect in the formal verifier, not a content or Explorer defect.

The final implementation embeds the pinned Node runner's printable-ASCII
primary and case weights. A regression test covers every printable ASCII
character in addition to the exact receipt tree, and non-ASCII path names
still fail closed. The verifier no longer depends on host locale packages.

The frozen source observations, 2,203-record catalogue, question suite,
search contract and journey manifests remain unchanged. Because the evaluator
is a governed build input recorded in `build-receipt.json`, each correction
required an honest rebuild:

| Candidate | Release root | Consumer tree | Outcome |
|---|---|---|---|
| Claude-reviewed candidate | `50506bff…` | Node `b10d6d99…`; old Python `5fc13c63…` | independent review failed closed |
| Host-locale correction | `0fdab21a…` | `91bc8aca…` | local checks passed; GitHub CI exposed missing locale |
| Portable final correction | `a3e0bdf7…` | `09ad960c…` | 137 tests and both exact Explorer suites pass locally |

Both exact Explorer journey manifests were rerun for the final bundle. The
search receipt passes 26/26 and the product receipt passes 6/6.

A fresh independent decision bound to `a3e0bdf7…` is still required because
the submitted decisions are explicitly `fail`; this analysis cannot convert
them to `pass`.

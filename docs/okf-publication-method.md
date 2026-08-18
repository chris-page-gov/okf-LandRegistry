# OKF publication method

This repository uses the OKF publication-method v1 lifecycle contract. The
contract records how authored sources, generated projections, documentation,
tests, release evidence, deployment and browser verification depend on one
another. It complements the semantic contract in `okf.semantic.json`; it does
not change the meaning of the graph or extend the frozen Bundle Wiki profile.

The machine-readable contract is [`okf.publication.json`](../okf.publication.json).
Its schema is published by OKF Explorer at
<https://chris-page-gov.github.io/okf-explorer/profile/publication-method/v1/>.
Commands are declarations to review against this repository's guidance before
execution, not instructions to execute automatically.

## Source and generated boundaries

The source family is the frozen, checksummed HM Land Registry public-metadata
snapshot and its governed registers. Acquisition is a separate authorised
operation. The normal build is offline and must not refresh an upstream source.
The contract links that source boundary to the semantic graph, Explorer runtime,
checksum manifest and validation evidence without changing any v0.3.0 bytes.

The v0.3.0 release evidence remains bound to its historical commit and digest.
Adopting this lifecycle contract does not reopen owner approval or turn a later
maintenance commit into release evidence.

## Change and publication sequence

1. Classify the changed paths with `scripts/change_impact.py`.
2. Update controlled implementation, documentation and `CHANGELOG.md` in the
   same change. Dependency updates have no blanket exemption.
3. Run the bounded routine checks or the full dependency closure selected by
   the impact report. Unknown paths fail closed.
   The initial lifecycle adoption has one narrow bootstrap exception: only the
   three named publication-contract, lockstep-checker and lockstep-test paths
   may be unmatched, and every changed path must belong to the enumerated
   adoption set. This routes that later maintenance through the immutable
   v0.3.0 evidence anchor rather than treating the maintenance commit as new
   release evidence.
4. For a full run, build once from the frozen snapshot and verify that the
   resulting `bundle/` is byte-identical to the committed candidate.
5. Run the independent full test branch in parallel. Deployment waits for both
   the exact-byte verification and full tests.
6. Deploy only through the owner-authorised manual workflow, using the exact
   approved commit and release root. The workflow uploads the verified bytes;
   it does not rebuild them.
7. Complete the bounded real-browser identity and journey check against the
   deployed URL. A public URL is not verified until that succeeds.

The shared estate registry records this repository's adoption state and any
remaining work. Integration of the reusable exact-commit installed-Chrome
receipt is still a declared limitation; the existing version-scoped runbook
remains authoritative until that integration is complete.

# Beginner checklist for the Land Registry G9 owner decision

## What G9 means

Land Registry G9 is the project owner's decision that one exact, already
tested set of bytes may proceed to release-candidate deployment.

You do not “pass” G1–G8 by saying that you accept them. Those gates are factual
claims supported by tests and independent reviews. Your job is to read their
results and decide whether the remaining risks are acceptable for this
labelled proof of concept.

G9 is also not final public verification. A G9 approval permits one RC
deployment. The exact deployed URL must then pass the real-browser identity
and journey check before it may be shared as verified or promoted to final
v0.2.0.

## Exact candidate you will be deciding on

| Item | Exact value |
|---|---|
| Version | `0.2.0` |
| Candidate commit | `40482c865dc4332162f1e93756d94ca93abe3559` |
| Bundle release root | `a3e0bdf7846893ce29255f6f20a509dad18ef2b367ba3dfbe48c28191377a704` |
| Candidate archive SHA-256 | `7f92e51cfa75fee9e3517788a0bd1b9c36de34525ea18d13732da3d24b61120d` |
| Profile-pack root | `47f0a5c1a89c78cdeda8e57623db46036753de752766588874fa5835a36a0d95` |
| Question-suite SHA-256 | `489ce0d6b6669be49a230b991913ee252a89bd0088949818cfb20f12b698e95d` |
| Pinned Explorer commit | `afd940b6de2d09809ae94dfc77c128936ac7928a` |
| Planned canonical route | **Unverified:** `https://chris-page-gov.github.io/okf-LandRegistry/` |

If any value changes, stop. The existing review and approval cannot be moved
to the new bytes.

## What to read

Read these in order:

1. the [independent G6 review](../../validation/reviews/passed-a3e0bdf7-release-review/g6-independent-review.json);
2. the [independent release recommendation](../../validation/reviews/passed-a3e0bdf7-release-review/independent-release-recommendation.json);
3. the [exact G1–G8 receipt index](../../validation/v0.2.0-pre-g9/pre-g9-evidence.json);
4. [`docs/product-contract.md`](../../docs/product-contract.md);
5. [`governance/risk-register.json`](../../governance/risk-register.json);
6. [`governance/rights-review.json`](../../governance/rights-review.json); and
7. the [beginner release tracker](../../docs/v0.2.0-release-tracker-and-publication-guide.md).

Check that the independent recommendation says `recommend_approval`, not
`withhold_approval`, and that it covers this exact root and archive. Check that
every G1–G8 receipt says `pass` with no failure or waiver.

## Exact G1–G8 receipt hashes

The pre-G9 index SHA-256 is
`23017105c0926a11f9525e3a244756730924e3a089bcd618d44c995d4e848d0f`.
It contains these exact receipt hashes:

| Gate | Beginner meaning | Receipt SHA-256 |
|---|---|---|
| G1 | Is the discovery profile valid and internally closed? | `0531c3487a4473854c977292e471e87d2b94fd9e7a8e39418fd74a1029990d95` |
| G2 | Are the frozen source observations complete and honestly bounded? | `0a99cfabcc7b5aaadd921cbd6dac0bdc0286afc9dadab742b61356df9d751e72` |
| G3 | Are rights, access, privacy and prohibited-content controls satisfied? | `168d418251bdc2e4d43240d95cfc7af71928b9aea2e791d196eae88eb13c9e1b` |
| G4 | Is the OKF/data bundle structurally valid and usable by the pinned Explorer? | `5846810cd7e1dd9969f4be1af38acb2c648ed294dc5f32d222287c46823fb2bb` |
| G5 | Does independently reviewed search evaluation pass its safety and retrieval thresholds? | `4c84f1b902c36c8151fb43f615f057c9fccefbe574afa0ae80a6770978eef1a2` |
| G6 | Do the user-facing, accessibility, security and performance checks pass at PoC assurance level? | `aa2a554fe5745f2d1e626516445415efb1fc035b65e06f0769f8d0280d087788` |
| G7 | Can the candidate be rebuilt twice with identical bytes and reconciled changes? | `57fb28fe8cc78d99e495b535fea0c71e5bd6adb03045d1a12c3bddd2851691ba` |
| G8 | Is the archive, dependency/workflow provenance and SBOM complete? | `117403872d62ea8cd2503245b606d0dfee16690553c8f6835d21870fdf8990ef` |

Every receipt records `status: pass`, no failure and no waiver. The index
records `ready_for_owner_review`; it is not G9 approval.

## Claims you would authorise

The release may say only that it is:

- an independent AI-generated proof of concept;
- a bounded discovery catalogue of public HM Land Registry material;
- metadata-only;
- generated from named, dated source observations;
- reproducible and checksum-bound; and
- tested against the exact pinned Explorer compatibility window.

## Claims you would not authorise

The release must not claim:

- that it is produced, operated or endorsed by HM Land Registry;
- legal advice, proof of ownership, priority or exact boundaries;
- completeness across HMLR's public estate;
- that public visibility means open reuse or anonymous service access;
- current fees, availability or legal/operational status beyond the dated
  source observations;
- Welsh parity;
- participant-validated user research;
- WCAG conformance; or
- completed independent human domain, legal, licence, privacy, security or
  accessibility assurance.

## Residual risks you must review

Review every entry `RISK-001` through `RISK-017` in
`governance/risk-register.json`. In particular:

- users may mistake discovery metadata for legal evidence;
- source pages may have changed since the 29 July 2026 cutoff;
- coverage remains bounded and incomplete;
- language and Welsh discovery remain limited;
- the project has no representative-user study; and
- independent review is AI-agent review, not a human audit;
- a structurally valid bundle could still be unusable in its declared
  consumer, controlled here by the pinned-Explorer journeys; and
- a change-impact heuristic could omit work or reuse stale evidence,
  controlled here by fail-closed all-gates selection and manual review.

G9 does not erase these risks. It records that you have seen them and accept
them for a clearly labelled RC under the stated controls.

## The decision choices

You may:

- approve the exact candidate for one RC deployment;
- reject it; or
- defer while asking for more evidence.

Approval does not authorise a rebuild, changed bytes, a different URL, silent
waivers or immediate final promotion.

## Exact approval statement to use after prerequisites pass

If, and only if, the independent G6 decision passes and the independent
release reviewer recommends approval, you can send:

> I, Chris Page-PoC, acting as project owner, approve the independently
> produced HM Land Registry public-estate OKF proof-of-concept v0.2.0
> candidate commit
> 40482c865dc4332162f1e93756d94ca93abe3559, bundle release root
> a3e0bdf7846893ce29255f6f20a509dad18ef2b367ba3dfbe48c28191377a704
> and candidate archive SHA-256
> 7f92e51cfa75fee9e3517788a0bd1b9c36de34525ea18d13732da3d24b61120d
> and pre-G9 G1–G8 receipt-index SHA-256
> 23017105c0926a11f9525e3a244756730924e3a089bcd618d44c995d4e848d0f
> for one release-candidate deployment to the planned canonical route. I
> have reviewed the exact G1–G8 receipts, permitted and prohibited claims,
> source cutoff, rights statement, all residual risks RISK-001 through
> RISK-017, and the absence of completed independent human audits and user
> research. This approval is conditional on the deployed URL serving the
> identical bytes and passing the real-browser identity and journey check
> before it is shared as verified or promoted.

That statement is specific enough to become the G9 owner input. “I accept all
gates” by itself is not: it omits the bytes, risks, claims and deployment
condition.

## What happens after approval

Approval allows the maintainer to:

1. assemble and validate the exact G1–G9 evidence;
2. set `OKF_RELEASE_ROOT_SHA256` to the approved full root;
3. merge the reviewed evidence;
4. create one annotated `v0.2.0-rc.1` tag and prerelease using the existing
   candidate archive; and
5. deploy the reviewed bundle.

The exact public route then receives a 60-second, tool-first real-browser
identity and journey check. A failure is reported immediately and the route
remains unverified. It does not silently trigger a rebuild.

Only a passing public receipt permits byte-identical final promotion.

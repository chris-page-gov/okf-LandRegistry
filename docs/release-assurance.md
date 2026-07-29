# Release assurance

Status: v0.2.0 AI-generated PoC release contract. The authoritative G1–G8
states and G9 owner decision are published under `validation/` and bind one
exact candidate.

## Evidence rule

Every gate produces a machine-readable receipt containing:

- gate and policy version;
- repository commit and candidate root digest;
- input snapshot and domain-profile root digests;
- tool/validator names and versions;
- start/end times for the check, without injecting them into governed output;
- checks performed, results, warnings and evidence paths;
- reviewer identity or role where manual review is required; and
- final `pass`, `fail` or `not_run` state.

A receipt from a different digest is not evidence for the candidate.
Warnings require a written disposition; a hard failure cannot be waived.

## Gates

| Gate | Required evidence | Pass criterion | v0.2.0 acceptance |
|---|---|---|---|
| G0 — decision readiness | decision register, consumer lock, dependency graph and named owners | no blocking decision open; release identity remains explicitly candidate until G9 | required before G1–G8 evidence is assembled |
| G1 — discovery profile | `VAL-DOMAIN-PROFILE`, checksums and pack root | JSON/YAML schema-valid and equivalent; references closed; pack rehashes | exact-candidate receipt required |
| G2 — source snapshot | `VAL-SNAPSHOT`, terminal-outcome and coverage ledgers | all envelopes rehash; one outcome per expected item; omissions explicit | exact-candidate receipt required |
| G3 — rights, privacy and safety | `VAL-RIGHTS`, rights review and sampled records | zero prohibited content, secrets, signed URLs or personal-level records; every record has access/rights state | exact-candidate receipt required |
| G4 — OKF and data integrity | `VAL-OKF`, `VAL-DATA-PLANE` and `VAL-EXPLORER-CONSUMER` | schemas, identities, references, paths, counts, shards and checksums pass; the pinned Explorer loads the descriptor, indexes and selected record | exact-candidate receipt required |
| G5 — evaluation | `VAL-EVALUATION`, independently reviewed questions | zero hard failures; MRR ≥ 0.80; Recall@10 ≥ 0.90; source and caveat coverage = 1.00 | exact-candidate receipt required |
| G6 — user-facing quality | `VAL-ACCESSIBILITY`, `VAL-EXPLORER-CONSUMER`, security and performance receipts | declared automated and assisted journeys pass in the authored site and pinned Explorer; no critical security issue; budgets met | exact-candidate receipt required |
| G7 — reproducibility | `VAL-REPRODUCIBILITY`, `VAL-CHANGE-IMPACT` and clean-build diff | two clean builds from frozen inputs are byte-identical; changed paths reconcile with predicted planes and gates | exact-candidate receipt required |
| G8 — package integrity | release manifest, SBOM/provenance where applicable, checksums and public-route plan | every artifact is digest-bound; workflow, consumer and dependency provenance are recorded | exact-candidate receipt required |
| G9 — independent review and owner approval | review record and recorded release decision | owner approves exact version, digest, residual risks, claims and canonical URL | explicit exact-digest decision required |

“Pending” and “not run” are not passes.

The release and gate reviewer identities for v0.2.0 must be disclosed in the
exact-candidate receipts. AI-agent review is not independent human legal,
licence or accessibility assurance; `RISK-015` remains a residual risk for
this proof of concept.

## Hard failures

Any of the following blocks publication:

- legal, ownership, priority or exact-boundary advice presented as a bundle
  conclusion;
- restricted, bespoke or unknown rights presented as open or OGL;
- catalogue modification presented as data currency or coverage;
- a restricted service called or described as an anonymous public API;
- a credential, certificate, token, signed URL, personal-level record or user
  upload in public output;
- a result without resolvable authoritative source and observation;
- candidate or untested evidence relabelled verified;
- unsafe redirect, path traversal, identifier collision or checksum mismatch;
  or
- a critical accessibility or security failure.

Aggregate scores never average away a hard failure.

## Evaluation thresholds

The first-release suite contains 24 reviewed questions spanning personas,
source families, access/rights states, stale/conflicting metadata, spatial
caveats, Welsh access and unsafe/unanswerable cases. Before G5 can pass, a
reviewer independent of the retrieval implementation verifies expected
propositions and near misses against the frozen snapshot. The retained
first-release expectations were reviewed by an independent AI agent and must
be rerun against the v0.2.0 candidate; this is explicitly not human domain
assurance.

Required thresholds:

- zero hard failures;
- complete question → story → persona → source/evidence traceability;
- primary-target mean reciprocal rank of at least 0.80;
- Recall@10 of at least 0.90 on answerable questions;
- every declared expected target or governed source alias present in the top 10
  for its question;
- required source resolution and caveat coverage of 1.00; and
- no new critical category in the second held-out adversarial pass.

## Release record

An approved release record must name:

- version, commit and candidate digest;
- profile and source snapshot digests;
- canonical repository and Pages URL;
- exact artifact manifest;
- pass receipts for G1–G8;
- known limitations and residual risks;
- source observation cutoff and supported refresh policy;
- licence/attribution statement; and
- owner decision closing `DEC-RELEASE`.

The public site must display the version, research cutoff, candidate/release
state and known limitations. A successful CI deployment is not by itself a
release approval.

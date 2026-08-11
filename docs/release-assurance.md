# Release assurance

Status: v0.3.0 **approval-neutral candidate-byte** release contract. Existing
final receipts under `validation/` are historical v0.2.0 evidence; they do not
bind, approve or release changed v0.3.0 bytes. Candidate bytes do not assert a
current G1–G9 or release state. Version-scoped exact-identity evidence,
independent review and the Land Registry G9 owner decision are authoritative.

Use the
[v0.3.0 release tracker and assurance runbook](v0.3.0-release-tracker-and-assurance-runbook.md)
to record the candidate identity and execute the dependency-ordered checks.
The [validation evidence layout](validation-evidence-layout.md) separates
immutable historical receipts from version-scoped v0.3.0 evidence.

## Which gate catalogue?

This document defines the project-specific **Land Registry release-evidence
gate catalogue v1**, represented by `okf-gate-receipt.v1` receipts. It is not
the generic Foundry G0–G9 catalogue.

Always qualify a gate reference when the surrounding context is not
unambiguous. For example:

- `Land Registry G5 — evaluation`;
- `Land Registry G9 — independent review and owner approval`;
- `Foundry G8 — RC and public validation`; and
- `Foundry G9 — promotion`.

The two catalogues organise evidence at different levels and do not have a
one-to-one number mapping:

| Generic Foundry gate | Land Registry evidence that contributes | Important difference |
|---|---|---|
| Foundry G0 — domain contract | Land Registry G0 and G1 | The project separates decision readiness from the validated discovery profile. |
| Foundry G1 — tiny fixture | Land Registry G4 and G6 | Producer, malformed-descriptor and pinned-Explorer fixture evidence is distributed across integrity and user-facing quality. |
| Foundry G2 — acquisition | Land Registry G2 | This is the closest direct match. |
| Foundry G3 — core and semantic integrity | Land Registry G3 and G4 | The project separates rights/privacy/safety from structural and semantic integrity. |
| Foundry G4 — Explorer and federation | Land Registry G4 and G6 | The project is not a federation; consumer integrity and browser journeys are recorded separately. |
| Foundry G5 — optional enrichment | Not applicable for the v0.3.0 candidate | `DEC-ENRICHMENT` prohibits public model-assisted enrichment in this candidate. Land Registry G5 is unrelated: it is evaluation. |
| Foundry G6 — evaluation | Land Registry G5 | This is the numbering collision most likely to cause confusion. |
| Foundry G7 — frozen candidate | Land Registry G3, G6, G7 and G8 | Security, accessibility, performance, reproducibility and package evidence are split across project gates. |
| Foundry G8 — RC and public validation | Land Registry G6 and G8, followed by the post-approval public browser check | A local G1–G8 pass does not prove the undeployed public URL. |
| Foundry G9 — promotion | Post-approval byte-identity comparison and final promotion | Land Registry G9 happens earlier and means owner approval of the candidate; it is not final promotion. |

The project workflow therefore requires both:

1. Land Registry G1–G8 evidence and Land Registry G9 owner approval before
   deployment; and
2. the generic Foundry G8 public check and Foundry G9 byte-identical promotion
   after an RC is deployed.

The
[v0.2.0 release tracker and public website guide](v0.2.0-release-tracker-and-publication-guide.md)
is retained as historical release evidence and must not be used to approve the
v0.3.0 candidate.

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

Before G9, `scripts/assemble_release_evidence.py --pre-g9` may assemble the
exact G1–G8 receipts and a `ready_for_owner_review` index from an input that
contains no owner decision. This lets the owner inspect the actual receipt
hashes before deciding. The pre-G9 index is not a release-evidence manifest,
contains no G9 record and cannot authorise deployment or publication. A later
approved G1–G9 assembly must reproduce the same G1–G8 receipt bytes.

Both the command line and Python writer APIs require an explicit directory.
For governed version `<version>`, the only writable locations are
`validation/candidate-v<version>/pre-g9` and
`validation/candidate-v<version>/final-g9`; root-level v0.2.0 evidence remains
read-only history. The writer stages complete files, publishes each with
no-follow/no-clobber descriptor operations and publishes the manifest last as
the set's commit marker. It rolls back links created by a failed invocation.
POSIX has no portable transaction spanning several existing files, so readers
must treat the manifest—not directory existence—as the evidence-set boundary.

The final assembler accepts `okf-release-assembly-input.v2` only. Its
`owner_approval.binding` is the decision the owner actually makes: it must
name the governed version and canonical URL, complete candidate identity, the
path and SHA-256 of the reviewed pre-G9 manifest, all eight exact receipt
hashes, every approved public claim, the complete risk-ID set plus SHA-256 of
`governance/risk-register.json`, the exact human-audit disclosure, the exact
independent-review object, and the path and SHA-256 of its separate review
evidence artefact. The version and canonical URL are not free-text approval
fields: the assembler derives them from `source/build-config.json`, whose
SHA-256 must itself occur exactly once in the build receipt's governed-input
inventory. The build refuses a publication-base argument that differs from
that governed value, and the build receipt repeats the exact version and
publication base for the release checker to cross-check. The assembler
rehashes each referenced file, compares the pre-G9
receipts with the newly assembled receipts and rejects a missing, stale,
partial or generic approval. It copies the verified binding into the G9
record; it does not add approval scope after the owner's decision.

The independent-review evidence uses
`okf-independent-release-review-evidence.v1`. It binds the complete candidate,
the exact review object and its `reviewed_at` time, the pre-G9 manifest digest,
approved claims and residual-risk IDs. A review copied into G9 without this
separately digest-bound artefact is not accepted. Identities and roles must be
trimmed, non-blank and control-free. The owner cannot also be an independent
gate or release reviewer, and one identity cannot change kind or independence
between gates. All release chronology must use strict UTC timestamps and be
ordered: each reviewed-check completion is no later than its matching
reviewer's time, which is no later than the gate `executed_at` time; every gate
is no later than the pre-G9 manifest, release review and owner approval; and
owner approval is no later than the final evidence-manifest time. A completed
human audit is not accepted for a new candidate until a separate digest-bound
human-audit workflow exists; `not_completed` must remain disclosed as a
reviewed residual risk.

For a `0.3.0` release record, `scripts/check_release_evidence.py` independently
enforces the same boundary. It rejects a schema-valid generic approval,
rehashes the owner-bound pre-G9 manifest, every referenced G1–G8 receipt, the
independent-review evidence and the governed risk register, and compares the
bound version, URL, candidate, review, claims, risks and human-audit disclosure
with the final G9 record. Digest-bound JSON and evidence are read once into a
bounded byte buffer; their SHA-256 and parsed document come from those same
bytes. Assembly inputs, schemas and evidence references must resolve through
non-symbolic-link directories to regular files using secure directory-relative
opening; the tooling fails closed where that facility is unavailable.

Exact byte binding does not authenticate the person or agent named by an
identity string and is not a digital signature. `RISK-018` records this as a
distinct accepted gap. The controlled owner/reviewer procedure remains
necessary, and neither the manifest nor this documentation may claim signed or
cryptographically authenticated approval.

Historical v0.1.0 and v0.2.0 unbound evidence remains readable only for its
recorded complete candidate identity *and* its frozen G9 and evidence-manifest
SHA-256 pair. The pairs are respectively
`6a50ff8e542c59d7d270aff20a6ce0582e58e8d66a350361cc456bcd1474657d` /
`746e5a840fbacb195d738d5be17246da1f2969cce2743b1d3acc072ba8d13b62`
and
`46aadf4563f878285de3155124870be7144ec91f9f00e825c0e297b5618e2c11` /
`facafcc21bf0b69a8b97a47df0cfd334b0a45f30680c6dc69c9c623f5423be9f`.
Changing a candidate, version string or either evidence digest does not
activate the exception and cannot satisfy or approve v0.3.0. Historical
admission is calculated only from the actual parsed G9 and manifest byte
buffers, never from digest strings supplied by a caller.

## Gates

| Land Registry gate | Required evidence | Pass criterion | v0.3.0 candidate acceptance |
|---|---|---|---|
| G0 — decision readiness | decision register, consumer lock, dependency graph and named owners | no blocking decision open; release identity remains explicitly candidate until Land Registry G9 | required before Land Registry G1–G8 evidence is assembled |
| G1 — discovery profile | `VAL-DOMAIN-PROFILE`, checksums and pack root | JSON/YAML schema-valid and equivalent; references closed; pack rehashes | exact-candidate receipt required |
| G2 — source snapshot | `VAL-SNAPSHOT`, terminal-outcome and coverage ledgers | all envelopes rehash; one outcome per expected item; omissions explicit | exact-candidate receipt required |
| G3 — rights, privacy and safety | `VAL-RIGHTS`, rights review and sampled records | zero prohibited content, secrets, signed URLs or personal-level records; every record has access/rights state | exact-candidate receipt required |
| G4 — OKF and data integrity | `VAL-OKF`, `VAL-DATA-PLANE` and `VAL-EXPLORER-CONSUMER` | schemas, identities, references, paths, counts, shards and checksums pass; the pinned Explorer loads the descriptor, indexes and selected record | exact-candidate receipt required |
| G5 — evaluation | `VAL-EVALUATION`, independently reviewed questions | zero hard failures; MRR ≥ 0.80; Recall@10 ≥ 0.90; source and caveat coverage = 1.00 | exact-candidate receipt required |
| G6 — user-facing quality | `VAL-ACCESSIBILITY`, `VAL-EXPLORER-CONSUMER`, security and performance receipts | declared automated and assisted journeys pass in the authored site and pinned Explorer; no critical security issue; the predeclared bounded-lazy constraint and any candidate-specific numerical budgets are met | exact-candidate receipt required |
| G7 — reproducibility | `VAL-REPRODUCIBILITY`, `VAL-CHANGE-IMPACT` and clean-build diff | two clean builds from frozen inputs are byte-identical; changed paths reconcile with predicted planes and gates | exact-candidate receipt required |
| G8 — package integrity | release manifest, SBOM/provenance where applicable, checksums and public-route plan | every artefact is digest-bound; workflow, consumer and dependency provenance are recorded | exact-candidate receipt required |
| G9 — independent review and owner approval | review record and recorded release decision | owner approves exact version, digest, residual risks, claims and canonical URL | explicit exact-digest decision required |

“Pending” and “not run” are not passes.

The locked Explorer `v0.6.1` runtime receipt hashes every supplied bundle file
in the runner's recursive English `localeCompare` path order. Formal G5
verification must reproduce that consumer algorithm rather than Python's
bytewise path order. The verifier uses an embedded, regression-tested
printable-ASCII collation key rather than relying on operating-system locale
packages; non-ASCII bundle paths fail closed. The receipt tree includes
`CHECKSUMS.sha256`; this is not self-referential because that file contains
the separate governed release root calculated over the other bundle members,
not the runtime tree digest.

The class-to-route sidecar is assessed only as a derived delivery index. Its
receipt must bind the canonical semantic-graph root and IRI-to-route registry
root and prove complete route-bearing-node coverage; it cannot supply class
membership, ontology authority or inference evidence for any gate.

The release and gate reviewer identities for the v0.3.0 candidate
must be disclosed in fresh exact-candidate receipts. AI-agent review is not
independent human legal, licence or accessibility assurance; `RISK-015`
remains a residual risk for this proof of concept.

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

The candidate suite contains 24 questions spanning personas,
source families, access/rights states, stale/conflicting metadata, spatial
caveats, Welsh access and unsafe/unanswerable cases. Before Land Registry G5
can pass, a
reviewer independent of the retrieval implementation verifies expected
propositions and near misses against the frozen snapshot. The retained
v0.2.0 expectations were reviewed by an independent AI agent, but the suite
and both locked-Explorer journeys must be rerun and independently reviewed
against the exact v0.3.0 candidate. Historical evidence is
explicitly not human domain assurance and cannot approve changed bytes.

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
- exact artefact manifest;
- pass receipts for Land Registry G1–G8;
- known limitations and residual risks;
- source observation cut-off and supported refresh policy;
- licence/attribution statement; and
- owner decision closing `DEC-RELEASE`.

The public site must display the version, research cut-off, candidate/release
state and known limitations. A successful CI deployment is not by itself a
release approval.

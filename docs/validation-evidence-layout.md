# Validation evidence layout

The `validation/` directory contains evidence from more than one product
version. A nearby file is not automatically evidence for a candidate: version,
commit, bundle release root, governed inputs and consumer identities must all
match.

## Historical released evidence

- `validation/release-evidence.json`, `validation/release-record.json` and
  `validation/receipts/` are released v0.2.0 historical evidence.
- `validation/evidence/` also contains released v0.2.0 supporting material.
- `validation/rights-safety.json` is v0.1.0 historical evidence.
- `validation/candidate-v0.2.0/`, `validation/v0.2.0-pre-g9/`,
  `validation/public-v0.2.0/` and `validation/reviews/` preserve historical
  candidate, review and publication records.

These files remain useful as digest-bound history and regression inputs. Do
not rewrite, relabel or use them to assert that changed v0.3.0 bytes have
passed a gate.

## v0.3.0 candidate evidence

v0.3.0 diagnostics and exact-candidate evidence use
`validation/candidate-v0.3.0/`. A diagnostic result is not a gate receipt. The
candidate bytes retain `not_run` as their approval-neutral baseline; only
version-scoped evidence bound to the same frozen commit, bundle release root
and governed inputs records the authoritative current state.

The version-scoped layout is:

- `assembly-inputs/pre-g9.json` and `assembly-inputs/final-g9.json` contain
  explicit reviewed inputs;
- `evidence/` contains supporting exact-candidate observations and reviews;
- `pre-g9/` contains the G1–G8 review manifest and receipts; and
- `final-g9/` contains the final evidence manifest, G9 record and G1–G8
  receipts after the independent review and owner decision.

The assembler and checker require their output directory or manifest to be
named explicitly. Never direct v0.3.0 assembly at the root-level historical
v0.2.0 files, and never use `--replace` to change reviewed evidence.

Follow the dependency order and evidence requirements in the
[v0.3.0 release tracker and assurance
runbook](v0.3.0-release-tracker-and-assurance-runbook.md).

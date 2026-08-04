# Maintenance and reproducibility

Status: operating procedure for the v0.1.1 AI-generated PoC and later
digest-bound releases.

## Two-stage lifecycle

### Stage 1 — discovery and contract

1. Inventory source families and define per-family denominators.
2. Establish source, semantic, operational and decision authority.
3. Record scope, rights, privacy, access and material unknowns.
4. Define personas, competency questions, hard failures and acceptance gates.
5. Validate JSON/YAML equivalence and hash-lock the complete profile pack.
6. Obtain review of all blocking decisions.

A material change to the problem, audience, source family, content boundary,
rights model or architecture invalidates the lock and returns the work to
Stage 1.

### Stage 2 — deterministic build

1. Acquire bounded public metadata into immutable envelopes.
2. Reconcile each denominator to one terminal outcome per expected item.
3. Build a tiny positive, negative and degraded fixture first.
4. Normalise without model-assisted enrichment.
5. Generate bundle, site, ledgers, manifests and evaluation assets.
6. Run schema, integrity, safety, rights, accessibility and evaluation checks.
7. Rebuild in a clean environment and compare governed bytes.
8. Assemble digest-bound release evidence for independent review and owner
   approval.

## Reproducibility contract

A reproducible candidate records:

- source snapshot ID and per-envelope SHA-256;
- expected/acquired/excluded/failed/unresolved counts per denominator;
- source URL, status, media type and observation time;
- repository commit and clean/dirty state;
- runtime, dependency lock and validator versions;
- normalisation rules and policy versions;
- command, locale, timezone and deterministic environment controls;
- generated file manifest and SHA-256 values; and
- validation and evaluation receipts bound to the candidate root digest.

The same frozen inputs, code, configuration and supported runtime must produce
byte-identical governed outputs. Expected volatile data—timestamps, temporary
paths, unordered maps and remote state—must be excluded from generation or
derived from the frozen snapshot, never masked after the fact.

## AI usage and cost accounting

Maintain `governance/ai-model-usage.json` as an authored build input and expose
its digest-bound projection at `bundle/data/ai-usage.json`. At candidate freeze,
record task-surface tokens only from platform evidence and state the tracking
cutoff. Leave pre-tracking usage unavailable; do not infer an input/output split
from a total.

Subscription fee allocation, separately billed API spend and a rate-card
equivalent are different measures. Do not turn a subscription into an invented
per-token charge. A zero API value is permitted only for a declared scope in
which no separately billed, user-keyed API calls occurred; it is never evidence
that total delivery cost was zero. When the exact model or applicable rate card
is not evidenced, leave the equivalent amount and source null.

## Refresh policy

Refresh is per source family, not one global “latest” switch. Start a candidate
refresh when:

- the planned release calendar calls for one;
- a publisher changes a route, licence, terms, schema or access method;
- a denominator changes or an unresolved source becomes available;
- a critical official notice makes a current candidate misleading; or
- a security, privacy, rights or accessibility finding requires withdrawal.

Every refresh creates a new immutable snapshot. Previously released artifacts
remain reproducible and are not backpatched.

## Dependency-led change impact

Run the governed classifier before implementation so a correction begins with
an explicit affected surface. This is the operating procedure for `REQ-020`
and `VAL-CHANGE-IMPACT`:

```bash
.venv/bin/python scripts/change_impact.py \
  --base <reviewed-base-commit> \
  --head <candidate-commit>
```

For an uncommitted design check, provide every changed or expected generated
path explicitly:

```bash
.venv/bin/python scripts/change_impact.py \
  --path scripts/build.py \
  --path bundle/okf-explorer.json \
  --check
```

The canonical JSON report identifies predicted generated artifacts,
requirements, risks, focused test commands, validations, gates and whether
Stage 1 or manual review is required. Every selected gate is reported as
`not_run`; the classifier cannot carry forward a pass. `--check` exits
non-zero for an unknown path or an unexplained generated change. A Stage 1 flag
is a workflow decision, not a command failure, and must still be acted on.

Use the report in two passes:

1. classify the authored change and review its predicted outputs and gates;
2. rebuild from immutable inputs, then classify the complete authored and
   generated diff together.

Every changed generated path must match an output edge from at least one
changed authored input. An extra path is evidence of an incomplete graph,
unexpected generator behaviour or a hand edit; stop and investigate it. When
a source path, output path, focused test, validation ID or gate mapping changes,
update `governance/artifact-dependency-graph.json`, its tests and the related
documentation in the same reviewed change.

Test and validator files are declared as `validation_inputs`: they select the
relevant checks and gates without claiming to generate bundle bytes. If a
validator also becomes a governed build input, model that producer edge
separately and review the resulting receipt/checksum outputs.

The report selects work but cannot record a pass, waive a gate or approve a
release. Under the present exact-root evidence model, every changed governed
byte invalidates the old candidate identity even when the dependency graph
correctly narrows which checks need new execution evidence.

## Change classification

| Change | Required response |
|---|---|
| Editorial documentation correction | review links and checks; no source refresh if semantics are unchanged |
| Adapter or normalisation rule | rebuild from fixtures and frozen snapshot; compare semantic diff |
| New field or relationship | update schema, model, traceability and evaluation |
| Source route or denominator | repeat acquisition reconciliation and coverage review |
| Licence, terms or public projection | repeat rights/privacy review; owner sign-off |
| Persona, story or evaluation expectation | independent review; preserve prior baseline |
| Canonical identity or deployment | close `DEC-RELEASE`; regenerate identifiers and checksums |

## Roles and separation

- **Researcher:** records evidence and unknowns; does not approve release.
- **Adapter maintainer:** implements bounded acquisition and terminal outcomes.
- **Bundle maintainer:** owns deterministic normalisation and generation.
- **Rights/privacy reviewer:** reviews intended operations and public fields.
- **Evaluation reviewer:** independently verifies expected propositions and
  near misses.
- **Accessibility reviewer:** performs manual and assisted-technology checks.
- **Release owner:** accepts residual risk and approves the named digest.

One person may fill several roles for development, but the expected-answer
review and final release decision should be independent of the code that
produced the candidate.

## Maintenance checks

For each proposed release:

- validate the domain profile and its checksum root;
- inspect source and semantic diffs, not only file counts;
- verify redirects still end on allowlisted authorities;
- review unknown and prohibited rights states;
- sample every adapter and all error/outlier buckets;
- run evaluation by persona, source family and failure stratum;
- complete keyboard, zoom/reflow, no-JavaScript and screen-reader journeys;
- run dependency and workflow provenance checks;
- build twice in clean environments; and
- archive the candidate manifest, receipts and approval record.

## Incident and withdrawal

Withdraw or disable deployment when published output contains personal data,
credentials, signed URLs, prohibited content, materially false rights or
authority claims, or a critical unsafe redirect. Preserve the last safe release
and a minimal incident record. Fix the input or rule, rebuild from scratch,
re-run every affected gate and publish a new version; do not edit generated
files in place.

See [`SECURITY.md`](../SECURITY.md) and
[`release-assurance.md`](release-assurance.md).

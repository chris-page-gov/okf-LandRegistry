# Maintenance and reproducibility

Status: operating procedure for the v0.2.0 AI-generated PoC and later
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

### Offline semantic-profile locks

The build resolves semantic schemas only from reviewed local bytes. Bundle Wiki
v1 remains frozen under `profiles/bundle-wiki/v1/` and its vendor lock. The
additive Predicate Registry v2 extension is frozen separately under
`profiles/predicate-registry/v2/`, with the adjacent
`profiles/predicate-registry/v2.lock.json` binding both files. Its schema is
exactly 7,551 bytes with SHA-256
`037151379a1ec0cbfe0666d41592585a891a63929f1fcf2845d1eb3de8dd5069`;
the two-file identity is
`75e444a35fdfe28fc111b6f0490cb8a0d569d20c1e4b62410174ead2608d86c6`.

Before reading the v2 schema, the builder verifies the lock digest, complete
directory inventory, file sizes, file digests, aggregate identity, internal
schema references and the matching `consumer.predicate_registry` entry in the
Explorer v0.6.1 consumer lock. It never fetches a schema or context during the
build. The exact schema is copied into
`bundle/data/semantic/schemas/predicate-registry.v2.schema.json`; its resource
digest and the complete local-lock receipt are recorded in semantic validation
and the build receipt.

The same frozen inputs, code, configuration and supported runtime must produce
byte-identical governed outputs. Expected volatile data—timestamps, temporary
paths, unordered maps and remote state—must be excluded from generation or
derived from the frozen snapshot, never masked after the fact.

## AI usage and cost accounting

Maintain `governance/ai-model-usage.json` as an authored build input and expose
its digest-bound projection at `bundle/data/ai-usage.json`. At candidate freeze,
record task-surface tokens only from platform evidence and state the tracking
cut-off. Leave pre-tracking usage unavailable; do not infer an input/output split
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

Every refresh creates a new immutable snapshot. Previously released artefacts
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

The canonical JSON report identifies predicted generated artefacts,
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

The graph separates two exact inventories:

- top-level `build_inputs` are bytes or environment locks causally consumed by
  the deterministic build; the current 44 patterns expand to 71 files and only
  these files appear in the build receipt; and
- stage `inputs` plus `validation_inputs` form the complete 153-file candidate
  control surface reported by the classifier, bound by the candidate commit
  and G1–G9 evidence.

Test and validator files are normally `validation_inputs`: they select the
relevant checks and gates without claiming to generate bundle bytes. Workflow,
runbook, release-tool and other assurance-only changes likewise do not predict
bundle outputs. Every causal build-input change must predict `bundle/**`, the
build receipt and checksum manifest, then select build-semantics, bundle and
reproducibility checks.

`requirements-lock.txt` is causal because it provisions the exact Python
dependencies used by the build. `pyproject.toml` is not: the release build does
not install this repository as a Python package. Both remain protected
candidate controls. If a validator later becomes a causal build input, add it
to the explicit top-level role and review the resulting receipt and checksum
change.

### Causal build transaction

Before a release build, stage every reviewed authored input that the build is
intended to consume. Do not stage generated `bundle/` changes until after the
build. The builder expands the 44-pattern causal contract, rejects any
unindexed causal file, compares each worktree payload with its stage-0 Git blob
and freezes the 71 accepted inputs in memory. All subsequent repository reads
must resolve to that snapshot; a newly discovered undeclared read fails rather
than silently widening the receipt.

Before reserving the atomic publication swap slot, the builder projects every
fresh source relationship through the exact Explorer v0.6.1 reader-retention
model. It fails if any row or the full projection exceeds the pinned UTF-16
text ceiling. The generated runtime must later reproduce that exact source
measurement, preventing a stale live bundle from standing in for candidate
scale during pre-build validation.

The build also rejects ignored extras in vendored profile and CPSV-AP trees,
even though ordinary Git input enumeration excludes ignored files. The vendor
lock is an exact worktree inventory as well as a digest list. The evaluation
subprocess runs a copied evaluator and copied causal contracts from the frozen
transaction, so a late editor write cannot alter its result.

The complete fresh semantic document, relationship planes and bounded CPSV-AP
projection are validated before the build reserves an atomic publication swap
slot. In particular, the organisation's spatial target must resolve to the
governed England and Wales `dcterms:Location`, while the EU administrative
territorial unit class must remain at its authorised-zero-evidence state. This
prevents a stale generated bundle from concealing a source/profile
contradiction until late in candidate generation. The receipt must distinguish
this passing bounded local check from the official CPSV-AP Public Organisation
spatial ATU range, which is deliberately not claimed as satisfied.

Immediately before publication of `bundle/`, the builder rechecks the complete
stage-0 index and every captured file identity and payload. Any change aborts
while the previous generated directory is still intact. This is the expected
response to concurrent edits; stage the intended bytes and start a new build.

Replacing a live bundle requires `--previous-output` to name an exact,
absolute, initially absent path outside the repository on the same file system.
The builder creates the candidate at that path and atomically exchanges the two
directory names, leaving the previous live bundle at the selected recovery
path without any interval in which `bundle/` is absent. A first publication
uses atomic no-replace. The builder never deletes, prunes, moves or overwrites a
recovery bundle and never falls back to deletion plus ordinary rename. Each
reproducibility build needs a distinct swap slot; record every returned path.
Moving or deleting a retained bundle is a separate owner-authorised operation.
The concrete path is excluded from generated bytes and appears only as a stable
placeholder in the reproduction invocation.

The exact runtime is repository-local CPython 3.12.11 in isolated mode with
bytecode writes, the user site and external Python startup paths disabled. A
fresh external empty `0700` cache namespace prevents stale repository bytecode
from being imported. The environment is created `--without-pip` and populated
by an external installer using `--no-compile --require-hashes`; no bootstrap or
local distribution is admitted. Installed versions must equal
`requirements-lock.txt`. Every installed member except the single narrowly
defined `RECORD` self-entry requires its declared SHA-256 and exact size; the
self-entry is hashed independently. The complete site inventory rejects `.pth`,
customiser, bytecode, symbolic-link, special and unowned files. Unknown lock
syntax is rejected.

The in-process observer starts after Python startup and initial imports; it
verifies the runtime before release work continues but does not attest the
exact source bytes already executed. The executable pre-invocation check
rejects `.pth`, bytecode and customiser hooks and holds a cooperating
single-writer lock for the build. This is a fail-closed operational mitigation,
not cryptographic proof against an uncooperative concurrent mutator. A pre-site
staged launcher would be needed to close that remaining gap and is proposed for
a future contract revision.

The installed tree and its `RECORD` receipts are checked and may be retained as
separate local audit evidence, but are not embedded in the bundle: compiled
wheel paths and bytes legitimately differ between macOS and Linux. The build
receipt instead binds the portable lock digest, exact package/version identity,
assurance states and compressor golden value.
Deterministic gzip output is governed by a complete golden
vector rather than by a particular operating-system zlib version. Bounded Git
inventories, input counts, file sizes and aggregate bytes fail before large or
damaged inputs can be materialised without limit.

The report selects work but cannot record a pass, waive a gate or approve a
release. Under the present exact-root evidence model, every changed candidate
control invalidates the old candidate commit and its evidence even when it does
not alter bundle bytes and the dependency graph correctly narrows which checks
need new execution evidence.

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

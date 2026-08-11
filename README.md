# HM Land Registry public-estate OKF

An independent, metadata-only [OKF 0.2](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/3fcbb9f828c2f23d109c855ee403c3a4c81f3a96/okf/SPEC.md)
bundle for discovering HM Land Registry publications, services, datasets,
developer resources and public repositories.

Version 0.2.0 is the historical **AI-generated proof-of-concept release**. It was created
with AI assistance and approved by the project owner for exact release root
`a3e0bdf7846893ce29255f6f20a509dad18ef2b367ba3dfbe48c28191377a704`.
It has not had a
representative-user study or independent human domain, legal, licence or
accessibility audit. It is not produced or endorsed by HM Land Registry.
It does not provide legal advice, prove ownership, establish a boundary or
replace an official copy or live service.

## Open in OKF Explorer

[**Launch the HM Land Registry bundle in OKF Explorer**](https://chris-page-gov.github.io/okf-explorer/?bundle=https%3A%2F%2Fchris-page-gov.github.io%2Fokf-LandRegistry%2Fokf-explorer.json&view=reader#overview)

The v0.2.0 bytes were checked in a real browser for bundle identity, overview
loading, static search and selected-record hydration. The mutable routes below
may serve a later version; their current identity and release authority must
be checked against version-scoped, digest-bound evidence. Every version
remains an independent metadata-discovery PoC, not an HM Land Registry service
or a source of legal, ownership, priority or exact-boundary conclusions.

Direct publication routes:

- [GitHub Pages site](https://chris-page-gov.github.io/okf-LandRegistry/)
- [OKF Explorer descriptor](https://chris-page-gov.github.io/okf-LandRegistry/okf-explorer.json)
- [Historical v0.2.0 exact-digest release record](validation/release-record.json)

## v0.3.0 semantic candidate bytes

Version 0.3.0 is built as approval-neutral candidate bytes. It is locked to
Explorer v0.6.0 and the exact 16-file Bundle Wiki semantic profile recorded in
`contracts/okf-explorer.consumer-lock.json`. The build emits canonical YAML-LD
and equivalent JSON-LD from one deterministic assertion source, then projects
the same identities, routes and predicates into the large-corpus Explorer
runtime. The pinned Draft 2020-12 assertion schema is exactly 7,308 bytes with
SHA-256
`f69480328db4b64d678d9c50b6534d808000f7fb50a30e8cc9e3bf2facbcb8bc`.

The present candidate contains 22,226 direct triples, 22,226 reified
`okf:RelationshipAssertion` nodes and 22,226 runtime rows over 13 governed
predicates. Its runtime addresses 6,733 routes through 89 relationship chunks
and 256 route-locator buckets. The reviewed CPSV-AP mapping considers 11
candidate service records: 7 are mapped, 4 are explicitly excluded, and 19
evidence references support the decisions. CPSV-AP 3.2.0 is vendored and
digest-bound, but its official SHACL shapes have not been run; semantic
inference has not been run either.

The browser runtime is a bounded projection, not a second semantic authority.
It preserves every assertion identity and triple plus the core authority,
evidence, observation and rights links needed by Explorer. Repeated optional
provenance strings are kept in the canonical YAML-LD/JSON-LD evidence envelope
rather than duplicated in every browser row. The build exhaustively measures
all full default-plane and route-specific hydration plans against the exact
pinned Explorer limits and fails before writing a conformant receipt if any
row, chunk, route or aggregate ceiling is exceeded.

These counts and local validation results describe candidate bytes, not a
release decision. Independent review found P1 issues in source-field evidence,
CPSV adversarial binding and URL hardening; implementation corrections are in
the candidate and locally regression-tested. Candidate bytes do not
self-assert a current G1–G9 state, exact-digest approval, release readiness or
deployment authority. Those decisions exist only in version-scoped,
digest-bound external evidence. Browser links must be checked against that
evidence and the root actually served. See
[`docs/metadata-model.md`](docs/metadata-model.md),
[`okf.semantic.json`](okf.semantic.json) and the
[`v0.3.0 release tracker`](docs/v0.3.0-release-tracker-and-assurance-runbook.md)
for the field, authority, generated-output, tooling and assurance boundaries.

## What it covers

The source map starts with the [HM Land Registry organisation
page](https://www.gov.uk/government/organisations/land-registry), the
[land-registration data collection](https://www.gov.uk/government/collections/land-registration-data),
[Use land and property data](https://use-land-property-data.service.gov.uk/),
[Business Gateway developer documentation](https://landregistry.github.io/bg-dev-pack-redesign/),
the [official GitHub organisation](https://github.com/LandRegistry), linked
data and the public transactional-service guidance.

The acquisition lane freezes public metadata only:

- every result returned by the GOV.UK Search API for the HMLR organisation at
  the observation time;
- bounded GOV.UK Content API `locale` and `available_translations` metadata
  for already-enumerated routes;
- every public repository returned by the official LandRegistry GitHub
  organisation API;
- HMLR rows in the CDDO API Catalogue;
- reviewed records for high-value HMLR datasets, services, law, rights,
  accessibility and operational entry points.

The bundle never acquires title-register records, title plans, property search
results, personal data, bulk ownership or transaction rows, signed download
links, credentials, authenticated portal responses or paid documents.

## Two-stage Foundry workflow

This repository follows the
[two-stage authoring prompt pack](https://github.com/chris-page-gov/okf-explorer/blob/main/docs/okf-authoring-prompt-kit.md)
in `okf-explorer`:

1. **Domain warm-up (read-only).** The reviewed, evidence-bound output is in
   [`domain-profile/`](domain-profile/). JSON and YAML validate as equivalent
   against the vendored Explorer schema. Its status is `reviewed`, not
   release-approved. The current v0.3.0 profile pack root is
   `8233326eedcbffb2de2359c7bd700837f077b92a23ccc8c5ea9ce365ed64bc6a`.
2. **Bundle build.** Public metadata acquisition is separated from the offline,
   deterministic build. The build first proves producer contracts on a tiny
   fixture, then executes the exact pinned Explorer consumer against those
   bytes and the corpus candidate. Every generated byte is bound by
   `bundle/CHECKSUMS.sha256`.

The build prompt also requires a reviewed producer-to-public-route dependency
graph, per-plane digest roots, bidirectional producer/consumer compatibility
fixtures and exact post-deployment deep-link checks. The graph identifies the
transitive checks affected by a change; unknown paths and generated-only edits
fail closed to all release gates.

The profile architecture describes the bounded v0.3.0 candidate.
`DEC-RELEASE` records a historical v0.2.0 approval scoped only to commit
`40482c865dc4332162f1e93756d94ca93abe3559` and the exact release root named
above. It cannot approve v0.3.0; only version-scoped G1–G8 evidence and the
digest-bound G9 decision record the current v0.3.0 state.

## AI usage and cost disclosure

[`governance/ai-model-usage.json`](governance/ai-model-usage.json) is the
authored, machine-readable usage ledger. It records measured task-surface
tokens only when the platform supplies evidence. Usage before goal tracking,
the allocation of a subscription fee and a rate-card equivalent remain
`null`/unavailable rather than being reconstructed or priced hypothetically.
Separately billed, user-keyed OpenAI API spend is reported as USD 0.00 for its
declared scope; that is not a claim that the subscription, staff time or total
production cost was zero. The generated site projects the ledger at
`data/ai-usage.json`.

## Repository map

```text
domain-profile/   Stage 1 report, profile, evidence and digest lock
research/         source-family discovery inventory
source/           curated catalogue and optional frozen acquisition snapshots
personas/         evidence-led task-based personas and user stories
evaluation/       24 calibration questions, candidate journeys and historical evidence
governance/       requirements, risks, rights and traceability
contracts/        pinned external-consumer contracts
docs/             product, architecture, provenance and operating guidance
pages/            authored static GitHub Pages experience
scripts/          acquire, build, validate and evaluate
schemas/          pinned profile and local control schemas
tests/            deterministic, safety, traceability and publication checks
bundle/           generated OKF control/data planes and Pages artefact
```

## Reproduce it

Exact candidate reproduction uses CPython 3.12.11 in a pip-free repository
virtual environment. The build verifies the isolated start-up flags, exact
hash-locked distribution set, RECORD hashes and sizes, absence of `.pth`,
customiser, bytecode and unowned site files, and a fresh external cache
namespace. The build receipt records only portable runtime identity; verified
platform-specific installed-tree detail belongs in separate local evidence so
Linux and macOS builds do not acquire different bundle roots merely because
their locked wheels differ.

```bash
set -euo pipefail
OKF_BASE_PYTHON="${OKF_BASE_PYTHON:?set the absolute CPython 3.12.11 executable}"
"$OKF_BASE_PYTHON" -I -c \
  'import platform; assert platform.python_implementation() == "CPython" and platform.python_version() == "3.12.11"'
test ! -e .venv
"$OKF_BASE_PYTHON" -B -m venv --without-pip .venv
"$OKF_BASE_PYTHON" -B -m pip --python .venv install \
  --no-compile --require-hashes -r requirements-lock.txt
.venv/bin/python -B scripts/check_domain_profile.py domain-profile/domain-profile.json \
  --equivalent domain-profile/domain-profile.yaml

# Refresh public metadata only when making a new, dated snapshot.
.venv/bin/python -B scripts/acquire.py \
  --observed-at 2026-07-29T09:19:15Z \
  --output-dir source/snapshots/2026-07-29T091915Z

# This step is offline. Freeze every authored causal input in the stage-zero
# index before building; stage generated and evidence surfaces separately.
git add -A -- . \
  ':(exclude,top)bundle/**' \
  ':(exclude,top)validation/**' \
  ':(exclude,top)dist/**'
git diff --cached --check
stage_check_cache="$(mktemp -d "${TMPDIR:-/tmp}/okf-python-cache.XXXXXX")"
chmod 700 "$stage_check_cache"
.venv/bin/python -I -B -X "pycache_prefix=$stage_check_cache" \
  scripts/check_release_transition.py staged-candidate
runtime_lock="${TMPDIR:-/tmp}/okf-landregistry-build.lock"
if ! mkdir -m 700 "$runtime_lock"; then
  echo "A governed Land Registry build already holds $runtime_lock" >&2
  exit 1
fi
cleanup_runtime_lock() { rmdir "$runtime_lock"; }
trap cleanup_runtime_lock EXIT INT TERM
test -d .venv
test ! -L .venv
test -x .venv/bin/python
if find .venv -type f \( -name '*.pth' -o -name '*.py[co]' -o -name sitecustomize.py -o -name usercustomize.py \) -print -quit | grep -q .; then
  echo 'The pre-invocation runtime contains a startup hook or bytecode file' >&2
  exit 1
fi
repository_parent="$(cd .. && pwd -P)"
recovery_parent="$(mktemp -d "${repository_parent}/okf-landregistry-build-recovery.XXXXXX")"
previous_output="${recovery_parent}/previous-bundle"
runtime_cache="$(mktemp -d "${TMPDIR:-/tmp}/okf-python-cache.XXXXXX")"
chmod 700 "$runtime_cache"
test ! -L "$runtime_cache"
test -z "$(find "$runtime_cache" -mindepth 1 -print -quit)"
.venv/bin/python -I -B -X "pycache_prefix=$runtime_cache" scripts/build.py \
  --snapshot-dir source/snapshots/2026-07-29T091915Z \
  --publication-base https://chris-page-gov.github.io/okf-LandRegistry/ \
  --replace \
  --previous-output "$previous_output"
test -d "$previous_output"
rmdir "$runtime_lock"
trap - EXIT INT TERM
git add -A -- bundle
.venv/bin/python -B scripts/check_release_evidence.py --staged-candidate
test_runtime_cache="$(mktemp -d "${TMPDIR:-/tmp}/okf-python-cache.XXXXXX")"
chmod 700 "$test_runtime_cache"
.venv/bin/python -E -s -B -X "pycache_prefix=$test_runtime_cache" \
  -m unittest discover -s tests -v
.venv/bin/python -B scripts/evaluate.py \
  --output validation/candidate-v0.3.0/evidence/evaluation-diagnostic.json \
  --k 10 \
  --min-expected-source-success-at-k 1.0 \
  --min-expected-target-recall-at-k 0.90 \
  --min-all-expected-target-success-at-k 1.0 \
  --min-mrr 0.80
```

The initially absent `previous_output` path is outside the repository but on
the same file system. The builder constructs the candidate there and performs
one atomic directory exchange: `bundle/` is always present, while the complete
previous bundle remains at the exact reported recovery path. The builder does
not delete, prune, overwrite or move that retained bundle. A later move or
deletion requires separate owner authorisation. Each repeat build must use a
different empty recovery path; the concrete path is excluded from generated
bytes and represented by a stable placeholder in the build receipt.

The in-process observer starts after Python startup and initial imports; it
verifies the runtime before release work continues but does not attest the
exact source bytes already executed. The executable pre-invocation check
rejects `.pth`, bytecode and customiser hooks and holds a cooperating
single-writer lock for the build. This is a fail-closed operational mitigation,
not cryptographic proof against an uncooperative concurrent mutator. A pre-site
staged launcher would be needed to close that remaining gap and is proposed for
a future contract revision.

That command is a deterministic calibration diagnostic, not Land Registry G5
acceptance.
Only a new independent review bound to the frozen v0.3.0 question suite,
bundle and consumer digests may generate the final acceptance receipt. The
existing `evaluation/acceptance-review.json` belongs to released v0.1.0
evidence and is not reusable. The builder digest-binds the v0.2.0 Explorer
journey manifests as historical regression inputs; they are not current
execution manifests for the v0.3.0 candidate.

Never edit `bundle/` by hand. Review source and profile changes, rebuild, then
compare the generated digest and evaluation receipt. Acquisition should be
rate-limited and rerun only for a declared new observation.

## Evaluation and release posture

The v0.3.0 calibration suite uses nine evidence-led persona hypotheses, twelve
stories and 24 traceable questions. The smaller suite is intentional: it
exercises the highest-risk distinctions without padding the full Explorer
scorer's 100-question reference target with invented questions. Expected
sources, propositions, executable forbidden targets and required caveat IDs
must receive a new independent review against the frozen v0.3.0 candidate.
Reviewer-owned held-out cases remain separate. This is AI-assisted review, not
participant research or human domain assurance.

Hard failures include exact-boundary claims, wrong licence or access statements,
catalogue dates presented as data currency, source-authority confusion,
restricted-service automation, unsupported completeness, inaccessible critical
tasks and loss of Welsh-language distinctions.

GitHub Actions validates, rebuilds and tests the exact artefact before the same
bytes are uploaded for GitHub Pages. Pages deployment is restricted to the
default branch and requires the repository variable
`OKF_RELEASE_ROOT_SHA256` to name the exact owner-approved release root. Pull
requests run verification without deployment.

For the current dependency-ordered Stage 1, Land Registry G5 and G9,
exact-candidate evidence and RC-to-public procedure, see the
[`v0.3.0 release tracker and assurance runbook`](docs/v0.3.0-release-tracker-and-assurance-runbook.md).
The
[`v0.2.0 release tracker and public website guide`](docs/v0.2.0-release-tracker-and-publication-guide.md)
is retained as historical release guidance.

## Rights and responsible use

Repository code and configuration are licensed under Apache-2.0. Original
documentation, evaluation fixtures, governance records and metadata are
licensed under CC BY 4.0; see [`LICENSE.md`](LICENSE.md). Source metadata,
documents and linked resources retain their own terms. Public or free access is
not treated as blanket Open Government Licence coverage. HMLR bespoke licences
and third-party Ordnance Survey, GeoPlace or Royal Mail rights may apply.
Consult the official record before reuse.

See [`docs/product-contract.md`](docs/product-contract.md),
[`docs/scope-and-coverage.md`](docs/scope-and-coverage.md) and
[`docs/sources-rights-and-ethics.md`](docs/sources-rights-and-ethics.md)
before extending the bundle.

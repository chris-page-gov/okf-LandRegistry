# Personas And User Stories

This directory contains the machine-readable, first-release candidate model of
who may use the HM Land Registry OKF bundle and which tasks the publication
should support.

The source of truth is
[`personas-and-user-stories.json`](personas-and-user-stories.json). The
corresponding reader-facing explanation is
[`../docs/personas-and-user-stories.md`](../docs/personas-and-user-stories.md),
and executable question and journey coverage is under
[`../evaluation/`](../evaluation/).

## Evidence Status

Every persona is a task-based hypothesis. The roles are grounded in services,
guidance, datasets, developer material or assurance duties visible in the
official source-family inventory. They are not demographic profiles and are not
presented as completed user research.

Do not add age, gender, ethnicity, disability, income, location, family status
or assumed technical ability. Accessibility and Welsh-language needs are
cross-cutting service requirements, not demographic claims.

A persona may move from `candidate` to a stronger status only when cited,
independent user research supports the change. Until then, evaluation results
show whether the publication supports the hypothesised task; they do not prove
that the persona model is complete.

## Traceability Contract

Stable IDs use these prefixes:

- `LR-P` — persona;
- `LR-S` — user story;
- `LR-Q` — candidate evaluation question;
- `LR-J` — executable interaction journey; and
- `HF-` — hard failure.

Every `LR-Q` question must appear in at least one story, and every story must
reference at least one persona. The explicit `traceability` array repeats this
mapping so a validator can fail when a question, story or persona becomes
orphaned.

When changing this catalogue, update all of:

1. `personas/personas-and-user-stories.json`;
2. `docs/personas-and-user-stories.md`;
3. `evaluation/questions.json`;
4. `evaluation/journeys.json`; and
5. `docs/evaluation.md`.

## Candidate Boundary

The first release contains 24 candidate competency and retrieval questions.
Expected propositions are not verified gold answers. They must be checked
against the named official sources at the release research cut-off and receive
independent review before being promoted.

The suite deliberately includes public, conveyancing, lending, data/GIS, API,
local-authority, provenance/licensing, accessibility and Welsh-language needs.
It is a bounded first-release suite, not evidence that all HMLR users or tasks
have been discovered.

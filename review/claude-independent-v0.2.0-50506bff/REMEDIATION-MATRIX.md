# Remediation matrix for the rejected `e07fafe2…` candidate

This matrix is a navigation aid, not proof that a finding is closed. The
independent reviewer must inspect and decide each row.

| Prior finding | Remediation in `50506bff…` | Evidence to inspect | Required reviewer decision |
|---|---|---|---|
| Fifteen Business Gateway endpoints were presented as anonymous/public and operational descriptions encouraged automation. | Every `businessgateway.landregistry.gov.uk` record is now `approved-professional-users`, `restricted-service`, `RIGHT-RESTRICTED`, with certificate/approval requirements, neutral metadata descriptions and `CAV-NO-RESTRICTED-AUTOMATION`. | `candidate/source/source-register.json`, `candidate/scripts/build.py`, `candidate/bundle/data/catalogue.json`, `candidate/bundle/data/rights.json`, build/bundle tests. | Confirm all 15 records fail closed and no text implies anonymous, open, free or unrestricted operation. |
| The public landing page claimed approval before G9. | Product maturity and publication authority are separate. The page says the bytes do not assert publication approval; approval remains digest-bound external evidence. | `candidate/source/build-config.json`, `candidate/pages/index.html`, `candidate/bundle/index.html`, `candidate/governance/rights-review.json`. | Confirm no candidate byte claims owner approval or public verification. |
| All 24 negative controls targeted the same absent `ros.gov.uk` URL. | Each question now has a present, question-specific, rank-bounded `must_not_retrieve` target; validation fails if a target is absent, overlaps an expected target or cannot be evaluated at `k`. | `candidate/evaluation/questions.json`, `candidate/scripts/evaluate.py`, `candidate/bundle/data/evaluation-report.json`. | Confirm every negative is a meaningful near miss and its maximum rank is appropriate. |
| v0.1.0 rights approval was projected into v0.2.0. | Rights review is candidate-scoped, `release_approved: false`, and explains that only external exact-digest evidence can authorise release. Public rights projection includes `release_authority` and `field_semantics`. | `candidate/governance/rights-review.json`, `candidate/bundle/data/rights.json`. | Confirm the current states are truthful and no earlier approval is inherited. |
| Source/caveat G5 metrics were asserted rather than measured; runtime journeys did not select the expected record or assert caveats. | Every record exposes governed caveat IDs. Twenty-six exact-Explorer journeys select expected records and visibly assert the union of all question-required caveats. Formal G5 now requires a passed exact-bundle runtime receipt. | `candidate/evaluation/explorer-search-calibration-v0.2.0.json`, `candidate/validation/candidate-v0.2.0/explorer-search-runtime-remediated-50506bff.json`, `candidate/scripts/evaluate.py`. | Confirm the source and caveat assertions really establish the question-level requirements. |
| Evaluator defaults were below governed thresholds. | Defaults are `k=10`, source success `1.0`, target recall `0.90`, all-target success `1.0`, MRR `0.80`. | `candidate/scripts/evaluate.py`, diagnostic report. | Confirm a default diagnostic enforces the declared retrieval thresholds while remaining non-formal without independent review. |
| Language vocabulary mixed display names and BCP-47 values. | Governed language values normalize to `en` and `cy`; unknown source metadata remains explicitly unknown. | `candidate/scripts/build.py`, language filter, catalogue records and tests. | Confirm no unsupported language value is invented and Welsh distinctions remain visible. |
| Governed documentation still used v0.1.0 status wording. | Current product documents are labelled v0.2.0 candidate and distinguish maturity from release approval. | `candidate/docs/`. | Confirm permitted and prohibited claims are consistent throughout. |
| Diagnostic ranking did not characterize the shipped Explorer worker. | Diagnostic lexical scoring remains labelled non-formal. Formal evidence now requires the exact locked Explorer worker and receipt. | Explorer consumer lock, search and product runtime receipts, `candidate/scripts/evaluate.py`. | Confirm the locked runtime rather than the baseline is used for formal source/caveat evidence. |
| Public rights projection omitted field semantics. | Projection includes explicit field semantics and release-authority explanation. | `candidate/bundle/data/rights.json`. | Confirm a public reader can distinguish record publication from service-use permission and release approval. |
| No-JavaScript landing counters showed dashes. | The initial HTML contains 2,203 records and 15 source families before enhancement. | `candidate/pages/index.html`, `candidate/bundle/index.html`. | Confirm core discovery status remains understandable without JavaScript. |
| Compatibility window was not stated. | The consumer lock certifies exact Explorer `0.5.7` only; widening requires fixture and full-candidate browser journeys. | `candidate/contracts/okf-explorer.consumer-lock.json`, runtime receipts. | Confirm the narrow window is explicit and evidence-backed. |

## New browser finding discovered during remediation

The first remediated search run passed 24 of 25 journeys and failed Q20
because one service record was incorrectly required to display a boundary
caveat belonging to a separate spatial-data record. The failed receipt is
preserved as
`explorer-search-runtime-remediated-dafdc945.json`.

Q20 is now represented by two selections: the Local Land Charges search
service for source/coverage caveats and the INSPIRE spatial dataset for the
boundary caveat. The fresh receipt passes all 26 journeys. Review whether this
split correctly proves the proposition rather than merely satisfying a test.

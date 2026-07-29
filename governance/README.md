# Governance controls

These files turn the reviewed domain profile into testable release controls:

- `requirements.json` states normative product and safety requirements;
- `traceability.json` maps those requirements to evidence, decisions,
  artifacts, risks and validation;
- `risk-register.json` records foreseeable harm, planned controls and release
  disposition; and
- `rights-review.json` records which metadata operations are permitted,
  conditional or prohibited for each source layer.

They are machine-readable inputs, not validation receipts. Their
`reviewed-scaffold` state means the control design has been documented; it does
not mean the generated bundle has passed a gate or is approved for publication.

## Authority

The domain profile and its evidence register are the discovery authority for
this scaffold. Current legislation, formal notices and publisher-operated HM
Land Registry sources remain authoritative for legal and operational facts.
If a governance file conflicts with a current source, stop the build, record
the conflict and return to Stage 1 review.

## Change control

Every requirement, risk and rights assessment has a stable ID. Do not reuse an
ID for a different meaning. Add a new entry or explicitly supersede the old
one. A change to scope, authority, public fields, acquisition, licence,
restricted-service treatment or release claims requires a fresh rights and
risk review.

Generated validation receipts should refer to these IDs and bind the exact
candidate and policy digests. Do not edit receipts or generated bundle files to
make a check pass; fix the source, rule or implementation and rebuild.

# CPSV-AP vendored profile

This directory contains a byte-exact, offline copy of the three CPSV-AP 3.2.0
artefacts used by the Land Registry semantic projection. The source is the
official [SEMICeu CPSV-AP release](https://github.com/SEMICeu/CPSV-AP/releases/tag/v3.2.0),
licensed under CC BY 4.0.

The local application is deliberately bounded to records that genuinely
describe public services. It does not make a public-service record legally
authoritative, infer eligibility or ownership, or extend CPSV-AP to datasets,
legislation and APIs where their native standards are more appropriate.

Do not edit the files under `3.2.0/`. Update them only by reviewing a new
upstream release, replacing the complete set and regenerating
`3.2.0.vendor-lock.json`.

# Schemas

## Vendored Explorer profile

`domain-profile.schema.json` is a byte-for-byte copy of the OKF Explorer
authoring schema observed on 29 July 2026 at:

`okf-explorer/profiles/authoring/v1/domain-profile.schema.json`

- Source SHA-256: `6cdf0882d306c89d8d3e8aef0d3daa5c7bcd954a0e833b06b49e493f112ca81f`
- Upstream project: <https://github.com/chris-page-gov/okf-explorer>

The copy makes profile validation reproducible without a sibling checkout.
Update it only through a reviewed schema-refresh change that records the old
and new digests and revalidates both profile representations.

## Local impact control

`artifact-dependency-graph.schema.json` is the local contract for
`governance/artifact-dependency-graph.json`. It constrains repository-relative
artifact and validation-input patterns, generated roots, test commands and
stage links to stable requirements, risks, validations and G1–G9 gates. The
impact checker performs the cross-file reference closure that JSON Schema
alone cannot express.

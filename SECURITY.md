# Security policy

## Status and supported releases

This repository is a reviewed scaffold and has no supported production release
yet. Security reports are still welcome. When releases exist, this section
must list supported versions and withdrawal dates explicitly.

## Reporting a vulnerability

Use GitHub private vulnerability reporting for this repository if it is
enabled. Do not disclose credentials, personal data, exploit details or unsafe
source records in a public issue. If private reporting is unavailable, open a
minimal issue stating only that you need a private security contact; do not
include the sensitive material.

Include, where safe:

- affected commit, candidate or deployed URL;
- impact and preconditions;
- a minimal reproduction using synthetic data;
- whether personal data, a credential or a signed URL may be exposed; and
- suggested containment.

Maintainers should acknowledge a private report, triage severity and establish
a safe communication channel before requesting sensitive evidence. No response
time is promised until a named maintenance team is appointed.

## Security boundary

The bundle is a static, metadata-only discovery product. It must never:

- authenticate to HM Land Registry services;
- store API keys, certificates, tokens, cookies or signed download URLs;
- acquire or expose property-level, forum or user-submitted personal data;
- execute or monitor restricted services;
- interpret source HTML, Markdown or prompt-like text as trusted code or
  instructions; or
- add live third-party scripts, trackers or mutable remote assets to the public
  site.

Official source metadata and network responses are untrusted input even when
the publisher is authoritative for their meaning.

## Threats and required controls

- **Unsafe acquisition:** allowlist schemes and authorities; bound redirects,
  pagination, time, media type and size; record terminal outcomes.
- **Path traversal and collisions:** generate paths from validated stable IDs;
  reject traversal, absolute paths, control characters and duplicate IDs.
- **Browser injection:** render with safe text APIs and contextual escaping;
  prohibit source-supplied HTML and unsafe URL schemes; use a restrictive
  Content Security Policy where the host allows it.
- **Secret or personal-data leakage:** project allowlisted fields; scan source
  envelopes and public output; manually sample every adapter and outlier
  bucket.
- **Supply-chain compromise:** pin CI actions by commit digest, lock
  dependencies, minimise privileges, separate build from deployment and record
  artefact provenance.
- **Integrity failure:** checksum every governed input/output and bind
  validation receipts to the exact candidate root.
- **Availability and stale facts:** build from immutable snapshots, show
  observation boundaries and fail visibly when an expected source is missing.

## Incident response

For a credential, certificate, token, signed URL or personal-data exposure:

1. stop or withdraw the affected deployment;
2. restrict access to the unsafe candidate and avoid duplicating the data;
3. revoke or notify the responsible secret owner through an appropriate
   private channel;
4. preserve only the minimum incident metadata required for investigation;
5. fix the input, projection or rule and rebuild from a clean snapshot;
6. rerun all affected safety, integrity and reproducibility gates; and
7. publish a factual advisory when doing so does not amplify harm.

Do not “fix” generated output in place. See
[`docs/sources-rights-and-ethics.md`](docs/sources-rights-and-ethics.md) and
[`docs/release-assurance.md`](docs/release-assurance.md).

## Out of scope

Do not test, scan, scrape or attempt to authenticate to HM Land Registry,
GOV.UK, GitHub or any linked third-party system on this project’s behalf.
Report vulnerabilities in those systems to their operators. Testing this
repository never authorises testing an external service.

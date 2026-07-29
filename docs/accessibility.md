# Accessibility

Status: WCAG 2.2 Level AA is the design target. Version 0.1.0 passed automated
and agent-assisted keyboard, reflow and no-JavaScript release journeys. It has
not had an independent human accessibility audit or representative-user test
and makes no conformance claim.

## Commitment and boundary

The GitHub Pages site is a static discovery interface for metadata and links.
Core discovery and navigation must work without JavaScript, a pointer, colour
perception, animation or a wide viewport. Machine-readable files are useful
alternatives but do not replace an accessible human interface.

## Design requirements

- Use semantic landmarks, a logical heading hierarchy and one descriptive
  page title.
- Provide a visible skip link and persistent keyboard focus indicators.
- Associate every input with an explicit label and useful instructions.
- Announce result-count changes without moving focus unexpectedly.
- Keep search and filter state in the URL where practical.
- Expose selected filters as text and provide an obvious reset action.
- Make the full record/card a sensible reading sequence; avoid nested
  interactive controls.
- Use link text that identifies the official destination and purpose.
- Never rely on colour alone for source authority, rights, access or status.
- Meet WCAG contrast requirements in default, hover, focus, visited, disabled
  and high-contrast/forced-colour states.
- Support 200% zoom and 320 CSS-pixel reflow without two-dimensional scrolling
  for ordinary content.
- Respect `prefers-reduced-motion`; essential state changes must not depend on
  motion.
- Give tables captions and headers, and provide a linear alternative for
  complex comparisons.
- Provide understandable error, empty, loading and degraded states.
- Keep source dates, caveats and legal-reliance warnings adjacent to the
  relevant result.
- Publish an accessibility statement with known limitations, test date,
  methods and a feedback route.

## Progressive enhancement

The serverless baseline must expose a navigable catalogue index, documentation
and raw entrypoints. JavaScript may add lexical search, filters, incremental
rendering and persisted state. If scripts or data shards fail, the page must
retain its purpose, official source links and explanation of alternatives.

No essential content may be present only in CSS-generated content, hover
states, canvas, client-side templates without fallback, or ARIA labels that
replace visible text.

## Test matrix

### Automated on every candidate

- HTML parsing and landmark/heading checks;
- unique IDs, labels, names, language and title;
- link and local asset integrity;
- rules-based accessibility scan;
- contrast token tests;
- forbidden interaction and unsafe DOM-pattern checks; and
- site operation with JavaScript disabled.

Automated checks are necessary but not evidence of conformance.

### Assisted inspection before release

- keyboard-only search, filtering, result traversal and source navigation;
- visible focus and focus order after state changes;
- VoiceOver with Safari and at least one additional screen reader/browser
  combination when a qualified human reviewer is available;
- 200% and 400% zoom, 320-pixel reflow and text-spacing overrides;
- forced colours/high contrast, reduced motion and dark/light preference;
- recognition of source authority, rights and caveats without colour;
- no-JavaScript and failed-shard recovery;
- Welsh-route discovery where an official representation exists; and
- plain-language comprehension with representative users when available.

Record browser, operating system, assistive technology, version, journey,
result, evidence and unresolved limitation in the accessibility receipt.

## Release criterion

`VAL-ACCESSIBILITY` passes only when automated checks and the declared assisted
journeys pass against the exact candidate digest, and every critical or serious
finding is fixed or blocks release. The receipt must identify which checks were
performed by software, an independent AI agent or a human. Owner acceptance
cannot turn a failed WCAG requirement into conformance.

Participant research, representative assistive-technology testing and an
independent human audit remain open
(`GAP-HUMAN-RESEARCH`). The public statement must say so.

Reference: [Web Content Accessibility Guidelines 2.2][WCAG].

[WCAG]: https://www.w3.org/TR/WCAG22/

# Implementation guardrails

This directory is a research specification for a future Chanlun implementation.

Before editing code or fixtures:

1. Read `README.md` and `docs/05_ambiguities_and_decisions.md`.
2. Treat `specs/invariants.json` as normative.
3. Preserve `endpoint_time`, `confirmed_at`, and `available_at` separately.
4. Do not allow `bi` to form a standard center.
5. Keep the standard third-point boundary inclusive (`B3 low >= ZG`, `S3 high <= ZD`).
6. Do not apply the segment non-extreme endpoint exception to `bi`.
7. Keep structural, auxiliary, heuristic, and experimental namespaces separate.
8. Never add a profit guarantee or bypass the risk layer.

When changing a rule:

- cite the lesson and exact page URL;
- add a new profile if compatibility changes;
- add positive, negative, equality, unfinished, and event-time tests;
- run both test commands from `README.md`;
- update `CHANGELOG.md` and regenerate `SHA256SUMS`.

Production code should consume confirmed events from the preceding layer. Strategies must not rescan raw bars to create private alternative geometry.

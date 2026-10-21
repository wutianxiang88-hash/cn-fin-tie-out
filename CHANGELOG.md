# Changelog

## 0.1.0 — 2026-10-21

First public release.

- Four deterministic checks: year-over-year change, AR-vs-revenue growth gap,
  operating-cash-flow vs net-profit ratio, and the balance-sheet identity.
- Two input formats: a model-extraction JSON file, or a flat CSV you fill in by hand.
- All thresholds are configurable per call and from the CLI.
- Exit codes suitable for CI: `0` clean, `1` at least one FAIL, `2` unusable input.
- Markdown and JSON reports.
- Base-period values missing from the extraction can be recovered from quoted
  text, and are flagged as recovered.

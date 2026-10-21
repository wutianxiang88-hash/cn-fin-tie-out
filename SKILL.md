---
name: cn-fin-tie-out
description: Deterministic tie-out checks for Chinese financial statements. Use when an agent has extracted figures from a balance sheet, income statement, cash-flow statement or annual report and must verify that they are internally consistent before writing a conclusion. Covers year-over-year change, receivables versus revenue growth, operating cash flow versus net profit, and the balance-sheet identity. Runs offline with no model call and no API key, and returns PASS / WARN / FAIL per check plus a machine-readable JSON report.
license: MIT
compatibility: Requires Python 3.9 or newer. No third-party packages and no network access.
---

# cn-fin-tie-out

A deterministic checker for the numbers an agent has already extracted. The point is
to remove arithmetic from the model's responsibility: the model extracts and
classifies, this tool recomputes, and the human reviews the FAIL rows.

## When to use this skill

Use it whenever the task involves judging figures taken from Chinese financial
statements — due-diligence screening, an investment memo, an audit preparation, a
post-investment review, or a diligence dataset — and you are about to write a
statement of the form "revenue grew X", "profit is Y", or "the balance sheet is
sound".

Do not use it to fetch data, to value a company, or to produce a report from scratch.
It checks consistency; it does not decide whether the figures are true.

## Hard rule

Never let the model compute a growth rate, a ratio, or a difference itself when this
skill can do it. If the tool is available and the task involves figures, run it. A
model-reported arithmetic result that was not produced by this tool must be labelled
as unverified.

## Workflow

1. **Extract.** Produce a JSON file (or a CSV) with one entry per figure. Never put
   raw statement text into the conversation — extract to a file and pass the path.

   Entry shape: `{"subject": "营业收入", "period": "2024年度", "value": 52000, "unit": "万元"}`
   Optional `"quote"` carries the source sentence, which lets the tool recover a base
   period the extractor forgot to tag.

2. **Recompute.** Run the checker and capture the exit code.

   ```bash
   cn-fin-tie-out check figures.json -o tie_out_report.md
   # or without installing
   PYTHONPATH=src python3 -m cn_fin_tie_out check figures.json --format json
   ```

3. **Read the FAIL rows, not the verdict.** Each FAIL row states the observed value,
   the expected rule, and the threshold that was applied. Quote those numbers back to
   the user rather than paraphrasing them.

4. **Report honestly.** A `PASS` proves internal consistency only. A `FAIL` is a
   candidate for human review, never a proven misstatement. State the exit code.

## Thresholds

Defaults are `ar_rev_gap_pct=20`, `ocf_np_low=0.5`, `ocf_np_high=2.0`,
`balance_tol_pct=0.5`, `yoy_spike_pct=50`. Override on the CLI with
`--ar-rev-gap`, `--ocf-np LOW HIGH`, `--balance-tol`, `--yoy-spike`, or in code with
`Tolerances(...)`. Say which tolerances were used whenever you report a result,
because a FAIL under a tight tolerance is a different claim from one under a loose one.

## Exit codes

`0` no FAIL (clean or WARN only) · `1` at least one FAIL · `2` unusable input.

Branch on the exit code rather than parsing the report text.

## Known limits

- Only four checks. Solvency, related-party transactions, revenue-recognition timing
  and footnote cross-references are out of scope.
- Subject matching is alias-based and keyword-based; an unusual chart of accounts may
  need its own alias mapping.
- A figure recovered from quoted text (`backfilled`) was never tagged with its own
  period. Treat it as tentative and verify against the source document.

See [README.md](README.md) for input formats and worked examples.

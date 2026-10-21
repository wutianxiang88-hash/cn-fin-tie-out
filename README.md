# cn-fin-tie-out

**A language model will happily tell you that 55 is 4.5. This tool won't let it — every number here is recomputed by a script.**

Deterministic tie-out checks for Chinese financial statements. No model, no network, no API key, no dependencies. Give it the numbers a model extracted (or a table you filled in yourself) and it tells you, line by line, whether they are consistent with each other.

[中文说明](README.zh-CN.md)

```text
[cn-fin-tie-out] PASS=6 WARN=2 FAIL=3 SKIP=0 ERROR=0 verdict=FAIL exit=1

| AR vs revenue growth gap | accounts receivable vs revenue | 2023→2024 | **FAIL** | revenue growth +30.0%, receivables growth +80.0%, gap +50.0 pp (threshold 20) → divergence |
| Operating cash flow vs net profit | operating cash flow vs net profit | 2024 | **FAIL** | operating cash flow 12,000,000.00, net profit 60,000,000.00, ratio 0.20 (expected range [0.5, 2]) → divergence |
| Balance sheet tie-out | assets / liabilities / equity | 2024 | **FAIL** | assets 1,000,000,000.00, liabilities 550,000,000.00, equity 418,000,000.00, difference 32,000,000.00 (tolerance 5,000,000.00, i.e. 0.5%) → does not balance |
```

That is a real run against [`examples/acme_manufacturing.json`](examples/acme_manufacturing.json) — see [`examples/expected_report.md`](examples/expected_report.md).

## Why this exists

If you use an LLM to read financial statements, you have probably noticed two things:

1. It extracts numbers well enough to be useful.
2. It cannot be trusted to do arithmetic on them, compare them across periods, or notice that a balance sheet does not balance.

The usual fix is "ask the model to double-check itself", which does not work — the same model that made the mistake is asked whether it made one. The fix here is to hand the numbers to code, and let the code say PASS / WARN / FAIL.

This package is the deterministic half of that split. It makes no judgement about whether a company is investable; it only answers "are these figures internally consistent, and where exactly are they not".

## What it checks

| Check | Rule | Default threshold |
|---|---|---|
| Year-over-year change | Any subject whose YoY change exceeds the line is flagged for attention | `50%` → WARN |
| AR vs revenue growth gap | `AR growth − revenue growth` above the line means receivables are running ahead of sales | `20 pp` → FAIL |
| Operating cash flow vs net profit | `OCF / net profit` falling outside the band separates reported profit from cash | `[0.5, 2.0]` → FAIL |
| Balance-sheet tie-out | `|assets − liabilities − equity| / assets` above tolerance means it does not balance | `0.5%` → FAIL |

Every threshold is overridable per call and from the CLI. Missing inputs produce an explicit `SKIP` row with a reason — never a silent pass.

## Quickstart

No installation needed:

```bash
git clone https://github.com/wutianxiang88-hash/cn-fin-tie-out.git
cd cn-fin-tie-out
PYTHONPATH=src python3 -m cn_fin_tie_out check examples/acme_manufacturing.json
```

Or install it:

```bash
pip install .
cn-fin-tie-out check examples/acme_manufacturing.csv --format json
```

## Input formats

**JSON** — the output of your extraction step. Either a bare array, or an object carrying the array under `numbers` / `records` / `data` / `items` / `rows`:

```json
{
  "company": "Acme Precision Manufacturing",
  "numbers": [
    {"subject": "营业收入", "period": "2024年度", "value": 52000, "unit": "万元"},
    {"subject": "应收账款", "period": "2023年末", "value": 10000, "unit": "万元"}
  ]
}
```

**CSV** — for when you would rather fill in a table by hand than run a model at all:

```csv
subject,period,value,unit,quote
营业收入,2024年度,52000,万元,"合并利润表：营业收入 52,000 万元"
```

Chinese subject aliases are merged before comparison (`营业收入` / `营业总收入` / `主营业务收入` are one line; `应收账款` / `应收帐款` likewise), so extraction differences do not produce false divergences. Units `元` / `千元` / `万元` / `十万` / `百万` / `亿元` are converted to CNY.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | No FAIL (clean, or WARN only) |
| `1` | At least one FAIL |
| `2` | The input could not be read, or some entries could not be parsed (reported as `ERROR`, never silently dropped) |

This makes it usable as a CI gate or as a step an agent can branch on.

## Use it from an agent

[`SKILL.md`](SKILL.md) is an Agent Skill definition, so Claude Code, DeepSeek Harness and other hosts that read `SKILL.md` can load the workflow directly. The intended split is:

1. the model extracts and *classifies*;
2. this tool *recomputes*;
3. a human reviews the FAIL rows.

## Scope

This is a review aid, not an audit opinion. A `PASS` here means the figures are mutually consistent — it says nothing about whether they are true, complete, or fairly presented. A `FAIL` is a candidate for review, not a proven error.

## License

MIT — see [LICENSE](LICENSE).

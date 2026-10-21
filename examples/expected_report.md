# Deterministic tie-out report

- Source: `examples/acme_manufacturing.json`
- Company: Acme Precision Manufacturing (示例公司)
- Tolerances: AR-vs-revenue growth gap 20 pp; OCF/net profit ratio [0.5, 2]; balance tolerance 0.5%; YoY spike 50%
- Verdict: **FAIL** — PASS 6 / WARN 2 / FAIL 3 / SKIP 0 / ERROR 0
- FAIL and WARN rows are review candidates for a human.

| Check | Subject | Period | Status | Detail |
|---|---|---|---|---|
| AR vs revenue growth gap | accounts receivable vs revenue | 2023→2024 | **FAIL** | revenue growth +30.0% (400,000,000.00 → 520,000,000.00), receivables growth +80.0% (100,000,000.00 → 180,000,000.00), gap +50.0 pp (threshold 20) → divergence |
| Operating cash flow vs net profit | operating cash flow vs net profit | 2024 | **FAIL** | 2024: operating cash flow 12,000,000.00, net profit 60,000,000.00, ratio 0.20 (expected range [0.5, 2]) → divergence |
| Balance sheet tie-out | assets / liabilities / equity | 2024 | **FAIL** | 2024: assets 1,000,000,000.00, liabilities 550,000,000.00, equity 418,000,000.00, difference 32,000,000.00 (tolerance 5,000,000.00, i.e. 0.5%) → does not balance |
| Year-over-year change | ar | 2023→2024 | **WARN** | 100,000,000.00 → 180,000,000.00, YoY +80.0% (above the 50% attention line) |
| Year-over-year change | ocf | 2023→2024 | **WARN** | 30,000,000.00 → 12,000,000.00, YoY -60.0% (above the 50% attention line) |
| Year-over-year change | assets | 2023→2024 | **PASS** | 850,000,000.00 → 1,000,000,000.00, YoY +17.6% |
| Year-over-year change | equity | 2023→2024 | **PASS** | 350,000,000.00 → 418,000,000.00, YoY +19.4% |
| Year-over-year change | liabilities | 2023→2024 | **PASS** | 500,000,000.00 → 550,000,000.00, YoY +10.0% |
| Year-over-year change | net_profit | 2023→2024 | **PASS** | 50,000,000.00 → 60,000,000.00, YoY +20.0% |
| Year-over-year change | revenue | 2023→2024 | **PASS** | 400,000,000.00 → 520,000,000.00, YoY +30.0% |
| Balance sheet tie-out | assets / liabilities / equity | 2023 | **PASS** | 2023: assets 850,000,000.00, liabilities 500,000,000.00, equity 350,000,000.00, difference 0.00 (tolerance 4,250,000.00, i.e. 0.5%) |

## Normalised inputs (CNY)

| Subject | Year | Raw value | Unit | CNY |
|---|---|---|---|---|
| ar | 2023 | 10000 | 万元 | 100,000,000.00 |
| ar | 2024 | 18000 | 万元 | 180,000,000.00 |
| assets | 2023 | 85000 | 万元 | 850,000,000.00 |
| assets | 2024 | 100000 | 万元 | 1,000,000,000.00 |
| equity | 2023 | 35000 | 万元 | 350,000,000.00 |
| equity | 2024 | 41800 | 万元 | 418,000,000.00 |
| liabilities | 2023 | 50000 | 万元 | 500,000,000.00 |
| liabilities | 2024 | 55000 | 万元 | 550,000,000.00 |
| net_profit | 2023 | 5000 | 万元 | 50,000,000.00 |
| net_profit | 2024 | 6000 | 万元 | 60,000,000.00 |
| ocf | 2023 | 3000 | 万元 | 30,000,000.00 |
| ocf | 2024 | 1200 | 万元 | 12,000,000.00 |
| revenue | 2023 | 40000 | 万元 | 400,000,000.00 |
| revenue | 2024 | 52000 | 万元 | 520,000,000.00 |

> Every figure above was computed by `cn-fin-tie-out` from the parsed inputs, with no model inference and no network access. Treat the output as a review aid, not as a substitute for human judgement.

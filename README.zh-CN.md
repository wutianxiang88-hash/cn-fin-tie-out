# cn-fin-tie-out

**大模型会把 55 算成 4.5。这个工具不让它碰算术——每一个数字都由脚本复算。**

中国财报的确定性勾稽检查。不调用模型、不联网、不需要 API Key、零第三方依赖。把模型抽取出来的数字（或你自己填的一张表）交给它，它逐项告诉你：这些数字彼此是否对得上。

[English README](README.md)

```text
[cn-fin-tie-out] PASS=6 WARN=2 FAIL=3 SKIP=0 ERROR=0 verdict=FAIL exit=1

| 应收 vs 收入增速背离 | accounts receivable vs revenue | 2023→2024 | **FAIL** | 收入增速 +30.0%，应收增速 +80.0%，背离 +50.0 pp（阈值 20）→ 背离 |
| 经营现金流 vs 净利润 | operating cash flow vs net profit | 2024 | **FAIL** | 经营现金流 12,000,000.00，净利润 60,000,000.00，比值 0.20（期望区间 [0.5, 2]）→ 背离 |
| 资产负债表勾稽 | assets / liabilities / equity | 2024 | **FAIL** | 资产 1,000,000,000.00，负债 550,000,000.00，权益 418,000,000.00，差额 32,000,000.00（容差 5,000,000.00，即 0.5%）→ 不平 |
```

以上是对 [`examples/acme_manufacturing.json`](examples/acme_manufacturing.json) 的真实运行结果，完整报告见 [`examples/expected_report.md`](examples/expected_report.md)。

## 为什么需要它

用大模型读财报的人大概都发现两件事：

1. 它抽数字抽得还不错，够用；
2. 但你没法信任它对这些数字做算术、做跨期比较、或者发现资产负债表不平。

常见的补救是"让模型自己再检查一遍"——这不管用，因为被问的正是刚才犯错的同一个模型。这里的做法是把数字交给代码，由代码给出 PASS / WARN / FAIL。

本包就是这种分工里的**确定性那一半**。它不判断这家公司值不值得投，只回答一件事：这些数字内部是否自洽，以及具体哪里不自洽。

## 检查项

| 检查 | 规则 | 默认阈值 |
|---|---|---|
| 同比变动 | 任一科目同比变化超过该线即提示关注 | `50%` → WARN |
| 应收 vs 收入增速背离 | `应收增速 − 收入增速` 超过该线，说明应收跑在收入前面 | `20 pp` → FAIL |
| 经营现金流 vs 净利润 | `经营现金流 / 净利润` 落在区间外，说明账面利润与现金脱节 | `[0.5, 2.0]` → FAIL |
| 资产负债表勾稽 | `|资产 − 负债 − 权益| / 资产` 超过容差，说明表不平 | `0.5%` → FAIL |

所有阈值都可在函数入参和命令行覆盖。缺科目、单位无法识别、除零等情况会输出明确的 `SKIP` 行并说明原因，**不会静默放过**。

## 30 秒上手

无需安装：

```bash
git clone https://github.com/wutianxiang88-hash/cn-fin-tie-out.git
cd cn-fin-tie-out
PYTHONPATH=src python3 -m cn_fin_tie_out check examples/acme_manufacturing.json
```

或安装后使用：

```bash
pip install .
cn-fin-tie-out check examples/acme_manufacturing.csv --format json
```

## 输入格式

**JSON** —— 抽取步骤的产物。可以是裸数组，也可以放在 `numbers` / `records` / `data` / `items` / `rows` 任一键下：

```json
{
  "company": "Acme Precision Manufacturing",
  "numbers": [
    {"subject": "营业收入", "period": "2024年度", "value": 52000, "unit": "万元"},
    {"subject": "应收账款", "period": "2023年末", "value": 10000, "unit": "万元"}
  ]
}
```

**CSV** —— 不想跑模型、直接手填一张表时用：

```csv
subject,period,value,unit,quote
营业收入,2024年度,52000,万元,"合并利润表：营业收入 52,000 万元"
```

中文科目别名会在比较前归并（`营业收入` / `营业总收入` / `主营业务收入` 视为同一行；`应收账款` / `应收帐款` 同理），避免因抽取口径不同产生假背离。单位支持 `元` / `千元` / `万元` / `十万` / `百万` / `亿元`，统一换算成人民币元。

## 退出码

| 码 | 含义 |
|---|---|
| `0` | 无 FAIL（全部通过，或仅有 WARN） |
| `1` | 至少一项 FAIL |
| `2` | 输入无法读取，或存在无法解析的条目（会记为 `ERROR`，绝不静默丢弃） |

因此它可以直接当 CI 门禁，也可以让 Agent 据此分支。

## 在 Agent 里用

[`SKILL.md`](SKILL.md) 是一份 Agent Skill 定义，Claude Code、DeepSeek Harness 等宿主可直接加载。推荐的分工：

1. 模型负责**抽取与分类**；
2. 本工具负责**复算**；
3. 人负责**看 FAIL 行**。

## 边界

这是审查辅助，不是审计意见。`PASS` 只代表这些数字互相自洽，不代表它们真实、完整或公允；`FAIL` 是**待复核的候选**，不是已证实的错误。

## 许可

MIT，见 [LICENSE](LICENSE)。

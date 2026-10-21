"""Report container and renderers (Markdown + JSON).

This module owns everything about *presenting* a result: the per-check row
record, the aggregate counts, the human-readable Markdown report and the
machine-readable JSON envelope. It deliberately imports nothing from
``core`` or ``loaders`` so the dependency graph stays acyclic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Statuses. PASS/WARN/FAIL are verdicts on a check; SKIP/ERROR describe input
# that could not be checked (never silently dropped).
# ---------------------------------------------------------------------------
PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
SKIP = "SKIP"
ERROR = "ERROR"

STATUS_ORDER = {FAIL: 0, ERROR: 1, WARN: 2, PASS: 3, SKIP: 4}

# Stable check ids. These are part of the JSON contract: CI jobs and agents
# should key on these strings, not on the human title.
CHECK_YOY = "yoy_change"
CHECK_AR_REV = "ar_revenue_divergence"
CHECK_OCF_NP = "ocf_net_profit_divergence"
CHECK_BALANCE = "balance_sheet_tie_out"
CHECK_INPUT = "input_parsing"

CHECK_TITLES = {
    CHECK_YOY: "Year-over-year change",
    CHECK_AR_REV: "AR vs revenue growth gap",
    CHECK_OCF_NP: "Operating cash flow vs net profit",
    CHECK_BALANCE: "Balance sheet tie-out",
    CHECK_INPUT: "Input parsing",
}

CHECK_ORDER = [CHECK_YOY, CHECK_AR_REV, CHECK_OCF_NP, CHECK_BALANCE, CHECK_INPUT]


def format_amount(value: Optional[float]) -> str:
    """Format a CNY amount with thousands separators (no magnitude suffix)."""
    if value is None:
        return "—"
    return "{:,.2f}".format(value)


def _cell(text: Any) -> str:
    """Make a value safe inside a Markdown table cell."""
    return str(text).replace("|", "\\|").replace("\n", " ")


@dataclass
class CheckResult:
    """One row of the report: one check applied to one subject/period."""

    name: str
    subject: str
    period: str
    status: str
    message: str
    observed: Optional[float] = None
    expected: Optional[str] = None
    flag: bool = False

    @property
    def title(self) -> str:
        return CHECK_TITLES.get(self.name, self.name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "subject": self.subject,
            "period": self.period,
            "status": self.status,
            "observed": None if self.observed is None else round(float(self.observed), 6),
            "expected": self.expected,
            "message": self.message,
            "flag": bool(self.flag),
        }


class Report:
    """Collects check rows, data-quality notes and the normalised input.

    ``check()`` returns one of these; ``render()`` / :meth:`to_markdown` turn it
    into the Markdown report and :meth:`to_json` into the machine format.
    """

    def __init__(
        self,
        source: str = "",
        company: Optional[str] = None,
        tolerances: Any = None,
        version: str = "",
    ) -> None:
        self.source = source
        self.company = company
        self.tolerances = tolerances
        self.version = version
        self.items: List[CheckResult] = []
        self.notes: List[Dict[str, str]] = []
        self.backfilled: List[Dict[str, Any]] = []
        self.numbers: Dict[str, List[Dict[str, Any]]] = {}

    # -- collection ---------------------------------------------------------
    def add(
        self,
        name: str,
        subject: str,
        period: Any,
        status: str,
        message: str,
        observed: Optional[float] = None,
        expected: Optional[str] = None,
        flag: bool = False,
    ) -> CheckResult:
        item = CheckResult(
            name=name,
            subject=subject,
            period=str(period),
            status=status,
            message=message,
            observed=observed,
            expected=expected,
            flag=bool(flag) or status in (FAIL, WARN),
        )
        self.items.append(item)
        return item

    def note(self, message: str, level: str = "note", subject: str = "", period: str = "") -> None:
        self.notes.append(
            {"level": level, "message": message, "subject": subject, "period": period}
        )

    # -- aggregates ---------------------------------------------------------
    def counts(self) -> Dict[str, int]:
        out = {PASS: 0, WARN: 0, FAIL: 0, SKIP: 0, ERROR: 0}
        for item in self.items:
            out[item.status] = out.get(item.status, 0) + 1
        return out

    def verdict(self) -> str:
        """Worst status wins. A parse ERROR outranks a WARN: silently dropping an
        entry must never read as a clean report."""
        counts = self.counts()
        if counts.get(FAIL):
            return FAIL
        if counts.get(ERROR):
            return ERROR
        if counts.get(WARN):
            return WARN
        return PASS

    def exit_code(self) -> int:
        """1 on any FAIL, 2 on any parse ERROR, else 0 (WARN alone still exits 0)."""
        counts = self.counts()
        if counts.get(FAIL):
            return 1
        if counts.get(ERROR):
            return 2
        return 0

    def sorted_items(self) -> List[CheckResult]:
        return sorted(
            self.items,
            key=lambda i: (
                STATUS_ORDER.get(i.status, 9),
                CHECK_ORDER.index(i.name) if i.name in CHECK_ORDER else 99,
                i.subject,
                i.period,
            ),
        )

    def summary_line(self, prefix: str = "[cn-fin-tie-out]") -> str:
        c = self.counts()
        return (
            "{prefix} PASS={p} WARN={w} FAIL={f} SKIP={s} ERROR={e} "
            "verdict={v} exit={x}".format(
                prefix=prefix,
                p=c.get(PASS, 0),
                w=c.get(WARN, 0),
                f=c.get(FAIL, 0),
                s=c.get(SKIP, 0),
                e=c.get(ERROR, 0),
                v=self.verdict(),
                x=self.exit_code(),
            )
        )

    # -- renderers ----------------------------------------------------------
    def to_markdown(self) -> str:
        counts = self.counts()
        tol = self.tolerances
        lines: List[str] = ["# Deterministic tie-out report", ""]
        lines.append("- Source: `{}`".format(self.source) if self.source else "- Source: (in-memory)")
        if self.company:
            lines.append("- Company: {}".format(self.company))
        if tol is not None:
            lines.append(
                "- Tolerances: AR-vs-revenue growth gap {g:g} pp; "
                "OCF/net profit ratio [{lo:g}, {hi:g}]; balance tolerance {bal:g}%; "
                "YoY spike {yoy:g}%".format(
                    g=tol.ar_rev_gap_pct,
                    lo=tol.ocf_np_low,
                    hi=tol.ocf_np_high,
                    bal=tol.balance_tol_pct,
                    yoy=tol.yoy_spike_pct,
                )
            )
        lines.append(
            "- Verdict: **{}** — PASS {} / WARN {} / FAIL {} / SKIP {} / ERROR {}".format(
                self.verdict(),
                counts.get(PASS, 0),
                counts.get(WARN, 0),
                counts.get(FAIL, 0),
                counts.get(SKIP, 0),
                counts.get(ERROR, 0),
            )
        )
        if self.backfilled:
            lines.append(
                "- Base-period backfill: {} value(s) recovered from quoted text; "
                "verify them before relying on the result.".format(len(self.backfilled))
            )
        if counts.get(FAIL):
            lines.append("- FAIL and WARN rows are review candidates for a human.")
        lines.append("")
        lines.append("| Check | Subject | Period | Status | Detail |")
        lines.append("|---|---|---|---|---|")
        for item in self.sorted_items():
            lines.append(
                "| {} | {} | {} | **{}** | {} |".format(
                    _cell(item.title),
                    _cell(item.subject),
                    _cell(item.period),
                    item.status,
                    _cell(item.message),
                )
            )
        notes = [n for n in self.notes if n.get("level") != "error"]
        if notes:
            lines.append("")
            lines.append("## Data quality notes")
            lines.append("")
            for note in notes:
                lines.append("- {}".format(note["message"]))
        if self.numbers:
            lines.append("")
            lines.append("## Normalised inputs (CNY)")
            lines.append("")
            lines.append("| Subject | Year | Raw value | Unit | CNY |")
            lines.append("|---|---|---|---|---|")
            for subject in sorted(self.numbers):
                points = sorted(self.numbers[subject], key=lambda p: p["year"])
                for point in points:
                    mark = " (backfilled)" if point.get("backfilled") else ""
                    lines.append(
                        "| {} | {}{} | {} | {} | {} |".format(
                            _cell(subject),
                            point["year"],
                            mark,
                            _cell(point.get("raw")),
                            _cell(point.get("unit")),
                            format_amount(point.get("yuan")),
                        )
                    )
        lines.append("")
        lines.append(
            "> Every figure above was computed by `cn-fin-tie-out` from the parsed inputs, "
            "with no model inference and no network access. Treat the output as a review "
            "aid, not as a substitute for human judgement."
        )
        return "\n".join(lines) + "\n"

    def to_dict(self) -> Dict[str, Any]:
        version = self.version
        if not version:
            try:  # avoid an import cycle at module load time
                from . import __version__ as version  # type: ignore
            except Exception:  # pragma: no cover - defensive
                version = ""
        return {
            "tool": "cn-fin-tie-out",
            "version": version,
            "source": self.source,
            "company": self.company,
            "tolerances": self.tolerances.to_dict() if self.tolerances is not None else None,
            "summary": dict(
                self.counts(),
                verdict=self.verdict(),
                exit_code=self.exit_code(),
            ),
            "checks": [item.to_dict() for item in self.sorted_items()],
            "notes": list(self.notes),
            "backfilled": list(self.backfilled),
            "numbers": [
                {
                    "subject": subject,
                    "year": point["year"],
                    "value_cny": point["yuan"],
                    "unit": point.get("unit"),
                    "raw": point.get("raw"),
                    "backfilled": bool(point.get("backfilled")),
                }
                for subject in sorted(self.numbers)
                for point in sorted(self.numbers[subject], key=lambda p: p["year"])
            ],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=False, allow_nan=False)

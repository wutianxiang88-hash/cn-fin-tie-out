"""The four deterministic checks, ported from ``numcheck.py``.

Everything here is pure arithmetic on numbers already extracted from a filing.
No model call, no network, no third-party dependency.

1. ``yoy_change``                year-over-year change per subject (WARN on spikes)
2. ``ar_revenue_divergence``     receivables growth minus revenue growth
3. ``ocf_net_profit_divergence`` operating cash flow / net profit ratio
4. ``balance_sheet_tie_out``     assets versus liabilities + equity

The report keeps the original behaviour where it was deliberate (alias
merging, base-period backfill, inclusive tolerance boundaries) and replaces
silent skipping with an explicit SKIP/ERROR row plus a reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .loaders import normalize_entries
from .report import (
    CHECK_AR_REV,
    CHECK_BALANCE,
    CHECK_INPUT,
    CHECK_OCF_NP,
    CHECK_YOY,
    ERROR,
    FAIL,
    PASS,
    SKIP,
    WARN,
    Report,
    format_amount,
)

# ---------------------------------------------------------------------------
# Default thresholds (identical to the values numcheck.py shipped with)
# ---------------------------------------------------------------------------
DEFAULT_AR_REV_GAP_PCT = 20.0
DEFAULT_OCF_NP_LOW = 0.5
DEFAULT_OCF_NP_HIGH = 2.0
DEFAULT_BALANCE_TOL_PCT = 0.5
DEFAULT_YOY_SPIKE_PCT = 50.0


@dataclass(frozen=True)
class Tolerances:
    """All thresholds in one place, overridable per call and from the CLI."""

    ar_rev_gap_pct: float = DEFAULT_AR_REV_GAP_PCT
    ocf_np_low: float = DEFAULT_OCF_NP_LOW
    ocf_np_high: float = DEFAULT_OCF_NP_HIGH
    balance_tol_pct: float = DEFAULT_BALANCE_TOL_PCT
    yoy_spike_pct: float = DEFAULT_YOY_SPIKE_PCT

    def validated(self) -> "Tolerances":
        """Raise ``ValueError`` on a nonsensical threshold combination."""
        for name in ("ar_rev_gap_pct", "ocf_np_low", "ocf_np_high", "balance_tol_pct", "yoy_spike_pct"):
            value = float(getattr(self, name))
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError("tolerance '{}' must be a finite number".format(name))
            if value < 0:
                raise ValueError("tolerance '{}' must be >= 0, got {}".format(name, value))
        if self.ocf_np_low >= self.ocf_np_high:
            raise ValueError(
                "ocf_np_low ({}) must be smaller than ocf_np_high ({})".format(
                    self.ocf_np_low, self.ocf_np_high
                )
            )
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ar_rev_gap_pct": self.ar_rev_gap_pct,
            "ocf_np": [self.ocf_np_low, self.ocf_np_high],
            "balance_tol_pct": self.balance_tol_pct,
            "yoy_spike_pct": self.yoy_spike_pct,
        }


DEFAULT_TOLERANCES = Tolerances()

# ---------------------------------------------------------------------------
# Units and subjects
# ---------------------------------------------------------------------------
UNIT_TO_YUAN = {
    "元": 1.0,
    "千元": 1e3,
    "万元": 1e4,
    "十万": 1e5,
    "百万元": 1e6,
    "百万": 1e6,
    "亿元": 1e8,
    # English aliases so a non-Chinese reader can fill in the CSV
    "cny": 1.0,
    "yuan": 1.0,
    "rmb": 1.0,
    "thousand": 1e3,
    "k cny": 1e3,
    "million": 1e6,
    "m cny": 1e6,
    "hundred million": 1e8,
}

# Alias merge: extraction dictionaries disagree ("营业收入" vs "营业总收入"),
# so normalise to one accounting line before comparing periods.
SUBJECT_ALIASES = {
    "营业收入": "revenue",
    "营业总收入": "revenue",
    "主营业务收入": "revenue",
    "收入": "revenue",
    "revenue": "revenue",
    "净利润": "net_profit",
    "归母净利润": "net_profit",
    "净利润(归母)": "net_profit",
    "归属于母公司股东的净利润": "net_profit",
    "利润总额": "net_profit",
    "net profit": "net_profit",
    "net_profit": "net_profit",
    "经营活动产生的现金流量净额": "ocf",
    "经营活动现金流量净额": "ocf",
    "经营现金流净额": "ocf",
    "ocf": "ocf",
    "operating cash flow": "ocf",
    "应收账款": "ar",
    "应收帐款": "ar",
    "ar": "ar",
    "accounts receivable": "ar",
    "资产总计": "assets",
    "资产总额": "assets",
    "总资产": "assets",
    "assets": "assets",
    "负债合计": "liabilities",
    "负债总额": "liabilities",
    "总负债": "liabilities",
    "liabilities": "liabilities",
    "所有者权益合计": "equity",
    "股东权益合计": "equity",
    "净资产": "equity",
    "归属于母公司所有者权益合计": "equity",
    "equity": "equity",
}

# A number in running text, with optional thousands separators.
NUMBER_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\d])")

# Any four-digit year inside a period string ("2024", "FY2024", "2024-12-31").
YEAR_RE = re.compile(r"((?:19|20)\d{2})")

# Phrases that introduce a base-period figure. Used by the backfill pass.
BASE_YEAR_HINTS = ("较期初", "期初", "上年同期", "同比", "较年初", "上期", "期初余额", "上年末")

# Round computed metrics to this many decimals before comparing to a
# threshold, so "exactly at the boundary" is not decided by float noise.
METRIC_DECIMALS = 6


def _metric(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), METRIC_DECIMALS)


def canon_subject(subject: Any) -> str:
    """Collapse an extracted subject name onto its canonical accounting line."""
    text = "" if subject is None else str(subject).strip()
    if text in SUBJECT_ALIASES:
        return SUBJECT_ALIASES[text]
    for alias, canonical in SUBJECT_ALIASES.items():
        if alias and alias in text:
            return canonical
    return text or "unnamed_subject"


def parse_period(period: Any) -> Optional[int]:
    """Return the last four-digit year found in a period string, else None."""
    if period is None:
        return None
    if isinstance(period, int) and not isinstance(period, bool):
        return period if 1900 <= period <= 2100 else None
    matches = YEAR_RE.findall(str(period))
    if not matches:
        return None
    return int(matches[-1])


def parse_value(value: Any) -> Optional[float]:
    """Parse a numeric value that may carry thousands separators."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("，", "").replace(" ", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def convert_to_yuan(value: Any, unit: Any) -> Optional[float]:
    """Convert to CNY. Returns None when the unit is missing or unknown."""
    number = parse_value(value)
    if number is None:
        return None
    key = "" if unit is None else str(unit).strip().lower()
    factor = UNIT_TO_YUAN.get(key)
    if factor is None:
        return None
    return number * factor


def growth_pct(current: Optional[float], base: Optional[float]) -> Optional[float]:
    """Period-over-period growth in percent; None when undefined."""
    if current is None or base is None or base == 0:
        return None
    return (current / base - 1.0) * 100.0


def _points(data: Dict[str, List[Dict[str, Any]]], subject: str) -> List[Dict[str, Any]]:
    return sorted(data.get(subject) or [], key=lambda point: point["year"])


# ---------------------------------------------------------------------------
# Building the normalised series
# ---------------------------------------------------------------------------
def build(entries: Sequence[Dict[str, Any]]) -> Tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, str]]]:
    """Group entries into ``{canonical subject: [point, ...]}``.

    Returns ``(data, notes)``. Notes with ``level == "error"`` are turned into
    ERROR rows by :func:`check`; they carry the reason an entry was dropped.
    """
    data: Dict[str, List[Dict[str, Any]]] = {}
    notes: List[Dict[str, str]] = []

    for index, entry in enumerate(entries):
        subject_text = entry.get("subject")
        subject_text = "" if subject_text is None else str(subject_text).strip()
        raw_period = entry.get("period")
        period_text = "—" if raw_period is None else str(raw_period)
        where = "entry {}".format(index + 1)
        if subject_text:
            where += " ({})".format(subject_text)

        if not subject_text:
            notes.append(
                {
                    "level": "error",
                    "subject": "(unnamed)",
                    "period": period_text,
                    "message": "{}: missing subject; entry ignored".format(where),
                }
            )
            continue

        year = parse_period(raw_period)
        if year is None:
            notes.append(
                {
                    "level": "error",
                    "subject": subject_text,
                    "period": period_text,
                    "message": "{}: cannot read a four-digit year from period {!r}; entry ignored".format(
                        where, raw_period
                    ),
                }
            )
            continue

        value = parse_value(entry.get("value"))
        if value is None:
            notes.append(
                {
                    "level": "error",
                    "subject": subject_text,
                    "period": str(year),
                    "message": "{}: value {!r} is not a number; entry ignored".format(
                        where, entry.get("value")
                    ),
                }
            )
            continue

        unit = entry.get("unit")
        yuan = convert_to_yuan(value, unit)
        if yuan is None:
            notes.append(
                {
                    "level": "error",
                    "subject": subject_text,
                    "period": str(year),
                    "message": "{}: unknown unit {!r} (expected 元/千元/万元/亿元 or an English "
                    "equivalent such as CNY, thousand, million); entry ignored".format(where, unit),
                }
            )
            continue

        subject = canon_subject(subject_text)
        points = data.setdefault(subject, [])
        existing = next((point for point in points if point["year"] == year), None)
        if existing is not None:
            same = abs(existing["yuan"] - yuan) < 1e-9
            notes.append(
                {
                    "level": "note",
                    "subject": subject,
                    "period": str(year),
                    "message": "duplicate '{}' {}: {} vs {} — {}".format(
                        subject,
                        year,
                        format_amount(existing["yuan"]),
                        format_amount(yuan),
                        "identical, deduplicated"
                        if same
                        else "conflicting, kept the first occurrence; verify manually",
                    ),
                }
            )
            continue
        points.append(
            {
                "year": year,
                "yuan": yuan,
                "unit": unit,
                "raw": entry.get("value"),
                "backfilled": False,
            }
        )
    return data, notes


# ---------------------------------------------------------------------------
# Check 1: year-over-year change
# ---------------------------------------------------------------------------
def check_yoy(data: Dict[str, List[Dict[str, Any]]], report: Report, tolerances: Tolerances) -> None:
    for subject in sorted(data):
        points = _points(data, subject)
        for earlier, later in zip(points, points[1:]):
            growth = _metric(growth_pct(later["yuan"], earlier["yuan"]))
            span = "{}→{}".format(earlier["year"], later["year"])
            if growth is None:
                report.add(
                    CHECK_YOY,
                    subject,
                    span,
                    SKIP,
                    "base period is missing or zero, so growth is undefined ({} → {})".format(
                        format_amount(earlier["yuan"]), format_amount(later["yuan"])
                    ),
                )
                continue
            unit_note = ""
            if earlier.get("unit") != later.get("unit"):
                unit_note = " (note: the two periods use different units {}/{}, both converted to CNY)".format(
                    earlier.get("unit"), later.get("unit")
                )
            detail = "{} → {}, YoY {:+.1f}%{}".format(
                format_amount(earlier["yuan"]), format_amount(later["yuan"]), growth, unit_note
            )
            expected = "|YoY| < {:g}%".format(tolerances.yoy_spike_pct)
            if abs(growth) >= tolerances.yoy_spike_pct:
                report.add(
                    CHECK_YOY,
                    subject,
                    span,
                    WARN,
                    detail + " (above the {:g}% attention line)".format(tolerances.yoy_spike_pct),
                    observed=growth,
                    expected=expected,
                )
            else:
                report.add(CHECK_YOY, subject, span, PASS, detail, observed=growth, expected=expected)


# ---------------------------------------------------------------------------
# Check 2: receivables growth vs revenue growth
# ---------------------------------------------------------------------------
def check_ar_vs_revenue(
    data: Dict[str, List[Dict[str, Any]]], report: Report, tolerances: Tolerances
) -> None:
    label = "accounts receivable vs revenue"
    missing = []
    if not data.get("revenue"):
        missing.append("revenue")
    if not data.get("ar"):
        missing.append("accounts receivable")
    expected = "receivables growth - revenue growth <= {:g} pp".format(tolerances.ar_rev_gap_pct)
    if missing:
        report.add(
            CHECK_AR_REV,
            label,
            "—",
            SKIP,
            "missing data: {} (check names/units in the input)".format(", ".join(missing)),
        )
        return

    revenue = _points(data, "revenue")
    receivables = _points(data, "ar")
    if len(revenue) < 2 or len(receivables) < 2:
        report.add(
            CHECK_AR_REV,
            label,
            str(max(revenue[-1]["year"], receivables[-1]["year"])),
            SKIP,
            "two periods of both revenue and receivables are required "
            "(revenue periods: {}, receivables periods: {})".format(len(revenue), len(receivables)),
        )
        return

    revenue_growth = _metric(growth_pct(revenue[-1]["yuan"], revenue[0]["yuan"]))
    ar_growth = _metric(growth_pct(receivables[-1]["yuan"], receivables[0]["yuan"]))
    span = "{}→{}".format(revenue[0]["year"], revenue[-1]["year"])
    if revenue_growth is None or ar_growth is None:
        report.add(
            CHECK_AR_REV,
            label,
            span,
            SKIP,
            "a base period is zero or missing, so one of the growth rates is undefined",
        )
        return

    gap = _metric(ar_growth - revenue_growth)
    detail = (
        "revenue growth {:+.1f}% ({} → {}), receivables growth {:+.1f}% ({} → {}), "
        "gap {:+.1f} pp (threshold {:g})".format(
            revenue_growth,
            format_amount(revenue[0]["yuan"]),
            format_amount(revenue[-1]["yuan"]),
            ar_growth,
            format_amount(receivables[0]["yuan"]),
            format_amount(receivables[-1]["yuan"]),
            gap,
            tolerances.ar_rev_gap_pct,
        )
    )
    if gap > tolerances.ar_rev_gap_pct:
        report.add(CHECK_AR_REV, label, span, FAIL, detail + " → divergence", observed=gap, expected=expected)
    else:
        report.add(CHECK_AR_REV, label, span, PASS, detail, observed=gap, expected=expected)


# ---------------------------------------------------------------------------
# Check 3: operating cash flow vs net profit
# ---------------------------------------------------------------------------
def check_ocf_vs_np(
    data: Dict[str, List[Dict[str, Any]]], report: Report, tolerances: Tolerances
) -> None:
    label = "operating cash flow vs net profit"
    missing = []
    if not data.get("ocf"):
        missing.append("operating cash flow")
    if not data.get("net_profit"):
        missing.append("net profit")
    expected = "ratio in [{:g}, {:g}]".format(tolerances.ocf_np_low, tolerances.ocf_np_high)
    if missing:
        report.add(
            CHECK_OCF_NP,
            label,
            "—",
            SKIP,
            "missing data: {} (check names/units in the input)".format(", ".join(missing)),
        )
        return

    ocf = _points(data, "ocf")
    net_profit = _points(data, "net_profit")
    latest = max(ocf[-1]["year"], net_profit[-1]["year"])
    cash = ocf[-1]["yuan"]
    profit = net_profit[-1]["yuan"]
    if profit == 0:
        report.add(
            CHECK_OCF_NP,
            label,
            str(latest),
            SKIP,
            "net profit is zero, so the ratio is undefined",
        )
        return

    ratio = _metric(cash / profit)
    detail = (
        "{}: operating cash flow {}, net profit {}, ratio {:.2f} "
        "(expected range [{:g}, {:g}])".format(
            latest,
            format_amount(cash),
            format_amount(profit),
            ratio,
            tolerances.ocf_np_low,
            tolerances.ocf_np_high,
        )
    )
    if profit < 0:
        # Same verdict rule as the original script; the note keeps a negative
        # denominator from being read as a meaningful ratio.
        detail += " (note: net profit is negative, so the ratio is not economically meaningful)"
    if ratio < tolerances.ocf_np_low or ratio > tolerances.ocf_np_high:
        report.add(CHECK_OCF_NP, label, str(latest), FAIL, detail + " → divergence", observed=ratio, expected=expected)
    else:
        report.add(CHECK_OCF_NP, label, str(latest), PASS, detail, observed=ratio, expected=expected)


# ---------------------------------------------------------------------------
# Check 4: balance sheet tie-out
# ---------------------------------------------------------------------------
def check_balance(
    data: Dict[str, List[Dict[str, Any]]], report: Report, tolerances: Tolerances
) -> None:
    label = "assets / liabilities / equity"
    required = ("assets", "liabilities", "equity")
    missing = [name for name in required if not data.get(name)]
    if missing:
        report.add(
            CHECK_BALANCE,
            label,
            "—",
            SKIP,
            "missing data: {} (check names/units in the input)".format(", ".join(missing)),
        )
        return

    periods_by_subject = [{point["year"] for point in data[name]} for name in required]
    common = set.intersection(*periods_by_subject)
    if not common:
        report.add(
            CHECK_BALANCE,
            label,
            "—",
            SKIP,
            "assets, liabilities and equity share no common accounting period",
        )
        return

    expected = "|assets - liabilities - equity| <= {:g}% of total assets".format(
        tolerances.balance_tol_pct
    )
    for year in sorted(common):
        assets = next(point["yuan"] for point in data["assets"] if point["year"] == year)
        liabilities = next(point["yuan"] for point in data["liabilities"] if point["year"] == year)
        equity = next(point["yuan"] for point in data["equity"] if point["year"] == year)
        if assets == 0:
            report.add(
                CHECK_BALANCE,
                label,
                str(year),
                SKIP,
                "total assets is zero, so the tolerance cannot be computed",
            )
            continue
        difference = _metric(assets - liabilities - equity)
        tolerance = abs(assets) * tolerances.balance_tol_pct / 100.0
        deviation_pct = _metric(difference / abs(assets) * 100.0)
        detail = (
            "{}: assets {}, liabilities {}, equity {}, difference {} "
            "(tolerance {}, i.e. {:g}%)".format(
                year,
                format_amount(assets),
                format_amount(liabilities),
                format_amount(equity),
                format_amount(difference),
                format_amount(tolerance),
                tolerances.balance_tol_pct,
            )
        )
        if abs(difference) > _metric(tolerance):
            report.add(
                CHECK_BALANCE,
                label,
                str(year),
                FAIL,
                detail + " → does not balance",
                observed=deviation_pct,
                expected=expected,
            )
        else:
            report.add(
                CHECK_BALANCE,
                label,
                str(year),
                PASS,
                detail,
                observed=deviation_pct,
                expected=expected,
            )


# ---------------------------------------------------------------------------
# Base-period backfill (ported unchanged in behaviour)
# ---------------------------------------------------------------------------
def backfill_base_years(
    entries: Sequence[Dict[str, Any]],
    data: Dict[str, List[Dict[str, Any]]],
    notes: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, Any]]:
    """Recover a missing base period from the quoted sentence around a value.

    Only fires when a subject has exactly one period, its quote contains a
    base-period phrase, and the candidate number is in the same order of
    magnitude as the current value. Every recovered value is recorded, so a
    human can check it — these values never had their own period tag.
    """
    added: List[Dict[str, Any]] = []
    for entry in entries:
        subject_text = entry.get("subject")
        subject_text = "" if subject_text is None else str(subject_text).strip()
        if not subject_text:
            continue
        subject = canon_subject(subject_text)
        points = data.get(subject) or []
        if len(points) != 1:
            continue  # only a subject with a single, current period qualifies
        quote = entry.get("quote")
        if not isinstance(quote, str) or not any(hint in quote for hint in BASE_YEAR_HINTS):
            continue
        current = points[0]
        base_year = current["year"] - 1
        if not (2000 <= base_year <= 2100):
            continue
        for match in NUMBER_RE.finditer(quote):
            token = match.group(1)
            if _same_number(token, current.get("raw")):
                continue  # skip the current value itself
            if _is_year(quote, match):
                continue  # "2024年度" — the 2024 is a year, not an amount
            if not _follows_base_hint(quote, match):
                continue  # only numbers right after "较期初/上年同期" qualify
            yuan = convert_to_yuan(token.replace(",", ""), entry.get("unit"))
            if yuan is None:
                continue
            if not _plausible_base(current["yuan"], yuan):
                continue  # caught a percentage or a growth rate, not a base amount
            points.append(
                {
                    "year": base_year,
                    "yuan": yuan,
                    "unit": entry.get("unit"),
                    "raw": token,
                    "backfilled": True,
                }
            )
            added.append(
                {
                    "subject": subject,
                    "year": base_year,
                    "value_cny": yuan,
                    "quote": quote,
                }
            )
            if notes is not None:
                notes.append(
                    {
                        "level": "note",
                        "subject": subject,
                        "period": str(base_year),
                        "message": "base period recovered from quoted text for '{}' {}: {} "
                        "(quote: …{}…). It was never tagged with its own period, but it took "
                        "part in the calculations — verify before relying on it.".format(
                            subject, base_year, format_amount(yuan), quote[:40]
                        ),
                    }
                )
            break
    return added


def _same_number(token: str, raw: Any) -> bool:
    try:
        return abs(float(token.replace(",", "")) - float(raw)) < 1e-9
    except (TypeError, ValueError):
        return False


def _is_year(quote: str, match: "re.Match") -> bool:
    """True when the match is a year ("2024年度" / "较2023年"), not an amount."""
    token = match.group(1)
    if not re.fullmatch(r"\d{4}", token) or not (1990 <= int(token) <= 2100):
        return False
    before = quote[max(0, match.start(1) - 1) : match.start(1)]
    after = quote[match.end(1) : match.end(1) + 1]
    return "年" in (before + after)


def _follows_base_hint(quote: str, match: "re.Match", span: int = 6) -> bool:
    """True when a base-period phrase appears within ``span`` chars before it."""
    window = quote[max(0, match.start(1) - span) : match.start(1)]
    return any(hint in window for hint in BASE_YEAR_HINTS)


def _plausible_base(current_yuan: float, base_yuan: float) -> bool:
    """Guard against treating a percentage as a base amount (0.2x ~ 5x)."""
    if current_yuan == 0:
        return base_yuan == 0
    ratio = base_yuan / current_yuan
    return 0.2 <= ratio <= 5.0


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def check(
    numbers: Iterable[Dict[str, Any]],
    tolerances: Optional[Tolerances] = None,
    backfill: bool = True,
    source: str = "",
    company: Optional[str] = None,
    version: str = "",
) -> Report:
    """Run the four checks over raw entries and return a :class:`Report`.

    ``numbers`` accepts the canonical keys (subject/period/value/unit/quote),
    the Chinese keys from an extraction JSON (科目/期间/值/单位/原文引用), or a
    mix of both.
    """
    tol = (tolerances or Tolerances()).validated()
    entries, entry_errors = normalize_entries(numbers)
    data, notes = build(entries)

    report = Report(source=source, company=company, tolerances=tol, version=version)
    for error in entry_errors:
        notes.append(error)
    for note in notes:
        if note.get("level") == "error":
            report.add(
                CHECK_INPUT,
                note.get("subject") or "(unnamed)",
                note.get("period") or "—",
                ERROR,
                note["message"],
                expected="entry parses to a subject, a four-digit year, "
                "a numeric value and a known unit",
            )
        else:
            report.note(note["message"], level=note.get("level", "note"),
                        subject=note.get("subject", ""), period=note.get("period", ""))

    if backfill:
        report.backfilled = backfill_base_years(entries, data, notes=None)
        for item in report.backfilled:
            report.note(
                "base period recovered from quoted text for '{}' {}: {} — the value was never "
                "tagged with its own period; verify before relying on it.".format(
                    item["subject"], item["year"], format_amount(item["value_cny"])
                )
            )

    check_yoy(data, report, tol)
    check_ar_vs_revenue(data, report, tol)
    check_ocf_vs_np(data, report, tol)
    check_balance(data, report, tol)

    report.numbers = data
    return report


def with_tolerances(tolerances: Tolerances, **overrides: Any) -> Tolerances:
    """Return a copy with individual thresholds overridden (convenience)."""
    return replace(tolerances, **overrides)

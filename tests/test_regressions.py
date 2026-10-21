"""Regression tests for the failures found in independent review (2026-10-21).

Each test below corresponds to a bug that shipped a *false* verdict — a false PASS
in most cases, which is the dangerous direction: it tells a reviewer the figures are
consistent when they have not been checked at all.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from cn_fin_tie_out import ERROR, FAIL, PASS, SKIP, WARN, check  # noqa: E402
from cn_fin_tie_out.core import canon_subject  # noqa: E402


def e(subject, period, value, unit="元", quote=None):
    row = {"subject": subject, "period": period, "value": value, "unit": unit}
    if quote is not None:
        row["quote"] = quote
    return row


def status_of(report, name):
    return [item.status for item in report.items if item.name == name]


class TestBackfillDoesNotInventABasePeriod(unittest.TestCase):
    """Review finding #1: a growth rate was read as a base-period amount."""

    def test_percentage_in_quote_is_not_a_base_value(self):
        rep = check([e("营业收入", "2024年度", 52000, "万元",
                       "营业收入 52,000 万元，较上年同期增长15.2%")])
        self.assertEqual(rep.backfilled, [], "a growth rate must never become a base period")
        self.assertNotIn(WARN, status_of(rep, "yoy_change"),
                         "a phantom base period would produce a YoY warning")

    def test_amount_without_percent_sign_still_backfills(self):
        rep = check([e("营业收入", "2024年度", 52000, "万元",
                       "营业收入 52,000 万元，上年同期 40,000 万元")])
        self.assertEqual(len(rep.backfilled), 1)
        self.assertEqual(rep.backfilled[0]["value_cny"], 400000000.0)


class TestSubjectMergingIsConservative(unittest.TestCase):
    """Review finding #2: substring matching merged unrelated lines into revenue."""

    def test_derived_lines_keep_their_own_bucket(self):
        for name in ("营业外收入", "净利润率", "利润总额", "总资产收益率", "应收账款融资"):
            self.assertNotIn(
                canon_subject(name), ("revenue", "net_profit", "assets", "ar"),
                "{} must not be merged into a primary accounting line".format(name),
            )

    def test_legitimate_aliases_still_merge(self):
        self.assertEqual(canon_subject("营业总收入"), "revenue")
        self.assertEqual(canon_subject("营业收入"), "revenue")
        self.assertEqual(canon_subject("应收帐款"), "ar")

    def test_non_operating_income_does_not_fake_an_ar_pass(self):
        rep = check([
            e("营业收入", "2023年度", 1000), e("营业外收入", "2024年度", 1500),
            e("应收账款", "2023年末", 1000), e("应收账款", "2024年末", 1500),
        ])
        self.assertNotIn(PASS, status_of(rep, "ar_revenue_divergence"))


class TestNonFiniteNumbers(unittest.TestCase):
    """Review finding #6: NaN made every comparison false, so the sheet "passed"."""

    def test_nan_is_an_error_not_a_pass(self):
        payload = [
            e("资产总计", "2024年末", 1000), e("负债合计", "2024年末", 600),
            {"subject": "所有者权益合计", "period": "2024年末", "value": float("nan"), "unit": "元"},
        ]
        rep = check(payload)
        # The NaN entry becomes a parse ERROR; the balance check must then SKIP
        # rather than compare against NaN and fall through to a PASS.
        self.assertIn(ERROR, [item.status for item in rep.items])
        self.assertNotIn(PASS, status_of(rep, "balance_sheet_tie_out"))
        self.assertNotEqual(rep.verdict(), PASS)
        self.assertEqual(rep.exit_code(), 2)

    def test_json_output_stays_valid_with_nan_input(self):
        rep = check([{"subject": "资产总计", "period": "2024年末",
                      "value": float("inf"), "unit": "元"}])
        json.loads(rep.to_json())


class TestCrossPeriodComparisons(unittest.TestCase):
    """Review findings #4 and #5: growth was computed across mismatched spans."""

    def test_ar_comparison_requires_shared_years(self):
        rep = check([
            e("营业收入", "2022年度", 800), e("营业收入", "2024年度", 1300),
            e("应收账款", "2023年末", 1000), e("应收账款", "2024年末", 1500),
        ])
        self.assertIn(SKIP, status_of(rep, "ar_revenue_divergence"))
        self.assertNotIn(FAIL, status_of(rep, "ar_revenue_divergence"))

    def test_cash_flow_and_profit_must_share_a_year(self):
        rep = check([
            e("净利润", "2023年度", 1000), e("经营活动产生的现金流量净额", "2024年度", 200),
        ])
        self.assertIn(SKIP, status_of(rep, "ocf_net_profit_divergence"))


class TestNegativeProfit(unittest.TestCase):
    """Review finding #3: a negative-over-negative ratio read as a healthy PASS."""

    def test_negative_profit_is_skipped_not_passed(self):
        rep = check([
            e("净利润", "2024年度", -100),
            e("经营活动产生的现金流量净额", "2024年度", -100),
        ])
        self.assertIn(SKIP, status_of(rep, "ocf_net_profit_divergence"))
        self.assertNotIn(PASS, status_of(rep, "ocf_net_profit_divergence"))


class TestVerdictAndExitCodeHonourErrors(unittest.TestCase):
    """Review finding #7: an all-unparseable input reported PASS and exit 0."""

    def test_all_bad_entries_do_not_read_as_clean(self):
        rep = check([{"subject": "?", "period": "??", "value": "abc", "unit": "??"}])
        self.assertEqual(rep.verdict(), ERROR)
        self.assertEqual(rep.exit_code(), 2)

    def test_exit_code_two_from_cli_on_unparseable_input(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as fh:
            fh.write("subject,period,value,unit\n???,,abc,???\n")
            path = fh.name
        try:
            env = dict(os.environ, PYTHONPATH=SRC)
            proc = subprocess.run([sys.executable, "-m", "cn_fin_tie_out", "check", path, "-q"],
                                  cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(proc.returncode, 2)
        finally:
            os.unlink(path)


class TestYoyBoundaryMatchesTheDocs(unittest.TestCase):
    """Review finding #8: the code used >= while the docs said "exceeds"."""

    def test_exactly_on_the_line_is_not_a_warning(self):
        rep = check([e("营业收入", "2023年度", 1000), e("营业收入", "2024年度", 1500)])
        self.assertIn(PASS, status_of(rep, "yoy_change"))

    def test_just_over_the_line_warns(self):
        rep = check([e("营业收入", "2023年度", 1000), e("营业收入", "2024年度", 1501)])
        self.assertIn(WARN, status_of(rep, "yoy_change"))


class TestInvalidToleranceCombination(unittest.TestCase):
    def test_inverted_ratio_band_is_rejected(self):
        env = dict(os.environ, PYTHONPATH=SRC)
        proc = subprocess.run(
            [sys.executable, "-m", "cn_fin_tie_out", "check",
             os.path.join(ROOT, "examples", "acme_manufacturing.json"), "--ocf-np", "3", "1"],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()

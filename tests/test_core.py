"""Unit tests for cn-fin-tie-out. Standard library only: python3 -m unittest discover -s tests -v"""
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

from cn_fin_tie_out import FAIL, PASS, WARN, Tolerances, check, load  # noqa: E402
from cn_fin_tie_out.loaders import InputError  # noqa: E402

EXAMPLES = os.path.join(ROOT, "examples")


def run(entries, **kwargs):
    return check(entries, **kwargs)


def statuses(report, name=None):
    return [
        item.status
        for item in report.items
        if name is None or item.name == name
    ]


def entries(pairs):
    out = []
    for subject, period, value, unit in pairs:
        out.append({"subject": subject, "period": period, "value": value, "unit": unit})
    return out


class TestYearOverYear(unittest.TestCase):
    def test_yoy_pass_under_spike_line(self):
        rep = run(entries([("营业收入", "2023年度", 100, "元"), ("营业收入", "2024年度", 130, "元")]))
        self.assertIn(PASS, statuses(rep, "yoy_change"))

    def test_yoy_warn_over_spike_line(self):
        rep = run(entries([("营业收入", "2023年度", 100, "元"), ("营业收入", "2024年度", 200, "元")]))
        self.assertIn(WARN, statuses(rep, "yoy_change"))

    def test_yoy_warn_on_large_decline(self):
        rep = run(entries([("净利润", "2023年度", 100, "元"), ("净利润", "2024年度", 40, "元")]))
        self.assertIn(WARN, statuses(rep, "yoy_change"))


class TestArVsRevenue(unittest.TestCase):
    def test_ar_gap_pass_when_close(self):
        rep = run(entries([
            ("营业收入", "2023年度", 1000, "元"), ("营业收入", "2024年度", 1300, "元"),
            ("应收账款", "2023年末", 1000, "元"), ("应收账款", "2024年末", 1490, "元"),
        ]))
        self.assertIn(PASS, statuses(rep, "ar_revenue_divergence"))

    def test_ar_gap_fail_when_diverging(self):
        rep = run(entries([
            ("营业收入", "2023年度", 1000, "元"), ("营业收入", "2024年度", 1300, "元"),
            ("应收账款", "2023年末", 1000, "元"), ("应收账款", "2024年末", 1510, "元"),
        ]))
        self.assertIn(FAIL, statuses(rep, "ar_revenue_divergence"))


class TestOcfVsNetProfit(unittest.TestCase):
    def test_ratio_inside_band_passes(self):
        rep = run(entries([
            ("净利润", "2024年度", 1000, "元"),
            ("经营活动产生的现金流量净额", "2024年度", 800, "元"),
        ]))
        self.assertIn(PASS, statuses(rep, "ocf_net_profit_divergence"))

    def test_ratio_below_band_fails(self):
        rep = run(entries([
            ("净利润", "2024年度", 1000, "元"),
            ("经营活动产生的现金流量净额", "2024年度", 200, "元"),
        ]))
        self.assertIn(FAIL, statuses(rep, "ocf_net_profit_divergence"))

    def test_ratio_above_band_fails(self):
        rep = run(entries([
            ("净利润", "2024年度", 1000, "元"),
            ("经营活动产生的现金流量净额", "2024年度", 5000, "元"),
        ]))
        self.assertIn(FAIL, statuses(rep, "ocf_net_profit_divergence"))

    def test_ratio_exactly_on_lower_bound_passes(self):
        rep = run(entries([
            ("净利润", "2024年度", 1000, "元"),
            ("经营活动产生的现金流量净额", "2024年度", 500, "元"),
        ]))
        self.assertIn(PASS, statuses(rep, "ocf_net_profit_divergence"))


class TestBalance(unittest.TestCase):
    def _row(self, assets, liabilities, equity):
        return entries([
            ("资产总计", "2024年末", assets, "元"),
            ("负债合计", "2024年末", liabilities, "元"),
            ("所有者权益合计", "2024年末", equity, "元"),
        ])

    def test_balanced_sheet_passes(self):
        rep = run(self._row(1000000, 600000, 400000))
        self.assertIn(PASS, statuses(rep, "balance_sheet_tie_out"))

    def test_exactly_at_tolerance_passes(self):
        # tolerance is 0.5% of 1,000,000 = 5,000; difference is exactly 5,000
        rep = run(self._row(1000000, 600000, 395000))
        self.assertIn(PASS, statuses(rep, "balance_sheet_tie_out"))

    def test_just_over_tolerance_fails(self):
        rep = run(self._row(1000000, 600000, 394900))
        self.assertIn(FAIL, statuses(rep, "balance_sheet_tie_out"))

    def test_zero_assets_skips_instead_of_dividing(self):
        rep = run(self._row(0, 0, 0))
        self.assertTrue(set(statuses(rep)) & {"SKIP", "ERROR"})


class TestNormalisation(unittest.TestCase):
    def test_subject_aliases_share_one_bucket(self):
        rep = run(entries([
            ("营业收入", "2023年度", 1000, "元"),
            ("营业总收入", "2024年度", 1300, "元"),
            ("应收账款", "2023年末", 1000, "元"),
            ("应收帐款", "2024年末", 1490, "元"),
        ]))
        self.assertIn(PASS, statuses(rep, "ar_revenue_divergence"))

    def test_unit_conversion_yi_yuan(self):
        rep = run(entries([("营业收入", "2023年度", 1, "亿元"), ("营业收入", "2024年度", 2, "亿元")]))
        revenue = [row for row in rep.to_dict()["numbers"] if row["subject"] == "revenue"]
        self.assertEqual(sorted(row["value_cny"] for row in revenue), [1e8, 2e8])

    def test_unknown_unit_is_reported_not_crashed(self):
        rep = run(entries([("营业收入", "2023年度", 1, "光年"), ("营业收入", "2024年度", 2, "光年")]))
        self.assertTrue(set(statuses(rep)) & {"SKIP", "ERROR"})

    def test_missing_subjects_skip_not_crash(self):
        rep = run(entries([("营业收入", "2024年度", 100, "元")]))
        self.assertTrue(set(statuses(rep)) & {"SKIP", "ERROR"})

    def test_zero_base_does_not_raise(self):
        rep = run(entries([("营业收入", "2023年度", 0, "元"), ("营业收入", "2024年度", 100, "元")]))
        self.assertIsInstance(report_statuses(rep), dict)


def report_statuses(rep):
    return {name: rep.counts().get(name, 0) for name in ("PASS", "WARN", "FAIL", "SKIP", "ERROR")}


class TestLoaders(unittest.TestCase):
    def test_load_json_object_root(self):
        ds = load(os.path.join(EXAMPLES, "acme_manufacturing.json"))
        self.assertGreater(len(ds.numbers), 10)
        self.assertIsNotNone(ds.company)

    def test_load_csv(self):
        ds = load(os.path.join(EXAMPLES, "acme_manufacturing.csv"))
        self.assertGreater(len(ds.numbers), 10)

    def test_json_and_csv_agree_on_verdict(self):
        a = check(load(os.path.join(EXAMPLES, "acme_manufacturing.json")).numbers)
        b = check(load(os.path.join(EXAMPLES, "acme_manufacturing.csv")).numbers)
        self.assertEqual(a.verdict(), b.verdict())
        self.assertEqual(a.counts()[FAIL], 3)

    def test_bare_array_root(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(entries([("营业收入", "2023年度", 1, "万元"), ("营业收入", "2024年度", 2, "万元")]), fh)
            path = fh.name
        try:
            self.assertEqual(len(load(path).numbers), 2)
        finally:
            os.unlink(path)

    def test_missing_file_raises_input_error(self):
        with self.assertRaises(InputError):
            load(os.path.join(EXAMPLES, "does-not-exist.json"))


class TestReportSerialisation(unittest.TestCase):
    def test_json_output_is_parsable_and_stable(self):
        rep = check(load(os.path.join(EXAMPLES, "acme_manufacturing.json")).numbers)
        payload = json.loads(rep.to_json())
        self.assertEqual(payload["tool"], "cn-fin-tie-out")
        self.assertEqual(payload["summary"]["verdict"], FAIL)
        self.assertTrue(all("status" in item for item in payload["checks"]))

    def test_markdown_contains_verdict_and_rows(self):
        rep = check(load(os.path.join(EXAMPLES, "acme_manufacturing.json")).numbers)
        text = rep.to_markdown()
        self.assertIn("Verdict", text)
        self.assertIn("FAIL", text)


class TestTolerancesAndCli(unittest.TestCase):
    def test_tolerance_override_changes_outcome(self):
        data = entries([
            ("营业收入", "2023年度", 1000, "元"), ("营业收入", "2024年度", 1300, "元"),
            ("应收账款", "2023年末", 1000, "元"), ("应收账款", "2024年末", 1490, "元"),
        ])
        lenient = check(data, tolerances=Tolerances(ar_rev_gap_pct=5.0))
        strict = check(data, tolerances=Tolerances(ar_rev_gap_pct=50.0))
        self.assertIn(FAIL, statuses(lenient, "ar_revenue_divergence"))
        self.assertIn(PASS, statuses(strict, "ar_revenue_divergence"))

    def test_cli_all_pass_returns_zero(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as fh:
            fh.write("subject,period,value,unit\n营业收入,2023年度,1000,元\n营业收入,2024年度,1100,元\n")
            path = fh.name
        try:
            rc = self._cli(["check", path, "-q"])
            self.assertEqual(rc, 0)
        finally:
            os.unlink(path)

    def test_cli_fail_returns_one(self):
        rc = self._cli(["check", os.path.join(EXAMPLES, "acme_manufacturing.json"), "-q"])
        self.assertEqual(rc, 1)

    def test_cli_missing_file_returns_two(self):
        rc = self._cli(["check", os.path.join(EXAMPLES, "nope.csv"), "-q"])
        self.assertEqual(rc, 2)

    def _cli(self, argv):
        env = dict(os.environ)
        env["PYTHONPATH"] = SRC
        proc = subprocess.run(
            [sys.executable, "-m", "cn_fin_tie_out"] + argv,
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        return proc.returncode


if __name__ == "__main__":
    unittest.main()

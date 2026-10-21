"""Command-line interface for cn-fin-tie-out.

Exit codes (documented in README and relied on by CI):
    0  no FAIL (PASS, or WARN only)
    1  at least one FAIL
    2  the input file could not be read or is not usable
"""
import argparse
import os
import sys
from typing import List, Optional

from . import __version__
from .core import Tolerances, check
from .loaders import InputError, load

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_INPUT = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cn-fin-tie-out",
        description="Deterministic tie-out checks for Chinese financial statements "
                    "(YoY, AR vs revenue, OCF vs net profit, balance identity).",
    )
    parser.add_argument("--version", action="version", version="cn-fin-tie-out {}".format(__version__))
    sub = parser.add_subparsers(dest="command")

    p_check = sub.add_parser("check", help="run the tie-out checks on one file (.json or .csv)")
    p_check.add_argument("input", help="input file: extraction JSON or a flat CSV")
    p_check.add_argument("-o", "--output", help="write the report here instead of stdout")
    p_check.add_argument(
        "--format", choices=("md", "json"), default="md",
        help="report format (default: md)",
    )
    p_check.add_argument("--company", default=None, help="company label used in the report header")
    p_check.add_argument(
        "--ar-rev-gap", type=float, default=None, metavar="PCT",
        help="AR growth minus revenue growth above this (percentage points) is a FAIL (default 20)",
    )
    p_check.add_argument(
        "--ocf-np", type=float, nargs=2, default=None, metavar=("LOW", "HIGH"),
        help="acceptable OCF/net-profit ratio band (default 0.5 2.0)",
    )
    p_check.add_argument(
        "--balance-tol", type=float, default=None, metavar="PCT",
        help="balance-identity tolerance as a percentage of total assets (default 0.5)",
    )
    p_check.add_argument(
        "--yoy-spike", type=float, default=None, metavar="PCT",
        help="YoY change above this is a WARN, not a FAIL (default 50)",
    )
    p_check.add_argument(
        "--no-backfill", action="store_true",
        help="disable recovering a missing base-period figure from quoted text",
    )
    p_check.add_argument("-q", "--quiet", action="store_true", help="print only the one-line summary")
    return parser


def _tolerances(args: argparse.Namespace) -> Tolerances:
    base = Tolerances()
    updates = {}
    if args.ar_rev_gap is not None:
        updates["ar_rev_gap_pct"] = args.ar_rev_gap
    if args.ocf_np is not None:
        updates["ocf_np_low"], updates["ocf_np_high"] = args.ocf_np[0], args.ocf_np[1]
    if args.balance_tol is not None:
        updates["balance_tol_pct"] = args.balance_tol
    if args.yoy_spike is not None:
        updates["yoy_spike_pct"] = args.yoy_spike
    if not updates:
        return base.validated()
    from dataclasses import replace

    return replace(base, **updates).validated()


def run_check(args: argparse.Namespace) -> int:
    if not os.path.exists(args.input):
        sys.stderr.write("error: input file not found: {}\n".format(args.input))
        return EXIT_INPUT
    try:
        tolerances = _tolerances(args)
    except ValueError as exc:
        sys.stderr.write("error: {}\n".format(exc))
        return EXIT_INPUT
    try:
        dataset = load(args.input)
    except InputError as exc:
        sys.stderr.write("error: {}\n".format(exc))
        return EXIT_INPUT
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write("error: cannot read {}: {}\n".format(args.input, exc))
        return EXIT_INPUT

    report = check(
        dataset.numbers,
        tolerances=tolerances,
        backfill=not args.no_backfill,
        source=dataset.source or args.input,
        company=args.company or dataset.company,
        version=__version__,
    )

    if args.format == "json":
        text = report.to_json() + "\n"
    else:
        text = report.to_markdown()

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
        sys.stdout.write(report.summary_line() + "\n")
    else:
        if args.quiet:
            sys.stdout.write(report.summary_line() + "\n")
        else:
            sys.stdout.write(text)

    return report.exit_code()


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "check":
        parser.print_help()
        return EXIT_INPUT
    return run_check(args)


if __name__ == "__main__":
    sys.exit(main())

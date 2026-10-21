"""cn-fin-tie-out — deterministic tie-out checks for Chinese financial statements.

The model extracts numbers; this package does the arithmetic. Nothing here calls a
language model, and nothing here decides whether a figure is *acceptable* — it only
recomputes whether the figures are *consistent* with each other.
"""
from .core import Tolerances, check, with_tolerances
from .loaders import Dataset, InputError, load
from .report import ERROR, FAIL, PASS, SKIP, WARN, Report

__version__ = "0.1.0"

__all__ = [
    "check",
    "with_tolerances",
    "Tolerances",
    "load",
    "Dataset",
    "InputError",
    "Report",
    "PASS",
    "WARN",
    "FAIL",
    "SKIP",
    "ERROR",
    "__version__",
]

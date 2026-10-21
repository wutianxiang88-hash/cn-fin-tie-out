"""Input loaders: JSON (model extraction output) and CSV (a hand-filled table).

Both formats normalise onto one canonical entry shape::

    {"subject": str, "period": str, "value": Any, "unit": str, "quote": str|None}

Keys are accepted in English or Chinese so an existing extraction JSON
(``科目`` / ``期间`` / ``值`` / ``单位`` / ``原文引用``) works unchanged, and a
human filling in a CSV can use the documented English header row.

A *structural* problem (missing file, unparsable JSON, wrong CSV header) raises
:class:`InputError` — the CLI maps that to exit code 2. A problem in a single
row (unknown unit, missing subject) is reported as an ERROR row by
``core.build`` instead, so one bad row never hides the rest of the file.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

CANONICAL_FIELDS = ("subject", "period", "value", "unit", "quote")

# Order matters: the first alias that matches wins, canonical names first.
KEY_ALIASES = {
    "subject": ("subject", "科目", "item", "account"),
    "period": ("period", "期间", "year", "年度", "fiscal_year"),
    "value": ("value", "值", "amount", "金额"),
    "unit": ("unit", "单位"),
    "quote": ("quote", "原文引用", "source_quote", "evidence"),
}

REQUIRED_CSV_FIELDS = ("subject", "period", "value", "unit")


class InputError(Exception):
    """Raised when the input as a whole cannot be read (CLI exit code 2)."""


@dataclass
class Dataset:
    """A loaded input file: normalised entries plus provenance."""

    numbers: List[Dict[str, Any]] = field(default_factory=list)
    source: str = ""
    company: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


def _match_field(key: str) -> Optional[str]:
    """Map one raw key (header name or JSON key) to a canonical field name."""
    if not isinstance(key, str):
        return None
    needle = key.strip().lstrip("\ufeff").lower()
    for field_name in CANONICAL_FIELDS:
        for alias in KEY_ALIASES[field_name]:
            if needle == alias.lower():
                return field_name
    return None


def normalize_entry(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Re-key one raw entry onto the canonical field names (missing -> None)."""
    out = {field_name: None for field_name in CANONICAL_FIELDS}
    for key, value in raw.items():
        field_name = _match_field(key)
        if field_name is not None and out[field_name] is None:
            out[field_name] = value
    # Trim whitespace on the string fields; leave ``value`` untouched so the
    # numeric parser (which tolerates commas) can decide.
    for name in ("subject", "period", "unit", "quote"):
        if isinstance(out[name], str):
            out[name] = out[name].strip()
    return out


def normalize_entries(numbers: Iterable[Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Normalise a sequence of raw entries; non-objects become error notes."""
    entries: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for index, raw in enumerate(numbers):
        if not isinstance(raw, dict):
            errors.append(
                {
                    "level": "error",
                    "subject": "(unreadable entry)",
                    "period": "—",
                    "message": "entry {}: expected a JSON object, got {}; entry ignored".format(
                        index + 1, type(raw).__name__
                    ),
                }
            )
            continue
        entries.append(normalize_entry(raw))
    return entries, errors


def load(path: str) -> Dataset:
    """Load a JSON or CSV file, choosing the loader by extension then by sniff."""
    if not isinstance(path, str) or not path:
        raise InputError("no input path given")
    if not os.path.isfile(path):
        raise InputError("input file not found: {}".format(path))
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        return load_json(path)
    if ext in (".csv", ".tsv"):
        return load_csv(path)
    # Unknown extension: look at the first non-space character.
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            head = handle.read(1)
    except OSError as exc:
        raise InputError("cannot read {}: {}".format(path, exc))
    except UnicodeDecodeError as exc:
        raise InputError("{} is not UTF-8 text: {}".format(path, exc))
    if head.strip().startswith(("[", "{")):
        return load_json(path)
    return load_csv(path)


def load_json(path: str) -> Dataset:
    """Load model-extracted numbers from a JSON file.

    Accepted roots: a list of entries, or an object carrying the list under
    ``numbers`` / ``records`` / ``data`` / ``items`` / ``rows`` (the original
    ``screen_result.json`` shape is ``{"numbers": [...]}``).
    """
    if not os.path.isfile(path):
        raise InputError("input file not found: {}".format(path))
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
    except OSError as exc:
        raise InputError("cannot read {}: {}".format(path, exc))
    except UnicodeDecodeError as exc:
        raise InputError("{} is not UTF-8 text: {}".format(path, exc))
    except json.JSONDecodeError as exc:
        raise InputError("invalid JSON in {}: {} (line {}, column {})".format(path, exc.msg, exc.lineno, exc.colno))

    metadata: Dict[str, Any] = {}
    company: Optional[str] = None
    numbers: Optional[List[Any]] = None

    if isinstance(payload, list):
        numbers = payload
    elif isinstance(payload, dict):
        for key in ("numbers", "records", "data", "items", "rows"):
            if isinstance(payload.get(key), list):
                numbers = payload[key]
                break
        if numbers is None:
            if any(_match_field(key) for key in payload):
                numbers = [payload]  # a single bare entry
            else:
                raise InputError(
                    "JSON object has no 'numbers' array; expected "
                    '{"numbers": [...]} where each entry has subject/period/value/unit'
                )
        metadata = {
            key: value
            for key, value in payload.items()
            if not isinstance(value, (list, dict)) and key != "numbers"
        }
        for key in ("company", "entity", "name", "公司", "标的"):
            candidate = payload.get(key)
            if isinstance(candidate, str) and candidate.strip():
                company = candidate.strip()
                break
    else:
        raise InputError("JSON root must be an object or an array, got {}".format(type(payload).__name__))

    if not numbers:
        raise InputError("no numbers found in {}".format(path))
    for index, raw in enumerate(numbers):
        if not isinstance(raw, dict):
            raise InputError(
                "{}: numbers[{}] is {}, expected an object".format(path, index, type(raw).__name__)
            )
    return Dataset(
        numbers=[normalize_entry(raw) for raw in numbers],
        source=path,
        company=company,
        metadata=metadata,
    )


def load_csv(path: str) -> Dataset:
    """Load a hand-filled table with header ``subject,period,value,unit[,quote]``.

    The header is matched case-insensitively and the Chinese aliases
    (``科目,期间,值,单位,原文引用``) are accepted too.
    """
    if not os.path.isfile(path):
        raise InputError("input file not found: {}".format(path))
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            if not fieldnames:
                raise InputError("{} is empty; expected a header row".format(path))
            mapped: Dict[str, str] = {}
            for header in fieldnames:
                field_name = _match_field(header)
                if field_name is not None and field_name not in mapped:
                    mapped[field_name] = header
            missing = [name for name in REQUIRED_CSV_FIELDS if name not in mapped]
            if missing:
                raise InputError(
                    "{} is missing required column(s): {}; expected header "
                    "subject,period,value,unit[,quote]".format(path, ", ".join(missing))
                )
            for raw in reader:
                if raw is None:
                    continue
                if all(
                    not isinstance(value, str) or not value.strip()
                    for value in raw.values()
                ):
                    continue  # blank line
                rows.append(
                    {
                        field_name: (raw.get(mapped[field_name]) if field_name in mapped else None)
                        for field_name in CANONICAL_FIELDS
                    }
                )
    except OSError as exc:
        raise InputError("cannot read {}: {}".format(path, exc))
    except UnicodeDecodeError as exc:
        raise InputError("{} is not UTF-8 text: {}".format(path, exc))
    except csv.Error as exc:
        raise InputError("malformed CSV in {}: {}".format(path, exc))

    if not rows:
        raise InputError("{} has a header but no data rows".format(path))
    return Dataset(numbers=rows, source=path)

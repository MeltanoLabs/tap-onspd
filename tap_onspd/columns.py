"""Column naming and typing for ONSPD CSV files."""

from __future__ import annotations

import re
from collections import Counter

from singer_sdk import typing as th

# ONSPD and its lookup files embed the geography vintage in the column name,
# e.g. ``lad26cd`` (LAD code, 2026 boundaries) or ``RGN25NM``. The vintage moves
# on with each release, which would rename columns downstream every quarter.
_VINTAGE = re.compile(r"^(?P<prefix>[a-z]+?)\d{2}(?P<suffix>cd|nm|nmw|ind|desc)(?P<n>\d?)$")

_NUMBER_COLUMNS = re.compile(r"^(lat|long)$")
_INTEGER_COLUMNS = re.compile(r"^imd\d{2}ind$")


def normalize_columns(header: list[str], *, strip_vintage: bool) -> list[str | None]:
    """Map raw CSV header names to output property names.

    Names are lower-cased. With ``strip_vintage``, the two-digit vintage is
    dropped (``lad26cd`` -> ``lad_cd``), except where that would collide with
    another column in the same file (``oa01cd``/``oa11cd``/``oa21cd`` keep
    their original names). Blank header cells (trailing empty columns in some
    lookup files) map to ``None`` and are skipped.

    Args:
        header: The raw header row.
        strip_vintage: Whether to drop vintage years from column names.

    Returns:
        One output name (or ``None``) per header cell.
    """
    lowered = [cell.strip().lower() for cell in header]
    if not strip_vintage:
        return [name or None for name in lowered]

    candidates = []
    for name in lowered:
        match = _VINTAGE.match(name)
        candidates.append(f"{match['prefix']}_{match['suffix']}{match['n']}" if match else name)

    counts = Counter(candidates)
    return [
        None if not raw else (candidate if counts[candidate] == 1 else raw)
        for raw, candidate in zip(lowered, candidates, strict=True)
    ]


def column_type(raw_name: str) -> th.JSONTypeHelper:
    """Return the JSON schema type for a raw (lower-cased) column name.

    Args:
        raw_name: Lower-cased raw header name.

    Returns:
        The property type.
    """
    if _NUMBER_COLUMNS.match(raw_name):
        return th.NumberType()
    if _INTEGER_COLUMNS.match(raw_name):
        return th.IntegerType()
    return th.StringType()


def convert_value(raw_name: str, value: str) -> str | float | int | None:
    """Convert a raw CSV value to its schema type; empty strings become ``None``.

    Args:
        raw_name: Lower-cased raw header name.
        value: The raw CSV value.

    Returns:
        The converted value.
    """
    if value == "":
        return None
    if _NUMBER_COLUMNS.match(raw_name):
        return float(value)
    if _INTEGER_COLUMNS.match(raw_name):
        return int(value)
    return value

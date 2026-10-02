"""Strict, deterministic parsing for numeric values found in user tables."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_CURRENCY = re.compile(r"(?i)(?:₽|руб(?:\.)?|р\.|\$|€|£|¥)")
_DIGITS = re.compile(r"^\d+$")


def _grouped_integer(value: str, separator: str | None) -> str | None:
    if separator is None or separator not in value:
        return value if _DIGITS.fullmatch(value) else None
    groups = value.split(separator)
    if not (1 <= len(groups[0]) <= 3 and _DIGITS.fullmatch(groups[0])):
        return None
    if any(len(group) != 3 or not _DIGITS.fullmatch(group) for group in groups[1:]):
        return None
    return "".join(groups)


def parse_decimal(value: object) -> Decimal | None:
    """Parse a localized decimal without guessing at invalid groupings.

    Whitespace, including non-breaking spaces, is accepted as a thousands
    separator. A single comma or dot is always a decimal separator; repeated
    separators are accepted as grouping only when every group has three digits.
    When both comma and dot occur, the rightmost is decimal and the other must
    form valid three-digit groups. This matches common Russian and spreadsheet
    notation while making ambiguous values deterministic.
    """

    candidate = str(value).strip().replace("−", "-")
    if not candidate or len(candidate) > 1024:
        return None
    candidate = _CURRENCY.sub("", candidate)
    negative_parentheses = candidate.startswith("(") and candidate.endswith(")")
    if negative_parentheses:
        candidate = candidate[1:-1].strip()
    sign = ""
    if candidate[:1] in {"+", "-"}:
        sign, candidate = candidate[0], candidate[1:]
    candidate = candidate.strip()
    if not candidate:
        return None

    decimal_separator: str | None = None
    grouping_separator: str | None = None
    if "," in candidate and "." in candidate:
        decimal_separator = "," if candidate.rfind(",") > candidate.rfind(".") else "."
        grouping_separator = "." if decimal_separator == "," else ","
    elif "," in candidate or "." in candidate:
        separator = "," if "," in candidate else "."
        if candidate.count(separator) == 1:
            decimal_separator = separator
        else:
            grouped = _grouped_integer(candidate, separator)
            if grouped is None:
                return None
            candidate = grouped

    if decimal_separator:
        if candidate.count(decimal_separator) != 1:
            return None
        integer, fraction = candidate.rsplit(decimal_separator, 1)
        if not _DIGITS.fullmatch(fraction):
            return None
        if any(char.isspace() for char in integer):
            parts = re.split(r"\s+", integer)
            if len(parts) < 2 or not _DIGITS.fullmatch(parts[0]) or any(
                len(part) != 3 or not _DIGITS.fullmatch(part) for part in parts[1:]
            ):
                return None
            normalized_integer = "".join(parts)
        else:
            normalized_integer = _grouped_integer(integer, grouping_separator)
        if normalized_integer is None:
            return None
        candidate = f"{normalized_integer}.{fraction}"
    elif any(char.isspace() for char in candidate):
        parts = re.split(r"\s+", candidate)
        if len(parts) < 2 or not _DIGITS.fullmatch(parts[0]) or any(
            len(part) != 3 or not _DIGITS.fullmatch(part) for part in parts[1:]
        ):
            return None
        candidate = "".join(parts)
    elif not _DIGITS.fullmatch(candidate):
        return None

    if negative_parentheses:
        if sign:
            return None
        sign = "-"
    try:
        number = Decimal(f"{sign}{candidate}")
    except InvalidOperation:
        return None
    return number if number.is_finite() else None

"""Helpers to condense verbose GCP payloads into token-friendly summaries."""

from __future__ import annotations

from typing import Any


def truncate(text: Any, limit: int = 400) -> str:
    s = "" if text is None else str(text)
    s = s.strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def first_line(text: Any, limit: int = 300) -> str:
    s = "" if text is None else str(text)
    line = s.strip().splitlines()[0] if s.strip() else ""
    return truncate(line, limit)


def money_to_float(money: Any) -> float | None:
    """Convert a google.type.Money proto to a float amount."""
    if money is None:
        return None
    units = getattr(money, "units", 0) or 0
    nanos = getattr(money, "nanos", 0) or 0
    return round(units + nanos / 1e9, 4)


def parse_list_arg(value: str | None) -> list[str]:
    """Parse a comma-separated string argument into a clean list."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_duration_seconds(freshness: str, default_seconds: int = 3600) -> int:
    """Parse a duration like '30m', '2h', '1d', '90s' into seconds."""
    if not freshness:
        return default_seconds
    freshness = freshness.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    unit = freshness[-1]
    if unit in units:
        try:
            amount = float(freshness[:-1])
        except ValueError:
            return default_seconds
        return int(amount * units[unit])
    # Bare number -> treat as seconds.
    try:
        return int(float(freshness))
    except ValueError:
        return default_seconds

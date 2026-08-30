"""Payload-condensing helpers, including the parsers agents feed free text to."""

from __future__ import annotations

import pytest

from platform_mcp.formatting import (
    first_line,
    money_to_float,
    parse_duration_seconds,
    parse_list_arg,
    truncate,
)


class TestParseDuration:
    @pytest.mark.parametrize(
        ("text", "seconds"),
        [
            ("90s", 90),
            ("30m", 1800),
            ("2h", 7200),
            ("1d", 86400),
            ("1.5h", 5400),
            ("  6H  ", 21600),
            ("300", 300),
        ],
    )
    def test_understood_durations(self, text, seconds):
        assert parse_duration_seconds(text) == seconds

    @pytest.mark.parametrize("text", ["", "soon", "abcs", "h"])
    def test_unparseable_input_falls_back_to_the_default(self, text):
        assert parse_duration_seconds(text, default_seconds=42) == 42


class TestTruncate:
    def test_short_text_is_untouched(self):
        assert truncate("hello") == "hello"

    def test_long_text_is_marked_as_cut(self):
        result = truncate("x" * 500, limit=100)
        assert len(result) == 100
        assert result.endswith("…")

    def test_none_becomes_empty(self):
        assert truncate(None) == ""


class TestFirstLine:
    def test_only_the_first_line_survives(self):
        assert first_line("boom\n  at frame one\n  at frame two") == "boom"

    def test_blank_input_is_empty(self):
        assert first_line("   ") == ""


class TestParseListArg:
    def test_splits_and_trims(self):
        assert parse_list_arg(" a , b ,c ") == ["a", "b", "c"]

    def test_empty_entries_are_dropped(self):
        assert parse_list_arg("a,,b,") == ["a", "b"]

    def test_empty_input_is_an_empty_list(self):
        assert parse_list_arg("") == []
        assert parse_list_arg(None) == []


class TestMoneyToFloat:
    def test_units_and_nanos_combine(self):
        class Money:
            units = -12
            nanos = -500000000

        assert money_to_float(Money()) == -12.5

    def test_none_is_none(self):
        assert money_to_float(None) is None

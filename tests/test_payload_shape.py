"""Response-shape guarantees that a measurement run against live GCP motivated.

Each assertion here corresponds to a number in `evals/BASELINE.md`. Stub objects
stand in for the GCP protos, so the suite stays offline and carries no recorded
production data -- what is being guarded is the shape of what we emit, which is
what a measurement run can see and a code review cannot.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from platform_mcp.formatting import truncate_reported
from platform_mcp.tools import logging_tools
from platform_mcp.tools.inventory_tools import _format_resource

pytestmark = pytest.mark.usefixtures("configured")


def make_entry(message: str, severity: str = "ERROR"):
    return SimpleNamespace(
        payload={"message": message},
        severity=severity,
        log_name="projects/demo/logs/run.googleapis.com%2Fstderr",
        resource=SimpleNamespace(type="cloud_run_revision"),
        timestamp=SimpleNamespace(isoformat=lambda: "2026-08-30T10:00:00+00:00"),
    )


def make_resource(name: str, display_name: str | None = None, state: str = "",
                  labels: dict | None = None, location: str = "us-central1"):
    return SimpleNamespace(
        name=f"//run.googleapis.com/projects/demo/locations/{location}/services/{name}",
        asset_type="run.googleapis.com/Service",
        display_name=display_name if display_name is not None else name,
        location=location,
        state=state,
        labels=labels or {},
        additional_attributes={},
    )


class TestMessageTruncation:
    """A clipped stack trace must never look like a complete one."""

    def test_a_short_message_is_untouched_and_unflagged(self):
        row = logging_tools._format_entry(make_entry("boom"))
        assert row["message"] == "boom"
        assert "message_truncated" not in row
        assert "message_full_length" not in row

    def test_a_long_message_is_flagged_with_its_real_length(self):
        row = logging_tools._format_entry(make_entry("x" * 5000))
        assert row["message_truncated"] is True
        assert row["message_full_length"] == 5000
        assert row["message"].endswith("…")
        assert len(row["message"]) == logging_tools._MESSAGE_LIMIT

    def test_the_cap_is_generous_enough_for_a_stack_trace(self):
        # Measured: at a 400-character cap, 96% of live error messages were
        # clipped and the median length sat exactly on the limit -- the cap,
        # not the content, was deciding what the agent saw.
        assert logging_tools._MESSAGE_LIMIT >= 1500

    def test_truncate_reported_returns_the_original_length(self):
        text, original = truncate_reported("y" * 100, 10)
        assert original == 100
        assert len(text) == 10


class TestPayloadBudget:
    """Bound the response as a whole, and say when the bound was what stopped it."""

    def test_many_short_entries_all_fit(self, monkeypatch):
        rows, used, stopped = self._pack([make_entry("short") for _ in range(50)])
        assert len(rows) == 50
        assert stopped is False

    def test_long_entries_stop_at_the_budget(self):
        rows, used, stopped = self._pack([make_entry("x" * 1500) for _ in range(50)])
        assert stopped is True
        assert len(rows) < 50
        assert used <= logging_tools._MAX_PAYLOAD_CHARS

    def test_one_oversized_entry_is_still_returned(self):
        # Returning nothing because the first record is large is worse than
        # returning the one record the caller can actually act on.
        rows, _, _ = self._pack([make_entry("x" * 100_000)])
        assert len(rows) == 1

    @staticmethod
    def _pack(entries):
        rows, used, stopped = [], 0, False
        for entry in entries:
            row = logging_tools._format_entry(entry)
            size = len(json.dumps(row, default=str))
            if rows and used + size > logging_tools._MAX_PAYLOAD_CHARS:
                stopped = True
                break
            rows.append(row)
            used += size
        return rows, used, stopped


class TestResourceShape:
    """Repeating an identifier is not information."""

    def test_display_name_is_dropped_when_it_repeats_the_name(self):
        # Measured: identical in 77 of 77 live Cloud Run services.
        row = _format_resource(make_resource("checkout"))
        assert row["name"] == "checkout"
        assert "display_name" not in row

    def test_a_genuinely_different_display_name_is_kept(self):
        row = _format_resource(make_resource("checkout", display_name="Checkout API"))
        assert row["display_name"] == "Checkout API"

    def test_empty_fields_are_omitted(self):
        # Measured: state was empty in 77 of 77 records, shipped every time.
        row = _format_resource(make_resource("checkout", state=""))
        assert "state" not in row
        assert "machine_type" not in row

    def test_labels_are_opt_in(self):
        # Measured: the single largest field at 46% of the payload.
        labels = {"commit": "a" * 40, "managed-by": "terraform"}
        assert "labels" not in _format_resource(make_resource("c", labels=labels))
        assert _format_resource(make_resource("c", labels=labels),
                                include_labels=True)["labels"] == labels

    def test_the_resource_path_survives_for_follow_up_calls(self):
        row = _format_resource(make_resource("checkout"))
        assert row["full_name"].endswith("/services/checkout")

    def test_the_lean_shape_is_materially_smaller(self):
        fat = make_resource("checkout", display_name="checkout", state="",
                            labels={"commit": "a" * 40, "team": "payments"})
        lean = len(json.dumps(_format_resource(fat)))
        full = len(json.dumps(_format_resource(fat, include_labels=True)))
        assert lean < full * 0.75

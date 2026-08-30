"""The audit trail, and the promise that logging never reaches stdout."""

from __future__ import annotations

import json

import pytest

from platform_mcp import observability
from platform_mcp.observability import instrument

pytestmark = pytest.mark.usefixtures("configured")


@pytest.fixture
def audit_file(monkeypatch, tmp_path):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("PLATFORM_MCP_AUDIT_LOG", str(path))
    return path


def _records(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_a_successful_call_is_recorded_with_size_and_duration(audit_file):
    @instrument
    def sample_tool(environment: str = "") -> dict:
        return {"environment": "staging", "project": "demo-staging", "count": 2}

    sample_tool(environment="staging")

    (record,) = _records(audit_file)
    assert record["tool"] == "sample_tool"
    assert record["environment"] == "staging"
    assert record["project"] == "demo-staging"
    assert record["count"] == 2
    assert record["error"] is None
    assert record["bytes"] > 0
    assert record["duration_ms"] >= 0


def test_a_failure_is_recorded_and_the_error_still_propagates(audit_file):
    @instrument
    def failing_tool(environment: str = "") -> dict:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        failing_tool(environment="production")

    (record,) = _records(audit_file)
    assert record["error"] == "RuntimeError"
    assert "boom" in record["error_message"]


def test_free_text_arguments_are_redacted(audit_file):
    @instrument
    def sample_tool(filter: str = "", environment: str = "") -> dict:
        return {"environment": "staging"}

    # A Cloud Logging filter can carry user ids or email addresses from the
    # logs being searched; the audit file must not become a copy of them.
    sample_tool(filter='jsonPayload.email="person@example.com"', environment="staging")

    (record,) = _records(audit_file)
    assert record["arguments"]["filter"] == "<redacted>"
    assert record["arguments"]["environment"] == "staging"
    assert "person@example.com" not in json.dumps(record)


def test_auditing_can_be_switched_off(monkeypatch, tmp_path):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("PLATFORM_MCP_AUDIT_LOG", "off")

    @instrument
    def sample_tool() -> dict:
        return {}

    sample_tool()
    assert not path.exists()


def test_an_unwritable_audit_path_does_not_break_the_call(monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    monkeypatch.setenv("PLATFORM_MCP_AUDIT_LOG", str(blocker / "audit.jsonl"))

    @instrument
    def sample_tool() -> dict:
        return {"ok": True}

    assert sample_tool() == {"ok": True}


def test_logging_is_configured_on_stderr_only(capsys):
    observability.logger.handlers.clear()
    observability.configure_logging()

    @instrument
    def sample_tool() -> dict:
        return {"environment": "staging"}

    sample_tool()
    captured = capsys.readouterr()
    # stdout carries the JSON-RPC stream; a single stray byte there ends the
    # session, so nothing in this package may write to it.
    assert captured.out == ""
    assert "sample_tool" in captured.err


class TestAsyncTools:
    """Async tools must be audited on completion, not on coroutine creation."""

    @pytest.fixture
    def anyio_backend(self):
        return "asyncio"

    @pytest.mark.anyio
    async def test_a_successful_async_call_is_recorded(self, audit_file):
        @instrument
        async def sample_tool(environment: str = "") -> dict:
            return {"environment": "staging", "count": 1}

        assert await sample_tool(environment="staging") == {
            "environment": "staging",
            "count": 1,
        }
        (record,) = _records(audit_file)
        assert record["tool"] == "sample_tool"
        assert record["environment"] == "staging"
        assert record["error"] is None
        # A coroutine object would serialise to a useless size; a real result
        # does not. This is what catches wrapping an async tool synchronously.
        assert record["bytes"] > 0

    @pytest.mark.anyio
    async def test_an_async_failure_is_recorded_and_propagates(self, audit_file):
        @instrument
        async def failing_tool(environment: str = "") -> dict:
            raise RuntimeError("async boom")

        with pytest.raises(RuntimeError, match="async boom"):
            await failing_tool(environment="staging")
        (record,) = _records(audit_file)
        assert record["error"] == "RuntimeError"

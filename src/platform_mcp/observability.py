"""Logging and the tool-call audit trail.

Two separate channels, because they answer different questions:

* **stderr logging** is for a human debugging a session right now. In stdio
  transport stdout carries the JSON-RPC stream, so a stray ``print`` corrupts
  the protocol and drops the connection -- every log line goes to stderr and
  nothing in this package may write to stdout.
* **the audit log** is a durable JSONL record of every tool call: which tool,
  which environment, how long, how much came back, and what failed. Claude Code
  keeps no per-server log file, so without this there is no history of what the
  server was asked about production.

Set ``PLATFORM_MCP_AUDIT_LOG=off`` to disable the audit file, or to a path to
move it. ``PLATFORM_MCP_LOG_LEVEL`` controls stderr verbosity.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from .errors import explain_exception

logger = logging.getLogger("platform_mcp")

_DEFAULT_AUDIT = Path.home() / ".local" / "state" / "platform-mcp" / "audit.jsonl"

# Arguments whose values are echoed into the audit log. Everything else is
# recorded by name only -- a Cloud Logging filter can carry user ids or emails
# from the logs being searched, and the audit file must not become a second
# copy of that data.
_SAFE_ARGS = frozenset(
    {
        "environment",
        "group_by",
        "days",
        "limit",
        "hours",
        "freshness",
        "window",
        "aligner",
        "alignment_period",
        "metric_type",
        "asset_types",
    }
)


def configure_logging() -> None:
    """Send package logs to stderr. Never stdout: that is the protocol channel."""
    if logger.handlers:
        return
    level = os.environ.get("PLATFORM_MCP_LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s platform-mcp %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, level, logging.INFO))
    logger.propagate = False


def _audit_path() -> Path | None:
    raw = os.environ.get("PLATFORM_MCP_AUDIT_LOG", "").strip()
    if raw.lower() in {"off", "0", "false", "none"}:
        return None
    return Path(raw).expanduser() if raw else _DEFAULT_AUDIT


def _write_audit(record: dict) -> None:
    path = _audit_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError as exc:
        # Never let auditing break a tool call; a warning on stderr is enough.
        logger.warning("could not write audit record: %s", exc)


def _safe_arguments(kwargs: dict) -> dict:
    recorded = {}
    for key, value in kwargs.items():
        if value in ("", None):
            continue
        recorded[key] = value if key in _SAFE_ARGS else "<redacted>"
    return recorded


def _response_size(result: Any) -> int:
    try:
        return len(json.dumps(result, default=str))
    except (TypeError, ValueError):
        return -1


def _start_record(fn: Callable, kwargs: dict) -> dict:
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tool": fn.__name__,
        "arguments": _safe_arguments(kwargs),
    }


def _finish_ok(record: dict, fn: Callable, result: Any, started: float) -> None:
    record["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
    if isinstance(result, dict):
        record["environment"] = result.get("environment")
        record["project"] = result.get("project")
        if "count" in result:
            record["count"] = result["count"]
    record["bytes"] = _response_size(result)
    record["error"] = None
    _write_audit(record)
    logger.info(
        "%s ok env=%s %sms %sB",
        fn.__name__,
        record.get("environment"),
        record["duration_ms"],
        record["bytes"],
    )


def _finish_error(record: dict, fn: Callable, exc: Exception, started: float) -> Exception:
    record["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
    # A failed call never reaches the result dict, so the environment has to
    # come from the arguments -- otherwise the audit trail cannot say which
    # project a failure was about, which is most of its value.
    record.setdefault("environment", record["arguments"].get("environment"))
    record["error"] = type(exc).__name__
    record["error_message"] = str(exc)[:500]
    _write_audit(record)
    logger.error("%s failed: %s: %s", fn.__name__, type(exc).__name__, exc)
    # Replace opaque credential failures with something the agent can act on;
    # anything else propagates unchanged.
    return explain_exception(exc, record["arguments"].get("environment", ""))


def instrument(fn: Callable) -> Callable:
    """Time, size and record one tool call, then re-raise anything it threw.

    ``functools.wraps`` keeps ``__doc__``, ``__annotations__`` and the
    signature intact, which is what FastMCP introspects to build the tool
    schema -- the wrapper must stay invisible to the protocol. Async tools are
    wrapped as async, so a coroutine is never recorded as a finished call.
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            started = time.perf_counter()
            record = _start_record(fn, kwargs)
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                raise _finish_error(record, fn, exc, started) from exc
            _finish_ok(record, fn, result, started)
            return result

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        started = time.perf_counter()
        record = _start_record(fn, kwargs)
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            raise _finish_error(record, fn, exc, started) from exc
        _finish_ok(record, fn, result, started)
        return result

    return wrapper

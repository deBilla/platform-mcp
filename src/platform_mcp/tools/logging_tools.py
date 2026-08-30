"""Cloud Logging tools (read-only)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from ..clients import get_logging_client
from ..registration import register_tool
from ..config import resolve_environment
from ..formatting import parse_duration_seconds, truncate_reported


def _since_timestamp(freshness: str) -> str:
    seconds = parse_duration_seconds(freshness, default_seconds=3600)
    since = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return since.strftime("%Y-%m-%dT%H:%M:%SZ")


# Log messages carry stack traces and structured context, which is the whole
# diagnostic value of the tool, so the per-message cap is generous.
_MESSAGE_LIMIT = 1500

# The real constraint is the agent's context window, not any single message, so
# the response is bounded as a whole rather than by clipping every entry to fit
# the worst case. Short messages therefore yield many entries and long ones
# yield fewer -- and the caller is told when the budget, not the query, ended
# the list. Roughly 10k tokens.
_MAX_PAYLOAD_CHARS = 40_000


def _payload_text(entry: Any) -> tuple[str, int]:
    payload = getattr(entry, "payload", None)
    if payload is None:
        payload = getattr(entry, "payload_json", None) or getattr(entry, "payload_pb", None)
    if isinstance(payload, dict):
        # Structured logs usually carry the human message under "message".
        msg = payload.get("message") or payload.get("msg")
        return truncate_reported(msg if msg else payload, _MESSAGE_LIMIT)
    return truncate_reported(payload, _MESSAGE_LIMIT)


def _format_entry(entry: Any) -> dict:
    resource = getattr(entry, "resource", None)
    resource_type = getattr(resource, "type", None) if resource else None
    ts = getattr(entry, "timestamp", None)
    message, original_length = _payload_text(entry)
    row = {
        "timestamp": ts.isoformat() if ts else None,
        "severity": str(getattr(entry, "severity", "") or ""),
        "log": (getattr(entry, "log_name", "") or "").split("/")[-1],
        "resource_type": resource_type,
        "message": message,
    }
    if original_length > _MESSAGE_LIMIT:
        row["message_truncated"] = True
        row["message_full_length"] = original_length
    return row


def query_logs(
    filter: str = "",
    freshness: str = "1h",
    limit: int = 50,
    environment: str = "",
) -> dict:
    """Query Cloud Logging with an advanced-filter expression.

    Args:
        filter: Cloud Logging advanced filter (e.g. 'severity>=WARNING AND
            resource.type="cloud_run_revision"'). Leave empty to match all logs.
        freshness: How far back to look, e.g. '30m', '1h', '2d'. Default '1h'.
        limit: Maximum number of entries to return (newest first).
        environment: Which configured GCP environment to query, e.g. 'staging'
            or 'production'. Omit to use the default environment. Call
            list_environments to see what is configured.
    """
    env = resolve_environment(environment)
    client = get_logging_client(env)
    limit = max(1, min(limit, 500))
    time_filter = f'timestamp>="{_since_timestamp(freshness)}"'
    full_filter = f"({filter}) AND {time_filter}" if filter.strip() else time_filter

    from google.cloud import logging_v2

    entries = client.list_entries(
        filter_=full_filter,
        order_by=logging_v2.DESCENDING,
        max_results=limit,
        page_size=min(limit, 500),
    )
    rows = []
    used = 0
    stopped_for_size = False
    for entry in entries:
        row = _format_entry(entry)
        size = len(json.dumps(row, default=str))
        if rows and used + size > _MAX_PAYLOAD_CHARS:
            stopped_for_size = True
            break
        rows.append(row)
        used += size

    result = {
        "environment": env.name,
        "project": env.project,
        "filter": full_filter,
        "count": len(rows),
        "entries": rows,
    }
    if stopped_for_size:
        # Say so, rather than letting a size-limited page look like the full
        # set of matches for the window.
        result["stopped_for_size"] = True
        result["requested_limit"] = limit
        result["note"] = (
            f"Returned {len(rows)} of up to {limit} entries to stay within a "
            "response size budget. Narrow the filter or shorten the freshness "
            "window to see more of what matched."
        )
    return result


def get_recent_errors(
    service: str = "",
    hours: int = 1,
    limit: int = 50,
    environment: str = "",
) -> dict:
    """Return recent log entries at severity ERROR or higher.

    Args:
        service: Optional service name to narrow to (matched against
            resource.labels.service_name and logName).
        hours: How many hours back to search. Default 1.
        limit: Maximum number of entries to return (newest first).
        environment: Which configured GCP environment to query, e.g. 'staging'
            or 'production'. Omit to use the default environment.
    """
    parts = ["severity>=ERROR"]
    if service.strip():
        s = service.strip()
        parts.append(f'(resource.labels.service_name="{s}" OR logName:"{s}")')
    return query_logs(
        filter=" AND ".join(parts),
        freshness=f"{max(1, hours)}h",
        limit=limit,
        environment=environment,
    )


def register(mcp) -> None:
    register_tool(mcp, query_logs)
    register_tool(mcp, get_recent_errors)

"""Cloud Logging tools (read-only)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..clients import get_logging_client
from ..config import get_settings, require_project
from ..formatting import parse_duration_seconds, truncate


def _since_timestamp(freshness: str) -> str:
    seconds = parse_duration_seconds(freshness, default_seconds=3600)
    since = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return since.strftime("%Y-%m-%dT%H:%M:%SZ")


def _payload_text(entry: Any) -> str:
    payload = getattr(entry, "payload", None)
    if payload is None:
        payload = getattr(entry, "payload_json", None) or getattr(entry, "payload_pb", None)
    if isinstance(payload, dict):
        # Structured logs usually carry the human message under "message".
        msg = payload.get("message") or payload.get("msg")
        return truncate(msg if msg else payload)
    return truncate(payload)


def _format_entry(entry: Any) -> dict:
    resource = getattr(entry, "resource", None)
    resource_type = getattr(resource, "type", None) if resource else None
    ts = getattr(entry, "timestamp", None)
    return {
        "timestamp": ts.isoformat() if ts else None,
        "severity": str(getattr(entry, "severity", "") or ""),
        "log": (getattr(entry, "log_name", "") or "").split("/")[-1],
        "resource_type": resource_type,
        "message": _payload_text(entry),
    }


def query_logs(filter: str = "", freshness: str = "1h", limit: int = 50) -> dict:
    """Query Cloud Logging with an advanced-filter expression.

    Args:
        filter: Cloud Logging advanced filter (e.g. 'severity>=WARNING AND
            resource.type="cloud_run_revision"'). Leave empty to match all logs.
        freshness: How far back to look, e.g. '30m', '1h', '2d'. Default '1h'.
        limit: Maximum number of entries to return (newest first).
    """
    client = get_logging_client()
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
    rows = [_format_entry(e) for e in entries]
    return {
        "project": require_project(),
        "filter": full_filter,
        "count": len(rows),
        "entries": rows,
    }


def get_recent_errors(service: str = "", hours: int = 1, limit: int = 50) -> dict:
    """Return recent log entries at severity ERROR or higher.

    Args:
        service: Optional service name to narrow to (matched against
            resource.labels.service_name and logName).
        hours: How many hours back to search. Default 1.
        limit: Maximum number of entries to return (newest first).
    """
    parts = ["severity>=ERROR"]
    if service.strip():
        s = service.strip()
        parts.append(f'(resource.labels.service_name="{s}" OR logName:"{s}")')
    return query_logs(filter=" AND ".join(parts), freshness=f"{max(1, hours)}h", limit=limit)


def register(mcp) -> None:
    mcp.tool()(query_logs)
    mcp.tool()(get_recent_errors)

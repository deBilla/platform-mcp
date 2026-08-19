"""Error Reporting tools (read-only)."""

from __future__ import annotations

from ..clients import get_error_stats_client
from ..config import resolve_environment
from ..formatting import first_line


def _period_for_hours(hours: int):
    from google.cloud import errorreporting_v1beta1 as er

    period = er.QueryTimeRange.Period
    if hours <= 1:
        return period.PERIOD_1_HOUR
    if hours <= 6:
        return period.PERIOD_6_HOURS
    if hours <= 24:
        return period.PERIOD_1_DAY
    if hours <= 24 * 7:
        return period.PERIOD_1_WEEK
    return period.PERIOD_30_DAYS


def list_error_groups(
    hours: int = 24,
    service: str = "",
    limit: int = 25,
    environment: str = "",
) -> dict:
    """List grouped application errors from Error Reporting with counts.

    Args:
        hours: Lookback window; snapped to the nearest supported period
            (1h, 6h, 1d, 1w, 30d). Default 24.
        service: Optional service name filter (Error Reporting "service" label).
        limit: Maximum number of error groups to return (most frequent first).
        environment: Which configured GCP environment to query, e.g. 'staging'
            or 'production'. Omit to use the default environment.
    """
    from google.cloud import errorreporting_v1beta1 as er

    env = resolve_environment(environment)
    client = get_error_stats_client(env)
    project = env.project
    limit = max(1, min(limit, 100))

    request: dict = {
        "project_name": f"projects/{project}",
        "time_range": er.QueryTimeRange(period=_period_for_hours(hours)),
        "order": er.ErrorGroupOrder.COUNT_DESC,
        "page_size": limit,
    }
    if service.strip():
        request["service_filter"] = er.ServiceContextFilter(service=service.strip())

    groups = []
    for stats in client.list_group_stats(request=request):
        representative = getattr(stats, "representative", None)
        message = first_line(getattr(representative, "message", "")) if representative else ""
        groups.append(
            {
                "group_id": getattr(stats.group, "group_id", ""),
                "count": stats.count,
                "affected_users": stats.affected_users_count,
                "first_seen": stats.first_seen_time.isoformat() if stats.first_seen_time else None,
                "last_seen": stats.last_seen_time.isoformat() if stats.last_seen_time else None,
                "message": message,
            }
        )
        if len(groups) >= limit:
            break

    return {
        "environment": env.name,
        "project": project,
        "count": len(groups),
        "error_groups": groups,
    }


def register(mcp) -> None:
    mcp.tool()(list_error_groups)

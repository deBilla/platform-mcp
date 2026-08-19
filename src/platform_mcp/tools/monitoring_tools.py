"""Cloud Monitoring tools (read-only): metrics, alert policies, uptime checks."""

from __future__ import annotations

import time

from ..clients import get_alert_policy_client, get_metric_client, get_uptime_client
from ..config import resolve_environment
from ..formatting import parse_duration_seconds, truncate

_ALIGNERS = {
    "MEAN": "ALIGN_MEAN",
    "MAX": "ALIGN_MAX",
    "MIN": "ALIGN_MIN",
    "SUM": "ALIGN_SUM",
    "COUNT": "ALIGN_COUNT",
    "RATE": "ALIGN_RATE",
    "PERCENTILE_99": "ALIGN_PERCENTILE_99",
}


def _ts(t):
    """Format a time-series point timestamp (datetime or proto Timestamp)."""
    if t is None:
        return None
    if hasattr(t, "isoformat"):
        return t.isoformat()
    if hasattr(t, "ToDatetime"):
        return t.ToDatetime().isoformat()
    return str(t)


def query_metric(
    metric_type: str,
    resource_filter: str = "",
    window: str = "1h",
    aligner: str = "MEAN",
    alignment_period: str = "5m",
    limit: int = 10,
    environment: str = "",
) -> dict:
    """Query a Cloud Monitoring metric time series.

    Args:
        metric_type: Metric type, e.g. 'compute.googleapis.com/instance/cpu/utilization'.
        resource_filter: Optional extra filter, e.g. 'resource.labels.instance_id="123"'.
        window: How far back to query, e.g. '1h', '6h', '1d'. Default '1h'.
        aligner: Aggregation across the alignment period: MEAN, MAX, MIN, SUM,
            COUNT, RATE, PERCENTILE_99. Default MEAN.
        alignment_period: Bucket size for aggregation, e.g. '1m', '5m'. Default '5m'.
        limit: Maximum number of time series to return.
        environment: Which configured GCP environment to query, e.g. 'staging'
            or 'production'. Omit to use the default environment.
    """
    from google.cloud import monitoring_v3

    env = resolve_environment(environment)
    client = get_metric_client(env)
    project = env.project
    limit = max(1, min(limit, 50))

    now = int(time.time())
    window_s = parse_duration_seconds(window, default_seconds=3600)
    interval = monitoring_v3.TimeInterval(
        {"end_time": {"seconds": now}, "start_time": {"seconds": now - window_s}}
    )
    align_s = parse_duration_seconds(alignment_period, default_seconds=300)
    aligner_enum = getattr(
        monitoring_v3.Aggregation.Aligner, _ALIGNERS.get(aligner.upper(), "ALIGN_MEAN")
    )
    aggregation = monitoring_v3.Aggregation(
        {"alignment_period": {"seconds": align_s}, "per_series_aligner": aligner_enum}
    )
    filter_str = f'metric.type = "{metric_type}"'
    if resource_filter.strip():
        filter_str += f" AND {resource_filter.strip()}"

    results = client.list_time_series(
        request={
            "name": f"projects/{project}",
            "filter": filter_str,
            "interval": interval,
            "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            "aggregation": aggregation,
        }
    )

    series = []
    for ts in results:
        points = []
        for p in ts.points[:12]:
            # Pick the field actually set in the TypedValue oneof so a real 0
            # isn't lost to truthiness (critical for gauges like DLQ depth).
            pb = p.value._pb if hasattr(p.value, "_pb") else p.value
            kind = pb.WhichOneof("value")
            if kind == "distribution_value":
                v = {"count": p.value.distribution_value.count,
                     "mean": p.value.distribution_value.mean}
            elif kind:
                v = getattr(p.value, kind)
            else:
                v = None
            points.append({"t": _ts(p.interval.end_time), "v": v})
        series.append(
            {
                "resource": dict(ts.resource.labels),
                "metric_labels": dict(ts.metric.labels),
                "points": points,
            }
        )
        if len(series) >= limit:
            break

    return {
        "environment": env.name,
        "project": project,
        "metric_type": metric_type,
        "aligner": aligner.upper(),
        "series_count": len(series),
        "series": series,
    }


def list_alert_policies(limit: int = 100, environment: str = "") -> dict:
    """List Cloud Monitoring alert policies and whether they are enabled.

    Args:
        limit: Maximum number of policies to return.
        environment: Which configured GCP environment to query, e.g. 'staging'
            or 'production'. Omit to use the default environment.
    """
    env = resolve_environment(environment)
    client = get_alert_policy_client(env)
    project = env.project
    policies = []
    for p in client.list_alert_policies(name=f"projects/{project}"):
        policies.append(
            {
                "name": p.name.split("/")[-1],
                "display_name": p.display_name,
                "enabled": bool(p.enabled) if p.enabled is not None else None,
                "conditions": [truncate(c.display_name, 120) for c in p.conditions],
            }
        )
        if len(policies) >= limit:
            break
    return {
        "environment": env.name,
        "project": project,
        "count": len(policies),
        "alert_policies": policies,
    }


def list_uptime_checks(limit: int = 100, environment: str = "") -> dict:
    """List Cloud Monitoring uptime check configurations.

    Args:
        limit: Maximum number of uptime checks to return.
        environment: Which configured GCP environment to query, e.g. 'staging'
            or 'production'. Omit to use the default environment.
    """
    env = resolve_environment(environment)
    client = get_uptime_client(env)
    project = env.project
    checks = []
    for c in client.list_uptime_check_configs(parent=f"projects/{project}"):
        monitored = c.monitored_resource.labels if c.monitored_resource else {}
        checks.append(
            {
                "name": c.name.split("/")[-1],
                "display_name": c.display_name,
                "period_seconds": c.period.seconds if c.period else None,
                "host": dict(monitored).get("host") or dict(monitored).get("project_id"),
            }
        )
        if len(checks) >= limit:
            break
    return {
        "environment": env.name,
        "project": project,
        "count": len(checks),
        "uptime_checks": checks,
    }


def register(mcp) -> None:
    mcp.tool()(query_metric)
    mcp.tool()(list_alert_policies)
    mcp.tool()(list_uptime_checks)

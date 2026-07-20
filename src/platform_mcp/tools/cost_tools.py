"""Cost tools (read-only): BigQuery billing-export analysis and billing info."""

from __future__ import annotations

import os

from google.api_core.exceptions import GoogleAPICallError

from ..clients import get_bigquery_client, get_billing_client
from ..config import require_project

# Whitelisted group-by columns -> billing export column expressions. Kept as a
# whitelist because column identifiers cannot be passed as query parameters.
_GROUP_BY_COLUMNS = {
    "service": "service.description",
    "sku": "sku.description",
    "project": "project.id",
    "region": "location.region",
}


def get_cost_breakdown(group_by: str = "service", days: int = 30, limit: int = 20) -> dict:
    """Summarize recent spend from the BigQuery billing export.

    Requires a standard-usage billing export configured in BigQuery and the
    table set via the BILLING_EXPORT_TABLE env var (fully qualified, e.g.
    'my-project.billing.gcp_billing_export_v1_XXXXXX').

    Args:
        group_by: One of 'service', 'sku', 'project', 'region'. Default 'service'.
        days: Lookback window in days over usage_start_time. Default 30.
        limit: Max rows returned (highest net cost first).
    """
    table = os.environ.get("BILLING_EXPORT_TABLE", "").strip()
    if not table:
        return {
            "status": "not_configured",
            "message": (
                "BigQuery billing export is not configured. Enable a Standard "
                "usage cost export (Billing > Billing export) and set the "
                "BILLING_EXPORT_TABLE env var to the fully-qualified table id."
            ),
        }

    column = _GROUP_BY_COLUMNS.get(group_by.lower())
    if column is None:
        return {
            "status": "invalid_argument",
            "message": f"group_by must be one of {sorted(_GROUP_BY_COLUMNS)}",
        }

    days = max(1, min(days, 365))
    limit = max(1, min(limit, 200))
    sql = f"""
        SELECT
            {column} AS group_key,
            ROUND(SUM(cost), 2) AS gross_cost,
            ROUND(SUM(cost) + SUM(IFNULL((
                SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)), 2) AS net_cost,
            ANY_VALUE(currency) AS currency
        FROM `{table}`
        WHERE usage_start_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
        GROUP BY group_key
        ORDER BY net_cost DESC
        LIMIT @limit
    """

    from google.cloud import bigquery

    client = get_bigquery_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("days", "INT64", days),
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
        ]
    )
    try:
        rows = list(client.query(sql, job_config=job_config).result())
    except GoogleAPICallError as exc:
        return {"status": "error", "message": str(exc), "table": table}

    breakdown = [
        {
            "group_key": r["group_key"],
            "gross_cost": r["gross_cost"],
            "net_cost": r["net_cost"],
            "currency": r["currency"],
        }
        for r in rows
    ]
    return {
        "table": table,
        "group_by": group_by.lower(),
        "days": days,
        "total_net_cost": round(sum(b["net_cost"] or 0 for b in breakdown), 2),
        "rows": breakdown,
    }


def get_billing_info() -> dict:
    """Return the billing account linked to the project and its status."""
    client = get_billing_client()
    project = require_project()
    info = client.get_project_billing_info(name=f"projects/{project}")
    return {
        "project": project,
        "billing_account": info.billing_account_name,
        "billing_enabled": info.billing_enabled,
    }


def register(mcp) -> None:
    mcp.tool()(get_cost_breakdown)
    mcp.tool()(get_billing_info)

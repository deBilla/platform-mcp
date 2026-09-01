"""Cost tools (read-only): BigQuery billing-export analysis and billing info."""

from __future__ import annotations

from google.api_core.exceptions import GoogleAPICallError

from ..clients import get_bigquery_client, get_billing_client
from ..errors import PlatformMCPError
from ..registration import register_tool
from ..config import list_environments, resolve_environment

# Whitelisted group-by columns -> billing export column expressions. Kept as a
# whitelist because column identifiers cannot be passed as query parameters.
_GROUP_BY_COLUMNS = {
    "service": "service.description",
    "sku": "sku.description",
    "project": "project.id",
    "region": "location.region",
}


def get_cost_breakdown(
    group_by: str = "service",
    days: int = 30,
    limit: int = 20,
    all_projects: bool = False,
    environment: str = "",
) -> dict:
    """Summarize recent spend from the BigQuery billing export.

    By default this reports spend for the selected environment's project only.
    A billing export table covers every project on the billing account, and
    several environments commonly share one table, so an unfiltered query would
    return identical account-wide totals for staging and production.

    Only the environment holding the export needs 'billing_export_table' set:
    one billing account exports to one project, and that export already covers
    every project on the account. Asking any other environment for costs returns
    an error naming the one that has it.

    Args:
        group_by: One of 'service', 'sku', 'project', 'region'. Default 'service'.
        days: Lookback window in days over usage_start_time. Default 30.
        limit: Max rows returned (highest net cost first).
        all_projects: Report the whole billing account instead of just this
            environment's project. Pair with group_by='project' to compare
            projects; the totals then are not specific to this environment.
        environment: Which configured GCP environment to bill against, e.g.
            'staging' or 'production'. Omit to use the default environment.
    """
    env = resolve_environment(environment)
    table = env.billing_export_table
    if not table:
        # One billing account usually exports to one project, so the other
        # environments legitimately have no table of their own. Point at the
        # environment that does rather than dead-ending: the export covers
        # every project on the account, so the answer is reachable.
        elsewhere = [e.name for e in list_environments() if e.billing_export_table]
        if elsewhere:
            names = ", ".join(f"'{n}'" for n in elsewhere)
            raise PlatformMCPError(
                f"Environment '{env.name}' has no billing export of its own. "
                f"Billing is exported once for the whole account, under {names}. "
                f"Call get_cost_breakdown(environment='{elsewhere[0]}', "
                "all_projects=true, group_by='project') to see every project's "
                f"spend, including {env.project}."
            )
        raise PlatformMCPError(
            "BigQuery billing export is not configured for any environment. "
            "Enable a usage cost export (Billing > Billing export) and set "
            "'billing_export_table' in the platform-mcp config."
        )

    column = _GROUP_BY_COLUMNS.get(group_by.lower())
    if column is None:
        raise ValueError(
            f"group_by must be one of {sorted(_GROUP_BY_COLUMNS)}, got '{group_by}'."
        )

    days = max(1, min(days, 365))
    limit = max(1, min(limit, 200))
    project_filter = "" if all_projects else "AND project.id = @project"
    sql = f"""
        SELECT
            {column} AS group_key,
            ROUND(SUM(cost), 2) AS gross_cost,
            ROUND(SUM(cost) + SUM(IFNULL((
                SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)), 2) AS net_cost,
            ANY_VALUE(currency) AS currency
        FROM `{table}`
        WHERE usage_start_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
        {project_filter}
        GROUP BY group_key
        ORDER BY net_cost DESC
        LIMIT @limit
    """

    from google.cloud import bigquery

    client = get_bigquery_client(env)
    parameters = [
        bigquery.ScalarQueryParameter("days", "INT64", days),
        bigquery.ScalarQueryParameter("limit", "INT64", limit),
    ]
    if not all_projects:
        parameters.append(
            bigquery.ScalarQueryParameter("project", "STRING", env.project)
        )
    job_config = bigquery.QueryJobConfig(query_parameters=parameters)
    try:
        rows = list(client.query(sql, job_config=job_config).result())
    except GoogleAPICallError as exc:
        # Raise rather than returning a success-shaped dict with a status
        # field: every other tool signals failure by raising, and the agent
        # needs one contract to reason about.
        raise PlatformMCPError(
            f"environment '{env.name}': querying the billing export table "
            f"'{table}' failed. The impersonated service account needs "
            "roles/bigquery.jobUser in this project and roles/bigquery.dataViewer "
            "on the dataset holding the export, which is often in a different "
            f"project.\nUnderlying error: {str(exc)[:300]}"
        ) from exc

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
        "environment": env.name,
        "project": env.project,
        "table": table,
        "group_by": group_by.lower(),
        "scope": "billing_account" if all_projects else f"project:{env.project}",
        "days": days,
        "total_net_cost": round(sum(b["net_cost"] or 0 for b in breakdown), 2),
        "rows": breakdown,
    }


def get_billing_info(environment: str = "") -> dict:
    """Return the billing account linked to the project and its status.

    Args:
        environment: Which configured GCP environment to inspect, e.g. 'staging'
            or 'production'. Omit to use the default environment.
    """
    env = resolve_environment(environment)
    client = get_billing_client(env)
    project = env.project
    info = client.get_project_billing_info(name=f"projects/{project}")
    return {
        "environment": env.name,
        "project": project,
        "billing_account": info.billing_account_name,
        "billing_enabled": info.billing_enabled,
    }


def register(mcp) -> None:
    register_tool(mcp, get_cost_breakdown)
    register_tool(mcp, get_billing_info)

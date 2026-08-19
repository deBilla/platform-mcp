"""Resource inventory tools (read-only) via Cloud Asset Inventory."""

from __future__ import annotations

from ..clients import get_asset_client
from ..config import resolve_environment
from ..formatting import parse_list_arg


def _format_resource(r) -> dict:
    labels = dict(getattr(r, "labels", {}) or {})
    additional = dict(getattr(r, "additional_attributes", {}) or {})
    return {
        "name": r.name.split("/")[-1] if r.name else "",
        "type": r.asset_type,
        "display_name": r.display_name or None,
        "location": r.location or None,
        "state": r.state or None,
        "labels": labels or None,
        "machine_type": additional.get("machineType") or None,
        "full_name": r.name,
    }


def search_assets(
    asset_types: str = "",
    query: str = "",
    limit: int = 100,
    environment: str = "",
) -> dict:
    """Search all cloud resources in the project via Cloud Asset Inventory.

    Args:
        asset_types: Optional comma-separated asset types to filter, e.g.
            'compute.googleapis.com/Instance,run.googleapis.com/Service'.
        query: Optional free-text/structured query, e.g. 'state:RUNNING' or
            'location:us-central1'.
        limit: Maximum number of resources to return.
        environment: Which configured GCP environment to search, e.g. 'staging'
            or 'production'. Omit to use the default environment. Call
            list_environments to see what is configured.
    """
    env = resolve_environment(environment)
    client = get_asset_client(env)
    project = env.project
    limit = max(1, min(limit, 500))

    request: dict = {"scope": f"projects/{project}", "page_size": min(limit, 500)}
    types = parse_list_arg(asset_types)
    if types:
        request["asset_types"] = types
    if query.strip():
        request["query"] = query.strip()

    resources = []
    for r in client.search_all_resources(request=request):
        resources.append(_format_resource(r))
        if len(resources) >= limit:
            break

    return {
        "environment": env.name,
        "project": project,
        "asset_types": types or "ALL",
        "count": len(resources),
        "resources": resources,
    }


def list_compute_instances(limit: int = 100, environment: str = "") -> dict:
    """List Compute Engine VM instances with location and status.

    Args:
        limit: Maximum number of instances to return.
        environment: Which configured GCP environment to query, e.g. 'staging'
            or 'production'. Omit to use the default environment.
    """
    return search_assets(
        "compute.googleapis.com/Instance", limit=limit, environment=environment
    )


def list_cloud_run_services(limit: int = 100, environment: str = "") -> dict:
    """List Cloud Run services.

    Args:
        limit: Maximum number of services to return.
        environment: Which configured GCP environment to query, e.g. 'staging'
            or 'production'. Omit to use the default environment.
    """
    return search_assets(
        "run.googleapis.com/Service", limit=limit, environment=environment
    )


def list_gke_clusters(limit: int = 100, environment: str = "") -> dict:
    """List GKE (Kubernetes Engine) clusters.

    Args:
        limit: Maximum number of clusters to return.
        environment: Which configured GCP environment to query, e.g. 'staging'
            or 'production'. Omit to use the default environment.
    """
    return search_assets(
        "container.googleapis.com/Cluster", limit=limit, environment=environment
    )


def list_sql_instances(limit: int = 100, environment: str = "") -> dict:
    """List Cloud SQL instances.

    Args:
        limit: Maximum number of instances to return.
        environment: Which configured GCP environment to query, e.g. 'staging'
            or 'production'. Omit to use the default environment.
    """
    return search_assets(
        "sqladmin.googleapis.com/Instance", limit=limit, environment=environment
    )


def register(mcp) -> None:
    mcp.tool()(search_assets)
    mcp.tool()(list_compute_instances)
    mcp.tool()(list_cloud_run_services)
    mcp.tool()(list_gke_clusters)
    mcp.tool()(list_sql_instances)

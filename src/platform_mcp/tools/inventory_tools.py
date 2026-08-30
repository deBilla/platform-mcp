"""Resource inventory tools (read-only) via Cloud Asset Inventory."""

from __future__ import annotations

from ..clients import get_asset_client
from ..registration import register_tool
from ..config import resolve_environment
from ..formatting import parse_list_arg


def _format_resource(r, include_labels: bool = False) -> dict:
    """Condense one Asset Inventory record.

    Measured against 77 live Cloud Run services, the original shape spent 42%
    of its bytes carrying the resource name three times -- display_name was
    identical to name in every record, and name is the last segment of
    full_name -- while state was empty in every record and labels (deployment
    metadata such as commit shas) were the single largest field at 46%.
    Repeating an identifier is not information, so empty and duplicate fields
    are dropped and labels are opt-in.
    """
    name = r.name.split("/")[-1] if r.name else ""
    additional = dict(getattr(r, "additional_attributes", {}) or {})
    row = {
        "name": name,
        "type": r.asset_type,
        # full_name is kept because follow-up API calls need the resource path.
        "full_name": r.name,
    }
    if r.display_name and r.display_name != name:
        row["display_name"] = r.display_name
    for key, value in (
        ("location", r.location),
        ("state", r.state),
        ("machine_type", additional.get("machineType")),
    ):
        if value:
            row[key] = value
    if include_labels:
        labels = dict(getattr(r, "labels", {}) or {})
        if labels:
            row["labels"] = labels
    return row


def search_assets(
    asset_types: str = "",
    query: str = "",
    limit: int = 50,
    include_labels: bool = False,
    environment: str = "",
) -> dict:
    """Search all cloud resources in the project via Cloud Asset Inventory.

    Args:
        asset_types: Optional comma-separated asset types to filter, e.g.
            'compute.googleapis.com/Instance,run.googleapis.com/Service'.
        query: Optional free-text/structured query, e.g. 'state:RUNNING' or
            'location:us-central1'.
        limit: Maximum number of resources to return. Default 50; raise it when
            you know you need a full inventory, since each resource costs
            context.
        include_labels: Include resource labels. Off by default because
            deployment labels are usually the largest part of the response and
            rarely answer the question being asked.
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
        resources.append(_format_resource(r, include_labels=include_labels))
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
    register_tool(mcp, search_assets)
    register_tool(mcp, list_compute_instances)
    register_tool(mcp, list_cloud_run_services)
    register_tool(mcp, list_gke_clusters)
    register_tool(mcp, list_sql_instances)

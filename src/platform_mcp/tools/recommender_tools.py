"""Recommender tools (read-only): cost and general GCP recommendations."""

from __future__ import annotations

from google.api_core.exceptions import GoogleAPICallError, NotFound, PermissionDenied

from ..clients import get_asset_client, get_recommender_client
from ..config import Environment, resolve_environment
from ..formatting import money_to_float, parse_list_arg, truncate

# Cost-related recommenders. Most are zonal/regional, so we fan out across the
# locations where the project actually has resources (discovered below).
COST_RECOMMENDERS = [
    "google.compute.instance.MachineTypeRecommender",
    "google.compute.instance.IdleResourceRecommender",
    "google.compute.disk.IdleResourceRecommender",
    "google.compute.address.IdleResourceRecommender",
    "google.compute.image.IdleResourceRecommender",
    "google.cloudsql.instance.IdleRecommender",
    "google.cloudsql.instance.OverprovisionedRecommender",
    "google.run.service.CostRecommender",
]

_LOCATION_ASSET_TYPES = [
    "compute.googleapis.com/Instance",
    "compute.googleapis.com/Disk",
    "compute.googleapis.com/Address",
    "compute.googleapis.com/Image",
    "sqladmin.googleapis.com/Instance",
    "run.googleapis.com/Service",
]


def _region_of(location: str) -> str | None:
    parts = location.split("-")
    if len(parts) >= 3:  # zone like us-central1-a -> region us-central1
        return "-".join(parts[:2])
    return None


def _discover_locations(env: Environment) -> list[str]:
    """Find zones/regions where the project has cost-relevant resources."""
    locations: set[str] = set()
    try:
        client = get_asset_client(env)
        results = client.search_all_resources(
            request={
                "scope": f"projects/{env.project}",
                "asset_types": _LOCATION_ASSET_TYPES,
                "page_size": 500,
            }
        )
        for r in results:
            loc = getattr(r, "location", "") or ""
            if not loc or loc == "global":
                continue
            locations.add(loc)
            region = _region_of(loc)
            if region:
                locations.add(region)
    except (GoogleAPICallError, Exception):
        pass
    return sorted(locations)


def _format_recommendation(rec) -> dict:
    impact = rec.primary_impact
    cost = None
    if impact and impact.cost_projection and impact.cost_projection.cost:
        # Cost savings are represented as a negative cost delta.
        cost = money_to_float(impact.cost_projection.cost)
    return {
        "name": rec.name.split("/")[-1],
        "description": truncate(rec.description, 300),
        "category": impact.category.name if impact else None,
        "monthly_cost_impact": cost,
        "currency": (
            impact.cost_projection.cost.currency_code
            if impact and impact.cost_projection and impact.cost_projection.cost
            else None
        ),
        "state": rec.state_info.state.name if rec.state_info else None,
        "priority": rec.priority.name if rec.priority else None,
    }


def list_recommendations(
    recommender_id: str,
    location: str = "global",
    limit: int = 50,
    environment: str = "",
) -> dict:
    """List recommendations from a specific recommender at a specific location.

    Args:
        recommender_id: Recommender id, e.g.
            'google.compute.instance.MachineTypeRecommender' or
            'google.iam.policy.Recommender'.
        location: Zone (e.g. 'us-central1-a'), region (e.g. 'us-central1'), or
            'global' depending on the recommender. Default 'global'.
        limit: Maximum number of recommendations to return.
        environment: Which configured GCP environment to query, e.g. 'staging'
            or 'production'. Omit to use the default environment.
    """
    env = resolve_environment(environment)
    client = get_recommender_client(env)
    project = env.project
    parent = f"projects/{project}/locations/{location}/recommenders/{recommender_id}"
    items = []
    for rec in client.list_recommendations(parent=parent):
        items.append(_format_recommendation(rec))
        if len(items) >= limit:
            break
    return {
        "environment": env.name,
        "project": project,
        "recommender": recommender_id,
        "location": location,
        "count": len(items),
        "recommendations": items,
    }


def list_cost_recommendations(
    locations: str = "",
    limit_per_call: int = 50,
    environment: str = "",
) -> dict:
    """Aggregate GCP cost-optimization recommendations (idle/rightsizing/etc).

    Fans out the cost recommenders across the locations where the project has
    resources. Recommenders/locations that are empty or not enabled are skipped.

    Args:
        locations: Optional comma-separated zones/regions to scan (e.g.
            'us-central1,us-central1-a'). If empty, auto-discovers from Asset
            Inventory (requires the Cloud Asset API).
        limit_per_call: Max recommendations to pull per recommender+location.
        environment: Which configured GCP environment to query, e.g. 'staging'
            or 'production'. Omit to use the default environment.
    """
    env = resolve_environment(environment)
    client = get_recommender_client(env)
    project = env.project

    loc_list = parse_list_arg(locations) or _discover_locations(env)
    if not loc_list:
        return {
            "environment": env.name,
            "project": project,
            "status": "no_locations",
            "message": (
                "Could not determine any resource locations to scan. Pass "
                "'locations' explicitly (e.g. 'us-central1,us-central1-a'), and "
                "ensure the Cloud Asset API is enabled for auto-discovery."
            ),
            "recommendations": [],
        }

    findings = []
    skipped = 0
    total_savings = 0.0
    for recommender_id in COST_RECOMMENDERS:
        for location in loc_list:
            parent = (
                f"projects/{project}/locations/{location}/recommenders/{recommender_id}"
            )
            try:
                pulled = 0
                for rec in client.list_recommendations(parent=parent):
                    item = _format_recommendation(rec)
                    item["recommender"] = recommender_id
                    item["location"] = location
                    findings.append(item)
                    if item["monthly_cost_impact"]:
                        total_savings += item["monthly_cost_impact"]
                    pulled += 1
                    if pulled >= limit_per_call:
                        break
            except (NotFound, PermissionDenied, GoogleAPICallError):
                skipped += 1
                continue

    findings.sort(key=lambda x: x.get("monthly_cost_impact") or 0)
    # Savings are negative cost deltas; report as a positive number.
    return {
        "environment": env.name,
        "project": project,
        "locations_scanned": loc_list,
        "count": len(findings),
        "skipped_calls": skipped,
        "estimated_monthly_savings": round(-total_savings, 2),
        "recommendations": findings,
    }


def register(mcp) -> None:
    mcp.tool()(list_cost_recommendations)
    mcp.tool()(list_recommendations)

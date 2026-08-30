"""Recommender tools (read-only): cost and general GCP recommendations."""

from __future__ import annotations

import anyio
from google.api_core.exceptions import GoogleAPICallError, NotFound, PermissionDenied
from mcp.server.fastmcp import Context

from ..clients import get_asset_client, get_recommender_client
from ..errors import PlatformMCPError
from ..registration import register_tool
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


def _discover_locations(env: Environment) -> tuple[list[str], str]:
    """Find zones/regions holding cost-relevant resources.

    Returns the locations and an error string. A failure here used to be
    swallowed, which made a permissions problem look like a project with
    nothing to optimize -- the worst possible answer from a cost tool.
    """
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
    except GoogleAPICallError as exc:
        return sorted(locations), f"{type(exc).__name__}: {str(exc)[:200]}"
    return sorted(locations), ""


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


async def list_cost_recommendations(
    locations: str = "",
    limit_per_call: int = 50,
    environment: str = "",
    ctx: Context | None = None,
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

    loc_list = parse_list_arg(locations)
    discovery_error = ""
    if not loc_list:
        loc_list, discovery_error = _discover_locations(env)
    if not loc_list:
        detail = (
            f" Location discovery failed with: {discovery_error}"
            if discovery_error
            else " Ensure the Cloud Asset API is enabled for auto-discovery."
        )
        raise PlatformMCPError(
            f"environment '{env.name}': could not determine any resource "
            "locations to scan, so no recommendations could be gathered. This "
            "is not the same as having no savings available. Pass 'locations' "
            "explicitly (e.g. 'us-central1,us-central1-a')." + detail
        )

    def _pull(parent: str) -> list[dict]:
        items = []
        for rec in client.list_recommendations(parent=parent):
            items.append(_format_recommendation(rec))
            if len(items) >= limit_per_call:
                break
        return items

    findings = []
    skipped = 0
    total_savings = 0.0
    total_calls = len(COST_RECOMMENDERS) * len(loc_list)
    done = 0
    for recommender_id in COST_RECOMMENDERS:
        for location in loc_list:
            parent = (
                f"projects/{project}/locations/{location}/recommenders/{recommender_id}"
            )
            try:
                # The client is synchronous; running it inline would block the
                # event loop and stop progress notifications from being sent.
                pulled = await anyio.to_thread.run_sync(_pull, parent)
                for item in pulled:
                    item["recommender"] = recommender_id
                    item["location"] = location
                    findings.append(item)
                    if item["monthly_cost_impact"]:
                        total_savings += item["monthly_cost_impact"]
            except (NotFound, PermissionDenied, GoogleAPICallError):
                skipped += 1
            done += 1
            if ctx is not None:
                await ctx.report_progress(
                    done, total_calls, f"{recommender_id.split('.')[-1]} in {location}"
                )

    # Tell the model about partial coverage rather than leaving it as a number
    # in the payload it may not read: an incomplete scan must not be summarised
    # as "no savings available".
    if ctx is not None and skipped:
        await ctx.warning(
            f"{skipped} of {total_calls} recommender/location combinations could "
            "not be read (API disabled or permission denied). These results are "
            "partial; do not report them as a complete picture."
        )

    findings.sort(key=lambda x: x.get("monthly_cost_impact") or 0)
    # Savings are negative cost deltas; report as a positive number.
    return {
        "environment": env.name,
        "project": project,
        "locations_scanned": loc_list,
        "location_discovery_error": discovery_error or None,
        "count": len(findings),
        "skipped_calls": skipped,
        "estimated_monthly_savings": round(-total_savings, 2),
        "recommendations": findings,
    }


def register(mcp) -> None:
    register_tool(mcp, list_cost_recommendations)
    register_tool(mcp, list_recommendations)

"""Environment discovery (read-only).

Lets the agent see which GCP environments this server can reach before it picks
one, so a prompt like "check staging" resolves to a real configured target
instead of a guess.
"""

from __future__ import annotations

from ..config import get_settings


def list_environments() -> dict:
    """List the GCP environments this server can query and which is the default.

    Pass one of the returned names as the `environment` argument of any other
    tool to target that project (e.g. environment='production'). Omitting the
    argument uses the default environment shown here. Common shorthands such as
    'prod' and 'stg' are accepted, as is a bare project id.
    """
    settings = get_settings()
    return {
        "default_environment": settings.default_environment,
        "count": len(settings.environments),
        "environments": [
            {
                "name": env.name,
                "project": env.project,
                "is_default": env.name == settings.default_environment,
                "aliases": list(env.aliases) or None,
                "impersonated_service_account": env.impersonate or None,
                "billing_export_configured": bool(env.billing_export_table),
            }
            for env in settings.environments
        ],
    }


def register(mcp) -> None:
    mcp.tool()(list_environments)

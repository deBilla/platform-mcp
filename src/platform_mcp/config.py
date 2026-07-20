"""Runtime settings for the platform-mcp server.

Resolves the target GCP project and a few defaults from the environment,
falling back to whatever project Application Default Credentials provide.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

_PROJECT_ENV_VARS = (
    "GCP_PROJECT",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_QUOTA_PROJECT",
    "GCLOUD_PROJECT",
)


@dataclass(frozen=True)
class Settings:
    project: str
    default_location: str = "global"
    default_limit: int = 50


def _resolve_project() -> str:
    for var in _PROJECT_ENV_VARS:
        value = os.environ.get(var)
        if value:
            return value
    # Fall back to the ADC-associated project so the server works with a bare
    # `gcloud auth application-default login`.
    try:
        import google.auth

        _, project = google.auth.default()
        if project:
            return str(project)
    except Exception:
        pass
    return ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    limit_raw = os.environ.get("PLATFORM_MCP_DEFAULT_LIMIT", "50")
    try:
        default_limit = max(1, int(limit_raw))
    except ValueError:
        default_limit = 50
    return Settings(
        project=_resolve_project(),
        default_location=os.environ.get("GCP_LOCATION", "global"),
        default_limit=default_limit,
    )


def require_project() -> str:
    """Return the configured project id, raising a clear error if unset."""
    project = get_settings().project
    if not project:
        raise RuntimeError(
            "No GCP project configured. Set GCP_PROJECT (or run "
            "`gcloud auth application-default set-quota-project <id>`)."
        )
    return project

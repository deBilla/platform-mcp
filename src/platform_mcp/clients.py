"""Lazy, per-environment GCP client factory.

Base credentials are resolved once via Application Default Credentials. If
``GOOGLE_APPLICATION_CREDENTIALS`` points at a service-account key, google-auth
picks it up automatically. Each configured environment may name its own
read-only service account to impersonate, so staging and production are reached
through separate identities from the same process.

Every getter takes the resolved :class:`~platform_mcp.config.Environment` and is
cached per environment: switching back and forth costs nothing after the first
call, and no client is ever shared across projects.

Every client returned here is read-only in practice: the server never calls a
mutating method, and this is paired with viewer-only IAM identities.
"""

from __future__ import annotations

from functools import lru_cache

import google.auth
from google.auth import impersonated_credentials

from .config import Environment

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

# Number of distinct environments to keep clients for. Generous relative to any
# realistic registry, so caches never thrash between staging and production.
_CACHE_SIZE = 16


@lru_cache(maxsize=1)
def _base_credentials():
    creds, _ = google.auth.default(scopes=_SCOPES)
    return creds


@lru_cache(maxsize=_CACHE_SIZE)
def get_credentials(impersonate: str = ""):
    """Credentials for a target identity; impersonates when one is configured."""
    creds = _base_credentials()
    if impersonate:
        creds = impersonated_credentials.Credentials(
            source_credentials=creds,
            target_principal=impersonate,
            target_scopes=_SCOPES,
        )
    return creds


def _creds_for(env: Environment):
    return get_credentials(env.impersonate)


@lru_cache(maxsize=_CACHE_SIZE)
def get_logging_client(env: Environment):
    from google.cloud import logging_v2

    return logging_v2.Client(project=env.project, credentials=_creds_for(env))


@lru_cache(maxsize=_CACHE_SIZE)
def get_error_stats_client(env: Environment):
    from google.cloud import errorreporting_v1beta1

    return errorreporting_v1beta1.ErrorStatsServiceClient(credentials=_creds_for(env))


@lru_cache(maxsize=_CACHE_SIZE)
def get_metric_client(env: Environment):
    from google.cloud import monitoring_v3

    return monitoring_v3.MetricServiceClient(credentials=_creds_for(env))


@lru_cache(maxsize=_CACHE_SIZE)
def get_alert_policy_client(env: Environment):
    from google.cloud import monitoring_v3

    return monitoring_v3.AlertPolicyServiceClient(credentials=_creds_for(env))


@lru_cache(maxsize=_CACHE_SIZE)
def get_uptime_client(env: Environment):
    from google.cloud import monitoring_v3

    return monitoring_v3.UptimeCheckServiceClient(credentials=_creds_for(env))


@lru_cache(maxsize=_CACHE_SIZE)
def get_recommender_client(env: Environment):
    from google.cloud import recommender_v1

    return recommender_v1.RecommenderClient(credentials=_creds_for(env))


@lru_cache(maxsize=_CACHE_SIZE)
def get_asset_client(env: Environment):
    from google.cloud import asset_v1

    return asset_v1.AssetServiceClient(credentials=_creds_for(env))


@lru_cache(maxsize=_CACHE_SIZE)
def get_billing_client(env: Environment):
    from google.cloud import billing_v1

    return billing_v1.CloudBillingClient(credentials=_creds_for(env))


@lru_cache(maxsize=_CACHE_SIZE)
def get_bigquery_client(env: Environment):
    from google.cloud import bigquery

    return bigquery.Client(project=env.project, credentials=_creds_for(env))

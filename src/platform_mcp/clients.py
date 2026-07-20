"""Lazy, cached GCP client factory.

Credentials are resolved once via Application Default Credentials. If
``GOOGLE_APPLICATION_CREDENTIALS`` points at a service-account key, google-auth
picks it up automatically. Optionally, set ``IMPERSONATE_SERVICE_ACCOUNT`` to a
read-only service account email to impersonate it without downloading a key.

Every client returned here is read-only in practice: the server never calls a
mutating method, and Phase 2 pairs this with a viewer-only IAM identity.
"""

from __future__ import annotations

import os
from functools import lru_cache

import google.auth
from google.auth import impersonated_credentials

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


@lru_cache(maxsize=1)
def get_credentials():
    creds, _ = google.auth.default(scopes=_SCOPES)
    target = os.environ.get("IMPERSONATE_SERVICE_ACCOUNT")
    if target:
        creds = impersonated_credentials.Credentials(
            source_credentials=creds,
            target_principal=target,
            target_scopes=_SCOPES,
        )
    return creds


@lru_cache(maxsize=1)
def get_logging_client():
    from google.cloud import logging_v2

    from .config import require_project

    return logging_v2.Client(project=require_project(), credentials=get_credentials())


@lru_cache(maxsize=1)
def get_error_stats_client():
    from google.cloud import errorreporting_v1beta1

    return errorreporting_v1beta1.ErrorStatsServiceClient(credentials=get_credentials())


@lru_cache(maxsize=1)
def get_metric_client():
    from google.cloud import monitoring_v3

    return monitoring_v3.MetricServiceClient(credentials=get_credentials())


@lru_cache(maxsize=1)
def get_alert_policy_client():
    from google.cloud import monitoring_v3

    return monitoring_v3.AlertPolicyServiceClient(credentials=get_credentials())


@lru_cache(maxsize=1)
def get_uptime_client():
    from google.cloud import monitoring_v3

    return monitoring_v3.UptimeCheckServiceClient(credentials=get_credentials())


@lru_cache(maxsize=1)
def get_recommender_client():
    from google.cloud import recommender_v1

    return recommender_v1.RecommenderClient(credentials=get_credentials())


@lru_cache(maxsize=1)
def get_asset_client():
    from google.cloud import asset_v1

    return asset_v1.AssetServiceClient(credentials=get_credentials())


@lru_cache(maxsize=1)
def get_billing_client():
    from google.cloud import billing_v1

    return billing_v1.CloudBillingClient(credentials=get_credentials())


@lru_cache(maxsize=1)
def get_bigquery_client():
    from google.cloud import bigquery

    from .config import require_project

    return bigquery.Client(project=require_project(), credentials=get_credentials())

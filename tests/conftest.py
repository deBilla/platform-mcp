"""Shared fixtures.

Settings are cached with ``lru_cache`` so the server reads its configuration
once per process. Tests change that configuration constantly, so every fixture
clears the caches on the way in and on the way out; without that, test order
would decide the result.
"""

from __future__ import annotations

import json

import pytest

from platform_mcp import config

TEST_ENVIRONMENTS = {
    "staging": {
        "project": "demo-staging",
        "impersonate": "ro@demo-staging.iam.gserviceaccount.com",
    },
    "production": {
        "project": "demo-prod",
        "impersonate": "ro@demo-prod.iam.gserviceaccount.com",
        "billing_export_table": "demo-billing.billing.export_v1",
        "aliases": ["live"],
    },
}


def clear_caches() -> None:
    config.get_settings.cache_clear()
    config._lookup_table.cache_clear()


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    """Point every test at a throwaway config and no ambient environment."""
    for name in (
        "PLATFORM_MCP_ENVIRONMENTS",
        "PLATFORM_MCP_DEFAULT_ENVIRONMENT",
        "PLATFORM_MCP_DEFAULT_LIMIT",
        "GCP_PROJECT",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_QUOTA_PROJECT",
        "GCLOUD_PROJECT",
        "IMPERSONATE_SERVICE_ACCOUNT",
        "BILLING_EXPORT_TABLE",
    ):
        monkeypatch.delenv(name, raising=False)
    # An absent path, so a real config file on the developer's machine can
    # never leak into a test run.
    monkeypatch.setenv("PLATFORM_MCP_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("PLATFORM_MCP_AUDIT_LOG", "off")
    clear_caches()
    yield
    clear_caches()


@pytest.fixture
def configured(monkeypatch):
    """Two environments registered through the environment variable."""
    monkeypatch.setenv("PLATFORM_MCP_ENVIRONMENTS", json.dumps(TEST_ENVIRONMENTS))
    monkeypatch.setenv("PLATFORM_MCP_DEFAULT_ENVIRONMENT", "staging")
    clear_caches()
    yield
    clear_caches()

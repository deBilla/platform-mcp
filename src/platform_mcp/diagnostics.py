"""``platform-mcp doctor``: check that this machine can actually reach GCP.

Every prerequisite here fails at a different layer -- credentials, IAM, API
enablement, cross-project BigQuery grants -- and each one produces a different
opaque error at the first tool call. Checking them up front, per environment,
with the fix printed next to the failure, is the difference between onboarding
in a minute and onboarding in a support thread.

This runs as a CLI command, not over the protocol, so printing to stdout here
is safe. The server itself must never do that.
"""

from __future__ import annotations

import sys

from .config import Environment, config_path, get_settings

OK = "  ok  "
FAIL = " FAIL "
SKIP = " skip "


def _line(status: str, message: str) -> None:
    print(f"[{status}] {message}")


def _fix(text: str) -> None:
    for line in text.strip().splitlines():
        print(f"         {line}")


def _check_adc() -> bool:
    try:
        import google.auth

        credentials, project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    except Exception as exc:
        _line(FAIL, "Application Default Credentials")
        _fix(f"{exc}\nFix:  gcloud auth application-default login")
        return False
    kind = type(credentials).__name__
    _line(OK, f"Application Default Credentials ({kind}, quota project: {project or 'unset'})")
    if not project:
        _fix(
            "No quota project is set. Some APIs bill quota to it and will fail "
            "without one.\nFix:  gcloud auth application-default set-quota-project PROJECT_ID"
        )
    return True


def _check_impersonation(env: Environment) -> bool:
    if not env.impersonate:
        _line(SKIP, "impersonation not configured (using your own credentials)")
        return True
    from google.auth.transport.requests import Request

    from .clients import get_credentials

    try:
        credentials = get_credentials(env.impersonate)
        credentials.refresh(Request())
    except Exception as exc:
        _line(FAIL, f"impersonate {env.impersonate}")
        _fix(
            f"{str(exc)[:200]}\n"
            "Fix:  gcloud iam service-accounts add-iam-policy-binding \\\n"
            f"        {env.impersonate} \\\n"
            "        --member=user:YOUR_EMAIL \\\n"
            "        --role=roles/iam.serviceAccountTokenCreator"
        )
        return False
    _line(OK, f"impersonate {env.impersonate}")
    return True


def _check_read_api(env: Environment) -> bool:
    """One real read, so API enablement and viewer roles are proven, not assumed."""
    try:
        from google.cloud import logging_v2

        from .clients import get_logging_client

        client = get_logging_client(env)
        entries = client.list_entries(
            filter_="timestamp>=\"1970-01-01T00:00:00Z\"",
            order_by=logging_v2.DESCENDING,
            max_results=1,
            page_size=1,
        )
        next(iter(entries), None)
    except Exception as exc:
        _line(FAIL, f"read Cloud Logging in {env.project}")
        _fix(
            f"{str(exc)[:200]}\n"
            f"Fix:  grant roles/logging.viewer on {env.project} to "
            f"{env.impersonate or 'your user'}, and enable the Cloud Logging API."
        )
        return False
    _line(OK, f"read Cloud Logging in {env.project}")
    return True


def _check_billing_export(env: Environment) -> bool:
    if not env.billing_export_table:
        _line(SKIP, "billing export not configured (get_cost_breakdown unavailable)")
        return True
    try:
        from google.cloud import bigquery

        from .clients import get_bigquery_client

        client = get_bigquery_client(env)
        # A dry run costs nothing and still proves both grants: permission to
        # start a job here, and permission to read a table that usually lives
        # in a different project.
        job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        client.query(
            f"SELECT cost FROM `{env.billing_export_table}` LIMIT 1",
            job_config=job_config,
        )
    except Exception as exc:
        _line(FAIL, f"read billing export {env.billing_export_table}")
        _fix(
            f"{str(exc)[:200]}\n"
            f"Fix:  the identity needs roles/bigquery.jobUser on {env.project} "
            "AND roles/bigquery.dataViewer on the dataset holding the export "
            "(often a different project)."
        )
        return False
    _line(OK, f"read billing export {env.billing_export_table}")
    return True


def run_doctor() -> int:
    """Print a per-environment readiness report. Returns a process exit code."""
    path = config_path()
    print("platform-mcp doctor")
    print(f"config file: {path}{'' if path.exists() else '  (not found)'}")
    print()

    try:
        settings = get_settings()
    except Exception as exc:
        _line(FAIL, "configuration")
        _fix(str(exc))
        return 1

    healthy = _check_adc()
    print()

    for env in settings.environments:
        marker = " (default)" if env.name == settings.default_environment else ""
        print(f"environment: {env.name}{marker} -> {env.project}")
        results = [
            _check_impersonation(env),
            _check_read_api(env),
            _check_billing_export(env),
        ]
        healthy = healthy and all(results)
        print()

    if healthy:
        print("All checks passed.")
        return 0
    print("Some checks failed. Fix the items marked FAIL above, then re-run.")
    return 1


def main() -> None:  # pragma: no cover - thin CLI wrapper
    sys.exit(run_doctor())

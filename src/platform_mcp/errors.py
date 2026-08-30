"""Turn opaque GCP auth failures into instructions the agent can relay.

The client reading these messages is a language model, so an error that names
the environment, the identity and the exact command to run gets passed on to
the user as a fix. "Your default credentials were not found" does not.
"""

from __future__ import annotations


class PlatformMCPError(RuntimeError):
    """An error whose message is written to be read by an agent and a human."""


def _environment_hint(environment: str) -> tuple[str, str]:
    """Best-effort (label, impersonated SA) for the environment in play."""
    try:
        from .config import resolve_environment

        env = resolve_environment(environment)
        return f"environment '{env.name}' (project {env.project})", env.impersonate
    except Exception:
        return "the requested environment", ""


def _adc_message(label: str) -> str:
    return (
        f"{label}: no Application Default Credentials were found. "
        "Ask the user to run:\n"
        "    gcloud auth application-default login\n"
        "Then retry. If they use a service-account key instead, set "
        "GOOGLE_APPLICATION_CREDENTIALS to its path."
    )


def _impersonation_message(label: str, service_account: str, detail: str) -> str:
    return (
        f"{label}: could not impersonate the read-only service account "
        f"'{service_account}'. The signed-in user needs the Service Account "
        "Token Creator role on it. Ask an admin to run:\n"
        f"    gcloud iam service-accounts add-iam-policy-binding {service_account} \\\n"
        f"      --member=user:USER_EMAIL \\\n"
        "      --role=roles/iam.serviceAccountTokenCreator\n"
        f"Underlying error: {detail}"
    )


def _refresh_message(label: str) -> str:
    return (
        f"{label}: the stored credentials could not be refreshed, usually "
        "because they expired or were revoked. Ask the user to run:\n"
        "    gcloud auth application-default login"
    )


def explain_exception(exc: Exception, environment: str = "") -> Exception:
    """Return a clearer error for auth failures, or the original exception."""
    try:
        from google.auth import exceptions as auth_exceptions
    except ImportError:  # pragma: no cover - google-auth is a hard dependency
        return exc

    label, service_account = _environment_hint(environment)
    detail = str(exc)

    if isinstance(exc, auth_exceptions.DefaultCredentialsError):
        return PlatformMCPError(_adc_message(label))

    if isinstance(exc, auth_exceptions.RefreshError):
        # An impersonation denial arrives as a RefreshError wrapping a 403 from
        # the IAM Credentials API, which is a different fix from expiry.
        if service_account and (
            "iam.serviceAccounts.getAccessToken" in detail
            or "403" in detail
            or "Permission" in detail
        ):
            return PlatformMCPError(
                _impersonation_message(label, service_account, detail[:300])
            )
        return PlatformMCPError(_refresh_message(label))

    try:
        from google.api_core import exceptions as api_exceptions
    except ImportError:  # pragma: no cover
        return exc

    if isinstance(exc, api_exceptions.PermissionDenied):
        identity = service_account or "the signed-in user"
        return PlatformMCPError(
            f"{label}: permission denied. {identity} is missing a read role for "
            f"this API. Grant a viewer role on the project and retry.\n"
            f"Underlying error: {detail[:300]}"
        )

    if isinstance(exc, api_exceptions.NotFound):
        return PlatformMCPError(
            f"{label}: the requested resource does not exist. Check the project "
            f"and any resource name in the arguments.\nUnderlying error: {detail[:300]}"
        )

    return exc

"""Credential failures must arrive as instructions, not as stack traces.

The client reading these strings is a language model that will relay them to a
person, so each assertion checks for the thing that person needs: the command
to run, or the role to grant.
"""

from __future__ import annotations

import pytest
from google.api_core import exceptions as api_exceptions
from google.auth import exceptions as auth_exceptions

from platform_mcp.errors import PlatformMCPError, explain_exception

pytestmark = pytest.mark.usefixtures("configured")


def test_missing_credentials_gives_the_login_command():
    original = auth_exceptions.DefaultCredentialsError("not found")
    explained = explain_exception(original, "staging")
    assert isinstance(explained, PlatformMCPError)
    assert "gcloud auth application-default login" in str(explained)
    assert "staging" in str(explained)


def test_impersonation_denial_names_the_account_and_the_role():
    original = auth_exceptions.RefreshError(
        "403 Permission 'iam.serviceAccounts.getAccessToken' denied"
    )
    explained = explain_exception(original, "production")
    message = str(explained)
    assert "ro@demo-prod.iam.gserviceaccount.com" in message
    assert "roles/iam.serviceAccountTokenCreator" in message


def test_expired_credentials_are_distinguished_from_a_missing_grant():
    original = auth_exceptions.RefreshError("token has expired")
    message = str(explain_exception(original, "staging"))
    assert "gcloud auth application-default login" in message
    assert "TokenCreator" not in message


def test_permission_denied_names_the_identity():
    original = api_exceptions.PermissionDenied("caller lacks permission")
    message = str(explain_exception(original, "production"))
    assert "ro@demo-prod.iam.gserviceaccount.com" in message
    assert "demo-prod" in message


def test_unrelated_errors_pass_through_untouched():
    original = ValueError("something else entirely")
    assert explain_exception(original, "staging") is original


def test_explaining_never_raises_when_the_environment_is_unknown():
    original = auth_exceptions.DefaultCredentialsError("nope")
    # An unresolvable environment must not turn a helpful error into a crash.
    message = str(explain_exception(original, "does-not-exist"))
    assert "gcloud auth application-default login" in message

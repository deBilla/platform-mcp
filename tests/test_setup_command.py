"""`platform-mcp setup` must work from an installed package.

The setup script used to live in the repository, so the quickstart began with a
clone performed to obtain one file. These tests hold it inside the package:
resolvable through importlib.resources (not a repo-relative path), runnable,
and forwarding both arguments and exit status.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from importlib import resources

import pytest

from platform_mcp.provisioning import SCRIPT_NAME, run_setup

BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="no bash")


def pretend_gcloud_exists(monkeypatch):
    """Keep the real bash, fake only the gcloud probe."""
    monkeypatch.setattr(
        shutil, "which", lambda name: BASH if name == "bash" else f"/usr/bin/{name}"
    )


def script_path():
    return resources.files("platform_mcp").joinpath("scripts", SCRIPT_NAME)


def test_script_ships_with_the_package():
    assert script_path().is_file()


@needs_bash
def test_script_is_valid_bash():
    # Parse-only: catches a syntax error without touching gcloud.
    assert subprocess.call(["bash", "-n", str(script_path())]) == 0


@needs_bash
def test_help_documents_the_installed_invocation(capfd, monkeypatch):
    pretend_gcloud_exists(monkeypatch)
    assert run_setup(["--help"]) == 0
    out = capfd.readouterr().out
    assert "platform-mcp setup" in out
    assert "--project" in out
    # The old repo-relative invocation must not survive in the help text.
    assert "./scripts/" not in out


@needs_bash
def test_a_failing_script_fails_the_command(monkeypatch):
    pretend_gcloud_exists(monkeypatch)
    assert run_setup(["--nonsense"]) == 1


def test_missing_gcloud_is_reported_not_crashed(capfd, monkeypatch):
    monkeypatch.setattr(
        shutil, "which", lambda name: None if name == "gcloud" else f"/usr/bin/{name}"
    )
    assert run_setup(["--project", "demo"]) == 1
    assert "gcloud" in capfd.readouterr().err


def test_setup_is_dispatched_before_argparse(monkeypatch):
    """argparse would reject --project; the subcommand must intercept first."""
    from platform_mcp import provisioning, server

    seen: list[list[str]] = []
    monkeypatch.setattr(provisioning, "run_setup", lambda args: seen.append(args) or 0)
    monkeypatch.setattr(sys, "argv", ["platform-mcp", "setup", "--project", "demo"])
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 0
    assert seen == [["--project", "demo"]]

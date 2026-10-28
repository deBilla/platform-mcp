"""Environment resolution.

The failure this guards against is the expensive one: a production question
answered with staging data. Nothing in the response would look wrong, so the
resolution rules are asserted directly.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from platform_mcp import config
from platform_mcp.config import resolve_environment

from conftest import TEST_ENVIRONMENTS, clear_caches


@pytest.mark.usefixtures("configured")
class TestResolution:
    def test_default_is_used_when_omitted(self):
        assert resolve_environment().name == "staging"

    def test_exact_names(self):
        assert resolve_environment("production").project == "demo-prod"
        assert resolve_environment("staging").project == "demo-staging"

    @pytest.mark.parametrize("spoken", ["prod", "prd", "live", "PRODUCTION", " prod "])
    def test_production_shorthands(self, spoken):
        assert resolve_environment(spoken).name == "production"

    @pytest.mark.parametrize("spoken", ["stg", "stage", "qa", "Staging"])
    def test_staging_shorthands(self, spoken):
        assert resolve_environment(spoken).name == "staging"

    def test_project_id_resolves(self):
        assert resolve_environment("demo-prod").name == "production"

    def test_unknown_name_raises_and_never_falls_back(self):
        with pytest.raises(ValueError) as excinfo:
            resolve_environment("prodution")  # typo
        message = str(excinfo.value)
        assert "prodution" in message
        # The message must list the real options so the agent can retry.
        assert "production" in message and "staging" in message

    def test_impersonation_and_billing_carry_through(self):
        env = resolve_environment("production")
        assert env.impersonate == "ro@demo-prod.iam.gserviceaccount.com"
        assert env.billing_export_table == "demo-billing.billing.export_v1"


class TestDefaults:
    def test_staging_preferred_when_no_default_declared(self, monkeypatch):
        monkeypatch.setenv("PLATFORM_MCP_ENVIRONMENTS", json.dumps(TEST_ENVIRONMENTS))
        clear_caches()
        assert config.get_settings().default_environment == "staging"

    def test_unknown_default_is_rejected_at_load(self, monkeypatch):
        monkeypatch.setenv("PLATFORM_MCP_ENVIRONMENTS", json.dumps(TEST_ENVIRONMENTS))
        monkeypatch.setenv("PLATFORM_MCP_DEFAULT_ENVIRONMENT", "nope")
        clear_caches()
        with pytest.raises(RuntimeError, match="nope"):
            config.get_settings()

    def test_bare_string_is_shorthand_for_project(self, monkeypatch):
        monkeypatch.setenv("PLATFORM_MCP_ENVIRONMENTS", json.dumps({"dev": "just-a-project"}))
        clear_caches()
        assert resolve_environment("dev").project == "just-a-project"

    def test_malformed_json_names_the_variable(self, monkeypatch):
        monkeypatch.setenv("PLATFORM_MCP_ENVIRONMENTS", "{not json")
        clear_caches()
        with pytest.raises(RuntimeError, match="PLATFORM_MCP_ENVIRONMENTS"):
            config.get_settings()

    def test_environment_without_project_is_rejected(self, monkeypatch):
        monkeypatch.setenv("PLATFORM_MCP_ENVIRONMENTS", json.dumps({"broken": {}}))
        clear_caches()
        with pytest.raises(RuntimeError, match="project"):
            config.get_settings()

    @pytest.mark.parametrize(
        "registry, shared",
        [
            # The same alias on two environments (issue #9).
            (
                {
                    "staging": {"project": "demo-staging", "aliases": ["main"]},
                    "production": {"project": "demo-prod", "aliases": ["main"]},
                },
                "main",
            ),
            # An alias that is another environment's project id.
            (
                {
                    "staging": {"project": "demo-staging", "aliases": ["demo-prod"]},
                    "production": {"project": "demo-prod"},
                },
                "demo-prod",
            ),
            # An alias that is another environment's name.
            (
                {
                    "staging": {"project": "demo-staging", "aliases": ["production"]},
                    "production": {"project": "demo-prod"},
                },
                "production",
            ),
        ],
    )
    def test_ambiguous_alias_is_rejected_at_load(self, monkeypatch, registry, shared):
        monkeypatch.setenv("PLATFORM_MCP_ENVIRONMENTS", json.dumps(registry))
        clear_caches()
        with pytest.raises(RuntimeError, match=f"'{shared}'.*'staging' and 'production'"):
            config.get_settings()

    def test_shared_project_id_dropped_from_lookup(self, monkeypatch):
        monkeypatch.setenv(
            "PLATFORM_MCP_ENVIRONMENTS",
            json.dumps({"staging": "demo-shared", "production": "demo-shared"}),
        )
        clear_caches()
        config.get_settings()
        assert resolve_environment("staging").project == "demo-shared"
        assert resolve_environment("production").project == "demo-shared"
        with pytest.raises(ValueError, match="Unknown environment 'demo-shared'"):
            resolve_environment("demo-shared")

    def test_alias_repeating_its_own_name_or_project_is_fine(self, monkeypatch):
        monkeypatch.setenv(
            "PLATFORM_MCP_ENVIRONMENTS",
            json.dumps(
                {
                    "staging": {"project": "demo-staging", "aliases": ["staging", "demo-staging"]},
                    "production": {"project": "demo-prod", "aliases": ["live"]},
                }
            ),
        )
        clear_caches()
        assert resolve_environment("demo-staging").name == "staging"
        assert resolve_environment("live").name == "production"
        # Built-in shorthands keep their "only if unambiguous" rule.
        assert resolve_environment("prod").name == "production"


class TestConfigFile:
    def _write(self, monkeypatch, tmp_path, body: str):
        path = tmp_path / "config.toml"
        path.write_text(body, encoding="utf-8")
        monkeypatch.setenv("PLATFORM_MCP_CONFIG", str(path))
        clear_caches()
        return path

    def test_environments_load_from_toml(self, monkeypatch, tmp_path):
        self._write(
            monkeypatch,
            tmp_path,
            """
            default_environment = "production"

            [environments.staging]
            project = "file-staging"

            [environments.production]
            project = "file-prod"
            impersonate = "ro@file-prod.iam.gserviceaccount.com"
            """,
        )
        settings = config.get_settings()
        assert settings.default_environment == "production"
        assert resolve_environment("staging").project == "file-staging"
        assert resolve_environment("prod").impersonate.startswith("ro@file-prod")

    def test_environment_variable_overrides_the_file(self, monkeypatch, tmp_path):
        self._write(
            monkeypatch,
            tmp_path,
            """
            [environments.staging]
            project = "from-file"
            """,
        )
        monkeypatch.setenv(
            "PLATFORM_MCP_ENVIRONMENTS", json.dumps({"staging": "from-env"})
        )
        clear_caches()
        assert resolve_environment("staging").project == "from-env"

    def test_invalid_toml_names_the_file(self, monkeypatch, tmp_path):
        path = self._write(monkeypatch, tmp_path, "this is = = not toml")
        with pytest.raises(RuntimeError, match=str(path.name)):
            config.get_settings()

    def test_missing_file_is_not_an_error(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PLATFORM_MCP_CONFIG", str(tmp_path / "nope.toml"))
        monkeypatch.setenv("PLATFORM_MCP_ENVIRONMENTS", json.dumps({"dev": "p"}))
        clear_caches()
        assert resolve_environment("dev").project == "p"


def test_every_setting_is_read_somewhere():
    """A Settings field nothing reads is a setting that looks configurable and is not.

    default_limit and default_location were parsed from the environment and
    documented, but every tool hard-codes its own default, so setting them did
    nothing. Add a field only together with the code that reads it.
    """
    package = Path(config.__file__).parent
    consumers = "\n".join(
        path.read_text(encoding="utf-8")
        for path in package.rglob("*.py")
        if path != Path(config.__file__)
    )
    unused = [
        field.name
        for field in dataclasses.fields(config.Settings)
        if f".{field.name}" not in consumers
    ]
    assert unused == []

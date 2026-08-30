"""Runtime settings for the platform-mcp server.

Holds a registry of named GCP environments (staging, production, ...) so a
single server process can answer questions about any of them. Every tool takes
an optional ``environment`` argument that is resolved here; when it is omitted
the configured default environment is used.

The registry comes from ``PLATFORM_MCP_ENVIRONMENTS``, a JSON object mapping an
environment name to its settings::

    {
      "staging": {
        "project": "my-app-staging",
        "impersonate": "platform-mcp-ro@my-app-staging.iam.gserviceaccount.com"
      },
      "production": {
        "project": "my-app",
        "impersonate": "platform-mcp-ro@my-app.iam.gserviceaccount.com",
        "billing_export_table": "my-app.billing.gcp_billing_export_v1_XXXXXX",
        "aliases": ["live"]
      }
    }

A value may also be a bare project-id string when no impersonation is needed.
If ``PLATFORM_MCP_ENVIRONMENTS`` is unset the server falls back to the original
single-project behaviour (``GCP_PROJECT`` + ``IMPERSONATE_SERVICE_ACCOUNT``),
exposed as one environment named ``default``.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# Where the config file lives when PLATFORM_MCP_CONFIG does not name one.
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "platform-mcp" / "config.toml"

_PROJECT_ENV_VARS = (
    "GCP_PROJECT",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_QUOTA_PROJECT",
    "GCLOUD_PROJECT",
)

# Spoken shorthands an agent is likely to pick up from a prompt ("check prod").
# Listed in both directions so the environment can be named either way, and
# only applied when they do not collide with a real environment name.
_BUILTIN_ALIASES = {
    "prod": "production",
    "prd": "production",
    "live": "production",
    "production": "prod",
    "stg": "staging",
    "stage": "staging",
    "qa": "staging",
    "staging": "stage",
    "dev": "development",
    "development": "dev",
    "test": "testing",
    "testing": "test",
}


@dataclass(frozen=True)
class Environment:
    """One resolvable GCP target: a project plus how to authenticate to it."""

    name: str
    project: str
    impersonate: str = ""
    billing_export_table: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Settings:
    environments: tuple[Environment, ...]
    default_environment: str
    default_location: str = "global"
    default_limit: int = 50


def _adc_project() -> str:
    """Project associated with Application Default Credentials, if any."""
    try:
        import google.auth

        _, project = google.auth.default()
        return str(project) if project else ""
    except Exception:
        return ""


def _resolve_single_project() -> str:
    for var in _PROJECT_ENV_VARS:
        value = os.environ.get(var)
        if value:
            return value
    # Fall back to the ADC-associated project so the server still works with a
    # bare `gcloud auth application-default login` and no explicit config.
    return _adc_project()


def _parse_registry(raw: str) -> tuple[Environment, ...]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"PLATFORM_MCP_ENVIRONMENTS is not valid JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict) or not parsed:
        raise RuntimeError(
            "PLATFORM_MCP_ENVIRONMENTS must be a non-empty JSON object mapping "
            'environment name -> settings, e.g. {"staging": {"project": "..."}}'
        )

    # Global values act as the fallback for environments that don't set them,
    # which keeps a pre-existing single-project config working unchanged.
    global_impersonate = os.environ.get("IMPERSONATE_SERVICE_ACCOUNT", "").strip()
    global_billing = os.environ.get("BILLING_EXPORT_TABLE", "").strip()

    environments = []
    for name, spec in parsed.items():
        key = str(name).strip().lower()
        if not key:
            continue
        if isinstance(spec, str):
            spec = {"project": spec}
        if not isinstance(spec, dict):
            raise RuntimeError(
                f"PLATFORM_MCP_ENVIRONMENTS['{name}'] must be an object or a "
                "project-id string."
            )
        project = str(spec.get("project", "")).strip()
        if not project:
            raise RuntimeError(
                f"PLATFORM_MCP_ENVIRONMENTS['{name}'] is missing a 'project'."
            )
        aliases = spec.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        environments.append(
            Environment(
                name=key,
                project=project,
                impersonate=str(spec.get("impersonate", "") or global_impersonate).strip(),
                billing_export_table=str(
                    spec.get("billing_export_table", "") or global_billing
                ).strip(),
                aliases=tuple(str(a).strip().lower() for a in aliases if str(a).strip()),
            )
        )
    if not environments:
        raise RuntimeError("PLATFORM_MCP_ENVIRONMENTS defined no usable environments.")
    return tuple(environments)


def config_path() -> Path:
    """Path of the config file, whether or not it exists."""
    override = os.environ.get("PLATFORM_MCP_CONFIG", "").strip()
    return Path(override).expanduser() if override else DEFAULT_CONFIG_PATH


def _load_config_file() -> dict:
    """Read the TOML config file, or return an empty mapping if there is none.

    A config file is far kinder than a JSON blob squeezed into an environment
    variable, and it can be committed and shared across a team. Environment
    variables still win, so an existing setup keeps working and a one-off
    override needs no edit.
    """
    path = config_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise RuntimeError(f"Could not read config file {path}: {exc}") from exc
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"Config file {path} is not valid TOML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Config file {path} must define a TOML table.")
    return parsed


def _environments_from_file(file_config: dict) -> tuple[Environment, ...]:
    table = file_config.get("environments")
    if not table:
        return ()
    if not isinstance(table, dict):
        raise RuntimeError(
            "The [environments] section of the config file must be a table of "
            'named environments, e.g. [environments.staging].'
        )
    # Reuse the JSON parser so file and environment variable cannot drift apart.
    return _parse_registry(json.dumps(table))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    file_config = _load_config_file()

    limit_raw = os.environ.get("PLATFORM_MCP_DEFAULT_LIMIT", "").strip()
    if not limit_raw:
        limit_raw = str(file_config.get("default_limit", 50))
    try:
        default_limit = max(1, int(limit_raw))
    except ValueError:
        default_limit = 50

    raw_registry = os.environ.get("PLATFORM_MCP_ENVIRONMENTS", "").strip()
    file_environments = _environments_from_file(file_config)
    if raw_registry:
        environments = _parse_registry(raw_registry)
    elif file_environments:
        environments = file_environments
    else:
        environments = (
            Environment(
                name="default",
                project=_resolve_single_project(),
                impersonate=os.environ.get("IMPERSONATE_SERVICE_ACCOUNT", "").strip(),
                billing_export_table=os.environ.get("BILLING_EXPORT_TABLE", "").strip(),
            ),
        )

    requested_default = os.environ.get("PLATFORM_MCP_DEFAULT_ENVIRONMENT", "").strip().lower()
    if not requested_default:
        requested_default = str(file_config.get("default_environment", "")).strip().lower()
    known = {e.name for e in environments}
    if requested_default and requested_default not in known:
        raise RuntimeError(
            f"PLATFORM_MCP_DEFAULT_ENVIRONMENT='{requested_default}' is not one of "
            f"the configured environments: {sorted(known)}"
        )
    # With no explicit default, prefer the safest target when it exists rather
    # than silently defaulting to whichever key happened to be listed first.
    if requested_default:
        default_environment = requested_default
    elif "staging" in known:
        default_environment = "staging"
    else:
        default_environment = environments[0].name

    return Settings(
        environments=environments,
        default_environment=default_environment,
        default_location=os.environ.get("GCP_LOCATION", "global"),
        default_limit=default_limit,
    )


@lru_cache(maxsize=1)
def _lookup_table() -> dict[str, Environment]:
    """Map every accepted spelling of an environment to its Environment."""
    environments = get_settings().environments
    table: dict[str, Environment] = {}
    for env in environments:
        table[env.name] = env
        for alias in env.aliases:
            table.setdefault(alias, env)
        # Let the agent name the project id directly ("check my-app-prod").
        if env.project:
            table.setdefault(env.project.lower(), env)
    for alias, canonical in _BUILTIN_ALIASES.items():
        if canonical in table:
            table.setdefault(alias, table[canonical])
    return table


def list_environments() -> tuple[Environment, ...]:
    return get_settings().environments


def resolve_environment(name: str = "") -> Environment:
    """Resolve an environment name (or alias, or project id) to an Environment.

    An empty name yields the default environment. Unknown names raise a
    ``ValueError`` naming the valid options rather than silently falling back,
    so a typo can never send a production question to staging or vice versa.
    """
    settings = get_settings()
    key = (name or "").strip().lower()
    table = _lookup_table()

    if not key:
        env = table.get(settings.default_environment)
        if env is None:  # pragma: no cover - guarded by get_settings()
            raise RuntimeError("No environments are configured.")
    else:
        env = table.get(key)
        if env is None:
            valid = sorted({e.name for e in settings.environments})
            raise ValueError(
                f"Unknown environment '{name}'. Configured environments: "
                f"{', '.join(valid)}."
            )

    if not env.project:
        raise RuntimeError(
            f"Environment '{env.name}' has no GCP project configured. Set "
            "PLATFORM_MCP_ENVIRONMENTS (or GCP_PROJECT for single-project mode)."
        )
    return env


def require_project(environment: str = "") -> str:
    """Return the project id for an environment, raising if it is unset."""
    return resolve_environment(environment).project


def describe_environments() -> str:
    """One-line-per-environment summary used in the server instructions."""
    settings = get_settings()
    try:
        environments = settings.environments
    except RuntimeError:  # pragma: no cover - defensive
        return ""
    lines = []
    for env in environments:
        marker = " (default)" if env.name == settings.default_environment else ""
        lines.append(f"- {env.name}{marker}: project {env.project}")
    return "\n".join(lines)

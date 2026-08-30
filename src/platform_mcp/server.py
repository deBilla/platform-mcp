"""platform-mcp: a read-only GCP platform-engineer MCP server.

Exposes read-only tools over Cloud Logging, Error Reporting, Cloud Monitoring,
Recommender, Billing/BigQuery, and Cloud Asset Inventory so an agent can debug
incidents and surface cost-optimization recommendations. No mutating tool is
registered; pair with a viewer-only identity for a hard guarantee.

Every tool takes an optional ``environment`` argument, so one server can answer
questions about staging and production in the same conversation.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import __version__
from .config import describe_environments
from .observability import configure_logging, logger
from .tools import (
    cost_tools,
    environment_tools,
    error_reporting_tools,
    inventory_tools,
    logging_tools,
    monitoring_tools,
    recommender_tools,
)


def _instructions() -> str:
    base = (
        "Read-only GCP platform-engineering tools.\n\n"
        "Every tool accepts an optional `environment` argument selecting which "
        "GCP project to query. Set it from the user's wording (e.g. 'in prod', "
        "'on staging'); omit it to use the default environment. Never assume a "
        "guessed name — call list_environments if unsure. Each result echoes "
        "back the `environment` and `project` it came from; when comparing "
        "environments, call the same tool once per environment."
    )
    try:
        configured = describe_environments()
    except Exception:
        # A malformed registry should surface when a tool runs, not break
        # server startup and hide every tool from the client.
        configured = ""
    return f"{base}\n\nConfigured environments:\n{configured}" if configured else base


mcp = FastMCP("platform-mcp", instructions=_instructions())

for module in (
    environment_tools,
    logging_tools,
    error_reporting_tools,
    monitoring_tools,
    recommender_tools,
    cost_tools,
    inventory_tools,
):
    module.register(mcp)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="platform-mcp",
        description=(
            "Read-only GCP MCP server. With no arguments it serves the Model "
            "Context Protocol over stdio, which is how an MCP client starts it."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "command",
        nargs="?",
        choices=["serve", "doctor"],
        default="serve",
        help="'serve' (default) runs the server; 'doctor' checks GCP access.",
    )
    args = parser.parse_args()

    if args.command == "doctor":
        from .diagnostics import run_doctor

        raise SystemExit(run_doctor())

    configure_logging()
    logger.info("starting platform-mcp %s", __version__)
    mcp.run()


if __name__ == "__main__":
    main()

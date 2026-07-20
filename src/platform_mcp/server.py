"""platform-mcp: a read-only GCP platform-engineer MCP server.

Exposes read-only tools over Cloud Logging, Error Reporting, Cloud Monitoring,
Recommender, Billing/BigQuery, and Cloud Asset Inventory so an agent can debug
incidents and surface cost-optimization recommendations. No mutating tool is
registered; pair with a viewer-only identity for a hard guarantee.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .tools import (
    cost_tools,
    error_reporting_tools,
    inventory_tools,
    logging_tools,
    monitoring_tools,
    recommender_tools,
)

mcp = FastMCP("platform-mcp")

for module in (
    logging_tools,
    error_reporting_tools,
    monitoring_tools,
    recommender_tools,
    cost_tools,
    inventory_tools,
):
    module.register(mcp)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

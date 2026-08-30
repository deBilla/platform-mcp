"""The MCP contract, exercised through a real client session.

``create_connected_server_and_client_session`` wires a client to the server
over in-memory streams, so these assertions cover the same path a real client
takes -- schema generation, annotations, error shape -- without a subprocess,
a network, or GCP credentials.
"""

from __future__ import annotations

import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from platform_mcp.server import mcp

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("configured")]

EXPECTED_TOOLS = {
    "list_environments",
    "query_logs",
    "get_recent_errors",
    "list_error_groups",
    "query_metric",
    "list_alert_policies",
    "list_uptime_checks",
    "list_cost_recommendations",
    "list_recommendations",
    "get_cost_breakdown",
    "get_billing_info",
    "search_assets",
    "list_compute_instances",
    "list_cloud_run_services",
    "list_gke_clusters",
    "list_sql_instances",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _tools():
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        return (await client.list_tools()).tools


async def test_every_tool_is_exposed():
    names = {t.name for t in await _tools()}
    assert names == EXPECTED_TOOLS


async def test_all_tools_declare_themselves_read_only():
    for tool in await _tools():
        annotations = tool.annotations
        assert annotations is not None, f"{tool.name} has no annotations"
        assert annotations.readOnlyHint is True, tool.name
        assert annotations.destructiveHint is False, tool.name


async def test_only_config_tools_are_closed_world():
    closed = {t.name for t in await _tools() if t.annotations.openWorldHint is False}
    # list_environments reports this server's own config and touches nothing.
    assert closed == {"list_environments"}


async def test_every_tool_documents_itself():
    for tool in await _tools():
        assert tool.description, f"{tool.name} has no description"
        if tool.name != "list_environments":
            assert "environment" in tool.inputSchema["properties"], tool.name


async def test_instrumentation_did_not_alter_schemas():
    tools = {t.name: t for t in await _tools()}
    # metric_type is the one genuinely required argument in the whole server.
    assert tools["query_metric"].inputSchema["required"] == ["metric_type"]
    assert "all_projects" in tools["get_cost_breakdown"].inputSchema["properties"]
    assert tools["list_environments"].inputSchema["properties"] == {}


async def test_list_environments_reports_configuration():
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        result = await client.call_tool("list_environments", {})
    assert result.isError is False
    # Tools are annotated ``-> dict``, which FastMCP serialises as JSON text
    # rather than structured content; this is what a client actually receives.
    payload = json.loads(result.content[0].text)
    assert payload["default_environment"] == "staging"
    assert {e["name"] for e in payload["environments"]} == {"staging", "production"}


async def test_unknown_environment_is_an_error_naming_the_options():
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        result = await client.call_tool("query_logs", {"environment": "nope"})
    assert result.isError is True
    text = result.content[0].text
    assert "nope" in text and "staging" in text and "production" in text


async def test_environment_summary_lists_projects_and_the_default():
    # The server bakes this into its instructions at startup, so the summary
    # itself is what gets asserted rather than the frozen instruction string.
    from platform_mcp.config import describe_environments

    summary = describe_environments()
    assert "staging (default): project demo-staging" in summary
    assert "production: project demo-prod" in summary

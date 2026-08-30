"""Exercise every tool through a real MCP client session and measure it.

Goes through the protocol rather than calling functions directly, so what is
measured is exactly what a client receives: serialised payload size, latency,
and the shape of errors.

Raw responses are written to scratchpad only -- they contain live log lines and
resource names and must be redacted before any of it becomes a committed test
fixture.
"""

import json
import os
import pathlib
import sys
import time

import anyio

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = pathlib.Path(os.environ.get("MEASURE_OUT", pathlib.Path(__file__).parent / "measurements"))
ENV = sys.argv[1] if len(sys.argv) > 1 else "staging"

# Reads whatever configuration the server itself would read: the config file,
# or a local .mcp.json when running from a checkout.
local = REPO / ".mcp.json"
if local.exists() and "PLATFORM_MCP_ENVIRONMENTS" not in os.environ:
    cfg = json.load(open(local))["mcpServers"]["platform-mcp"].get("env", {})
    os.environ.update({k: v for k, v in cfg.items() if k.startswith("PLATFORM_MCP_")})
os.environ["PLATFORM_MCP_AUDIT_LOG"] = str(OUT / "audit.jsonl")
os.environ["PLATFORM_MCP_LOG_LEVEL"] = "WARNING"

from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402
from platform_mcp.server import mcp  # noqa: E402

# Arguments a real agent would plausibly send: defaults where the tool has
# them, one required value where it does not.
CALLS = [
    ("list_environments", {}),
    ("query_logs", {"freshness": "1h", "limit": 50}),
    ("query_logs", {"filter": "severity>=WARNING", "freshness": "6h", "limit": 50}),
    ("get_recent_errors", {"hours": 24, "limit": 50}),
    ("list_error_groups", {"hours": 24, "limit": 25}),
    ("query_metric", {"metric_type": "run.googleapis.com/request_count", "window": "1h"}),
    ("list_alert_policies", {}),
    ("list_uptime_checks", {}),
    ("list_recommendations", {"recommender_id": "google.iam.policy.Recommender"}),
    ("get_billing_info", {}),
    ("get_cost_breakdown", {"group_by": "service", "days": 30}),
    ("search_assets", {"limit": 100}),
    ("list_compute_instances", {}),
    ("list_cloud_run_services", {}),
    ("list_gke_clusters", {}),
    ("list_sql_instances", {}),
    ("list_cost_recommendations", {}),  # slowest; last
]


def summarise(name, args, text, is_error, ms):
    row = {
        "tool": name,
        "args": {k: v for k, v in args.items() if k != "environment"},
        "ms": round(ms, 1),
        "bytes": len(text),
        "error": bool(is_error),
    }
    if is_error:
        row["error_head"] = text.strip().splitlines()[0][:120]
        return row
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        row["note"] = "non-JSON payload"
        return row
    if isinstance(payload, dict):
        for k in ("count", "series_count", "series_truncated", "skipped_calls",
                  "points_per_series_cap", "scope", "period_applied"):
            if k in payload:
                row[k] = payload[k]
        # Rough token proxy: ~4 characters per token for JSON.
        row["est_tokens"] = round(len(text) / 4)
    return row


async def main():
    OUT.mkdir(exist_ok=True)
    rows = []
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        for name, args in CALLS:
            call_args = dict(args, environment=ENV)
            started = time.perf_counter()
            try:
                result = await client.call_tool(name, call_args)
                ms = (time.perf_counter() - started) * 1000
                text = result.content[0].text if result.content else ""
                row = summarise(name, args, text, result.isError, ms)
                (OUT / f"{ENV}__{name}__{len(rows)}.json").write_text(text)
            except Exception as exc:
                ms = (time.perf_counter() - started) * 1000
                row = {"tool": name, "args": args, "ms": round(ms, 1),
                       "bytes": 0, "error": True, "error_head": str(exc)[:120]}
            rows.append(row)
            flag = "ERR " if row["error"] else "    "
            print(f"  {flag}{row['ms']:>8.0f}ms {row['bytes']:>8}B  {name}", flush=True)

    (OUT / f"summary-{ENV}.json").write_text(json.dumps(rows, indent=2))
    return rows


rows = anyio.run(main)

ok = [r for r in rows if not r["error"]]
print(f"\n{len(ok)}/{len(rows)} succeeded")
if ok:
    print(f"total payload: {sum(r['bytes'] for r in ok):,} bytes "
          f"(~{sum(r.get('est_tokens', 0) for r in ok):,} tokens)")
    slow = sorted(ok, key=lambda r: -r["ms"])[:3]
    big = sorted(ok, key=lambda r: -r["bytes"])[:3]
    print("slowest: " + ", ".join(f"{r['tool']} {r['ms']:.0f}ms" for r in slow))
    print("largest: " + ", ".join(f"{r['tool']} {r['bytes']:,}B" for r in big))

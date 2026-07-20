# platform-mcp

A **read-only** [Model Context Protocol](https://modelcontextprotocol.io) server that turns an AI agent (Claude Code, Claude Desktop, or any MCP client) into a GCP platform engineer. Point it at a Google Cloud project and ask it to investigate incidents, take inventory, and surface cost-optimization opportunities — all without any ability to change your infrastructure.

> **Observation only.** No tool in this server mutates state. Combined with a viewer-only identity (below), that gives you a hard, defense-in-depth guarantee that an agent can look but never touch.

## What it can do

| Area | Tools |
| --- | --- |
| **Logs & errors** | `query_logs`, `get_recent_errors`, `list_error_groups` |
| **Metrics & alerting** | `query_metric`, `list_alert_policies`, `list_uptime_checks` |
| **Cost & recommendations** | `get_cost_breakdown`, `get_billing_info`, `list_cost_recommendations`, `list_recommendations` |
| **Resource inventory** | `search_assets`, `list_compute_instances`, `list_cloud_run_services`, `list_gke_clusters`, `list_sql_instances` |

Typical prompts once it's connected:

- *"What are the top error groups in the last 24 hours, and which one is newest?"*
- *"Which GKE node pools are over-provisioned? Show mean CPU against machine type."*
- *"Where can I reduce spend in this project?"*

## Requirements

- Python 3.11+
- A Google Cloud project and credentials (your own login, or a service account)
- The [`gcloud` CLI](https://cloud.google.com/sdk/docs/install) for the one-time setup

## Install

```bash
git clone https://github.com/deBilla/platform-mcp.git
cd platform-mcp
python3 -m venv .venv
./.venv/bin/pip install -e .
```

## One-time GCP setup

**1. Enable the APIs the tools depend on:**

```bash
gcloud services enable \
  logging.googleapis.com monitoring.googleapis.com clouderrorreporting.googleapis.com \
  recommender.googleapis.com cloudasset.googleapis.com cloudbilling.googleapis.com \
  bigquery.googleapis.com \
  --project YOUR_PROJECT_ID
```

**2. Grant read-only access to the identity the server runs as.**

For local development with your own login (Application Default Credentials):

```bash
gcloud auth application-default login
```

The identity needs these viewer roles on the project, plus `roles/billing.viewer`
on the billing account:

```
roles/viewer                # broad read (compute, run, gke, sql via Asset Inventory)
roles/logging.viewer
roles/monitoring.viewer
roles/errorreporting.viewer
roles/recommender.viewer
roles/cloudasset.viewer
roles/bigquery.dataViewer    # only for get_cost_breakdown
roles/bigquery.jobUser       # only for get_cost_breakdown
```

**3. (Recommended) Use a dedicated read-only service account** instead of your login:

```bash
PROJECT=YOUR_PROJECT_ID
gcloud iam service-accounts create platform-mcp-ro \
  --display-name "platform-mcp read-only" --project $PROJECT

SA=platform-mcp-ro@$PROJECT.iam.gserviceaccount.com
for ROLE in roles/viewer roles/logging.viewer roles/monitoring.viewer \
  roles/errorreporting.viewer roles/recommender.viewer roles/cloudasset.viewer; do
  gcloud projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:$SA" --role="$ROLE"
done

# Let your own login impersonate it (no key file to manage):
gcloud iam service-accounts add-iam-policy-binding $SA \
  --member="user:you@example.com" \
  --role="roles/iam.serviceAccountTokenCreator" --project $PROJECT
```

Then impersonate it (preferred — no key file):

```bash
export IMPERSONATE_SERVICE_ACCOUNT=$SA
```

…or point at a downloaded key via `GOOGLE_APPLICATION_CREDENTIALS`.

## Security model

Read-only is enforced by **IAM, not by OAuth scope.** The server requests the
broad `cloud-platform` scope and stays read-only purely because it never calls a
mutating API. **Do not rely on the code alone** — run it under a viewer-only
identity (step 3 above) so the credential itself is incapable of writing,
regardless of what code executes. This gives you two independent layers: the
server doesn't try to write, and the identity couldn't if it did.

## Configuration

Copy the example config and fill in your values:

```bash
cp .mcp.json.example .mcp.json
```

`.mcp.json` is git-ignored, so your project id and service-account email stay
local. Environment variables it (or your shell) can set:

| Variable | Purpose |
| --- | --- |
| `GCP_PROJECT` | Target project. Falls back to your ADC default project if unset. |
| `IMPERSONATE_SERVICE_ACCOUNT` | Read-only SA to impersonate (no key file needed). |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to a read-only SA key file (alternative to impersonation). |
| `BILLING_EXPORT_TABLE` | Fully-qualified BigQuery billing export table, required only for `get_cost_breakdown` (e.g. `YOUR_PROJECT_ID.billing.gcp_billing_export_v1_XXXXXX`). |
| `PLATFORM_MCP_DEFAULT_LIMIT` | Default max rows for list-style tools (default 50). |

## Register with a client

**Claude Code / Claude Desktop** — add the block from `.mcp.json.example` to your
MCP config (`.mcp.json` in a project for Claude Code, or
`claude_desktop_config.json` for Desktop), pointing `command` at the venv's
console script so no global install is needed:

```json
{
  "mcpServers": {
    "platform-mcp": {
      "command": "/absolute/path/to/platform-mcp/.venv/bin/platform-mcp",
      "env": {
        "GCP_PROJECT": "YOUR_PROJECT_ID",
        "IMPERSONATE_SERVICE_ACCOUNT": "platform-mcp-ro@YOUR_PROJECT_ID.iam.gserviceaccount.com"
      }
    }
  }
}
```

**MCP Inspector** — for interactive testing:

```bash
./.venv/bin/mcp dev src/platform_mcp/server.py
```

## Notes

- All tools cap result counts and truncate long payloads to stay token-friendly.
- Cost recommenders are zonal/regional; `list_cost_recommendations` auto-discovers
  the locations where you have resources (via Asset Inventory) and fans out,
  skipping locations and recommenders that are empty or unavailable.
- `get_cost_breakdown` uses parameterized BigQuery queries with a whitelisted set
  of group-by columns.

## License

[MIT](LICENSE) © 2026 Dimuthu Wickramanayake

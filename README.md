# platform-mcp

A **read-only** [Model Context Protocol](https://modelcontextprotocol.io) server that turns an AI agent (Claude Code, Claude Desktop, or any MCP client) into a GCP platform engineer. Point it at your Google Cloud projects and ask it to investigate incidents, take inventory, and surface cost-optimization opportunities — all without any ability to change your infrastructure.

> **Observation only.** No tool in this server mutates state. Combined with a viewer-only identity (below), that gives you a hard, defense-in-depth guarantee that an agent can look but never touch.

## What it can do

| Area | Tools |
| --- | --- |
| **Environments** | `list_environments` |
| **Logs & errors** | `query_logs`, `get_recent_errors`, `list_error_groups` |
| **Metrics & alerting** | `query_metric`, `list_alert_policies`, `list_uptime_checks` |
| **Cost & recommendations** | `get_cost_breakdown`, `get_billing_info`, `list_cost_recommendations`, `list_recommendations` |
| **Resource inventory** | `search_assets`, `list_compute_instances`, `list_cloud_run_services`, `list_gke_clusters`, `list_sql_instances` |

Typical prompts once it's connected:

- *"What are the top error groups in the last 24 hours, and which one is newest?"*
- *"Which GKE node pools are over-provisioned? Show mean CPU against machine type."*
- *"Where can I reduce spend in this project?"*

## Multiple environments

One server can reach several projects. Define them under
`PLATFORM_MCP_ENVIRONMENTS` (see [Configuration](#configuration)) and the agent
picks one from the wording of your prompt:

- *"Any errors in **staging** in the last hour?"*
- *"Compare Cloud Run services between **staging** and **prod**."*

Every tool takes an optional `environment` argument. Omit it and the default
environment is used; pass `environment="production"` to target another. Names,
any aliases you define, common shorthands (`prod`, `stg`, `qa`, …) and bare
project ids all resolve. An unrecognized name is an error listing the valid
options — a typo can never silently retarget the wrong project.

Each environment carries its own service account, so staging and production are
reached through separate identities from the same process, and every result
echoes back the `environment` and `project` it came from.

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

Run these once **per project** you want to reach — staging and production each
need their own APIs enabled and their own read-only service account.

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

Then reference it as that environment's `impersonate` value in
`PLATFORM_MCP_ENVIRONMENTS` (preferred — no key file), or point at a downloaded
key via `GOOGLE_APPLICATION_CREDENTIALS`.

> Impersonation is performed by whatever identity your ADC resolves to. If your
> ADC is itself an impersonated service account, that SA — not your user — needs
> `roles/iam.serviceAccountTokenCreator` on each `platform-mcp-ro`.

## Security model

Read-only is enforced by **IAM, not by OAuth scope.** The server requests the
broad `cloud-platform` scope and stays read-only purely because it never calls a
mutating API. **Do not rely on the code alone** — run it under a viewer-only
identity (step 3 above) so the credential itself is incapable of writing,
regardless of what code executes. This gives you two independent layers: the
server doesn't try to write, and the identity couldn't if it did.

With multiple environments this stays per-project: each environment
authenticates as its own service account, so a staging identity is never used
to reach production. Grant each one viewer-only access to its project alone.

## Configuration

Copy the example config and fill in your values:

```bash
cp .mcp.json.example .mcp.json
```

`.mcp.json` is git-ignored, so your project ids and service-account emails stay
local. Environment variables it (or your shell) can set:

| Variable | Purpose |
| --- | --- |
| `PLATFORM_MCP_ENVIRONMENTS` | JSON map of environment name → settings. The recommended way to configure the server. |
| `PLATFORM_MCP_DEFAULT_ENVIRONMENT` | Environment used when a tool call omits `environment`. Defaults to `staging` if configured, else the first entry. |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to a read-only SA key file (alternative to impersonation). |
| `PLATFORM_MCP_DEFAULT_LIMIT` | Default max rows for list-style tools (default 50). |

`PLATFORM_MCP_ENVIRONMENTS` holds a JSON object; each entry accepts:

| Key | Purpose |
| --- | --- |
| `project` | **Required.** GCP project id. |
| `impersonate` | Read-only SA to impersonate for this environment (no key file needed). |
| `billing_export_table` | Fully-qualified BigQuery billing export table, required only for `get_cost_breakdown` (e.g. `YOUR_PROJECT_ID.billing.gcp_billing_export_v1_XXXXXX`). |
| `aliases` | Extra names the agent may use for this environment. |

A bare string value is shorthand for `{"project": "..."}`. As JSON inside
`.mcp.json` the quotes must be escaped; unescaped it reads:

```json
{
  "staging": {
    "project": "my-app-staging",
    "impersonate": "platform-mcp-ro@my-app-staging.iam.gserviceaccount.com"
  },
  "production": {
    "project": "my-app",
    "impersonate": "platform-mcp-ro@my-app.iam.gserviceaccount.com",
    "billing_export_table": "my-app.billing.gcp_billing_export_v1_XXXXXX"
  }
}
```

**Single-environment mode.** If `PLATFORM_MCP_ENVIRONMENTS` is unset the server
behaves as before, exposing one environment named `default`:

| Variable | Purpose |
| --- | --- |
| `GCP_PROJECT` | Target project. Falls back to your ADC default project if unset. |
| `IMPERSONATE_SERVICE_ACCOUNT` | Read-only SA to impersonate. Also the fallback for registry entries with no `impersonate`. |
| `BILLING_EXPORT_TABLE` | Billing export table. Also the fallback for registry entries with no `billing_export_table`. |

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
        "PLATFORM_MCP_DEFAULT_ENVIRONMENT": "staging",
        "PLATFORM_MCP_ENVIRONMENTS": "{\"staging\":{\"project\":\"YOUR_STAGING_PROJECT_ID\",\"impersonate\":\"platform-mcp-ro@YOUR_STAGING_PROJECT_ID.iam.gserviceaccount.com\"},\"production\":{\"project\":\"YOUR_PROD_PROJECT_ID\",\"impersonate\":\"platform-mcp-ro@YOUR_PROD_PROJECT_ID.iam.gserviceaccount.com\"}}"
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
- GCP clients are built lazily and cached per environment, so switching between
  staging and production mid-conversation costs one client construction each.
- Cost recommenders are zonal/regional; `list_cost_recommendations` auto-discovers
  the locations where you have resources (via Asset Inventory) and fans out,
  skipping locations and recommenders that are empty or unavailable.
- `get_cost_breakdown` uses parameterized BigQuery queries with a whitelisted set
  of group-by columns.

## License

[MIT](LICENSE) © 2026 Dimuthu Wickramanayake

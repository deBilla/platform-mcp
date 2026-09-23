# platform-mcp

<!-- Identifier for the official MCP registry; must match server.json. -->
mcp-name: io.github.deBilla/platform-mcp

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
uvx platform-mcp          # no install step; uv fetches it on demand
pipx install platform-mcp # or keep it on PATH
```

From a checkout, for development:

```bash
git clone https://github.com/deBilla/platform-mcp.git
cd platform-mcp
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
```

### Check your setup

```bash
platform-mcp doctor
```

This checks, for every configured environment, that Application Default
Credentials exist, that the read-only service account can be impersonated, that
a real API read succeeds, and that the billing export is readable — printing the
exact command to fix whatever fails. Run it before reporting a problem.

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

**3. (Recommended) Use a dedicated read-only service account** instead of your
login. `platform-mcp setup` does every step below, is safe to re-run, and prints
the config stanza at the end. It ships with the package, so there is nothing to
clone:

```bash
uvx platform-mcp setup \
  --project YOUR_PROJECT_ID \
  --user you@example.com \
  --billing-dataset YOUR_BILLING_PROJECT:billing   # optional
```

Or by hand:

```bash
PROJECT=YOUR_PROJECT_ID
gcloud iam service-accounts create platform-mcp-ro \
  --display-name "platform-mcp read-only" --project $PROJECT

SA=platform-mcp-ro@$PROJECT.iam.gserviceaccount.com
for ROLE in roles/viewer roles/logging.viewer roles/monitoring.viewer \
  roles/errorreporting.viewer roles/recommender.viewer roles/cloudasset.viewer \
  roles/bigquery.jobUser; do
  gcloud projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:$SA" --role="$ROLE" --condition=None
done

# Let your own login impersonate it (no key file to manage):
gcloud iam service-accounts add-iam-policy-binding $SA \
  --member="user:you@example.com" \
  --role="roles/iam.serviceAccountTokenCreator" --project $PROJECT
```

**The grant everyone forgets.** `roles/bigquery.jobUser` above only lets the
account *start* a query; it grants no access to any data. A billing export
almost always lives in a **different project**, so the account also needs read
on that dataset. Without it `get_cost_breakdown` returns 403 while every other
tool works, which reads like a bug in the tool rather than a missing grant:

```bash
bq add-iam-policy-binding \
  --member="serviceAccount:$SA" --role=roles/bigquery.dataViewer \
  YOUR_BILLING_PROJECT:billing
```

If you lack admin on the billing project, that one line is what to send to
someone who has it. `platform-mcp doctor` checks it and says which side is
missing.

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

The friendliest option is a config file, which keeps project ids and service
account emails out of every client config you own:

```bash
mkdir -p ~/.config/platform-mcp
cp config.toml.example ~/.config/platform-mcp/config.toml
$EDITOR ~/.config/platform-mcp/config.toml
```

With that in place, registering the server takes no environment variables at
all. Point `PLATFORM_MCP_CONFIG` elsewhere to use a different file — a copy
committed to your infrastructure repo, for instance.

Environment variables still work and always win over the file, so an existing
setup keeps running unchanged and a one-off override needs no edit:

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

**Claude Code** — with a config file in place, this is the whole thing:

```bash
claude mcp add platform-mcp --scope user -- uvx platform-mcp
```

**Claude Desktop** — needs an absolute path to `uvx`; see
[Claude Desktop](#claude-desktop) for the full walkthrough.

Without a config file, add the environment variables from
`.mcp.json.example` to either form.

### Skip the approval prompt

Every tool here is read-only, so approving each call individually adds nothing.
Allow the whole server once, in Claude Code settings:

```json
{ "permissions": { "allow": ["mcp__platform-mcp__*"] } }
```

The glob must sit after a literal `mcp__<server>__` prefix — an unanchored
pattern like `mcp__*` is ignored with a warning and approves nothing.

**MCP Inspector** — for interactive testing:

```bash
uvx --with 'mcp[cli]' mcp dev src/platform_mcp/server.py
```

## Claude Desktop

Installing for a teammate who uses Claude Desktop rather than the CLI. It works
the same as Claude Code, with one failure mode the CLI does not have.

**Claude Desktop does not inherit your shell `PATH`.** It launches from the
Finder, so `uvx` and anything installed by Homebrew or `uv` are invisible to it.
A config that says `"command": "uvx"` fails with `ENOENT` — the server never
starts, and the error names the command rather than the reason. **The command
path must be absolute.**

What does *not* break is credentials. Application Default Credentials are a
file the Google libraries read directly, so `gcloud` is needed once, in a
terminal, to create that file — not at runtime. The config file is found the
same way, from the home directory, whatever launched the server.

### Before you start: the admin grant

Each person impersonates the read-only service accounts, so an admin runs this
once per person, per environment (the `platform-mcp setup` script does it for
whoever runs it, but not for anyone else):

```bash
gcloud iam service-accounts add-iam-policy-binding \
  platform-mcp-ro@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --member="user:teammate@example.com" \
  --role="roles/iam.serviceAccountTokenCreator" --project YOUR_PROJECT_ID
```

Without it everything installs fine and `doctor` fails at the impersonation
step.

### 1. Install uv (macOS)

An analyst's laptop usually has neither Homebrew nor the Xcode Command Line
Tools, and nothing here needs them. Avoid `git`, `make` and the stock
`/usr/bin/python3` along the way — they are stubs that pop a dialog offering to
install a gigabyte of developer tooling. `uv` is a standalone binary:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
which uvx      # note this absolute path — Claude Desktop needs it
```

Typically `/Users/<you>/.local/bin/uvx`.

### 2. Install the Google Cloud CLI

The tarball bundles its own Python, so it needs nothing else. Avoid
`brew install --cask google-cloud-sdk`: Homebrew itself requires the Command
Line Tools. Pick the build for your chip — `uname -m` prints `arm64` for Apple
Silicon, `x86_64` for Intel:

```bash
# Apple Silicon
curl -O https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-darwin-arm.tar.gz
tar -xzf google-cloud-cli-darwin-arm.tar.gz

# Intel — same, with the other file
# curl -O https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-darwin-x86_64.tar.gz
# tar -xzf google-cloud-cli-darwin-x86_64.tar.gz

./google-cloud-sdk/install.sh --quiet
```

### 3. Authenticate

```bash
./google-cloud-sdk/bin/gcloud auth application-default login
```

Log in as **yourself**. If your ADC is itself an impersonated service account,
that account — not you — becomes the identity doing the impersonation, and it
will not hold the grant above.

### 4. Write the config file

```bash
mkdir -p ~/.config/platform-mcp
touch ~/.config/platform-mcp/config.toml
open -e ~/.config/platform-mcp/config.toml   # or any editor
```

```toml
default_environment = "staging"

[environments.staging]
project = "my-app-staging"
impersonate = "platform-mcp-ro@my-app-staging.iam.gserviceaccount.com"

[environments.production]
project = "my-app"
impersonate = "platform-mcp-ro@my-app.iam.gserviceaccount.com"
billing_export_table = "my-app.billing.gcp_billing_export_v1_XXXXXX"
```

See [Configuration](#configuration) for every key.

### 5. Check it worked

```bash
~/.local/bin/uvx platform-mcp doctor
```

Every environment should pass. Fix anything that fails before touching Claude
Desktop — a server that cannot authenticate still starts, and then every tool
call errors.

### 6. Edit the Claude Desktop config

**Settings → Developer → Edit Config** opens it, or edit it directly:

| OS | File |
| --- | --- |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

(Settings → Connectors lists hosted connectors; a local server like this one is
not added there.)

**The file usually already exists and holds your Desktop preferences.** Add
`mcpServers` as one more top-level key — do not replace the file, or you will
lose those settings:

```json
{
  "preferences": { "...": "your existing settings, left alone" },
  "mcpServers": {
    "platform-mcp": {
      "command": "/Users/YOU/.local/bin/uvx",
      "args": ["platform-mcp@latest"]
    }
  }
}
```

Replace `/Users/YOU/.local/bin/uvx` with what `which uvx` printed. On Windows
the path looks like `C:\\Users\\YOU\\.local\\bin\\uvx.exe` — backslashes doubled
in JSON. `@latest` makes each launch pick up new releases.

Check it still parses before restarting — a stray comma disables **every**
server, silently. This uses only what ships with macOS:

```bash
osascript -l JavaScript -e 'function run(a){ObjC.import("Foundation");JSON.parse($.NSString.stringWithContentsOfFileEncodingError(a[0],4,null).js);return "valid"}' \
  ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

### 7. Restart Claude Desktop

Quit it fully (**Cmd-Q**, not just closing the window) and reopen. Then ask
*"list the configured environments"* — it should answer from `list_environments`.

If it does not appear, the logs are in `~/Library/Logs/Claude/`
(`mcp-server-platform-mcp.log`). `ENOENT` there means the `command` path is
wrong.

### Why not a key file?

You can skip steps 2–3 by pointing `GOOGLE_APPLICATION_CREDENTIALS` (in the
server's `env` block) at a downloaded service-account key. Think twice: a key
is a long-lived credential sitting on a laptop, and the production read-only
account can read every log, metric and billing row in that project.
`application-default login` issues short-lived tokens tied to a person, and
removing one grant revokes that person alone.

## Observability

Every tool call appends one JSON line to `~/.local/state/platform-mcp/audit.jsonl`:

```json
{"ts":"2026-08-30T18:20:11+0800","tool":"query_logs","environment":"production",
 "project":"my-app","duration_ms":412,"count":50,"bytes":18422,"error":null}
```

Free-text arguments are recorded by name only — a Cloud Logging filter can carry
user ids from the logs being searched, and the audit file must not become a
second copy of that. Set `PLATFORM_MCP_AUDIT_LOG` to another path, or to `off`.

Diagnostic logs go to **stderr** (`PLATFORM_MCP_LOG_LEVEL` to adjust); in stdio
transport stdout carries the protocol, so nothing else may be written there. In
Claude Code, read them with `claude --debug=mcp`.

For a record that does not depend on this server at all, enable **Data Access
audit logs** in GCP for the read-only service accounts. Token minting already
appears in Admin Activity logs without any configuration.

## Development

```bash
./.venv/bin/python -m pytest
```

The suite runs against an in-memory MCP client — no network and no GCP
credentials — and covers environment resolution, the tool contract,
annotations, error translation and the audit log. The only subprocess is
`bash -n` over the packaged setup script, which is skipped where bash is absent.

The setup script lives at `src/platform_mcp/scripts/` because it ships as
package data; `platform-mcp setup` runs it from wherever the package is
installed, so the quickstart needs no checkout.

## Notes

- All tools cap result counts and truncate long payloads to stay token-friendly.
- GCP clients are built lazily and cached per environment, so switching between
  staging and production mid-conversation costs one client construction each.
- Cost recommenders are zonal/regional; `list_cost_recommendations` auto-discovers
  the locations where you have resources (via Asset Inventory) and fans out,
  skipping locations and recommenders that are empty or unavailable. It reports
  `skipped_calls` and fails loudly if it cannot discover any location, because
  "I could not look" and "there is nothing to save" must not look alike.
- `get_cost_breakdown` uses parameterized BigQuery queries with a whitelisted set
  of group-by columns, and filters to the selected environment's project. A
  billing export covers the whole billing account, so pass `all_projects=true`
  when you want account-wide totals.

## License

[MIT](LICENSE) © 2026 Dimuthu Wickramanayake

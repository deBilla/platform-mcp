#!/usr/bin/env bash
#
# Create the read-only service account platform-mcp impersonates for one
# environment, grant it exactly the roles the tools need, and print the config
# stanza to paste.
#
# Safe to re-run: every step is idempotent, and nothing here grants any
# permission to change infrastructure.
#
#   platform-mcp setup \
#       --project my-app-staging \
#       --user you@example.com \
#       --billing-dataset my-billing-project:billing
#
# --billing-dataset is optional and only needed for get_cost_breakdown. The
# billing export usually lives in a *different* project from the one being
# monitored, which is the single most-missed grant in this setup: the account
# gets viewer roles on its own project, the cost tool still returns 403, and
# nothing says why.

set -euo pipefail

PROJECT=""
USER_EMAIL=""
BILLING_DATASET=""
SA_NAME="platform-mcp-ro"

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --project)         PROJECT="$2"; shift 2 ;;
    --user)            USER_EMAIL="$2"; shift 2 ;;
    --billing-dataset) BILLING_DATASET="$2"; shift 2 ;;
    --sa-name)         SA_NAME="$2"; shift 2 ;;
    -h|--help)         usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage 1 ;;
  esac
done

[ -n "$PROJECT" ] || { echo "error: --project is required" >&2; usage 1; }
if [ -z "$USER_EMAIL" ]; then
  USER_EMAIL="$(gcloud config get-value account 2>/dev/null)"
  echo "note: --user not given, using the active gcloud account: $USER_EMAIL"
fi

SA="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"

echo
echo "project:         $PROJECT"
echo "service account: $SA"
echo "impersonated by: $USER_EMAIL"
echo "billing dataset: ${BILLING_DATASET:-(none - get_cost_breakdown disabled)}"
echo

echo "==> Enabling APIs"
gcloud services enable \
  logging.googleapis.com monitoring.googleapis.com clouderrorreporting.googleapis.com \
  recommender.googleapis.com cloudasset.googleapis.com cloudbilling.googleapis.com \
  bigquery.googleapis.com iamcredentials.googleapis.com \
  --project "$PROJECT"

echo "==> Creating the service account (ignored if it already exists)"
gcloud iam service-accounts create "$SA_NAME" \
  --display-name "platform-mcp read-only" \
  --project "$PROJECT" 2>/dev/null || echo "    already exists"

echo "==> Granting read-only roles on $PROJECT"
# roles/viewer covers resource inventory; the rest are the per-service readers.
# bigquery.jobUser lets the account *start* a query job in this project -- it
# grants no access to any data, which comes from the dataset grant below.
for ROLE in \
  roles/viewer \
  roles/logging.viewer \
  roles/monitoring.viewer \
  roles/errorreporting.viewer \
  roles/recommender.viewer \
  roles/cloudasset.viewer \
  roles/bigquery.jobUser
do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:$SA" --role="$ROLE" \
    --condition=None --quiet >/dev/null
  echo "    $ROLE"
done

echo "==> Allowing $USER_EMAIL to impersonate the account"
# This is what removes key files from the picture: people authenticate as
# themselves and borrow the account, so there is no secret to leak or rotate.
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --member="user:$USER_EMAIL" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --project "$PROJECT" --quiet >/dev/null
echo "    roles/iam.serviceAccountTokenCreator"

if [ -n "$BILLING_DATASET" ]; then
  BILLING_PROJECT="${BILLING_DATASET%%:*}"
  DATASET_ID="${BILLING_DATASET##*:}"
  echo "==> Granting read on $BILLING_PROJECT:$DATASET_ID"
  if ! bq --project_id="$BILLING_PROJECT" show --format=none "$BILLING_DATASET" 2>/dev/null; then
    echo "    cannot read that dataset -- check the name, or ask an admin of"
    echo "    $BILLING_PROJECT to run the grant below."
  fi
  TMP="$(mktemp)"
  if bq --project_id="$BILLING_PROJECT" show --format=prettyjson "$BILLING_DATASET" > "$TMP" 2>/dev/null; then
    python3 - "$TMP" "$SA" <<'PY'
import json, sys
path, sa = sys.argv[1], sys.argv[2]
d = json.load(open(path))
access = d.setdefault("access", [])
if any(e.get("userByEmail") == sa for e in access):
    print("    already granted")
else:
    access.append({"role": "READER", "userByEmail": sa})
    json.dump(d, open(path, "w"))
    print("    adding READER")
PY
    if bq --project_id="$BILLING_PROJECT" update --source "$TMP" "$BILLING_DATASET" >/dev/null 2>&1; then
      echo "    READER granted on $BILLING_DATASET"
    else
      echo "    FAILED -- you need bigquery.datasets.update on $BILLING_PROJECT."
      echo "    Ask an admin of that project to add this account as a READER:"
      echo "      $SA"
    fi
  fi
  rm -f "$TMP"
fi

cat <<EOF

Done. Add this to ~/.config/platform-mcp/config.toml:

[environments.CHANGE_ME]
project = "$PROJECT"
impersonate = "$SA"$([ -n "$BILLING_DATASET" ] && printf '\nbilling_export_table = "%s.%s.CHANGE_ME_TABLE"' "${BILLING_DATASET%%:*}" "${BILLING_DATASET##*:}")

Then verify with:

    platform-mcp doctor
EOF

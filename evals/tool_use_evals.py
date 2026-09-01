"""Layer 3: does a real agent use these tools correctly?

Layers 1 and 2 prove the server answers correctly when asked correctly. This
layer asks the question a user would ask and checks what the agent did with it.
The client under test is the Claude Code CLI, because that is the client people
actually use -- driving the API with a hand-rolled tool loop would evaluate a
harness nobody runs.

Scoring is on the trajectory: which tool was called, against which environment.
That is deterministic and cheap, and it catches the failures that matter. The
server's own audit log is the trajectory record, so no instrumentation is
needed beyond pointing PLATFORM_MCP_AUDIT_LOG at a per-case file.

The one thing a trajectory cannot see is whether the agent invented an answer,
so a couple of cases also assert on the text.

    python evals/tool_use_evals.py               # every case
    python evals/tool_use_evals.py wrong-env     # one case by id

Each case spends real model tokens. There are few cases on purpose.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = pathlib.Path(os.environ.get("EVAL_OUT", REPO / "evals" / "results"))
MODEL = os.environ.get("EVAL_MODEL", "claude-opus-5")
TIMEOUT = int(os.environ.get("EVAL_TIMEOUT", "240"))

ERROR_TOOLS = {"get_recent_errors", "list_error_groups", "query_logs"}
INVENTORY_TOOLS = {"list_cloud_run_services", "search_assets"}

# `expect_environment` is the assertion that matters most. A production question
# answered from staging is not a wrong answer the user can spot -- it is a
# confident answer about the wrong system, and nothing in the response says so.
CASES = [
    {
        "id": "explicit-prod",
        "prompt": "Are there any errors in production right now? Just tell me what you find.",
        "expect_environment": "production",
        "expect_any_tool": ERROR_TOOLS,
    },
    {
        "id": "alias-prod",
        "prompt": "Check prod for recent errors.",
        "expect_environment": "production",
        "expect_any_tool": ERROR_TOOLS,
        "why": "'prod' is an alias, not a configured environment name.",
    },
    {
        "id": "default-env",
        "prompt": "Any errors in the last hour?",
        "expect_environment": "staging",
        "forbid_environment": "production",
        "expect_any_tool": ERROR_TOOLS,
        "why": "Unqualified questions must not reach production. The first "
               "version of this case only checked that staging was among the "
               "environments touched, so it passed while the agent queried "
               "both -- an assertion weaker than the claim it was making.",
    },
    {
        "id": "wrong-env",
        "prompt": "I only care about staging. What is broken there?",
        "expect_environment": "staging",
        "forbid_environment": "production",
        "expect_any_tool": ERROR_TOOLS,
    },
    {
        "id": "tool-choice-inventory",
        "prompt": "How many Cloud Run services are deployed in staging?",
        "expect_environment": "staging",
        "expect_any_tool": INVENTORY_TOOLS,
        "forbid_any_tool": {"query_logs", "get_recent_errors"},
        "why": "An inventory question should not be answered from logs.",
    },
    {
        "id": "no-fabrication",
        "prompt": "What did we spend in production over the last 30 days, broken down by service?",
        "expect_any_tool": {"get_cost_breakdown"},
        "dollars_require_success": "get_cost_breakdown",
        "why": "A spend figure in the answer must be backed by a cost query that "
               "actually succeeded. Asserting that the tool *fails* would tie the "
               "case to today's IAM, and it would start passing for the wrong "
               "reason the moment a grant landed.",
    },
    {
        "id": "cost-redirect",
        "prompt": "What did staging cost us over the last 30 days?",
        "expect_any_tool": {"get_cost_breakdown"},
        "dollars_require_success": "get_cost_breakdown",
        "why": "Staging has no billing export of its own; billing is exported "
               "once for the whole account. The error names the environment that "
               "does have it, so the agent should recover rather than give up.",
    },
]


def load_environments() -> dict:
    if os.environ.get("PLATFORM_MCP_ENVIRONMENTS"):
        return {k: v for k, v in os.environ.items() if k.startswith("PLATFORM_MCP_")}
    local = REPO / ".mcp.json"
    if not local.exists():
        sys.exit("No PLATFORM_MCP_ENVIRONMENTS set and no .mcp.json to read one from.")
    cfg = json.load(open(local))["mcpServers"]["platform-mcp"].get("env", {})
    return {k: v for k, v in cfg.items() if k.startswith("PLATFORM_MCP_")}


def run_case(case: dict, base_env: dict, workdir: pathlib.Path) -> dict:
    audit = workdir / "audit.jsonl"
    env = dict(os.environ)
    env.update(base_env)
    env["PLATFORM_MCP_AUDIT_LOG"] = str(audit)
    env["PLATFORM_MCP_LOG_LEVEL"] = "WARNING"

    # A config the CLI loads on its own, so the agent reaches the server exactly
    # as a user's would -- over stdio, from the installed console script.
    mcp_config = workdir / "mcp.json"
    mcp_config.write_text(json.dumps({
        "mcpServers": {
            "platform-mcp": {
                # Launch through the interpreter running this script, so the
                # server under test is the working copy rather than whatever
                # happens to be on PATH.
                "command": sys.executable,
                "args": ["-m", "platform_mcp"],
                "env": {k: v for k, v in env.items() if k.startswith("PLATFORM_MCP_")},
            }
        }
    }))

    argv = [
        "claude", "-p", case["prompt"],
        "--model", MODEL,
        "--mcp-config", str(mcp_config),
        "--strict-mcp-config",
        "--allowedTools", "mcp__platform-mcp__*",
        "--output-format", "json",
    ]
    started = time.perf_counter()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=TIMEOUT, cwd=workdir, env=env)
        raw = proc.stdout
    except subprocess.TimeoutExpired:
        return {"id": case["id"], "status": "timeout", "seconds": TIMEOUT,
                "calls": [], "failures": [f"no answer within {TIMEOUT}s"]}
    seconds = round(time.perf_counter() - started, 1)

    answer = raw
    try:
        parsed = json.loads(raw)
        answer = parsed.get("result") or parsed.get("text") or raw
    except json.JSONDecodeError:
        pass

    calls = []
    if audit.exists():
        for line in audit.read_text().splitlines():
            if line.strip():
                calls.append(json.loads(line))

    return score(case, calls, str(answer), seconds)


def score(case: dict, calls: list[dict], answer: str, seconds: float) -> dict:
    tools = [c["tool"] for c in calls]
    failures = []

    if not calls:
        failures.append("no tool was called at all")

    considered = [c for c in calls if c["tool"] != "list_environments"]

    want = case.get("expect_any_tool")
    if want and not (set(tools) & want):
        failures.append(f"expected one of {sorted(want)}, called {tools or 'nothing'}")

    forbid = case.get("forbid_any_tool")
    if forbid and (set(tools) & forbid):
        failures.append(f"called forbidden {sorted(set(tools) & forbid)}")

    expect_env = case.get("expect_environment")
    if expect_env:
        seen = {c.get("arguments", {}).get("environment") for c in considered}
        seen.discard(None)
        # An omitted environment argument means the server's default was used,
        # which is only correct when the default is what the case expects.
        used_default = any("environment" not in c.get("arguments", {}) for c in considered)
        resolved = {c.get("environment") for c in considered if c.get("environment")}
        if expect_env not in (seen | resolved):
            failures.append(
                f"expected environment {expect_env!r}, "
                f"saw args={sorted(seen)} resolved={sorted(resolved)}"
                + (" (relied on the default)" if used_default else "")
            )

    forbid_env = case.get("forbid_environment")
    if forbid_env:
        touched = {c.get("environment") for c in considered}
        if forbid_env in touched:
            failures.append(f"touched forbidden environment {forbid_env!r}")

    expect_err = case.get("expect_tool_error")
    if expect_err:
        errored = [c for c in calls if c["tool"] == expect_err and c.get("error")]
        if not errored:
            failures.append(f"expected {expect_err} to fail in this environment; it did not")

    # A number in the answer must be backed by a call that actually returned
    # one. This holds whether or not the export is readable today, so the
    # assertion does not quietly become vacuous when the IAM changes.
    guarded = case.get("dollars_require_success")
    if guarded:
        attempts = [c for c in calls if c["tool"] == guarded]
        succeeded = [c for c in attempts if not c.get("error")]
        money = re.search(r"\$\s?[\d,]+", answer)
        if money and not succeeded:
            failures.append(
                f"answer quotes {money.group(0)!r} but no {guarded} call succeeded "
                f"({len(attempts)} attempted, all failed) — fabricated"
            )
        if not money and succeeded:
            failures.append(f"{guarded} succeeded but the answer quotes no figure")
        if not attempts:
            failures.append(f"never called {guarded}")

    if case.get("forbid_text") and re.search(case["forbid_text"], answer, re.I):
        hit = re.search(case["forbid_text"], answer, re.I).group(0)
        failures.append(f"answer contains forbidden pattern ({hit!r}) — likely fabricated")

    if case.get("require_text") and not re.search(case["require_text"], answer, re.I):
        failures.append("answer does not explain the failure to the user")

    return {
        "id": case["id"],
        "status": "pass" if not failures else "fail",
        "seconds": seconds,
        "calls": [
            {"tool": c["tool"], "environment": c.get("environment"),
             "arg_environment": c.get("arguments", {}).get("environment"),
             "error": c.get("error")}
            for c in calls
        ],
        "failures": failures,
        "answer_head": answer.strip().replace("\n", " ")[:180],
    }


def main() -> int:
    wanted = sys.argv[1:]
    cases = [c for c in CASES if not wanted or c["id"] in wanted]
    if not cases:
        sys.exit(f"No case matched {wanted}. Ids: {[c['id'] for c in CASES]}")

    base_env = load_environments()
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"model: {MODEL}   cases: {len(cases)}\n")

    results = []
    for case in cases:
        print(f"  running {case['id']} ...", flush=True)
        with tempfile.TemporaryDirectory(prefix=f"eval-{case['id']}-") as tmp:
            result = run_case(case, base_env, pathlib.Path(tmp))
        results.append(result)
        mark = "PASS" if result["status"] == "pass" else result["status"].upper()
        print(f"    {mark}  {result['seconds']}s  "
              f"{[c['tool'] for c in result['calls']] or 'no calls'}")
        for failure in result["failures"]:
            print(f"      - {failure}")

    (OUT / "tool-use.json").write_text(json.dumps(results, indent=2))
    passed = sum(1 for r in results if r["status"] == "pass")
    print(f"\n{passed}/{len(results)} passed   report: {OUT / 'tool-use.json'}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

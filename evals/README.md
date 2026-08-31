# Evaluation harness

Three layers, cheapest first. Only the first two run without credentials.

| Layer | Where | Needs |
| --- | --- | --- |
| Contract tests | `tests/` | nothing — in-memory MCP client |
| Response shape | `tests/test_payload_shape.py` | nothing — stubbed protos |
| Scorer tests | `tests/test_eval_scoring.py` | nothing — synthetic trajectories |
| Measurement run | `evals/measure.py` | live GCP credentials |
| Tool-use evals | `evals/tool_use_evals.py` | live GCP + the `claude` CLI |

## Measurement run

```bash
./.venv/bin/python evals/measure.py staging
```

Calls every tool through a real client session and records latency, serialised
payload size, an estimated token cost, and the shape of any error. It goes
through the protocol rather than calling functions directly, so what it measures
is what a client actually receives.

Output lands in `evals/measurements/` (git-ignored). **Those files contain live
log lines and resource names** — redact before using any of it as a fixture.

Run it after changing a tool's response shape. The numbers are the only way to
see the failures that look fine in code review: a truncation limit that fires on
almost every record, a field that is always empty, an identifier repeated three
times per row.

## Baseline

`BASELINE.md` records what a run measured and what changed as a result.

## Tool-use evals

```bash
python evals/tool_use_evals.py               # every case
python evals/tool_use_evals.py default-env   # one case
```

Asks the question a user would ask and checks what the agent did with it. The
client under test is the Claude Code CLI, because that is the client people
actually use — driving the API with a hand-rolled tool loop would evaluate a
harness nobody runs.

Scoring is on the **trajectory**: which tool, against which environment. That is
deterministic and cheap, and it catches the failure that matters most — a
question about one environment answered from another, which no user can spot in
the reply. The server's own audit log *is* the trajectory record, so the harness
needs no instrumentation beyond pointing `PLATFORM_MCP_AUDIT_LOG` at a per-case
file. Two cases also assert on the text, because a trajectory cannot see whether
the agent invented an answer.

Each case spends real model tokens, so there are few of them on purpose.

**A suite that passes on its first run proves nothing.**
`tests/test_eval_scoring.py` feeds the scorer the trajectories each case exists
to reject — wrong environment, no tool call, forbidden tool, a fabricated dollar
figure — and runs offline for free. Read it before trusting a green run here.

### What these evals have caught

`default-env` asks "Any errors in the last hour?" and expects the answer to come
from the default environment alone. It passed at first — because the assertion
only checked that staging appeared among the environments touched, while the
agent had queried staging *and* production. The assertion was weaker than the
claim it made. Tightened to forbid production, it failed reproducibly.

The fix was not to the test. The server's instructions said only that omitting
the argument uses the default; they never said not to survey the others. Saying
so explicitly fixed the behaviour, and the case has passed since. That is the
loop this layer exists for: tool and server descriptions are the highest-leverage
thing you can change, and nothing except an eval tells you they need changing.

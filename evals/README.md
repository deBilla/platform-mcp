# Evaluation harness

Three layers, cheapest first. Only the first two run without credentials.

| Layer | Where | Needs |
| --- | --- | --- |
| Contract tests | `tests/` | nothing — in-memory MCP client |
| Golden payloads | `tests/fixtures/` | nothing — recorded responses |
| Measurement run | `evals/measure.py` | live GCP credentials |

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

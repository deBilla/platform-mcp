# Measurement baseline

What `evals/measure.py` measured, and what changed because of it. Every number
here came from running all seventeen calls against a live staging project
through a real MCP client session — not from reading the code.

## Run of 2026-08-30 · staging · 16/17 calls succeeded

The one failure was `get_cost_breakdown`, which returned its actionable IAM
error. That environment's service account cannot read the billing export.

### What the first run found

**The truncation limit, not the content, was deciding what the agent saw.**

| | |
| --- | --- |
| `query_logs` messages hitting the 400-char cap | 41 / 50 |
| `get_recent_errors` messages hitting the cap | 48 / 50 |
| Median message length | exactly 400 |

A median sitting precisely on the limit is the signature of a cap that is too
low. For a tool whose job is diagnosing incidents, clipping at 400 characters
removes the part of a stack trace that identifies the fault, and nothing in the
response said a message had been cut.

**Inventory responses spent most of their bytes repeating themselves.** Across
77 live Cloud Run services:

| Observation | Count | Share of payload |
| --- | --- | --- |
| `display_name` identical to `name` | 77 / 77 | 7% |
| `name` derivable from `full_name` | 77 / 77 | 7% |
| `state` empty | 77 / 77 | 1% |
| `labels` (deployment metadata) | — | 46% |

**`list_cost_recommendations` took 23 seconds** with full coverage
(`skipped_calls=0`), which is what motivated progress reporting.

### What changed

1. Message cap 400 → 1500, with `message_truncated` and `message_full_length`
   on any entry that still hits it.
2. A whole-response budget of 40,000 characters for log queries. The context
   window is the real constraint, not any single message, so short messages
   yield many entries and long ones yield fewer. When the budget ends the list,
   the response says `stopped_for_size` rather than looking like the complete
   set of matches.
3. `_format_resource` drops duplicate and empty fields; `labels` became opt-in
   via `include_labels`. `search_assets` default limit 100 → 50.

### Result

| Tool | Baseline | Cap only | Budgeted |
| --- | ---: | ---: | ---: |
| `query_logs` (1h) | 27,086 | 33,708 | 35,990 |
| `query_logs` (warn, 6h) | 14,034 | 77,316 | 41,323 |
| `get_recent_errors` | 30,794 | 87,086 | 40,036 |
| `search_assets` | 56,281 | 31,118 | 31,118 |
| `list_cloud_run_services` | 47,166 | 20,704 | 20,704 |
| `list_compute_instances` | 6,547 | 3,247 | 3,247 |
| **Total across 17 calls** | **212,856** | 286,421 | **205,677** |
| Estimated tokens | 53,214 | 71,605 | 51,419 |

Raising the cap alone cost 35% more context. With the budget, total context is
slightly *below* where it started while each message carries about 3.75× more
content — the bytes moved from repeated identifiers into diagnostic text.

The equilibrium is now 22 fuller entries instead of 50 clipped ones for the same
size, and both limits are declared in the response rather than silent.

`tests/test_payload_shape.py` locks each of these behaviours in.

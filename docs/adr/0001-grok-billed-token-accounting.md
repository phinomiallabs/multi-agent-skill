# Grok billed token accounting via managed-grok stdout capture

Status: accepted

Grok worker usage was previously read from grok's session-log `totalTokens` gauge — a context-size snapshot, not billed usage, so it undercounted real consumption ~12–16× and carried no cache-read (see `CONTEXT.md`: *conversation units*). The managed grok CLI (≥ 0.2.103) emits an exact billed `usage` object on stdout under `--output-format json` (uncached / cache-read / output / total + USD cost), cumulative across all model calls and inclusive of spawned subagents. **Decision:** capture that object at run time in the worker (pipe stdout into `grok_tokens.py --record`, mirroring the cursor pipeline) and record grok as a first-class *billed* provider comparable to Claude and cursor.

## Considered options

- **Session-log ledger (post-hoc sweep).** 0.2.103 also persists `turn_completed.usage` to `updates.jsonl`. Rejected as the primary path: stdout is richer (carries USD cost + per-model breakdown), is one clean object, and is verified subagent-inclusive. Kept only as a possible future fallback.
- **Call the xAI API directly.** Would yield billed usage without the CLI, but abandons grok-agent's agentic capabilities. Rejected.
- **Overwrite the standalone `grok-agent` binary.** The worker called a hand-placed `~/.local/bin/grok-agent` copy that `grok update` never refreshes — it froze at 0.2.93, which is *why* billed usage was unavailable. Rejected in favour of the managed `grok` binary (maintained by `grok update`); the standalone copy was removed.

## Consequences

- The legacy gauge + `chars÷4` estimate path is retained as a **gated fallback** (keyed on each row's `units`/`exact`) so old (≤ 0.2.93) runs still render honestly — the conversation-unit/estimate rendering is deliberate, not dead code.
- Capture is **worker-only**: grok persists no usage to disk, so a grok run launched outside the worker is not accounted (same limitation cursor has).
- USD cost is captured into the run-record but **not visualised** — a cross-provider cost view needs an estimated Claude price table, deferred.

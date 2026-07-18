# multi-agent-skill

A Claude Code skill for **multi-agent orchestration**. Claude acts as an *advisor*
that plans the work, delegates implementation and investigation to non-Claude
subagents (e.g. Grok), and adversarially verifies the results. The full workflow
lives in [`SKILL.md`](SKILL.md).

## Quick start

Run

```
/multi-agent-skill

* Work item(s) — spec, plan, tickets, or issues (often a single git issue containing all of them)
* Implementer model
* Investigator model
* (Optional) instructions: markdowns, htmls, etc.

```

Fill in the bullets. 

## Roles

- **Advisor** (Claude) — orchestrates, designs tests, and adversarially verifies. Doesn't write code itself.
- **Investigator** (a non-advisor model) — reads and scopes the codebase.
- **Implementer** (a non-advisor model) — writes code and tests.

## Layout

| Path | What it is |
|------|------------|
| `SKILL.md` | The skill: workflow, roles, rules, monitoring, and how to drive non-Claude models. |
| `monitor/` | Token-accounting and live-HTML-monitor tooling (see below). |
| `templates/grok-worker.sh` | Fill-in prompt template for running `grok-agent` as an implementation worker. |
| `templates/cursor-worker.sh` | Fill-in template for running the Cursor CLI (`agent`) as a worker; captures exact billed token usage at run time. |
| `runs/` | Per-run records and artifacts. **Gitignored** — they hold repo-specific, sensitive data. |

## Monitor tooling (`monitor/`)

Each run is captured in a single JSON record; these scripts read and write it.
Every script is self-documenting — run `python monitor/<script>.py -h`.

| Script | Purpose |
|--------|---------|
| `aggregate_tokens.py` | One-shot token sweep: advisor + direct + nested Claude subagents + grok. |
| `generate_monitor.py` | Renders the live HTML monitor — agents, phases, and token-distribution **donut charts** by role and by model — from a run record. |
| `summarize_tokens.py` | Computes the by-role and by-model token breakdowns (with percentages). |
| `claude_tokens.py` / `grok_tokens.py` / `cursor_tokens.py` | Per-source token helpers used by `aggregate_tokens.py`. Cursor usage is exact/billed (comparable to Claude); it is captured at run time by `cursor-worker.sh` since the Cursor CLI persists no tokens on disk. |

The generated page is published as a Claude artifact and refreshes itself, so token
counts update live as the run progresses.

## Requirements

- Python 3 — standard library only, no dependencies.
- `grok-agent` and/or the Cursor CLI (`agent`) on `PATH` for the non-Claude implementer workers.

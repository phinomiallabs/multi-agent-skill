---
name: multi-agent-skill
description: Delegates implementation work from specifications and plans to non-Claude models while Claude remains responsible for orchestration. Use when the user provides specification, implementation-plan, design, requirements, or task files and asks Claude to implement them, execute the plan, and allocate work to non-Claude models.
---

# Multi-agent-skill

The user should provide you with
* Work item(s) — spec, plan, tickets, or issues (often a single git issue containing all of them)
* Implementer model
* Investigator model
* (Optional) instructions: markdowns, htmls, etc.

You are the advisor. Your job is to orchestrate and verify.

## Workflow

1. The advisor reads the work item and (optional) instructions, then extracts two distinct things from it: the **spec** (the fixed "what must be true" — the oracle the verifier checks against) and the **plan** (the mutable "what to do" — refined by investigation). If the work item states work but no explicit spec, **derive the spec first** — otherwise there is nothing to verify against. After that,
    * Divide the work into investigation, implementation, and verification
    * Assign investigation work (e.g. scoping, reading the codebase) to the investigator subagents and implementation work (e.g. coding) to the implementer subagents. 
2. The advisor launches investigator subagents to finish investigation.
3. With the results from investigation, improve implementation plans. Assign implementation plans to implementer subagents. 
4. At the same time, the advisor should function as an adversarial verifier. 
    - As the adversarial verifier, it should aim to break the implementation.
    - It can start designing verification tests. An implementer subagent can write the tests. 
5. When implementation is done, start adversarial verification.
    - Run the round 1 tests from step 4. Find bugs and issues. 
    - Launch an implementer subagent to verify whether you fix the bugs and issues. Report the results back to the advisor.
    - The advisor should decide whether the bug fixes are satisfactory. The advisor again functions as an adversarial verifier to break the code.
    - Iterate until the advisor is satisfied with the bug fixes and can no longer break the code. 

### Adversarial verifier
* The advisor should serve as the adversarial verifier. 
* The advisor may launch sub-verifiers. Those sub-verifiers should be implementers or investigators.
* The advisor should outsource writing code and reading files to implementers and investigators. The advisor is responsible for ideation only.

### Rules
* Coding and writing verification tests are encouraged to be done in parallel. It's better to write verification test without seeing the implemented code. The advisor should know the objectives of the spec and then can write tests without the implementation.
* Implementer subagents should use skills /implement and /tdd when available.
* When making technical decisions, do not give much weight to development cost. Instead, prefer quality, simplicity, robustness, scalability, and long term maintainability.
* Use subagents and, if suitable, dynamic workflows. 
    - Try to divide the work so that subagents can execute them in parallel. Protect your context window.
* Here's a quick check on assignment of subagents
    - Designing tests: advisor
    - Writing tests: implementer
    - Coding: implementer
    - Investigation (scoping, reading, etc.): investigator
    - Rule of thumb: The advisor shouldn't need to write code. Implementers should write code. Investigators read. 
    - Subagents should never use the same model as the advisor. As a corollary, subagents should always write code or read. They should always be implementers or investigators. 
* Pass the following message to every subagent

```text
=== SHARED MESSAGE FOR ALL SUBAGENTS ===
    Read [(optional) instructions]
    Model selection guide
        - Writing tests: [implementer model name]
        - Coding: [implementer model name]
        - Investigation (scoping, reading, etc.): [investigator model name]
        - Rule of thumb: [implementer model name] should write code. [investigator model name] read. 
        - Subagents can never use the same model as the adivsor ([advisor model name]). As a corollary, subagents should always write code or read. They should always be implementers or investigators. 
    Pass the same message to your subagents and ask your subagents to pass the same message to their subagents, if they choose to launch subagents, including this statement itself.
=== END SHARED MESSAGE ===
```


## Monitoring

Deliverables:
* A **live HTML monitor**, published as an artifact, showing every subagent, its model, and its input/output tokens increasing. Re-render and re-publish it on every phase transition.
* A **final report** on subagents, models, token consumption, and time.

When you run a non-Claude model, actively confirm the shell is progressing or has finished — you tend to miss when a background shell completes.

### Run-record workflow (MANDATORY — never hand-type token numbers)

Hand-transcribing numbers from `aggregate_tokens.py` table output into the run-record JSON is **forbidden**. It drops fields (especially `cache_read`) and corrupts uncached shares. Use the automated path:

1. **Create** the run-record JSON at `runs/<date>-<slug>.json` with human fields only: `title`, `subtitle`, `phases`, `agents` (`name` / `model` / `role` / `status` / `label`), optional friendly `token_log` agent names, and a per-agent **`source`** key when known (`"advisor"`, `"direct:<agentId>"`, `"nested"`, `"grok:<sessionId>"`). Leave token numbers empty or zero — do **not** type them from a table.
2. **Refresh numbers** at every phase transition and at run end via:

```bash
python monitor/update_run_record.py runs/<date>-<slug>.json \
  --session-id <uuid> --project-slug <slug> \
  [--repo-cwd <repo> ...] [--direct <agentId> ...]
```

Discovery args are saved into the record's `accounting` block, so later refreshes are just `python monitor/update_run_record.py runs/<date>-<slug>.json`.

3. **Summarize + render**:

```bash
python monitor/summarize_tokens.py runs/<date>-<slug>.json
python monitor/generate_monitor.py runs/<date>-<slug>.json -o runs/<date>-<slug>.html
```

### Units warning (billed vs conversation)

* Claude rows: `units: "billed"` — per-call billed input (full context re-counted every API call, incl. `cache_read`).
* **Cursor** rows: `units: "billed"` too, and **exact** — the `agent` CLI reports real per-run usage (`inputTokens`/`outputTokens`/`cacheReadTokens`/`cacheWriteTokens`). Directly comparable to Claude. Cursor persists nothing on disk, so the worker captures it at run time into `~/.cursor-agent-usage/<cwd>/<session>.json`; `aggregate`/`update_run_record` pick it up via `--repo-cwd` (same flag as grok). The model is tagged `"<model> (cursor)"` so a cursor grok-4.5 run is never conflated with a native grok run — and because that label contains "grok", cursor rows **always** carry an explicit `units: "billed"`.
* **Grok** rows: `units: "billed"` and **exact** on grok ≥0.2.103 — the managed `grok` binary reports real per-run billed usage (uncached input / cache-read / output + USD cost), captured at run time into `~/.grok-agent-usage/<cwd>/<session>.json` (grok persists none on disk, exactly like cursor) and picked up via `--repo-cwd`. Directly comparable to Claude and cursor; the billed model string (e.g. `grok-4.5`) contains "grok", so these rows set `units: "billed"` explicitly. **Legacy** grok (≤0.2.93, no billed record) falls back to `units: "conversation"` — session `totalTokens` is **conversation size**, not per-call billed input, **not comparable** to billed columns (rough billed-equivalent ≈ avg context size × n_calls, often ~10–20× larger); the agents table keeps a † footnote on those conversation-unit rows.
* **Uncached**: Claude, cursor, and billed grok are exact (`tokens − cache_read`). Only **legacy** grok (conversation units, no billed record) is **estimated** — uncached ≈ the conversation total (each unique token processed uncached once; assumes prefix caching on re-read context — actual billed uncached ≥ this lower bound). Estimated rows set `uncached_estimated: true` and appear in Unc% / uncached donuts/bars with legend values marked `~…(est.)`.

### Tooling

All scripts are in `monitor/`. Each documents its arguments and exact token semantics in its own docstring — run `python monitor/<script>.py -h` and follow that rather than guessing.

* One **run-record JSON** per run at `runs/<date>-<slug>.json` is the single source of truth *and* the reviewable token record. Schema: top of `generate_monitor.py`.
* **`update_run_record.py <run.json> [...]`** — **the only supported way to put token numbers into the record**. Runs the aggregate sweep, merges exact numbers (incl. `cache_read`, `units`) in place by `source` key, appends unmatched rows, prints a diff. Prefer this over calling aggregate for paste.
* `aggregate_tokens.py --session-id <id> --project-slug <slug> [--repo-cwd <repo> ...] [--direct <agentId> ...]` — one-shot complete sweep (used by `update_run_record.py`). `--json` for machine-readable rows; do **not** hand-transcribe the table into the record.
* `generate_monitor.py <run.json> -o <out.html>` — renders the monitor page from the record; publish/refresh the artifact from it.
* `summarize_tokens.py <run.json>` — writes the by-role and by-model breakdowns (with percentages) back into the record. Run after every `update_run_record`.
* `claude_tokens.py`, `grok_tokens.py`, and `cursor_tokens.py` are the per-source helpers `aggregate_tokens.py` already calls; use them directly only for spot checks. `grok_tokens.py --record` and `cursor_tokens.py --record` (each fed the captured `grok` / `agent` JSON on stdin) are how a run's exact billed usage lands in its store — the workers do this automatically; run them by hand only to re-record a run whose output you captured separately.

### Finding the ids (no memory required)

`update_run_record.py` / `aggregate_tokens.py` need your `session-id` and `project-slug`; both are in your scratchpad path `/tmp/claude-1000/<project-slug>/<session-id>/scratchpad`:
* `project-slug` — the first segment, e.g. `-home-ubuntu-repos-multi-agent-skill`
* `session-id` — the UUID directory under it

`--direct` = the agentIds of the subagents you launched directly; `--repo-cwd` = each repo where grok **or cursor** ran (one flag sweeps both stores).

### Reporting

Report every subagent's tokens split into input and output. Claude, cursor, and grok (≥0.2.103) splits are exact billed and comparable; only **legacy** grok rows (≤0.2.93) are conversation-size units with an estimated split (only the total exact in those units) — label them as such. Uncached views treat only legacy grok as an estimate (see Units warning). Model names for Claude rows come from each transcript's assistant `message.model` (friendly labels like Sonnet 5 / Fable 5); grok rows keep the model id from the billed record (e.g. `grok-4.5`), or session metadata for legacy rows.

## Using non-Claude models

**Grok** — use the managed `grok` binary as an implementation worker (the standalone `grok-agent` has been removed). The prompt template lives in `templates/grok-worker.sh`: fill in every `[FILL IN]`, then run it from the target repo. It invokes `grok --always-approve -p "<prompt>" --output-format json`. Like cursor, it runs headless in JSON mode and **captures the exact billed usage at run time** — piping grok's stdout into `monitor/grok_tokens.py --record` writes one record per run into `~/.grok-agent-usage/<cwd>/<session>.json`, which the monitor reads back via `--repo-cwd` (see [Monitoring](#monitoring)).

**Cursor** — use `agent` as an implementation worker. The template lives in `templates/cursor-worker.sh`; it invokes `agent --model "<model>" --force "<prompt>"`. Default model is `grok-4.5`. For a `cursor-<model>` request (e.g. `cursor-grok-4.5`), pass the part after `cursor-` as the model: `MODEL=grok-4.5 templates/cursor-worker.sh`. The worker runs headless (`-p --output-format json`) and **captures the exact billed usage at run time** — cursor persists no tokens on disk, so this is the only chance to record them (see [Monitoring](#monitoring)).

After every Grok or Cursor invocation, inspect the actual repository changes (the model's own report can overstate what changed):

```bash
git status --short
git diff --stat
git diff
```

Both grok and cursor persist no token usage on disk, so each worker captures the exact billed split at run time (`monitor/grok_tokens.py --record` / `monitor/cursor_tokens.py --record`); `aggregate`/`update_run_record` then pick it up via `--repo-cwd` (see [Monitoring](#monitoring)).
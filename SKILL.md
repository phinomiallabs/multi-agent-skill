---
name: multi-agent-skill
description: Delegates implementation work from specifications and plans to non-Claude models while Claude remains responsible for orchestration. Use when the user provides specification, implementation-plan, design, requirements, or task files and asks Claude to implement them, execute the plan, and allocate work to non-Claude models.
---

# Multi-agent-skill

The user should provide you with
* Spec
* Plans/tickets/issues
* Implementer model
* Investigator model
* (Optional) intructions: markdowns, htmls, etc.

You are the advisor. Your job is to orchestrate and verify.

## Workflow

1. The advisor reads the spec, plan and (optional) instructions. After that,
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
* Coding and writing verification tests are encouraged to be done in parallel. It's better to write verification test without seeing the implemented code. The advisor should know the objectives of the plan and hen can write tests without the implementation.
* Implementer subagents should use skills /implement and /tdd when available.
* When making technical decisions, do not give much weight to development cost. Instead, prefer quality, simplicity, robustness, scalability, and long term maintainability.
* Use subagents and dynamic workflows. 
    - Subagents can launch their own subagents. That applies to the subagents of any subagent.
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
    Scope your tasks. Protect your context window. Subagents are a good tool to protect your context window, among other uses. 
    You can launch subagents of your own. So can their subagents. Each subagent can launch its subagents.
    Model selection guide
        - Writing tests: [implementer model name]
        - Coding: [implementer model name]
        - Investigation (scoping, reading, etc.): [investigator model name]
        - Rule of thumb: [implementer model name] should write code. [investigator model name] read. 
        - Subagents can never use the same model as the adivsor ([advisor model name]). As a corollary, subagents should always write code or read. They should always be implementers or investigators. 
    Pass the same message to your subagents and ask your subagents to pass the same message to their subagents, including this statement itself. Subagent launching should be recursive.
    -------------------------------------------------
    -----         END OF SHARED MESSAGE        ------
    -------------------------------------------------
=== END SHARED MESSAGE ===
```


## Monitoring

Deliverables:
* A **live HTML monitor**, published as an artifact, showing every subagent, its model, and its input/output tokens increasing. Re-render and re-publish it on every phase transition.
* A **final report** on subagents, models, token consumption, and time.

When you run a non-Claude model, actively confirm the shell is progressing or has finished — you tend to miss when a background shell completes.

### Tooling

All scripts are in `monitor/`. Each documents its arguments and exact token semantics in its own docstring — run `python monitor/<script>.py -h` and follow that rather than guessing.

* One **run-record JSON** per run at `runs/<date>-<slug>.json` is the single source of truth *and* the reviewable token record. Schema: top of `generate_monitor.py`.
* `aggregate_tokens.py --session-id <id> --project-slug <slug> [--repo-cwd <repo> ...] [--direct <agentId> ...]` — one-shot complete sweep of advisor + direct + nested Claude subagents + grok. Run at every phase transition and at run end; `--json` emits paste-ready `agents`/`token_log` fragments for the record.
* `generate_monitor.py <run.json> -o <out.html>` — renders the monitor page from the record; publish/refresh the artifact from it.
* `summarize_tokens.py <run.json>` — writes the by-role and by-model breakdowns (with percentages) back into the record. Run at the end of every run.
* `claude_tokens.py` and `grok_tokens.py` are the per-source helpers `aggregate_tokens.py` already calls; use them directly only for spot checks.

### Finding the ids (no memory required)

`aggregate_tokens.py` needs your `session-id` and `project-slug`; both are in your scratchpad path `/tmp/claude-1000/<project-slug>/<session-id>/scratchpad`:
* `project-slug` — the first segment, e.g. `-home-ubuntu-repos-multi-agent-skill`
* `session-id` — the UUID directory under it

`--direct` = the agentIds of the subagents you launched directly; `--repo-cwd` = each repo where grok ran.

### Reporting

Report every subagent's tokens split into input and output. Claude splits are exact (from the harness/transcripts); grok splits are estimates, with only the total exact — label them as such. Model names for Claude rows come from each transcript's assistant `message.model` (friendly labels like Sonnet 5 / Fable 5); grok rows keep grok session metadata.

## Using non-Claude models

**Grok** — use `grok-agent` as an implementation worker. The prompt template lives in `templates/grok-worker.sh`: fill in every `[FILL IN]`, then run it from the target repo. It invokes `grok-agent --yolo -p "<prompt>"`.

After every Grok invocation, inspect the actual repository changes (Grok's own report can overstate what changed):

```bash
git status --short
git diff --stat
git diff
```

grok-agent prints no token usage in headless mode; account for it with `monitor/grok_tokens.py` (see [Monitoring](#monitoring)).
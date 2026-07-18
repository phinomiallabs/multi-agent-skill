#!/usr/bin/env bash
# Cursor implementation-worker template.
# Fill in every [FILL IN] below, then run this script from the target repo.
#
# Model selection:
#   Default is grok-4.5. For a "cursor-<model>" request (e.g. cursor-grok-4.5),
#   pass the part after "cursor-" as MODEL:  MODEL=grok-4.5 ./cursor-worker.sh
#
# Invocation form:  agent --model "<model>" --force "<prompt>"
# cursor prints no token usage in headless mode; account for it with
# monitor/grok_tokens.py (see SKILL.md ## Monitoring).

MODEL="${MODEL:-grok-4.5}"

agent --model "$MODEL" --force "$(cat <<'CURSOR_PROMPT'
You are an implementation worker operating inside the current repository.

## Objective
[FILL IN]

## Source files
[FILL IN]

## Repository context
[FILL IN]

## Required work
[FILL IN]

## Acceptance criteria
[FILL IN]

## Constraints
- Modify the repository directly; do not merely describe a solution.
- Follow existing repository conventions.
- Do not revert or touch unrelated changes; keep changes limited to the assigned task.
- Add or update tests where appropriate, and run relevant verification commands.
- Inspect the final diff for unrelated changes.

After completing the work, report:
1. Files changed.
2. Behavior implemented.
3. Verification commands and results.
4. Remaining risks or unresolved issues.
5. Time and token consumption.

Skills you should use: /implement, /tdd
CURSOR_PROMPT
)"

# After the run, inspect the actual repository changes:
#   git status --short && git diff --stat && git diff

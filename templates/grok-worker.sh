#!/usr/bin/env bash
# Grok implementation-worker template.
# Fill in every [FILL IN] below, then run this script from the target repo.
#
# Invocation:
#   Uses the managed `grok` binary (the standalone grok-agent has been removed).
#
# Token accounting:
#   grok persists NO token usage to disk, so we run headless in JSON mode
#   (`-p --output-format json`) and capture the exact billed usage from the
#   final result line at run time. Each run's usage (uncached input / output /
#   cache-read + USD cost) is recorded into
#     ~/.grok-agent-usage/<url-escaped-cwd>/<session-id>.json
#   which monitor/grok_tokens.py reads back (see SKILL.md ## Monitoring).

# Locate the skill's monitor/ dir so usage can be recorded from any target repo.
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PROMPT="$(cat <<'GROK_PROMPT'
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
GROK_PROMPT
)"

# Invocation form:  grok --always-approve -p "<prompt>" --output-format json
# (--output-format json is required to capture token usage headlessly.)
GROK_OUTPUT="$(grok --always-approve -p "$PROMPT" --output-format json)"
GROK_STATUS=$?

# Show the result (JSON: the model's report plus the billed usage object).
printf '%s\n' "$GROK_OUTPUT"

# Persist the exact billed usage for token accounting (grok keeps none on disk).
printf '%s' "$GROK_OUTPUT" | python3 "$SKILL_DIR/monitor/grok_tokens.py" --record --cwd "$PWD"

# After the run, inspect the actual repository changes:
#   git status --short && git diff --stat && git diff
exit $GROK_STATUS

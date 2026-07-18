#!/usr/bin/env bash
# Cursor implementation-worker template.
# Fill in every [FILL IN] below, then run this script from the target repo.
#
# Model selection:
#   Default is grok-4.5. For a "cursor-<model>" request (e.g. cursor-grok-4.5),
#   pass the part after "cursor-" as MODEL:  MODEL=grok-4.5 ./cursor-worker.sh
#
# Token accounting:
#   Cursor persists NO token usage to disk, so we run in headless JSON mode
#   (`-p --output-format json`) and capture the `usage` from the final result
#   line at run time. Each run's exact billed usage (uncached input / output /
#   cache-read / cache-write) is written to
#     ~/.cursor-agent-usage/<url-escaped-cwd>/<session-id>.json
#   which monitor/cursor_tokens.py reads back (see SKILL.md ## Monitoring).

MODEL="${MODEL:-grok-4.5}"

PROMPT="$(cat <<'CURSOR_PROMPT'
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

# Invocation form:  agent --model "<model>" --force "<prompt>"
# (-p --output-format json is required to capture token usage headlessly.)
CURSOR_OUTPUT="$(agent -p --output-format json --force --model "$MODEL" "$PROMPT")"
CURSOR_STATUS=$?

# Show the result (JSON: .result is the model's report, .usage is the token split).
printf '%s\n' "$CURSOR_OUTPUT"

# Persist the exact billed usage for token accounting (cursor keeps none on disk).
printf '%s' "$CURSOR_OUTPUT" | CURSOR_MODEL="$MODEL" CURSOR_CWD="$PWD" python3 -c '
import json, os, sys, time, urllib.parse
from pathlib import Path
raw = sys.stdin.read()
model = os.environ.get("CURSOR_MODEL", "grok-4.5")
cwd = Path(os.environ.get("CURSOR_CWD", ".")).resolve()
obj = fb = None
for line in raw.splitlines():
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        o = json.loads(line)
    except Exception:
        continue
    if isinstance(o, dict) and o.get("type") == "result":
        obj = o
    elif isinstance(o, dict) and isinstance(o.get("usage"), dict):
        fb = o
obj = obj or fb
if obj is None:
    sys.stderr.write("cursor-worker: no usage object in agent output; token record skipped\n")
    sys.exit(0)
u = obj.get("usage") or {}
sid = obj.get("session_id") or "unknown"
rec = {
    "schema": "cursor-agent-usage/1",
    "session_id": sid,
    "request_id": obj.get("request_id"),
    "model": model,
    "cwd": str(cwd),
    "created_at_ms": int(time.time() * 1000),
    "duration_ms": obj.get("duration_ms"),
    "is_error": bool(obj.get("is_error", False)),
    "result_text": obj.get("result") if isinstance(obj.get("result"), str) else None,
    "usage": {k: int(u.get(k) or 0) for k in ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")},
}
d = Path.home() / ".cursor-agent-usage" / urllib.parse.quote(str(cwd), safe="")
d.mkdir(parents=True, exist_ok=True)
p = d / (sid + ".json")
p.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
us = rec["usage"]
sys.stderr.write("cursor-worker: recorded usage -> %s\n  in(uncached)=%d cache_read=%d cache_write=%d out=%d\n"
                 % (p, us["inputTokens"], us["cacheReadTokens"], us["cacheWriteTokens"], us["outputTokens"]))
'

# After the run, inspect the actual repository changes:
#   git status --short && git diff --stat && git diff
exit $CURSOR_STATUS

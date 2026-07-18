#!/usr/bin/env python3
"""Cursor CLI (`agent`) token accounting — capture-at-run-time, exact & billed.

Why this is different from grok
-------------------------------
The Cursor agent CLI (`agent -p --output-format json …`) prints an exact usage
object on the FINAL `result` line of stdout::

    {"type":"result", ... ,"session_id":"…","usage":{
        "inputTokens": 8863, "outputTokens": 29,
        "cacheReadTokens": 9344, "cacheWriteTokens": 0}}

Those four fields are **exactly** what we track for every provider — uncached
input, output, cache-read, cache-write — and they are true *billed* usage,
accumulated across every API call in the run (a multi-tool run shows
`cacheReadTokens` growing as context is re-read). Semantics match Claude, so
cursor rows are ``units: "billed"`` and ARE comparable to Claude columns
(unlike grok's conversation-size units).

The catch: **cursor persists none of this to disk** (its `~/.cursor/chats`
store keeps messages but no token usage; `~/.cursor/ai-tracking` counts code
lines, not tokens). So usage only exists on stdout at invocation time and MUST
be captured then. `templates/cursor-worker.sh` does that automatically, writing
one record per run into a per-cwd store this module reads back:

    ~/.cursor-agent-usage/<url-escaped-cwd>/<session-id>.json

Record schema (schema "cursor-agent-usage/1"):
    session_id, request_id, model, cwd, created_at_ms, duration_ms, is_error,
    result_text, usage{inputTokens,outputTokens,cacheReadTokens,cacheWriteTokens}

The cursor result JSON does NOT carry the model name, so the recorder stamps the
model it invoked (`--model`, default grok-4.5). Model labels are tagged
``"<model> (cursor)"`` so the by-model donut never conflates a cursor run with a
native grok-agent run.

Usage:
    # read back a cwd's cursor runs (default):
    python cursor_tokens.py [--cwd /path/to/repo] [-n 5]

    # record a run's usage from captured stdout (used by cursor-worker.sh):
    agent -p --output-format json … | \\
        python cursor_tokens.py --record --model grok-4.5 --cwd /path/to/repo
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
from pathlib import Path

USAGE_ROOT = Path.home() / ".cursor-agent-usage"
SCHEMA = "cursor-agent-usage/1"


# --------------------------------------------------------------------------- #
# Store layout (writer and reader agree here; the worker's inline python mirrors
# write_record for standalone robustness).
# --------------------------------------------------------------------------- #
def cwd_dir(cwd: Path) -> Path:
    escaped = urllib.parse.quote(str(Path(cwd).resolve()), safe="")
    return USAGE_ROOT / escaped


def _fmt_elapsed(duration_ms: int | None) -> str:
    if not duration_ms or duration_ms < 0:
        return "?"
    secs = int(duration_ms // 1000)
    return f"{secs // 60}m {secs % 60:02d}s"


# --------------------------------------------------------------------------- #
# Recording (capture stdout -> store)
# --------------------------------------------------------------------------- #
def parse_result_line(raw: str) -> dict | None:
    """Return the `type == "result"` object from captured agent stdout.

    Scans every line for a JSON object; prefers the one whose ``type`` is
    ``"result"`` (the run summary carrying ``usage``), else the last object
    that has a ``usage`` field. Returns None if none is found.
    """
    result = None
    fallback = None
    for line in raw.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "result":
            result = obj
        elif isinstance(obj.get("usage"), dict):
            fallback = obj
    return result or fallback


def write_record(raw: str, model: str, cwd: Path, *, created_at_ms: int | None = None) -> Path | None:
    """Persist one cursor run's usage from captured stdout. Returns the path.

    Idempotent per session_id: re-recording the same run overwrites its file.
    Returns None when the stdout carried no parseable result/usage object.
    """
    obj = parse_result_line(raw)
    if obj is None:
        return None
    usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
    session_id = obj.get("session_id") or "unknown"
    record = {
        "schema": SCHEMA,
        "session_id": session_id,
        "request_id": obj.get("request_id"),
        "model": model,
        "cwd": str(Path(cwd).resolve()),
        "created_at_ms": int(created_at_ms if created_at_ms is not None else time.time() * 1000),
        "duration_ms": obj.get("duration_ms"),
        "is_error": bool(obj.get("is_error", False)),
        "result_text": obj.get("result") if isinstance(obj.get("result"), str) else None,
        "usage": {
            "inputTokens": int(usage.get("inputTokens") or 0),
            "outputTokens": int(usage.get("outputTokens") or 0),
            "cacheReadTokens": int(usage.get("cacheReadTokens") or 0),
            "cacheWriteTokens": int(usage.get("cacheWriteTokens") or 0),
        },
    }
    dest_dir = cwd_dir(Path(cwd))
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{session_id}.json"
    dest.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return dest


# --------------------------------------------------------------------------- #
# Reading (store -> per-session usage rows)
# --------------------------------------------------------------------------- #
def session_info(record_path: Path) -> dict:
    """Exact billed split for one cursor run record.

    Keys mirror the Claude/grok row shape used by aggregate_tokens:
      id, path, tokens_in (uncached+cache_r+cache_w), tokens_out, tokens,
      in_uncached, cache_r, cache_w, model (raw), model_label ("… (cursor)"),
      elapsed, title, exact (True), units ("billed").
    """
    try:
        rec = json.loads(record_path.read_text())
    except (OSError, json.JSONDecodeError):
        rec = {}
    usage = rec.get("usage") if isinstance(rec.get("usage"), dict) else {}
    in_unc = int(usage.get("inputTokens") or 0)
    cache_r = int(usage.get("cacheReadTokens") or 0)
    cache_w = int(usage.get("cacheWriteTokens") or 0)
    out = int(usage.get("outputTokens") or 0)
    input_all = in_unc + cache_r + cache_w
    model = rec.get("model") or "?"
    title = (rec.get("result_text") or "").strip().replace("\n", " ")[:60] or "cursor run"
    return {
        "id": rec.get("session_id") or record_path.stem,
        "path": str(record_path),
        "tokens_in": input_all,
        "tokens_out": out,
        "tokens": input_all + out,
        "in_uncached": in_unc,
        "cache_r": cache_r,
        "cache_w": cache_w,
        "model": model,
        "model_label": f"{model} (cursor)",
        "elapsed": _fmt_elapsed(rec.get("duration_ms")),
        "title": title,
        "exact": True,
        "units": "billed",
    }


def sessions_for_cwd(cwd: Path) -> list[Path]:
    root = cwd_dir(Path(cwd))
    if not root.is_dir():
        return []
    files = [f for f in root.glob("*.json") if f.is_file()]
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)


def cursor_sessions_for_cwd(cwd: Path) -> list[dict]:
    """Per-session exact billed usage for a cwd (newest first). Mirrors
    grok_tokens.grok_sessions_for_cwd but every row is exact & billed."""
    return [session_info(p) for p in sessions_for_cwd(cwd)]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _record_main(args: argparse.Namespace) -> None:
    raw = sys.stdin.read()
    dest = write_record(raw, model=args.model, cwd=args.cwd)
    if dest is None:
        raise SystemExit(
            "cursor_tokens --record: no `type:result` / usage object on stdin; "
            "was `agent` run with `-p --output-format json`?"
        )
    info = session_info(dest)
    print(
        f"recorded {dest}\n  model={info['model_label']} "
        f"in(uncached)={info['in_uncached']:,} cache_read={info['cache_r']:,} "
        f"cache_write={info['cache_w']:,} out={info['tokens_out']:,} "
        f"total(billed)={info['tokens']:,}"
    )


def _list_main(args: argparse.Namespace) -> None:
    rows = cursor_sessions_for_cwd(args.cwd)
    if not rows:
        raise SystemExit(
            f"no cursor sessions found for cwd {args.cwd} "
            f"(store: {cwd_dir(args.cwd)})"
        )
    print(f"{'session':<38} {'in':>11} {'cache_r':>11} {'out':>9} "
          f"{'total':>11} {'model':<18} {'elapsed':>8}  title")
    for info in rows[: args.n]:
        print(f"{info['id']:<38} {info['tokens_in']:>11,} {info['cache_r']:>11,} "
              f"{info['tokens_out']:>9,} {info['tokens']:>11,} "
              f"{info['model_label']:<18} {info['elapsed']:>8}  {info['title']}")
    total = sum(r["tokens"] for r in rows)
    print(f"\n{len(rows)} cursor sessions · total {total:,} billed tokens (all exact).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", action="store_true",
                        help="record mode: read captured `agent` stdout on STDIN "
                             "and write a usage record for --cwd")
    parser.add_argument("--model", default="grok-4.5",
                        help="model that was invoked (record mode; default grok-4.5)")
    parser.add_argument("--cwd", type=Path, default=Path.cwd(),
                        help="repo the cursor run was launched from (default: cwd)")
    parser.add_argument("-n", type=int, default=10, help="max sessions to list")
    args = parser.parse_args()
    if args.record:
        _record_main(args)
    else:
        _list_main(args)


if __name__ == "__main__":
    main()

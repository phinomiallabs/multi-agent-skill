#!/usr/bin/env python3
"""List grok-agent CLI sessions with their total token consumption.

grok-agent prints no usage in plain headless mode, but each run writes
~/.grok/sessions/<url-escaped-cwd>/<session-uuid>/ containing:
  - updates.jsonl : updates carrying params._meta.totalTokens (running
    counter; the LAST value is the session total — one combined number;
    grok exposes NO native input/output/cached split anywhere on disk)
  - chat_history.jsonl : the turns; assistant-generated text lives here
  - summary.json  : model id, reasoning_effort, timestamps, title

Input/output split: estimated. Output tokens ~= chars of assistant-generated
content (message text + tool-call arguments) / 4; input = total - output.
The split is an estimate — the total is the only exact number.

Usage:
    python grok_tokens.py [--cwd /path/to/repo] [-n 5]

Prints one line per session, newest first:
    <session-id>  <in~>  <out~>  <total>  <model>  <elapsed>  <title>
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
from datetime import datetime
from pathlib import Path

SESSIONS_ROOT = Path.home() / ".grok" / "sessions"
_TOKENS_RE = re.compile(rb'"totalTokens":\s*(\d+)')


def session_tokens(session_dir: Path) -> int | None:
    """Last totalTokens value in updates.jsonl, or None if absent."""
    updates = session_dir / "updates.jsonl"
    if not updates.is_file():
        return None
    last = None
    for match in _TOKENS_RE.finditer(updates.read_bytes()):
        last = int(match.group(1))
    return last


def _text_chars(value: object) -> int:
    """Character count of the human/model-visible text inside a content field."""
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(_text_chars(item) for item in value)
    if isinstance(value, dict):
        return _text_chars(value.get("text", ""))
    return 0


def session_output_tokens_est(session_dir: Path) -> int | None:
    """Estimated output tokens: assistant-generated chars / 4.

    Counts assistant message text, tool-call arguments, and the visible
    reasoning summary from chat_history.jsonl. Encrypted reasoning content is
    approximated as base64-decoded chars / 4. This is an ESTIMATE — grok
    stores no real input/output split; only the total is exact.
    """
    history = session_dir / "chat_history.jsonl"
    if not history.is_file():
        return None
    chars = 0.0
    for line in history.read_text(errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = entry.get("type")
        if kind == "assistant":
            chars += _text_chars(entry.get("content"))
            for call in entry.get("tool_calls") or []:
                chars += len(str(call.get("arguments", "")))
        elif kind == "reasoning":
            encrypted = entry.get("encrypted_content") or ""
            if encrypted:
                chars += len(encrypted) * 0.75  # ~base64 → plaintext bytes
            else:
                chars += _text_chars(entry.get("summary"))
    return int(chars / 4)


def session_io_tokens(session_dir: Path) -> tuple[int | None, int | None, int | None]:
    """(input_est, output_est, total). Split is estimated; total is exact.

    input_est = total - output_est (floored at 0). Any element may be None
    when its source file is missing.
    """
    total = session_tokens(session_dir)
    out_est = session_output_tokens_est(session_dir)
    if total is None or out_est is None:
        return (None, out_est, total)
    out_est = min(out_est, total)
    return (total - out_est, out_est, total)


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def session_info(session_dir: Path) -> dict:
    tokens_in, tokens_out, total = session_io_tokens(session_dir)
    info = {
        "id": session_dir.name,
        "tokens": total,
        "tokens_in": tokens_in,   # estimated (total - output_est)
        "tokens_out": tokens_out,  # estimated (generated chars / 4)
        "model": "?",
        "elapsed": "?",
        "title": "?",
    }
    summary_path = session_dir / "summary.json"
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text())
        info["model"] = summary.get("current_model_id", "?")
        info["title"] = summary.get("generated_title") or summary.get("session_summary", "?")
        start = _parse_ts(summary.get("created_at", ""))
        end = _parse_ts(summary.get("updated_at", ""))
        if start and end:
            secs = int((end - start).total_seconds())
            info["elapsed"] = f"{secs // 60}m {secs % 60:02d}s"
    return info


def sessions_for_cwd(cwd: Path) -> list[Path]:
    escaped = urllib.parse.quote(str(cwd), safe="")
    root = SESSIONS_ROOT / escaped
    if not root.is_dir():
        return []
    dirs = [d for d in root.iterdir() if d.is_dir()]
    return sorted(dirs, key=lambda d: d.stat().st_mtime, reverse=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cwd", type=Path, default=Path.cwd(),
                        help="repo the grok run was launched from (default: cwd)")
    parser.add_argument("-n", type=int, default=5, help="max sessions to list")
    args = parser.parse_args()

    dirs = sessions_for_cwd(args.cwd.resolve())
    if not dirs:
        raise SystemExit(f"no grok sessions found for cwd {args.cwd}")
    def fmt(value: int | None) -> str:
        return f"{value:,}" if value is not None else "?"

    print(f"{'session':<38} {'in~':>10} {'out~':>9} {'total':>10}  model       elapsed   title")
    for session_dir in dirs[: args.n]:
        info = session_info(session_dir)
        print(f"{info['id']:<38} {fmt(info['tokens_in']):>10} {fmt(info['tokens_out']):>9} "
              f"{fmt(info['tokens']):>10}  {info['model']:<10}  {info['elapsed']:>8}  {info['title']}")


if __name__ == "__main__":
    main()

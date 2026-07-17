#!/usr/bin/env python3
"""List grok-agent CLI sessions with their token accounting.

IMPORTANT — units honesty
-------------------------
Grok's `totalTokens` (and the session totals this script reports) measure
**conversation size** — how large the context window is at a given point —
NOT per-call billed input the way Claude transcripts do (Claude re-counts the
full context on every API call). These units are **not comparable** to Claude
columns in the same table/donut. A rough billed-equivalent is
`avg_context_size × n_calls`, which is often ~10–20× larger than the
conversation-size total. Run-record / monitor rows should carry
`units: "conversation"` for grok (vs `"billed"` for Claude).

Each grok run writes ~/.grok/sessions/<url-escaped-cwd>/<session-uuid>/:
  - updates.jsonl : streaming updates. TWO different token signals live here:
      * the ADDITIVE SESSION LEDGER — on `turn_completed` events a `usage`
        object with `inputTokens` / `outputTokens` / `cachedReadTokens`
        / `reasoningTokens`. Summed across turns; already FOLDS IN any
        subagents the session spawned. Preferred when present. Still
        conversation-level accounting, not Claude-style per-call billing.
      * `params._meta.totalTokens` — a live CONTEXT-SIZE gauge, overwritten
        every turn (NOT a running total; it can even drop after a compaction).
        Used only as a fallback when no ledger is present. This is pure
        conversation size (grows ~9k→59k over a typical worker run).
    Older grok / plain headless mode emits no ledger; then we fall back to the
    gauge and ESTIMATE the input/output split (output ~= assistant chars / 4).
  - chat_history.jsonl : the turns; assistant-generated text (for the estimate).
  - summary.json  : model id, timestamps, title, and — for a subagent —
    `parent_session_id` / `session_kind`.

Subagents: a session that spawns subagents records each child's exact total in
a `subagent_finished` event (`tokens_used`); the child also gets its own session
dir. `grok_sessions_for_cwd` de-duplicates: a child is dropped only when its
parent used the additive ledger (which already includes it); under the gauge
fallback the parent's number excludes the child, so the child is kept and its
gauge is upgraded to the parent-reported exact `tokens_used`.

Usage:
    python grok_tokens.py [--cwd /path/to/repo] [-n 5]
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


def _iter_updates(session_dir: Path):
    """Yield each parsed JSON object from a session's updates.jsonl."""
    updates = session_dir / "updates.jsonl"
    if not updates.is_file():
        return
    for line in updates.read_text(errors="replace").splitlines():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _update_body(entry: dict) -> dict | None:
    """The SessionUpdate dict inside a `*/session/update` notification."""
    params = entry.get("params")
    if isinstance(params, dict):
        body = params.get("update")
        if isinstance(body, dict):
            return body
    return None


def _usage_field(usage: dict, *names: str) -> int:
    for name in names:
        if usage.get(name) is not None:
            return int(usage[name])
    return 0


def session_ledger(session_dir: Path) -> dict | None:
    """Exact additive billing ledger summed over `turn_completed.usage` events.

    Returns {tokens, tokens_in, tokens_out, cached, reasoning, turns}, or None
    when the session persisted no usage ledger (older grok / headless mode).
    `tokens_in` includes cached reads as a subset, so the cache-neutral input
    is `tokens_in - cached`.
    """
    agg = {"tokens_in": 0, "tokens_out": 0, "cached": 0, "reasoning": 0, "turns": 0}
    found = False
    for entry in _iter_updates(session_dir):
        body = _update_body(entry)
        if not body or body.get("sessionUpdate") != "turn_completed":
            continue
        usage = body.get("usage")
        if not isinstance(usage, dict):
            continue
        found = True
        agg["turns"] += 1
        agg["tokens_in"] += _usage_field(usage, "inputTokens", "input_tokens")
        agg["tokens_out"] += _usage_field(usage, "outputTokens", "output_tokens")
        agg["cached"] += _usage_field(usage, "cachedReadTokens", "cached_read_tokens")
        agg["reasoning"] += _usage_field(usage, "reasoningTokens", "reasoning_tokens")
    if not found:
        return None
    agg["tokens"] = agg["tokens_in"] + agg["tokens_out"]
    return agg


def session_gauge(session_dir: Path) -> int | None:
    """Last `_meta.totalTokens` — the live context-size gauge (fallback only).

    NOT a running total: overwritten each turn and can decrease after a
    compaction. For a single-prompt headless session it approximates the final
    context size. Only used for sessions with no additive ledger.
    """
    updates = session_dir / "updates.jsonl"
    if not updates.is_file():
        return None
    last = None
    for match in _TOKENS_RE.finditer(updates.read_bytes()):
        last = int(match.group(1))
    return last


# Back-compat alias (older callers imported session_tokens).
session_tokens = session_gauge


def subagent_finished_totals(session_dir: Path) -> dict:
    """{child_session_id: exact tokens_used} from this session's
    `subagent_finished` events."""
    out: dict[str, int] = {}
    for entry in _iter_updates(session_dir):
        body = _update_body(entry)
        if not body or body.get("sessionUpdate") != "subagent_finished":
            continue
        cid = body.get("child_session_id") or body.get("subagent_id")
        if cid:
            out[cid] = int(body.get("tokens_used") or 0)
    return out


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
    approximated as base64-decoded chars / 4. This is an ESTIMATE, used only
    when the exact ledger is absent.
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
    """(input_est, output_est, gauge_total). Split estimated; total is the gauge.

    input_est = gauge - output_est (floored at 0). Any element may be None when
    its source file is missing. Used only for the gauge fallback path.
    """
    total = session_gauge(session_dir)
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


def _session_meta(session_dir: Path) -> dict:
    meta = {"model": "?", "elapsed": "?", "title": "?",
            "parent_session_id": None, "is_subagent": False}
    summary_path = session_dir / "summary.json"
    if not summary_path.is_file():
        return meta
    try:
        summary = json.loads(summary_path.read_text())
    except json.JSONDecodeError:
        return meta
    meta["model"] = summary.get("current_model_id", "?")
    meta["title"] = summary.get("generated_title") or summary.get("session_summary", "?")
    meta["parent_session_id"] = summary.get("parent_session_id")
    kind = summary.get("session_kind") or ""
    meta["is_subagent"] = (bool(meta["parent_session_id"])
                           or kind.startswith("subagent")
                           or bool(summary.get("is_subagent")))
    start = _parse_ts(summary.get("created_at", ""))
    end = _parse_ts(summary.get("updated_at", ""))
    if start and end:
        secs = int((end - start).total_seconds())
        meta["elapsed"] = f"{secs // 60}m {secs % 60:02d}s"
    return meta


def session_info(session_dir: Path) -> dict:
    """Per-session usage — exact ledger when present, else gauge + estimate.

    Keys: id, path, tokens, tokens_in, tokens_out, cached, reasoning,
    basis ('ledger'|'gauge'), exact (bool), folds_subagents (bool),
    model, elapsed, title, parent_session_id, is_subagent.
    """
    ledger = session_ledger(session_dir)
    if ledger is not None:
        info = {
            "tokens": ledger["tokens"],
            "tokens_in": ledger["tokens_in"],
            "tokens_out": ledger["tokens_out"],
            "cached": ledger["cached"],
            "reasoning": ledger["reasoning"],
            "basis": "ledger",
            "exact": True,
            "folds_subagents": True,
        }
    else:
        tin, tout, total = session_io_tokens(session_dir)
        info = {
            "tokens": total,
            "tokens_in": tin,
            "tokens_out": tout,
            "cached": None,
            "reasoning": None,
            "basis": "gauge",
            "exact": False,
            "folds_subagents": False,
        }
    info["id"] = session_dir.name
    info["path"] = str(session_dir)
    info.update(_session_meta(session_dir))
    return info


def sessions_for_cwd(cwd: Path) -> list[Path]:
    escaped = urllib.parse.quote(str(cwd), safe="")
    root = SESSIONS_ROOT / escaped
    if not root.is_dir():
        return []
    dirs = [d for d in root.iterdir() if d.is_dir()]
    return sorted(dirs, key=lambda d: d.stat().st_mtime, reverse=True)


def grok_sessions_for_cwd(cwd: Path) -> list[dict]:
    """Enriched, de-duplicated per-session usage for a cwd.

    - Prefers each session's exact ledger; gauge otherwise.
    - A subagent child is dropped only when its parent used the additive ledger
      (already includes it). Under the gauge fallback the child is kept and its
      total upgraded to the parent-reported exact `tokens_used` (split stays
      estimated).
    """
    dirs = sessions_for_cwd(cwd)
    infos = {d.name: session_info(d) for d in dirs}

    child_real: dict[str, int] = {}     # child id -> exact tokens_used
    folded: set[str] = set()            # children already inside a ledger parent
    for d in dirs:
        subs = subagent_finished_totals(d)
        if not subs:
            continue
        parent_has_ledger = infos[d.name]["basis"] == "ledger"
        for cid, used in subs.items():
            child_real[cid] = used
            if parent_has_ledger:
                folded.add(cid)

    rows = []
    for d in dirs:  # preserve newest-first order
        info = infos[d.name]
        if info["id"] in folded:
            continue  # already counted inside a ledger parent
        real = child_real.get(info["id"])
        if info["basis"] == "gauge" and real:
            out = info["tokens_out"] or 0
            info = dict(info)
            info["tokens"] = real
            info["tokens_out"] = min(out, real)
            info["tokens_in"] = real - info["tokens_out"]
            info["real_total"] = True  # exact total, estimated split
        rows.append(info)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cwd", type=Path, default=Path.cwd(),
                        help="repo the grok run was launched from (default: cwd)")
    parser.add_argument("-n", type=int, default=5, help="max sessions to list")
    args = parser.parse_args()

    rows = grok_sessions_for_cwd(args.cwd.resolve())
    if not rows:
        raise SystemExit(f"no grok sessions found for cwd {args.cwd}")

    def fmt(value: int | None) -> str:
        return f"{value:,}" if value is not None else "?"

    print(f"{'session':<38} {'in':>10} {'out':>9} {'total':>10} {'basis':>7} "
          f"{'model':<10} {'elapsed':>8}  title")
    for info in rows[: args.n]:
        basis = info["basis"] + ("*" if info.get("real_total") else "")
        print(f"{info['id']:<38} {fmt(info['tokens_in']):>10} {fmt(info['tokens_out']):>9} "
              f"{fmt(info['tokens']):>10} {basis:>7} {info['model']:<10} "
              f"{info['elapsed']:>8}  {info['title']}")
    total = sum(r["tokens"] or 0 for r in rows)
    exact = sum(1 for r in rows if r["exact"])
    print(f"\n{len(rows)} sessions · total {total:,} tokens "
          f"({exact} exact ledger, {len(rows) - exact} gauge/estimated). "
          f"'basis' ledger=exact, gauge=context-size estimate, *=exact total via parent.")


if __name__ == "__main__":
    main()

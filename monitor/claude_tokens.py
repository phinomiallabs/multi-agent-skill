#!/usr/bin/env python3
"""Exact input/output token split for a Claude subagent transcript JSONL.

The Agent-tool completion notification reports one combined subagent token
total. The transcript file (tasks/<agentId>.output or any Claude session
JSONL) carries per-call `usage` blocks with the real split. This sums them
WITHOUT printing any transcript content (safe for orchestrator context).

Definitions (matching API usage semantics):
  in        = sum of usage.input_tokens (uncached input)
  cache_r   = sum of usage.cache_read_input_tokens
  cache_w   = sum of usage.cache_creation_input_tokens
  out       = sum of usage.output_tokens
  total     = in + cache_r + cache_w + out   (all tokens processed)

Model labeling:
  Assistant entries carry message.model (e.g. claude-sonnet-5, claude-fable-5).
  transcript_usage returns the dominant raw id (most frequent; last-seen
  breaks ties) as "model", plus a friendly "model_label" via
  friendly_model_name. Unknown ids pass through verbatim.

Usage:
    python claude_tokens.py <transcript.jsonl> [more.jsonl ...] [--json]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def friendly_model_name(raw: str | None) -> str | None:
    """Map a Claude API model id to a short display name.

    Known prefixes (star = any suffix):
      claude-fable-5*  -> Fable 5
      claude-sonnet-5* -> Sonnet 5
      claude-opus-4-8  -> Opus 4.8
      claude-haiku-4-5* -> Haiku 4.5
    Unknown ids are returned unchanged; None stays None. Never raises.
    """
    if raw is None:
        return None
    try:
        s = str(raw)
    except Exception:
        return None
    if s.startswith("claude-fable-5"):
        return "Fable 5"
    if s.startswith("claude-sonnet-5"):
        return "Sonnet 5"
    if s == "claude-opus-4-8" or s.startswith("claude-opus-4-8"):
        return "Opus 4.8"
    if s.startswith("claude-haiku-4-5"):
        return "Haiku 4.5"
    return s


def _dominant_model(counts: Counter, last: str | None) -> str | None:
    if not counts:
        return None
    max_c = max(counts.values())
    if last is not None and counts.get(last) == max_c:
        return last
    return counts.most_common(1)[0][0]


def transcript_usage(path: Path) -> dict:
    counts = {"in": 0, "cache_r": 0, "cache_w": 0, "out": 0, "calls": 0}
    model_counts: Counter = Counter()
    last_model: str | None = None
    with path.open(errors="replace") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = entry.get("message") if isinstance(entry.get("message"), dict) else None
            if msg:
                mid = msg.get("model")
                if mid:
                    mid_s = str(mid)
                    model_counts[mid_s] += 1
                    last_model = mid_s
            usage = None
            for holder in (entry, msg or {}):
                if isinstance(holder, dict) and isinstance(holder.get("usage"), dict):
                    usage = holder["usage"]
                    break
            if not usage:
                continue
            counts["in"] += int(usage.get("input_tokens") or 0)
            counts["cache_r"] += int(usage.get("cache_read_input_tokens") or 0)
            counts["cache_w"] += int(usage.get("cache_creation_input_tokens") or 0)
            counts["out"] += int(usage.get("output_tokens") or 0)
            counts["calls"] += 1
    counts["input_all"] = counts["in"] + counts["cache_r"] + counts["cache_w"]
    counts["total"] = counts["input_all"] + counts["out"]
    raw = _dominant_model(model_counts, last_model)
    counts["model"] = raw
    counts["model_label"] = friendly_model_name(raw)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("transcripts", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true", help="emit one JSON object per file")
    args = parser.parse_args()

    fmt = "{:<52} {:>10} {:>11} {:>10} {:>9} {:>11} {:>6}  {}"
    if not args.json:
        print(fmt.format("transcript", "in", "cache_read", "cache_wr", "out", "total", "calls", "model"))
    for path in args.transcripts:
        c = transcript_usage(path)
        label = c.get("model_label") or c.get("model") or "—"
        if args.json:
            print(json.dumps({"file": str(path), **c}))
        else:
            print(fmt.format(path.name, f"{c['in']:,}", f"{c['cache_r']:,}", f"{c['cache_w']:,}",
                             f"{c['out']:,}", f"{c['total']:,}", c["calls"], label))


if __name__ == "__main__":
    main()

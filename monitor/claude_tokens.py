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

Usage:
    python claude_tokens.py <transcript.jsonl> [more.jsonl ...] [--json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def transcript_usage(path: Path) -> dict:
    counts = {"in": 0, "cache_r": 0, "cache_w": 0, "out": 0, "calls": 0}
    with path.open(errors="replace") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            usage = None
            for holder in (entry, entry.get("message") or {}):
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
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("transcripts", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true", help="emit one JSON object per file")
    args = parser.parse_args()

    fmt = "{:<52} {:>10} {:>11} {:>10} {:>9} {:>11} {:>6}"
    if not args.json:
        print(fmt.format("transcript", "in", "cache_read", "cache_wr", "out", "total", "calls"))
    for path in args.transcripts:
        c = transcript_usage(path)
        if args.json:
            print(json.dumps({"file": str(path), **c}))
        else:
            print(fmt.format(path.name, f"{c['in']:,}", f"{c['cache_r']:,}", f"{c['cache_w']:,}",
                             f"{c['out']:,}", f"{c['total']:,}", c["calls"]))


if __name__ == "__main__":
    main()

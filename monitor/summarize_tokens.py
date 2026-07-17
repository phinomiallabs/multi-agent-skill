#!/usr/bin/env python3
"""Compute by-role and by-model token breakdowns (with percentages) for a run record.

Reads the run-record JSON (see generate_monitor.py for the schema), groups
its `token_log` entries, and writes the aggregates back into the record as
`token_summary` (by role) and `model_summary` (by model) — which
generate_monitor.py renders as their own sections.

Grouping for token_summary: each token_log entry may carry a "group" field
(e.g. "investigation", "implementation", "verification"). Entries without
one are grouped by their "model" string.

Grouping for model_summary: by the entry's "model" string; `agents` is the
count of distinct agent names under that model. Rows are sorted by tokens
descending.

Units honesty (uncached views)
------------------------------
`uncached` / `uncached_pct` cover **all** tracked rows:

* **billed** (Claude): uncached = tokens − cache_read (exact).
* **conversation** (grok): uncached is **estimated** as the conversation
  total (``tokens``). Each unique token is processed uncached at least once;
  assumes prefix caching on re-read context, so actual billed uncached ≥ this
  lower bound. Rows/groups with any conversation portion set
  ``uncached_estimated: true``.

Fallback for legacy rows without ``units``: a real ``cache_read`` > 0 counts
as billed; model/name containing "grok" counts as conversation.

Percentages (`pct`, `out_pct`) still cover all tracked rows (mixed units).
Estimate notes for in/out splits live at the agent level; a one-line note is
only attached to model_summary if token_summary already carries one.

Usage:
    python summarize_tokens.py <run.json>            # write back + print tables
    python summarize_tokens.py <run.json> --print    # print only, no write
"""

from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from pathlib import Path

UNCACHED_NOTE = (
    "Uncached for grok is estimated (= unique conversation tokens; assumes "
    "prefix caching — actual billed uncached >= this); Claude uncached is "
    "exact (total - cache_read)."
)


def row_units(entry: dict) -> str:
    """Return 'billed' or 'conversation' for a token_log / agent row."""
    units = entry.get("units")
    if units in ("billed", "conversation"):
        return units
    model = str(entry.get("model") or "").lower()
    name = str(entry.get("agent") or entry.get("name") or "").lower()
    if "grok" in model or name.startswith("grok"):
        return "conversation"
    # Legacy Claude rows often have a real cache_read; treat as billed.
    if int(entry.get("cache_read") or 0) > 0:
        return "billed"
    # Default: assume billed (Claude-style) when unknown.
    return "billed"


def is_billed(entry: dict) -> bool:
    return row_units(entry) == "billed"


def _group_uncached(g: dict) -> tuple[int, bool]:
    """Return (uncached, uncached_estimated) for an aggregate group/model.

    Billed portion: tokens − cache_read (exact). Conversation portion:
    conversation total as a lower-bound estimate of uncached.
    """
    billed_unc = 0
    if g["has_billed"]:
        billed_unc = g["billed_tokens"] - g["billed_cache"]
    conv_unc = g["conversation_tokens"] if g["has_conversation"] else 0
    return billed_unc + conv_unc, g["has_conversation"]


def summarize(record: dict) -> list[dict]:
    groups: "OrderedDict[str, dict]" = OrderedDict()
    for entry in record.get("token_log", []):
        key = entry.get("group") or entry.get("model", "unknown")
        g = groups.setdefault(key, {"group": key, "models": set(), "agents": 0,
                                    "tokens": 0, "tokens_in": 0, "tokens_out": 0,
                                    "cache_read": 0, "billed_tokens": 0,
                                    "billed_cache": 0, "conversation_tokens": 0,
                                    "has_billed": False,
                                    "has_conversation": False})
        g["models"].add(entry.get("model", "?"))
        g["agents"] += 1
        g["tokens"] += int(entry.get("tokens", 0))
        g["tokens_in"] += int(entry.get("tokens_in") or 0)
        g["tokens_out"] += int(entry.get("tokens_out") or 0)
        g["cache_read"] += int(entry.get("cache_read") or 0)
        if is_billed(entry):
            g["has_billed"] = True
            g["billed_tokens"] += int(entry.get("tokens", 0))
            g["billed_cache"] += int(entry.get("cache_read") or 0)
        else:
            g["has_conversation"] = True
            g["conversation_tokens"] += int(entry.get("tokens", 0))

    total = sum(g["tokens"] for g in groups.values()) or 1
    # out_pct is the cache-neutral view: share of OUTPUT (generated) tokens,
    # which neither provider inflates — unlike `pct` (share of total tokens),
    # where Claude's cache-read tokens dominate the denominator.
    total_out = sum(g["tokens_out"] for g in groups.values()) or 1
    # Uncached share: billed exact + conversation estimated.
    total_uncached = sum(_group_uncached(g)[0] for g in groups.values()) or 1
    rows = []
    any_estimated = False
    for g in groups.values():
        uncached, estimated = _group_uncached(g)
        if estimated:
            any_estimated = True
        row = {
            "group": g["group"],
            "models": ", ".join(sorted(g["models"])),
            "agents": g["agents"],
            "tokens_in": g["tokens_in"],
            "tokens_out": g["tokens_out"],
            "tokens": g["tokens"],
            "cache_read": g["cache_read"],
            "uncached": uncached,
            "pct": round(100.0 * g["tokens"] / total, 1),
            "out_pct": round(100.0 * g["tokens_out"] / total_out, 1),
            "uncached_pct": round(100.0 * uncached / total_uncached, 1),
        }
        if estimated:
            row["uncached_estimated"] = True
        rows.append(row)
    tot_cache = sum(g["cache_read"] for g in groups.values())
    tot_uncached = sum(_group_uncached(g)[0] for g in groups.values())
    total_row = {
        "group": "total (tracked)",
        "models": "",
        "agents": sum(g["agents"] for g in groups.values()),
        "tokens_in": sum(g["tokens_in"] for g in groups.values()),
        "tokens_out": sum(g["tokens_out"] for g in groups.values()),
        "tokens": total,
        "cache_read": tot_cache,
        "uncached": tot_uncached,
        "pct": 100.0,
        "out_pct": 100.0,
        "uncached_pct": 100.0,
        "note": UNCACHED_NOTE,
    }
    if any_estimated:
        total_row["uncached_estimated"] = True
    rows.append(total_row)
    return rows


def summarize_by_model(record: dict) -> list[dict]:
    """Aggregate token_log by model. agents = distinct agent names per model."""
    models: "OrderedDict[str, dict]" = OrderedDict()
    for entry in record.get("token_log", []):
        key = entry.get("model") or "unknown"
        m = models.setdefault(key, {"model": key, "agents": set(),
                                    "tokens": 0, "tokens_in": 0, "tokens_out": 0,
                                    "cache_read": 0, "billed_tokens": 0,
                                    "billed_cache": 0, "conversation_tokens": 0,
                                    "has_billed": False,
                                    "has_conversation": False})
        m["agents"].add(entry.get("agent", "?"))
        m["tokens"] += int(entry.get("tokens", 0))
        m["tokens_in"] += int(entry.get("tokens_in") or 0)
        m["tokens_out"] += int(entry.get("tokens_out") or 0)
        m["cache_read"] += int(entry.get("cache_read") or 0)
        if is_billed(entry):
            m["has_billed"] = True
            m["billed_tokens"] += int(entry.get("tokens", 0))
            m["billed_cache"] += int(entry.get("cache_read") or 0)
        else:
            m["has_conversation"] = True
            m["conversation_tokens"] += int(entry.get("tokens", 0))

    total = sum(m["tokens"] for m in models.values()) or 1
    total_out = sum(m["tokens_out"] for m in models.values()) or 1  # cache-neutral base
    total_uncached = sum(_group_uncached(m)[0] for m in models.values()) or 1
    rows = []
    any_estimated = False
    for m in models.values():
        uncached, estimated = _group_uncached(m)
        if estimated:
            any_estimated = True
        row = {
            "model": m["model"],
            "agents": len(m["agents"]),
            "tokens_in": m["tokens_in"],
            "tokens_out": m["tokens_out"],
            "tokens": m["tokens"],
            "cache_read": m["cache_read"],
            "uncached": uncached,
            "pct": round(100.0 * m["tokens"] / total, 1),
            "out_pct": round(100.0 * m["tokens_out"] / total_out, 1),
            "uncached_pct": round(100.0 * uncached / total_uncached, 1),
        }
        if estimated:
            row["uncached_estimated"] = True
        rows.append(row)
    rows.sort(key=lambda r: r["tokens"], reverse=True)

    all_agents: set[str] = set()
    for m in models.values():
        all_agents |= m["agents"]
    tot_cache = sum(m["cache_read"] for m in models.values())
    tot_uncached = sum(_group_uncached(m)[0] for m in models.values())
    total_row = {
        "model": "total (tracked)",
        "agents": len(all_agents),
        "tokens_in": sum(m["tokens_in"] for m in models.values()),
        "tokens_out": sum(m["tokens_out"] for m in models.values()),
        "tokens": total,
        "cache_read": tot_cache,
        "uncached": tot_uncached,
        "pct": 100.0,
        "out_pct": 100.0,
        "uncached_pct": 100.0,
        "note": UNCACHED_NOTE,
    }
    if any_estimated:
        total_row["uncached_estimated"] = True
    rows.append(total_row)
    return rows


def _summary_note(token_summary: list | dict | None) -> str | None:
    """Return a one-line note if token_summary carries one; else None.

    token_summary is normally a list of row dicts. A dict wrapper with a
    'note' key is also accepted for forward compatibility.
    """
    if isinstance(token_summary, dict):
        note = token_summary.get("note")
        return str(note) if note else None
    if isinstance(token_summary, list) and token_summary:
        # Optional note on the total row or first row.
        for row in token_summary:
            if isinstance(row, dict) and row.get("note"):
                return str(row["note"])
    return None


def _fmt_unc_pct(value: object) -> str:
    """Format uncached_pct; None is legacy n/a (should not appear after re-summarize)."""
    if value is None:
        return "n/a"
    return f"{value}%"


def print_table(rows: list[dict]) -> None:
    fmt = "{:<28} {:<34} {:>7} {:>12} {:>12} {:>12} {:>6} {:>6} {:>6}"
    print(fmt.format("Role", "Model(s)", "Agents", "In", "Out", "Total", "Tot%", "Gen%", "Unc%"))
    for r in rows:
        print(fmt.format(r["group"], r["models"], r["agents"],
                         f"{r.get('tokens_in', 0):,}", f"{r.get('tokens_out', 0):,}",
                         f"{r['tokens']:,}", f"{r['pct']}%", f"{r.get('out_pct', '—')}%",
                         _fmt_unc_pct(r.get("uncached_pct"))))


def print_model_table(rows: list[dict]) -> None:
    fmt = "{:<28} {:>7} {:>12} {:>12} {:>12} {:>6} {:>6} {:>6}"
    print(fmt.format("Model", "Agents", "In", "Out", "Total", "Tot%", "Gen%", "Unc%"))
    for r in rows:
        print(fmt.format(r["model"], r["agents"],
                         f"{r.get('tokens_in', 0):,}", f"{r.get('tokens_out', 0):,}",
                         f"{r['tokens']:,}", f"{r['pct']}%", f"{r.get('out_pct', '—')}%",
                         _fmt_unc_pct(r.get("uncached_pct"))))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("record", type=Path)
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="print the tables without writing summaries back")
    args = parser.parse_args()

    record = json.loads(args.record.read_text())
    rows = summarize(record)
    model_rows = summarize_by_model(record)

    print_table(rows)
    print()
    print_model_table(model_rows)

    note = _summary_note(rows)
    if not note:
        note = _summary_note(record.get("token_summary"))
    if note:
        if model_rows and not model_rows[-1].get("note"):
            model_rows[-1]["note"] = note
        print(f"\nnote: {note}")

    if not args.print_only:
        record["token_summary"] = rows
        record["model_summary"] = model_rows
        args.record.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        print(f"\nwrote token_summary ({len(rows)} rows) and "
              f"model_summary ({len(model_rows)} rows) into {args.record}")


if __name__ == "__main__":
    main()

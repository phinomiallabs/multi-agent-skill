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

Percentages are of the tracked total (the orchestrating session itself is
usually untracked; say so in the record's notes, not here). Estimate notes
for in/out splits live at the agent level; a one-line note is only attached
to model_summary if token_summary already carries one.

Usage:
    python summarize_tokens.py <run.json>            # write back + print tables
    python summarize_tokens.py <run.json> --print    # print only, no write
"""

from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from pathlib import Path


def summarize(record: dict) -> list[dict]:
    groups: "OrderedDict[str, dict]" = OrderedDict()
    for entry in record.get("token_log", []):
        key = entry.get("group") or entry.get("model", "unknown")
        g = groups.setdefault(key, {"group": key, "models": set(), "agents": 0,
                                    "tokens": 0, "tokens_in": 0, "tokens_out": 0})
        g["models"].add(entry.get("model", "?"))
        g["agents"] += 1
        g["tokens"] += int(entry.get("tokens", 0))
        g["tokens_in"] += int(entry.get("tokens_in") or 0)
        g["tokens_out"] += int(entry.get("tokens_out") or 0)

    total = sum(g["tokens"] for g in groups.values()) or 1
    # out_pct is the cache-neutral view: share of OUTPUT (generated) tokens,
    # which neither provider inflates — unlike `pct` (share of total tokens),
    # where Claude's cache-read tokens dominate the denominator.
    total_out = sum(g["tokens_out"] for g in groups.values()) or 1
    rows = []
    for g in groups.values():
        rows.append({
            "group": g["group"],
            "models": ", ".join(sorted(g["models"])),
            "agents": g["agents"],
            "tokens_in": g["tokens_in"],
            "tokens_out": g["tokens_out"],
            "tokens": g["tokens"],
            "pct": round(100.0 * g["tokens"] / total, 1),
            "out_pct": round(100.0 * g["tokens_out"] / total_out, 1),
        })
    rows.append({
        "group": "total (tracked)",
        "models": "",
        "agents": sum(g["agents"] for g in groups.values()),
        "tokens_in": sum(g["tokens_in"] for g in groups.values()),
        "tokens_out": sum(g["tokens_out"] for g in groups.values()),
        "tokens": total,
        "pct": 100.0,
        "out_pct": 100.0,
    })
    return rows


def summarize_by_model(record: dict) -> list[dict]:
    """Aggregate token_log by model. agents = distinct agent names per model."""
    models: "OrderedDict[str, dict]" = OrderedDict()
    for entry in record.get("token_log", []):
        key = entry.get("model") or "unknown"
        m = models.setdefault(key, {"model": key, "agents": set(),
                                    "tokens": 0, "tokens_in": 0, "tokens_out": 0})
        m["agents"].add(entry.get("agent", "?"))
        m["tokens"] += int(entry.get("tokens", 0))
        m["tokens_in"] += int(entry.get("tokens_in") or 0)
        m["tokens_out"] += int(entry.get("tokens_out") or 0)

    total = sum(m["tokens"] for m in models.values()) or 1
    total_out = sum(m["tokens_out"] for m in models.values()) or 1  # cache-neutral base
    rows = []
    for m in models.values():
        rows.append({
            "model": m["model"],
            "agents": len(m["agents"]),
            "tokens_in": m["tokens_in"],
            "tokens_out": m["tokens_out"],
            "tokens": m["tokens"],
            "pct": round(100.0 * m["tokens"] / total, 1),
            "out_pct": round(100.0 * m["tokens_out"] / total_out, 1),
        })
    rows.sort(key=lambda r: r["tokens"], reverse=True)

    all_agents: set[str] = set()
    for m in models.values():
        all_agents |= m["agents"]
    rows.append({
        "model": "total (tracked)",
        "agents": len(all_agents),
        "tokens_in": sum(m["tokens_in"] for m in models.values()),
        "tokens_out": sum(m["tokens_out"] for m in models.values()),
        "tokens": total,
        "pct": 100.0,
        "out_pct": 100.0,
    })
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


def print_table(rows: list[dict]) -> None:
    fmt = "{:<28} {:<38} {:>7} {:>12} {:>12} {:>12} {:>7} {:>7}"
    print(fmt.format("Role", "Model(s)", "Agents", "In", "Out", "Total", "Tot%", "Gen%"))
    for r in rows:
        print(fmt.format(r["group"], r["models"], r["agents"],
                         f"{r.get('tokens_in', 0):,}", f"{r.get('tokens_out', 0):,}",
                         f"{r['tokens']:,}", f"{r['pct']}%", f"{r.get('out_pct', '—')}%"))


def print_model_table(rows: list[dict]) -> None:
    fmt = "{:<28} {:>7} {:>12} {:>12} {:>12} {:>7} {:>7}"
    print(fmt.format("Model", "Agents", "In", "Out", "Total", "Tot%", "Gen%"))
    for r in rows:
        print(fmt.format(r["model"], r["agents"],
                         f"{r.get('tokens_in', 0):,}", f"{r.get('tokens_out', 0):,}",
                         f"{r['tokens']:,}", f"{r['pct']}%", f"{r.get('out_pct', '—')}%"))


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

    note = _summary_note(record.get("token_summary") if args.print_only else rows)
    # Prefer note from newly computed token_summary only if rows carry one;
    # otherwise check any pre-existing note on the old token_summary.
    if not note:
        note = _summary_note(record.get("token_summary"))
    if note:
        # Attach only to the total row of model_summary when token_summary has a note.
        if model_rows:
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

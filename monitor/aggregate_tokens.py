#!/usr/bin/env python3
"""Complete-by-default token accounting for a multi-agent orchestration run.

The per-run monitor historically only tracked directly-launched Claude
subagents. This CLI sweeps every known ground-truth location so a run record
can include the advisor session, direct launches, nested (sub-subagent)
Claude transcripts, and grok-agent sessions.

Ground-truth locations
----------------------
* Advisor transcript (exact split via claude_tokens):
    ~/.claude/projects/<project-slug>/<session-id>.jsonl
* ALL Claude subagent transcripts — direct AND nested — land flat in:
    /tmp/claude-1000/<project-slug>/<session-id>/tasks/a*.output
  Parentage is NOT recorded. Tag stems listed in --direct as direct; every
  other a*.output is rolled into one aggregated "nested" group.
* Grok sessions (estimated in/out; exact total) for each --repo-cwd:
    enumerated by grok_tokens.sessions_for_cwd

Model labels
------------
Claude rows take their `model` string from the dominant assistant
message.model id in each transcript (via claude_tokens.friendly_model_name:
Fable 5, Sonnet 5, Opus 4.8, Haiku 4.5, or the raw id). The advisor/direct/
nested distinction stays in `source` (agents) / `group` (token_log) — never
in the model string. If a transcript has no model field, fall back to the
legacy generic labels ("claude (advisor session)", etc.). Grok rows keep
session metadata model ids unchanged. Old run JSONs without per-row model
ids still render; only the model string content changes for new sweeps.

Usage:
    python aggregate_tokens.py --session-id <id> --project-slug <slug> \\
        [--repo-cwd <path> ...] [--direct <agentId> ...] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# Same-directory imports (this package is invoked as scripts, not installed).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_tokens import friendly_model_name, transcript_usage  # noqa: E402
from grok_tokens import grok_sessions_for_cwd  # noqa: E402

ADVISOR_ROOT = Path.home() / ".claude" / "projects"
TASKS_ROOT = Path("/tmp/claude-1000")

# Legacy model labels used when a Claude transcript has no message.model.
_GENERIC_MODEL = {
    "advisor": "claude (advisor session)",
    "direct": "claude (direct)",
    "nested": "claude (nested)",
}
_GENERIC_TOKEN_LOG_MODEL = {
    "advisor": "claude",
    "direct": "claude",
    "nested": "claude",
}


def advisor_path(project_slug: str, session_id: str) -> Path:
    return ADVISOR_ROOT / project_slug / f"{session_id}.jsonl"


def tasks_dir(project_slug: str, session_id: str) -> Path:
    return TASKS_ROOT / project_slug / session_id / "tasks"


def claude_row(name: str, kind: str, counts: dict, **extra) -> dict:
    """Map claude_tokens.transcript_usage counts to a reporting row.

    tokens_in uses input_all (uncached + cache_read + cache_write), matching
    the harness total and existing run-record convention.

    model is the friendly transcript label when known; omitted when the
    transcript had no message.model (callers fall back to generic labels).
    """
    row = {
        "kind": kind,
        "name": name,
        "tokens_in": counts["input_all"],
        "tokens_out": counts["out"],
        "tokens": counts["total"],
        "exact": True,
        "calls": counts["calls"],
        "in_uncached": counts["in"],
        "cache_r": counts["cache_r"],
        "cache_w": counts["cache_w"],
    }
    label = counts.get("model_label") or friendly_model_name(counts.get("model"))
    if label:
        row["model"] = label
    row.update(extra)
    return row


def empty_counts() -> dict:
    return {"in": 0, "cache_r": 0, "cache_w": 0, "out": 0, "calls": 0,
            "input_all": 0, "total": 0}


def add_counts(acc: dict, c: dict) -> None:
    for key in ("in", "cache_r", "cache_w", "out", "calls", "input_all", "total"):
        acc[key] += c[key]


def collect(
    session_id: str,
    project_slug: str,
    direct_ids: set[str],
    repo_cwds: list[Path],
) -> dict:
    rows: list[dict] = []

    # (a) Advisor transcript
    adv = advisor_path(project_slug, session_id)
    if adv.is_file():
        rows.append(claude_row("advisor", "advisor", transcript_usage(adv),
                               path=str(adv), session_id=session_id))
    else:
        rows.append({
            "kind": "advisor",
            "name": "advisor",
            "tokens_in": 0,
            "tokens_out": 0,
            "tokens": 0,
            "exact": True,
            "missing": True,
            "path": str(adv),
            "session_id": session_id,
        })

    # (b) Claude subagent transcripts (direct + nested aggregate)
    tdir = tasks_dir(project_slug, session_id)
    transcripts = sorted(tdir.glob("a*.output")) if tdir.is_dir() else []
    nested_acc = empty_counts()
    nested_files: list[str] = []
    nested_model_votes: Counter = Counter()

    for path in transcripts:
        stem = path.stem  # agentId
        counts = transcript_usage(path)
        if stem in direct_ids:
            rows.append(claude_row(stem, "direct", counts, path=str(path)))
        else:
            add_counts(nested_acc, counts)
            nested_files.append(stem)
            label = counts.get("model_label") or friendly_model_name(counts.get("model"))
            if label:
                nested_model_votes[label] += 1

    n_nested = len(nested_files)
    nested_extra: dict = {
        "transcripts": n_nested,
        "agent_ids": nested_files,
        "path": str(tdir) if tdir.is_dir() else None,
    }
    if nested_model_votes:
        # One vote per nested transcript; most common label wins.
        nested_extra["model"] = nested_model_votes.most_common(1)[0][0]
    rows.append(claude_row(
        f"nested ×{n_nested}",
        "nested",
        nested_acc,
        **nested_extra,
    ))

    # (c) Grok sessions per repo cwd (deduped; exact ledger preferred, gauge
    # fallback, subagent children rolled up without double-counting).
    for cwd in repo_cwds:
        resolved = cwd.resolve()
        for info in grok_sessions_for_cwd(resolved):
            rows.append({
                "kind": "grok",
                "name": info["id"],
                "tokens_in": info["tokens_in"] or 0,
                "tokens_out": info["tokens_out"] or 0,
                "tokens": info["tokens"] or 0,
                # exact when read from the additive ledger; gauge/estimate else.
                "exact": info.get("exact", False),
                "basis": info.get("basis"),
                "cached": info.get("cached"),
                "model": info["model"],
                "elapsed": info["elapsed"],
                "title": info["title"],
                "cwd": str(resolved),
                "path": info.get("path"),
            })

    totals = {
        "tokens_in": sum(r["tokens_in"] or 0 for r in rows),
        "tokens_out": sum(r["tokens_out"] or 0 for r in rows),
        "tokens": sum(r["tokens"] or 0 for r in rows),
    }
    return {
        "session_id": session_id,
        "project_slug": project_slug,
        "rows": rows,
        "totals": totals,
        # Paste-ready shapes for the run record.
        "agents": _as_agents(rows),
        "token_log": _as_token_log(rows),
    }


def _as_agents(rows: list[dict]) -> list[dict]:
    agents = []
    for r in rows:
        if r.get("missing") and r["kind"] == "advisor":
            continue
        if r["kind"] == "nested" and r.get("transcripts", 0) == 0:
            continue
        name = {
            "advisor": "advisor (this session)",
            "direct": r["name"],
            "nested": f"nested-agents ×{r.get('transcripts', 0)}",
            "grok": f"grok:{r['name'][:8]}",
        }.get(r["kind"], r["name"])
        if r["kind"] == "grok":
            model = r.get("model", "grok")
        else:
            # Transcript-derived friendly name, else legacy generic source tag.
            model = r.get("model") or _GENERIC_MODEL.get(r["kind"], "?")
        entry = {
            "name": name,
            "model": model,
            "tokens": r["tokens"],
            "tokens_in": r["tokens_in"],
            "tokens_out": r["tokens_out"],
            # cache-read (re-reads): cache_r for Claude, cachedReadTokens for a
            # grok ledger, 0 for the grok gauge (no re-reads counted on disk).
            "cache_read": int((r.get("cached") if r["kind"] == "grok" else r.get("cache_r")) or 0),
            "source": r["kind"],
            "exact": r.get("exact", True),
        }
        if r["kind"] == "grok":
            entry["elapsed"] = r.get("elapsed", "?")
            entry["title"] = r.get("title", "?")
            entry["session_id"] = r["name"]
        if r["kind"] == "nested":
            entry["transcripts"] = r.get("transcripts", 0)
        if r["kind"] == "direct":
            entry["agent_id"] = r["name"]
        agents.append(entry)
    return agents


def _as_token_log(rows: list[dict]) -> list[dict]:
    log = []
    for r in rows:
        if r.get("missing") and r["kind"] == "advisor":
            continue
        if r["kind"] == "nested" and r.get("transcripts", 0) == 0:
            continue
        if r["kind"] == "grok":
            model = r.get("model", "grok")
        else:
            model = r.get("model") or _GENERIC_TOKEN_LOG_MODEL.get(r["kind"], "?")
        entry = {
            "agent": r["name"] if r["kind"] != "nested"
                     else f"nested-agents ×{r.get('transcripts', 0)}",
            "model": model,
            "group": r["kind"],
            "tokens": r["tokens"],
            "tokens_in": r["tokens_in"],
            "tokens_out": r["tokens_out"],
            "cache_read": int((r.get("cached") if r["kind"] == "grok" else r.get("cache_r")) or 0),
            "exact": r.get("exact", True),
        }
        if r["kind"] == "grok":
            entry["task"] = r.get("title", "")
            entry["session_id"] = r["name"]
        log.append(entry)
    return log


def print_table(result: dict) -> None:
    fmt = "{:<10} {:<42} {:>12} {:>12} {:>12}  {}"
    print(fmt.format("kind", "name", "in", "out", "total", "notes"))
    print(fmt.format("-" * 10, "-" * 42, "-" * 12, "-" * 12, "-" * 12, "-" * 20))
    for r in result["rows"]:
        if r.get("missing") and r["kind"] == "advisor":
            notes = "MISSING " + r.get("path", "")
            print(fmt.format(r["kind"], r["name"], "—", "—", "—", notes))
            continue
        notes = ""
        model = r.get("model")
        if r["kind"] == "nested":
            notes = f"{r.get('transcripts', 0)} transcripts (aggregate)"
            if model:
                notes = f"{model} · {notes}"
        elif r["kind"] == "advisor":
            notes = f"{model} · exact · own session" if model else "exact · own session"
        elif r["kind"] == "direct":
            notes = f"{model} · exact · direct launch" if model else "exact · direct launch"
        elif r["kind"] == "grok":
            notes = f"est. split · {r.get('model', '?')} · {r.get('elapsed', '?')}"
            if r.get("title") and r["title"] != "?":
                notes += f" · {r['title'][:40]}"
        print(fmt.format(
            r["kind"],
            r["name"][:42],
            f"{r['tokens_in']:,}",
            f"{r['tokens_out']:,}",
            f"{r['tokens']:,}",
            notes,
        ))
    t = result["totals"]
    print(fmt.format("-" * 10, "-" * 42, "-" * 12, "-" * 12, "-" * 12, "-" * 20))
    print(fmt.format(
        "TOTAL", "", f"{t['tokens_in']:,}", f"{t['tokens_out']:,}", f"{t['tokens']:,}",
        "advisor + direct + nested + grok",
    ))


def _normalize_argv(argv: list[str]) -> list[str]:
    """Allow option values that start with '-' (project slugs look like flags).

    Turns ``--project-slug -home-ubuntu-...`` into ``--project-slug=-home-...``
    so argparse does not swallow the value as unknown short options.
    """
    value_opts = {"--session-id", "--project-slug", "--repo-cwd", "--direct"}
    out: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in value_opts and i + 1 < len(argv):
            nxt = argv[i + 1]
            if nxt.startswith("-") and not nxt.startswith("--"):
                out.append(f"{arg}={nxt}")
                i += 2
                continue
        out.append(arg)
        i += 1
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Ground-truth paths are documented in the module docstring.",
    )
    parser.add_argument("--session-id", required=True,
                        help="Claude advisor session id (UUID)")
    parser.add_argument("--project-slug", required=True,
                        help="Claude project slug (e.g. -home-ubuntu-repos-foo)")
    parser.add_argument("--repo-cwd", action="append", default=[], type=Path,
                        help="repo cwd whose grok sessions to include (repeatable)")
    parser.add_argument("--direct", action="append", default=[],
                        help="agentId of a directly-launched Claude subagent "
                             "(basename stem of tasks/a*.output); repeatable")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable dict (rows/agents/token_log/totals)")
    args = parser.parse_args(_normalize_argv(argv if argv is not None else sys.argv[1:]))

    result = collect(
        session_id=args.session_id,
        project_slug=args.project_slug,
        direct_ids=set(args.direct),
        repo_cwds=list(args.repo_cwd),
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print_table(result)


if __name__ == "__main__":
    main()

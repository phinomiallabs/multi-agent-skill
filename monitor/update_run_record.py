#!/usr/bin/env python3
"""Merge exact aggregate_tokens numbers into a run-record JSON in place.

Workflow (MANDATORY — never hand-type token numbers)
----------------------------------------------------
1. Create the run-record JSON with human fields only: title, phases, agents
   with name/model/role/status/label, and an optional per-agent ``source`` key
   (``"advisor"``, ``"direct:<agentId>"``, ``"nested"``, ``"grok:<sessionId>"``).
   Do **not** fill tokens_in / tokens_out / tokens / cache_read by hand.
2. At every phase transition and at run end, refresh numbers::

       python monitor/update_run_record.py runs/<date>-<slug>.json \\
           --session-id <uuid> --project-slug <slug> \\
           [--repo-cwd <path> ...] [--direct <agentId> ...]

   Discovery args are saved into the record's ``accounting`` block, so later
   refreshes are just::

       python monitor/update_run_record.py runs/<date>-<slug>.json

3. Then re-summarize and re-render::

       python monitor/summarize_tokens.py runs/<date>-<slug>.json
       python monitor/generate_monitor.py runs/<date>-<slug>.json -o runs/<date>-<slug>.html

What this does
--------------
* Imports ``aggregate_tokens.collect`` (no shell-out) and sweeps the same
  ground-truth locations.
* MERGES numeric fields (tokens_in, tokens_out, tokens, cache_read, elapsed,
  units, exact) into matching ``agents`` and ``token_log`` rows **in place**.
* Matching order:
    1. per-row ``source`` key (preferred),
    2. best-effort name / agent_id / session_id / token-fingerprint heuristics
       for legacy rows without ``source``,
    3. unmatched aggregate rows are APPENDED with placeholder names so nothing
       is ever silently dropped.
* Human-authored fields (name, role, status, label, phase, model title/subtitle
  etc.) are never overwritten on matched rows.
* Prints a diff-style summary of every change.
* Writes the updated JSON back (indent=2).

Units: Claude rows get ``units: "billed"``; grok rows get
``units: "conversation"`` (conversation size, not per-call billed input —
not comparable to Claude columns). See aggregate_tokens / grok_tokens docs.

Usage:
    python update_run_record.py <run.json> \\
        [--session-id ID] [--project-slug SLUG] \\
        [--repo-cwd PATH ...] [--direct AGENT_ID ...] \\
        [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Same-directory imports (this package is invoked as scripts, not installed).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from aggregate_tokens import (  # noqa: E402
    _normalize_argv,
    collect,
)

# Numeric / accounting fields we overwrite on match. Everything else (name,
# role, status, label, phase, human model display, …) is left alone.
_MERGE_FIELDS = (
    "tokens_in",
    "tokens_out",
    "tokens",
    "cache_read",
    "elapsed",
    "units",
    "exact",
)


def source_key_from_agg(agg: dict, *, for_token_log: bool = False) -> str:
    """Canonical source key for an aggregate agents/token_log fragment."""
    kind = agg.get("source") or agg.get("group") or ""
    if kind == "advisor":
        return "advisor"
    if kind == "nested":
        return "nested"
    if kind == "direct":
        aid = agg.get("agent_id") or (agg.get("name") if not for_token_log
                                      else agg.get("agent"))
        return f"direct:{aid}"
    if kind == "grok":
        sid = agg.get("session_id") or ""
        if not sid and for_token_log:
            # token_log agent field may be the raw session id
            agent = agg.get("agent") or ""
            if len(agent) > 20 and "-" in agent:
                sid = agent
        return f"grok:{sid}" if sid else "grok:?"
    return kind or "?"


def infer_source_key(row: dict, *, is_token_log: bool = False) -> str | None:
    """Best-effort source key from an existing record row (may lack source)."""
    explicit = row.get("source")
    if explicit:
        # Already a canonical key, or a short kind from an older aggregate paste.
        if explicit in ("advisor", "nested") or ":" in str(explicit):
            return str(explicit)
        if explicit == "direct":
            aid = row.get("agent_id")
            if aid:
                return f"direct:{aid}"
            return None
        if explicit == "grok":
            sid = row.get("session_id")
            if sid:
                return f"grok:{sid}"
            return None
        return str(explicit)

    name = str(row.get("agent" if is_token_log else "name") or "").lower()
    model = str(row.get("model") or "").lower()
    if name.startswith("advisor") or name == "advisor":
        return "advisor"
    if "nested" in name:
        return "nested"
    if row.get("agent_id"):
        return f"direct:{row['agent_id']}"
    if row.get("session_id") and ("grok" in name or "grok" in model):
        return f"grok:{row['session_id']}"
    # Bare agentId-looking name (Claude direct transcript stem).
    if not is_token_log and name.startswith("a") and len(name) >= 16 and " " not in name:
        return f"direct:{row['name']}"
    return None


def _fingerprint(row: dict) -> tuple:
    """Token triple used for heuristic match of legacy rows."""
    return (
        int(row.get("tokens") or 0),
        int(row.get("tokens_in") or 0),
        int(row.get("tokens_out") or 0),
    )


def _is_conversation_row(row: dict) -> bool:
    units = row.get("units")
    if units == "conversation":
        return True
    if units == "billed":
        return False
    model = str(row.get("model") or "").lower()
    name = str(row.get("name") or row.get("agent") or "").lower()
    return "grok" in model or name.startswith("grok")


def _merge_into(dest: dict, src: dict, source_key: str) -> list[str]:
    """Copy merge fields from src into dest. Return list of change descriptions."""
    changes: list[str] = []
    for field in _MERGE_FIELDS:
        if field not in src and field not in ("elapsed",):
            # cache_read / units / exact always set from aggregate shape
            if field == "cache_read":
                new = int(src.get("cache_read") or 0)
            elif field == "units":
                new = src.get("units") or (
                    "conversation" if source_key.startswith("grok") else "billed"
                )
            elif field == "exact":
                new = src.get("exact", True)
            else:
                continue
        elif field == "elapsed" and "elapsed" not in src:
            continue
        else:
            if field == "cache_read":
                new = int(src.get("cache_read") or 0)
            else:
                new = src[field] if field in src else dest.get(field)

        old = dest.get(field)
        if old != new:
            changes.append(f"{field}: {old!r} → {new!r}")
            dest[field] = new

    if dest.get("source") != source_key:
        changes.append(f"source: {dest.get('source')!r} → {source_key!r}")
        dest["source"] = source_key
    return changes


def _match_agents(record_agents: list[dict], agg_agents: list[dict]
                  ) -> tuple[list[tuple[dict, dict, str]], list[dict]]:
    """Return (matches as (record_row, agg_row, source_key), unmatched_agg)."""
    used_rec: set[int] = set()
    matches: list[tuple[dict, dict, str]] = []

    # Index record agents by inferred/explicit source key (first wins for
    # non-unique keys like a bare "nested").
    by_source: dict[str, list[int]] = {}
    for i, a in enumerate(record_agents):
        key = infer_source_key(a, is_token_log=False)
        if key:
            by_source.setdefault(key, []).append(i)

    unmatched_agg: list[dict] = []

    for agg in agg_agents:
        sk = source_key_from_agg(agg, for_token_log=False)
        # 1) exact source key
        candidates = [i for i in by_source.get(sk, []) if i not in used_rec]
        if candidates:
            i = candidates[0]
            used_rec.add(i)
            matches.append((record_agents[i], agg, sk))
            continue

        # 2) advisor / nested name heuristics (in case source key form differed)
        if sk == "advisor":
            for i, a in enumerate(record_agents):
                if i in used_rec:
                    continue
                n = str(a.get("name") or "").lower()
                if n.startswith("advisor"):
                    used_rec.add(i)
                    matches.append((record_agents[i], agg, sk))
                    break
            else:
                unmatched_agg.append(agg)
            continue
        if sk == "nested":
            for i, a in enumerate(record_agents):
                if i in used_rec:
                    continue
                if "nested" in str(a.get("name") or "").lower():
                    used_rec.add(i)
                    matches.append((record_agents[i], agg, sk))
                    break
            else:
                unmatched_agg.append(agg)
            continue

        # 3) direct: agent_id appears in name, or exact name match
        if sk.startswith("direct:"):
            aid = sk.split(":", 1)[1]
            found = False
            for i, a in enumerate(record_agents):
                if i in used_rec:
                    continue
                name = str(a.get("name") or "")
                if name == aid or a.get("agent_id") == aid:
                    used_rec.add(i)
                    matches.append((record_agents[i], agg, sk))
                    found = True
                    break
            if found:
                continue
            # token fingerprint among non-conversation rows
            fp = _fingerprint(agg)
            for i, a in enumerate(record_agents):
                if i in used_rec or _is_conversation_row(a):
                    continue
                if _fingerprint(a) == fp and fp != (0, 0, 0):
                    used_rec.add(i)
                    matches.append((record_agents[i], agg, sk))
                    found = True
                    break
            if found:
                continue
            unmatched_agg.append(agg)
            continue

        # 4) grok: session_id field, or token fingerprint among conversation rows
        if sk.startswith("grok:"):
            sid = sk.split(":", 1)[1]
            found = False
            if sid and sid != "?":
                for i, a in enumerate(record_agents):
                    if i in used_rec:
                        continue
                    if a.get("session_id") == sid:
                        used_rec.add(i)
                        matches.append((record_agents[i], agg, sk))
                        found = True
                        break
            if found:
                continue
            fp = _fingerprint(agg)
            for i, a in enumerate(record_agents):
                if i in used_rec or not _is_conversation_row(a):
                    continue
                if _fingerprint(a) == fp and fp != (0, 0, 0):
                    used_rec.add(i)
                    matches.append((record_agents[i], agg, sk))
                    found = True
                    break
            if found:
                continue
            unmatched_agg.append(agg)
            continue

        unmatched_agg.append(agg)

    return matches, unmatched_agg


def _match_token_log(record_log: list[dict], agg_log: list[dict]
                     ) -> tuple[list[tuple[dict, dict, str]], list[dict]]:
    """Match token_log rows similarly; prefer source, then agent name, then fp."""
    used: set[int] = set()
    matches: list[tuple[dict, dict, str]] = []
    unmatched: list[dict] = []

    by_source: dict[str, list[int]] = {}
    for i, e in enumerate(record_log):
        key = infer_source_key(e, is_token_log=True)
        if key:
            by_source.setdefault(key, []).append(i)

    # Also index by agent name (case-insensitive) for friendly-name records.
    by_agent: dict[str, list[int]] = {}
    for i, e in enumerate(record_log):
        by_agent.setdefault(str(e.get("agent") or "").lower(), []).append(i)

    for agg in agg_log:
        sk = source_key_from_agg(agg, for_token_log=True)
        candidates = [i for i in by_source.get(sk, []) if i not in used]
        if candidates:
            i = candidates[0]
            used.add(i)
            matches.append((record_log[i], agg, sk))
            continue

        if sk == "advisor":
            for i, e in enumerate(record_log):
                if i in used:
                    continue
                if str(e.get("agent") or "").lower().startswith("advisor"):
                    used.add(i)
                    matches.append((record_log[i], agg, sk))
                    break
            else:
                unmatched.append(agg)
            continue
        if sk == "nested":
            for i, e in enumerate(record_log):
                if i in used:
                    continue
                if "nested" in str(e.get("agent") or "").lower():
                    used.add(i)
                    matches.append((record_log[i], agg, sk))
                    break
            else:
                unmatched.append(agg)
            continue

        # Fingerprint match (works for friendly-named directs/groks whose
        # tokens were previously hand-pasted from the same aggregate).
        fp = _fingerprint(agg)
        found = False
        if fp != (0, 0, 0):
            for i, e in enumerate(record_log):
                if i in used:
                    continue
                if _fingerprint(e) == fp:
                    # Prefer same unit class.
                    if sk.startswith("grok") and not _is_conversation_row(e):
                        continue
                    if not sk.startswith("grok") and _is_conversation_row(e):
                        continue
                    used.add(i)
                    matches.append((record_log[i], agg, sk))
                    found = True
                    break
        if found:
            continue

        # agent_id / session_id as agent field
        if sk.startswith("direct:"):
            aid = sk.split(":", 1)[1].lower()
            for i in by_agent.get(aid, []):
                if i not in used:
                    used.add(i)
                    matches.append((record_log[i], agg, sk))
                    found = True
                    break
        if found:
            continue
        if sk.startswith("grok:"):
            sid = sk.split(":", 1)[1]
            for i, e in enumerate(record_log):
                if i in used:
                    continue
                if e.get("session_id") == sid or str(e.get("agent")) == sid:
                    used.add(i)
                    matches.append((record_log[i], agg, sk))
                    found = True
                    break
        if found:
            continue
        unmatched.append(agg)

    return matches, unmatched


def _placeholder_agent(agg: dict, source_key: str) -> dict:
    name = agg.get("name") or source_key
    return {
        "name": name if not str(name).startswith("[") else name,
        "model": agg.get("model", "?"),
        "role": f"(auto-appended by update_run_record; source={source_key})",
        "status": "done",
        "label": "auto",
        "tokens": agg.get("tokens", 0),
        "tokens_in": agg.get("tokens_in", 0),
        "tokens_out": agg.get("tokens_out", 0),
        "cache_read": int(agg.get("cache_read") or 0),
        "source": source_key,
        "units": agg.get("units") or (
            "conversation" if source_key.startswith("grok") else "billed"
        ),
        "exact": agg.get("exact", True),
        **({"elapsed": agg["elapsed"]} if "elapsed" in agg else {}),
        **({"session_id": agg["session_id"]} if "session_id" in agg else {}),
        **({"agent_id": agg["agent_id"]} if "agent_id" in agg else {}),
        **({"title": agg["title"]} if "title" in agg else {}),
    }


def _placeholder_log(agg: dict, source_key: str) -> dict:
    entry = {
        "phase": "auto",
        "agent": agg.get("agent") or agg.get("name") or source_key,
        "model": agg.get("model", "?"),
        "tokens": agg.get("tokens", 0),
        "tokens_in": agg.get("tokens_in", 0),
        "tokens_out": agg.get("tokens_out", 0),
        "cache_read": int(agg.get("cache_read") or 0),
        "source": source_key,
        "units": agg.get("units") or (
            "conversation" if source_key.startswith("grok") else "billed"
        ),
        "exact": agg.get("exact", True),
    }
    if "session_id" in agg:
        entry["session_id"] = agg["session_id"]
    if "group" in agg:
        entry["group"] = agg["group"]
    if "task" in agg:
        entry["task"] = agg["task"]
    return entry


def resolve_accounting(args: argparse.Namespace, record: dict) -> dict:
    """Merge CLI discovery args with any saved accounting block."""
    saved = dict(record.get("accounting") or {})
    session_id = args.session_id or saved.get("session_id")
    project_slug = args.project_slug or saved.get("project_slug")
    repo_cwd = list(args.repo_cwd) if args.repo_cwd else list(saved.get("repo_cwd") or [])
    direct = list(args.direct) if args.direct else list(saved.get("direct") or [])
    if not session_id or not project_slug:
        raise SystemExit(
            "need --session-id and --project-slug (or an accounting block in "
            "the run record with those keys)"
        )
    return {
        "session_id": session_id,
        "project_slug": project_slug,
        "repo_cwd": [str(p) for p in repo_cwd],
        "direct": list(direct),
    }


def update_record(record: dict, accounting: dict) -> list[str]:
    """Mutate record in place; return human-readable diff lines."""
    result = collect(
        session_id=accounting["session_id"],
        project_slug=accounting["project_slug"],
        direct_ids=set(accounting.get("direct") or []),
        repo_cwds=[Path(p) for p in accounting.get("repo_cwd") or []],
    )
    lines: list[str] = []
    agents = record.setdefault("agents", [])
    token_log = record.setdefault("token_log", [])

    a_matches, a_unmatched = _match_agents(agents, result["agents"])
    for dest, src, sk in a_matches:
        changes = _merge_into(dest, src, sk)
        # Keep session_id / agent_id for future source-key matches.
        if src.get("session_id") and dest.get("session_id") != src["session_id"]:
            changes.append(f"session_id: {dest.get('session_id')!r} → {src['session_id']!r}")
            dest["session_id"] = src["session_id"]
        if src.get("agent_id") and dest.get("agent_id") != src["agent_id"]:
            changes.append(f"agent_id: {dest.get('agent_id')!r} → {src['agent_id']!r}")
            dest["agent_id"] = src["agent_id"]
        label = dest.get("name") or sk
        if changes:
            lines.append(f"  agents[{label!r}] ({sk}):")
            for c in changes:
                lines.append(f"    {c}")
        else:
            lines.append(f"  agents[{label!r}] ({sk}): (no change)")

    for agg in a_unmatched:
        sk = source_key_from_agg(agg)
        placeholder = _placeholder_agent(agg, sk)
        agents.append(placeholder)
        lines.append(
            f"  agents: APPENDED {placeholder['name']!r} ({sk}) "
            f"tokens={placeholder['tokens']:,} cache_read={placeholder['cache_read']:,}"
        )

    t_matches, t_unmatched = _match_token_log(token_log, result["token_log"])
    for dest, src, sk in t_matches:
        changes = _merge_into(dest, src, sk)
        if src.get("session_id") and dest.get("session_id") != src["session_id"]:
            changes.append(f"session_id: {dest.get('session_id')!r} → {src['session_id']!r}")
            dest["session_id"] = src["session_id"]
        label = dest.get("agent") or sk
        if changes:
            lines.append(f"  token_log[{label!r}] ({sk}):")
            for c in changes:
                lines.append(f"    {c}")
        else:
            lines.append(f"  token_log[{label!r}] ({sk}): (no change)")

    for agg in t_unmatched:
        sk = source_key_from_agg(agg, for_token_log=True)
        placeholder = _placeholder_log(agg, sk)
        token_log.append(placeholder)
        lines.append(
            f"  token_log: APPENDED {placeholder['agent']!r} ({sk}) "
            f"tokens={placeholder['tokens']:,} cache_read={placeholder['cache_read']:,}"
        )

    record["accounting"] = accounting
    lines.append(
        f"  accounting: session_id={accounting['session_id']} "
        f"project_slug={accounting['project_slug']} "
        f"direct={len(accounting.get('direct') or [])} "
        f"repo_cwd={len(accounting.get('repo_cwd') or [])}"
    )
    return lines


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Full workflow is documented in the module docstring.",
    )
    parser.add_argument("record", type=Path, help="run-record JSON to update in place")
    parser.add_argument("--session-id", default=None,
                        help="Claude advisor session id (UUID); saved in accounting")
    parser.add_argument("--project-slug", default=None,
                        help="Claude project slug; saved in accounting")
    parser.add_argument("--repo-cwd", action="append", default=[], type=Path,
                        help="repo cwd whose grok sessions to include (repeatable)")
    parser.add_argument("--direct", action="append", default=[],
                        help="agentId of a directly-launched Claude subagent "
                             "(repeatable)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the diff without writing the file")
    args = parser.parse_args(_normalize_argv(argv if argv is not None else sys.argv[1:]))

    if not args.record.is_file():
        raise SystemExit(f"run record not found: {args.record}")

    record = json.loads(args.record.read_text())
    accounting = resolve_accounting(args, record)
    lines = update_record(record, accounting)

    print(f"update_run_record: {args.record}")
    print("diff:")
    for line in lines:
        print(line)

    if args.dry_run:
        print("(dry-run — not written)")
        return

    args.record.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {args.record}")


if __name__ == "__main__":
    main()

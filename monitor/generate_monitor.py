#!/usr/bin/env python3
"""Render the multi-agent orchestration monitor HTML from a run-record JSON.

Usage:
    python generate_monitor.py <run.json> [-o out.html]

The run record is the single source of truth for a run: agents, models,
phases, and token consumption. Keep one JSON per run under runs/ in this
skill folder; re-render (and re-publish the artifact) whenever it changes.

Record schema (all string fields are plain text; they are HTML-escaped here):
{
  "title":       "Example feature — multi-agent run",
  "subtitle":    "Advisor: ... · Implementer: ... · Investigator: ...",
  "updated":     "2026-07-15 16:30 EDT",
  "phase_note":  "current phase blurb shown under the timestamp",
  "phases": [
    {"name": "1 · Investigate", "detail": "verify spec claims",
     "status": "done|run|wait|fail", "label": "confirmed"}
  ],
  "agents": [
    {"name": "investigator-1", "model": "Sonnet",
     "role": "what it does / did",
     "status": "done|run|wait|fail", "label": "done",
     "tokens": 43314,            # int total, or a string like "…" / "—"
     "tokens_in": 40000,         # optional int; input tokens (may be estimated)
     "tokens_out": 3314,         # optional int; output tokens (may be estimated)
     "elapsed": "2m 33s"}
  ],
  "agents_note": "footnote under the agents table (optional)",
  "gate_note":   "footnote under the phases block (optional)",
  "environment": [["Repo", "/path @ sha"], ["Spec", "docs/..."]],
  "token_summary": [   # optional; written by summarize_tokens.py
    {"group": "investigation", "models": "Sonnet", "agents": 2,
     "tokens_in": 1000, "tokens_out": 100, "tokens": 1100, "pct": 40.0},
    {"group": "total (tracked)", "models": "", "agents": 5,
     "tokens_in": 2500, "tokens_out": 250, "tokens": 2750, "pct": 100.0}
  ],
  "model_summary": [   # optional; written by summarize_tokens.py
    {"model": "Sonnet", "agents": 3,
     "tokens_in": 2000, "tokens_out": 200, "tokens": 2200, "pct": 80.0},
    {"model": "total (tracked)", "agents": 5,
     "tokens_in": 2500, "tokens_out": 250, "tokens": 2750, "pct": 100.0}
  ]
}
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

CSS = """\
  :root{
    --bg:#f7f8f6; --panel:#ffffff; --ink:#1d2321; --muted:#5d6a64; --line:#dde3df;
    --accent:#0e6f5c; --run:#b7791f; --done:#0e6f5c; --wait:#5d6a64; --fail:#b3362c;
    --chip-run:#fdf3e0; --chip-done:#e3f1ec; --chip-wait:#eceeed; --chip-fail:#fbe7e4;
    font-size:16px;
  }
  @media (prefers-color-scheme: dark){:root{
    --bg:#151a18; --panel:#1d2421; --ink:#e8ece9; --muted:#94a29b; --line:#2c3531;
    --accent:#4cc2a7; --run:#e0a94e; --done:#4cc2a7; --wait:#94a29b; --fail:#e07a6e;
    --chip-run:#33290f; --chip-done:#12332b; --chip-wait:#252b28; --chip-fail:#3a1d19;
  }}
  :root[data-theme="light"]{
    --bg:#f7f8f6; --panel:#ffffff; --ink:#1d2321; --muted:#5d6a64; --line:#dde3df;
    --accent:#0e6f5c; --run:#b7791f; --done:#0e6f5c; --wait:#5d6a64; --fail:#b3362c;
    --chip-run:#fdf3e0; --chip-done:#e3f1ec; --chip-wait:#eceeed; --chip-fail:#fbe7e4;
  }
  :root[data-theme="dark"]{
    --bg:#151a18; --panel:#1d2421; --ink:#e8ece9; --muted:#94a29b; --line:#2c3531;
    --accent:#4cc2a7; --run:#e0a94e; --done:#4cc2a7; --wait:#94a29b; --fail:#e07a6e;
    --chip-run:#33290f; --chip-done:#12332b; --chip-wait:#252b28; --chip-fail:#3a1d19;
  }
  body{background:var(--bg);color:var(--ink);font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;padding:2rem 1.25rem 4rem}
  main{max-width:60rem;margin:0 auto;display:flex;flex-direction:column;gap:1.25rem}
  h1{font-size:1.35rem;margin:0;letter-spacing:-.01em;text-wrap:balance}
  .sub{color:var(--muted);margin:.25rem 0 0;font-size:.9rem}
  .stamp{color:var(--muted);font-size:.8rem;font-variant-numeric:tabular-nums}
  section{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:1rem 1.25rem}
  h2{font-size:.78rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:0 0 .75rem;font-weight:600}
  table{border-collapse:collapse;width:100%;font-size:.88rem}
  th{text-align:left;color:var(--muted);font-weight:600;font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;padding:.35rem .6rem;border-bottom:1px solid var(--line)}
  td{padding:.45rem .6rem;border-bottom:1px solid var(--line);vertical-align:top}
  tr:last-child td{border-bottom:none}
  .num{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
  .chip{display:inline-block;padding:.1rem .55rem;border-radius:999px;font-size:.74rem;font-weight:600;white-space:nowrap}
  .chip.run{background:var(--chip-run);color:var(--run)}
  .chip.done{background:var(--chip-done);color:var(--done)}
  .chip.wait{background:var(--chip-wait);color:var(--wait)}
  .chip.fail{background:var(--chip-fail);color:var(--fail)}
  .wrap{overflow-x:auto}
  .phases{display:flex;flex-wrap:wrap;gap:.5rem}
  .phase{border:1px solid var(--line);border-radius:6px;padding:.5rem .75rem;flex:1 1 10rem;min-width:9rem}
  .phase b{display:block;font-size:.82rem}
  .phase span{font-size:.75rem;color:var(--muted)}
  .note{color:var(--muted);font-size:.82rem;margin:.6rem 0 0}
  code{font:.85em ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--chip-wait);padding:.05rem .3rem;border-radius:4px}
"""

VALID_STATUSES = {"run", "done", "wait", "fail"}


def esc(value: object) -> str:
    return html.escape(str(value))


def chip(status: str, label: str) -> str:
    if status not in VALID_STATUSES:
        raise ValueError(f"unknown status {status!r}; must be one of {sorted(VALID_STATUSES)}")
    return f'<span class="chip {status}">{esc(label)}</span>'


def fmt_tokens(value: object) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    return esc(value if value is not None else "—")


def render(record: dict) -> str:
    phases = "\n".join(
        f'      <div class="phase"><b>{esc(p["name"])}</b><span>{esc(p["detail"])}</span>'
        f'<br>{chip(p["status"], p["label"])}</div>'
        for p in record.get("phases", [])
    )

    agent_rows = "\n".join(
        f'        <tr><td>{esc(a["name"])}</td><td>{esc(a["model"])}</td>'
        f'<td>{esc(a["role"])}</td><td>{chip(a["status"], a["label"])}</td>'
        f'<td class="num">{fmt_tokens(a.get("tokens_in"))}</td>'
        f'<td class="num">{fmt_tokens(a.get("tokens_out"))}</td>'
        f'<td class="num">{fmt_tokens(a.get("tokens"))}</td>'
        f'<td class="num">{esc(a.get("elapsed", "—"))}</td></tr>'
        for a in record.get("agents", [])
    )

    env_rows = "\n".join(
        f"        <tr><td>{esc(k)}</td><td><code>{esc(v)}</code></td></tr>"
        for k, v in record.get("environment", [])
    )

    def _opt(s: dict, key: str) -> str:
        value = s.get(key)
        return f"{int(value):,}" if isinstance(value, int) else "—"

    # Optional by-role breakdown written by summarize_tokens.py.
    summary_section = ""
    if record.get("token_summary"):
        srows = "\n".join(
            f'        <tr><td>{esc(s["group"])}</td><td>{esc(s["models"])}</td>'
            f'<td class="num">{esc(s["agents"])}</td>'
            f'<td class="num">{_opt(s, "tokens_in")}</td>'
            f'<td class="num">{_opt(s, "tokens_out")}</td>'
            f'<td class="num">{int(s["tokens"]):,}</td>'
            f'<td class="num">{esc(s["pct"])}%</td></tr>'
            for s in record["token_summary"]
        )
        summary_section = f"""
  <section>
    <h2>Token breakdown by role</h2>
    <div class="wrap">
    <table>
      <thead><tr><th>Role</th><th>Model(s)</th><th class="num">Agents</th><th class="num">In</th><th class="num">Out</th><th class="num">Total</th><th class="num">Share</th></tr></thead>
      <tbody>
{srows}
      </tbody>
    </table>
    </div>
  </section>
"""

    # Optional by-model breakdown written by summarize_tokens.py.
    model_summary_section = ""
    if record.get("model_summary"):
        mrows = "\n".join(
            f'        <tr><td>{esc(s["model"])}</td>'
            f'<td class="num">{esc(s["agents"])}</td>'
            f'<td class="num">{_opt(s, "tokens_in")}</td>'
            f'<td class="num">{_opt(s, "tokens_out")}</td>'
            f'<td class="num">{int(s["tokens"]):,}</td>'
            f'<td class="num">{esc(s["pct"])}%</td></tr>'
            for s in record["model_summary"]
        )
        model_summary_section = f"""
  <section>
    <h2>Token breakdown by model</h2>
    <div class="wrap">
    <table>
      <thead><tr><th>Model</th><th class="num">Agents</th><th class="num">In</th><th class="num">Out</th><th class="num">Total</th><th class="num">Share</th></tr></thead>
      <tbody>
{mrows}
      </tbody>
    </table>
    </div>
  </section>
"""

    total = sum(a["tokens"] for a in record.get("agents", []) if isinstance(a.get("tokens"), int))
    total_in = sum(a["tokens_in"] for a in record.get("agents", []) if isinstance(a.get("tokens_in"), int))
    total_out = sum(a["tokens_out"] for a in record.get("agents", []) if isinstance(a.get("tokens_out"), int))

    gate_note = record.get("gate_note", "")
    gate_html = f'    <p class="note">{esc(gate_note)}</p>\n' if gate_note else ""
    agents_note = record.get("agents_note", "")
    agents_note_html = f'    <p class="note">{esc(agents_note)}</p>\n' if agents_note else ""

    # The artifact is static; the orchestrator republishes it on every
    # milestone. This meta tag makes the viewer's browser re-pull the page
    # each minute so republished data appears without a manual reload.
    return f"""<title>{esc(record["title"])}</title>
<meta http-equiv="refresh" content="60">
<style>
{CSS}</style>
<main>
  <header>
    <h1>{esc(record["title"])}</h1>
    <p class="sub">{esc(record["subtitle"])}</p>
    <p class="stamp">Last updated {esc(record["updated"])} · {esc(record.get("phase_note", ""))}</p>
  </header>

  <section>
    <h2>Phases</h2>
    <div class="phases">
{phases}
    </div>
{gate_html}  </section>

  <section>
    <h2>Agents</h2>
    <div class="wrap">
    <table>
      <thead><tr><th>Agent</th><th>Model</th><th>Role / assignment</th><th>Status</th><th class="num">In</th><th class="num">Out</th><th class="num">Total</th><th class="num">Elapsed</th></tr></thead>
      <tbody>
{agent_rows}
      </tbody>
    </table>
    </div>
    <p class="note">Known token totals so far: in <b>{total_in:,}</b> · out <b>{total_out:,}</b> · total <b>{total:,}</b> (agents with numeric counts only; grok in/out splits are estimated).</p>
{agents_note_html}  </section>
{summary_section}{model_summary_section}
  <section>
    <h2>Environment</h2>
    <table>
      <tbody>
{env_rows}
      </tbody>
    </table>
  </section>
</main>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("record", type=Path, help="run-record JSON file")
    parser.add_argument("-o", "--out", type=Path, help="output HTML path (default: stdout)")
    args = parser.parse_args()

    record = json.loads(args.record.read_text())
    page = render(record)
    if args.out:
        args.out.write_text(page)
        print(f"wrote {args.out} ({len(page)} bytes)")
    else:
        print(page)


if __name__ == "__main__":
    main()

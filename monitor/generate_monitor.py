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
  "token_summary": [   # optional; written by summarize_tokens.py from token_log.
                       # Renders the "by role" donuts + breakdown table.
                       # pct=share of total; out_pct=share of output (generated);
                       # uncached=tokens-cache_read (Claude exact) or ≈tokens
                       # (grok estimate); uncached_pct=share of uncached;
                       # uncached_estimated=true for grok conversation units.
    {"group": "investigation", "models": "Sonnet", "agents": 2,
     "tokens_in": 1000, "tokens_out": 100, "tokens": 1100, "cache_read": 800,
     "uncached": 300, "pct": 40.0, "out_pct": 40.0, "uncached_pct": 42.0},
    {"group": "total (tracked)", "models": "", "agents": 5,
     "tokens_in": 2500, "tokens_out": 250, "tokens": 2750, "cache_read": 2000,
     "uncached": 750, "pct": 100.0, "out_pct": 100.0, "uncached_pct": 100.0}
  ],
  "model_summary": [   # optional; written by summarize_tokens.py.
                       # Renders the "by model" donuts + breakdown table.
    {"model": "Sonnet", "agents": 3,
     "tokens_in": 2000, "tokens_out": 200, "tokens": 2200, "cache_read": 1800,
     "uncached": 400, "pct": 80.0, "out_pct": 80.0, "uncached_pct": 53.0},
    {"model": "total (tracked)", "agents": 5,
     "tokens_in": 2500, "tokens_out": 250, "tokens": 2750, "cache_read": 2000,
     "uncached": 750, "pct": 100.0, "out_pct": 100.0, "uncached_pct": 100.0}
  ],
  # Per-agent cache_read (Claude cache_read_input_tokens; grok 0 for the gauge,
  # cachedReadTokens for a ledger) is recorded by aggregate_tokens.py on each
  # agents/token_log entry, so the uncached views regenerate with no backfill.
  #
  # units (optional per agents/token_log row): "billed" (Claude) or
  # "conversation" (grok — totalTokens is conversation size, NOT per-call
  # billed input; not comparable to Claude total columns). Uncached for grok
  # is estimated as the conversation total (lower bound; assumes prefix
  # caching) and appears in uncached donuts/bars marked ~…(est.). Old records
  # without units fall back to model-name heuristics (name/model containing
  # "grok" → conversation). Prefer update_run_record.py over hand-typing.
}
"""

from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path

GROK_UNITS_FOOTNOTE = (
    "† grok ledger counts conversation size, not billed tokens; "
    "not comparable to Claude columns"
)

CSS = """\
  :root{
    --bg:#f7f8f6; --panel:#ffffff; --ink:#1d2321; --muted:#5d6a64; --line:#dde3df;
    --accent:#0e6f5c; --run:#b7791f; --done:#0e6f5c; --wait:#5d6a64; --fail:#b3362c;
    --chip-run:#fdf3e0; --chip-done:#e3f1ec; --chip-wait:#eceeed; --chip-fail:#fbe7e4;
    --s1:#2a78d6; --s2:#1baf7a; --s3:#eda100; --s4:#008300; --s5:#4a3aa7; --s6:#e34948; --s7:#e87ba4; --s8:#eb6834;
    font-size:16px;
  }
  @media (prefers-color-scheme: dark){:root{
    --bg:#151a18; --panel:#1d2421; --ink:#e8ece9; --muted:#94a29b; --line:#2c3531;
    --accent:#4cc2a7; --run:#e0a94e; --done:#4cc2a7; --wait:#94a29b; --fail:#e07a6e;
    --chip-run:#33290f; --chip-done:#12332b; --chip-wait:#252b28; --chip-fail:#3a1d19;
    --s1:#3987e5; --s2:#199e70; --s3:#c98500; --s4:#008300; --s5:#9085e9; --s6:#e66767; --s7:#d55181; --s8:#d95926;
  }}
  :root[data-theme="light"]{
    --bg:#f7f8f6; --panel:#ffffff; --ink:#1d2321; --muted:#5d6a64; --line:#dde3df;
    --accent:#0e6f5c; --run:#b7791f; --done:#0e6f5c; --wait:#5d6a64; --fail:#b3362c;
    --chip-run:#fdf3e0; --chip-done:#e3f1ec; --chip-wait:#eceeed; --chip-fail:#fbe7e4;
    --s1:#2a78d6; --s2:#1baf7a; --s3:#eda100; --s4:#008300; --s5:#4a3aa7; --s6:#e34948; --s7:#e87ba4; --s8:#eb6834;
  }
  :root[data-theme="dark"]{
    --bg:#151a18; --panel:#1d2421; --ink:#e8ece9; --muted:#94a29b; --line:#2c3531;
    --accent:#4cc2a7; --run:#e0a94e; --done:#4cc2a7; --wait:#94a29b; --fail:#e07a6e;
    --chip-run:#33290f; --chip-done:#12332b; --chip-wait:#252b28; --chip-fail:#3a1d19;
    --s1:#3987e5; --s2:#199e70; --s3:#c98500; --s4:#008300; --s5:#9085e9; --s6:#e66767; --s7:#d55181; --s8:#d95926;
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
  .pie-group{font-size:.82rem;font-weight:600;color:var(--ink);margin:1.25rem 0 .7rem}
  .pie-group:first-of-type{margin-top:.1rem}
  .pies{display:flex;flex-wrap:wrap;gap:1.5rem 2rem}
  .pie{flex:1 1 20rem;min-width:15rem;margin:0}
  .pie figcaption{font-size:.8rem;font-weight:600;color:var(--ink);margin:0 0 .7rem}
  .pie-body{display:flex;align-items:center;gap:1rem;flex-wrap:wrap}
  .donut{flex:0 0 auto}
  .legend{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:.35rem;font-size:.82rem;flex:1 1 9rem;min-width:0}
  .legend li{display:flex;align-items:center;gap:.5rem}
  .legend .sw{width:.72rem;height:.72rem;border-radius:3px;flex:0 0 auto}
  .legend .lab{flex:1 1 auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .legend .val{color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
  .comp{display:flex;flex-direction:column;gap:.5rem}
  .comp-row{display:flex;align-items:center;gap:.7rem;font-size:.82rem}
  .comp-lab{flex:0 0 8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .comp-track{flex:1 1 auto;min-width:0}
  .comp-bar{display:flex;height:.9rem;border-radius:3px;overflow:hidden;min-width:3px}
  .comp-seg{height:100%}
  .comp-val{flex:0 0 auto;color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap;font-size:.78rem}
  .comp-legend{display:flex;flex-wrap:wrap;gap:1rem;font-size:.76rem;color:var(--muted);margin:.75rem 0 .1rem}
  .comp-legend span{display:inline-flex;align-items:center;gap:.35rem}
  .comp-legend i{width:.72rem;height:.72rem;border-radius:2px;display:inline-block}
  .seg-cache{background:repeating-linear-gradient(45deg,var(--muted) 0 2px,transparent 2px 5px),var(--chip-wait)}
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


def row_units(row: dict) -> str:
    """Return 'billed' or 'conversation' for an agents/token_log/summary row."""
    units = row.get("units")
    if units in ("billed", "conversation"):
        return units
    model = str(row.get("model") or row.get("models") or "").lower()
    name = str(row.get("name") or row.get("agent") or row.get("group") or "").lower()
    if "grok" in model or name.startswith("grok"):
        return "conversation"
    return "billed"


def is_conversation_row(row: dict) -> bool:
    return row_units(row) == "conversation"


def has_conversation_agents(record: dict) -> bool:
    return any(is_conversation_row(a) for a in record.get("agents", []))


def fmt_compact(n: int) -> str:
    """Short token count for the small donut center/legend (1_234 -> '1.2k')."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return f"{n / 1_000_000:.2f}M".replace(".00M", "M")


def row_uncached_estimated(row: dict) -> bool:
    """True when uncached is a grok/conversation estimate (or flagged as such)."""
    if row.get("uncached_estimated"):
        return True
    # Fallback for records that have conversation units but predate the flag.
    return is_conversation_row(row)


def pie_slices(rows: list[dict], label_key: str, value_key: str = "tokens") -> list[dict]:
    """Extract drawable slices from a token_summary/model_summary list.

    `value_key` selects the slice measure — "tokens" (total), "tokens_out"
    (generated, cache-neutral), or "uncached". Skips the appended
    "total (tracked)" row and any zero/non-numeric value. Conversation-unit
    (grok) rows are included when they carry a numeric uncached estimate.
    Keeps record order (stable colour<->entity mapping); if there are more
    than 8 groups, the smallest fold into a single grey "other" slot so a
    9th hue is never invented (a data-viz non-negotiable).
    """
    items = []
    for r in rows:
        if not isinstance(r.get(value_key), int) or r[value_key] <= 0:
            continue
        if str(r.get(label_key, "")).strip().lower().startswith("total"):
            continue
        item = {
            "label": str(r.get(label_key, "?")),
            "tokens": r[value_key],
        }
        if value_key == "uncached" and row_uncached_estimated(r):
            item["estimated"] = True
        items.append(item)
    if len(items) > 8:
        keep = set(sorted(range(len(items)), key=lambda i: items[i]["tokens"],
                          reverse=True)[:7])
        folded = sum(it["tokens"] for i, it in enumerate(items) if i not in keep)
        any_est = any(items[i].get("estimated") for i in range(len(items))
                      if i not in keep)
        items = [it for i, it in enumerate(items) if i in keep]
        other = {"label": "other", "tokens": folded, "other": True}
        if any_est:
            other["estimated"] = True
        items.append(other)
    total = sum(it["tokens"] for it in items) or 1
    for i, it in enumerate(items):
        it["slot"] = "muted" if it.get("other") else str((i % 8) + 1)
        it["pct"] = 100.0 * it["tokens"] / total
    return items


def donut(items: list[dict], caption: str, size: int = 168, thick: int = 26,
          center_label: str = "total tokens") -> str:
    """Inline-SVG donut: one dash-arc <circle> per slice, transparent 2px
    gaps that reveal the panel surface, total in the hole. Colours are CSS
    vars (--s1..--s8 / --muted) so they swap with the light/dark theme."""
    total = sum(it["tokens"] for it in items) or 1
    r = (size - thick) / 2
    center = size / 2
    circ = 2 * math.pi * r
    gap = 200.0 / circ if len(items) > 1 else 0.0  # ~2px expressed on pathLength=100
    arcs, cum = [], 0.0
    for it in items:
        seg = 100.0 * it["tokens"] / total
        dash = max(seg - gap, 0.5)
        var = "--muted" if it["slot"] == "muted" else f"--s{it['slot']}"
        arcs.append(
            f'<circle cx="{center}" cy="{center}" r="{r:.2f}" fill="none" '
            f'pathLength="100" stroke-width="{thick}" style="stroke:var({var})" '
            f'stroke-dasharray="{dash:.3f} {100 - dash:.3f}" '
            f'stroke-dashoffset="{-cum:.3f}">'
            f'<title>{esc(it["label"])} — {it["tokens"]:,} tokens '
            f'({it["pct"]:.1f}%)</title></circle>'
        )
        cum += seg
    return (
        f'<svg class="donut" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}" role="img" '
        f'aria-label="Donut chart of {esc(caption)}">'
        f'<g transform="rotate(-90 {center} {center})">{"".join(arcs)}</g>'
        f'<text x="{center}" y="{center}" text-anchor="middle" '
        f'dominant-baseline="central" '
        f'style="fill:var(--ink);font:600 1.15rem system-ui,sans-serif">'
        f'{esc(fmt_compact(total))}</text>'
        f'<text x="{center}" y="{center + 18}" text-anchor="middle" '
        f'style="fill:var(--muted);font:.66rem system-ui,sans-serif;'
        f'letter-spacing:.04em">{esc(center_label)}</text></svg>'
    )


def fmt_legend_val(it: dict) -> str:
    """Compact legend value; estimated slices use ~…(est.)."""
    base = f'{fmt_compact(it["tokens"])} · {it["pct"]:.0f}%'
    if it.get("estimated"):
        return f"~{base} (est.)"
    return base


def legend(items: list[dict]) -> str:
    """Legend for drawable donut slices (estimated grok values marked ~…(est.))."""
    lis = [
        f'      <li><span class="sw" style="background:var('
        f'{"--muted" if it["slot"] == "muted" else f"--s{it["slot"]}"})"></span>'
        f'<span class="lab">{esc(it["label"])}</span>'
        f'<span class="val">{esc(fmt_legend_val(it))}</span></li>'
        for it in items
    ]
    if not lis:
        return '    <ul class="legend"></ul>'
    return f'    <ul class="legend">\n' + "\n".join(lis) + "\n    </ul>"


def pie_figure(items: list[dict], caption: str,
               center_label: str = "total tokens") -> str:
    return (
        f'    <figure class="pie">\n'
        f'      <figcaption>{esc(caption)}</figcaption>\n'
        f'      <div class="pie-body">\n'
        f'      {donut(items, caption, center_label=center_label)}\n'
        f'{legend(items)}\n'
        f'      </div>\n'
        f'    </figure>'
    )


def comp_bars(rows: list[dict], label_key: str) -> str:
    """Stacked composition bars: each summary row's total broken into input
    (processed once) + output + cache-read (re-reads, hatched). Bar length is
    total, on one shared scale, so the cache-read chunk is visible against the
    solid 'uncached' part. Conversation-unit (grok) rows appear as normal
    bars; their uncached value is marked ~…(est.)."""
    data = []
    for r in rows:
        if not isinstance(r.get("tokens"), int) or r["tokens"] <= 0:
            continue
        if str(r.get(label_key, "")).strip().lower().startswith("total"):
            continue
        data.append(r)
    if not data:
        return ""
    rows_html = []
    maxtot = max(r["tokens"] for r in data) or 1
    for r in data:
        tot = r["tokens"]
        cr = int(r.get("cache_read") or 0)
        out = int(r.get("tokens_out") or 0)
        new_in = max(tot - cr - out, 0)
        estimated = row_uncached_estimated(r)
        # Prefer summary uncached when present (includes grok estimate = tokens).
        if isinstance(r.get("uncached"), int):
            unc = r["uncached"]
        else:
            unc = tot - cr

        def seg(width: int, style: str, cls: str, label: str) -> str:
            if width <= 0:
                return ""
            return (f'<span class="comp-seg {cls}" style="width:{100.0 * width / tot:.3f}%;'
                    f'{style}" title="{esc(label)}: {width:,}"></span>')

        segs = (seg(new_in, "background:var(--s1)", "", "input (processed once)")
                + seg(out, "background:var(--s2)", "", "output")
                + seg(cr, "", "seg-cache", "cache-read (re-reads)"))
        unc_txt = f"~{fmt_compact(unc)} (est.)" if estimated else fmt_compact(unc)
        rows_html.append(
            f'      <div class="comp-row"><span class="comp-lab">{esc(r[label_key])}</span>'
            f'<span class="comp-track"><span class="comp-bar" style="width:{100.0 * tot / maxtot:.2f}%">'
            f'{segs}</span></span>'
            f'<span class="comp-val">{esc(fmt_compact(tot))} · uncached '
            f'{esc(unc_txt)}</span></div>'
        )
    legend_html = (
        '      <div class="comp-legend">'
        '<span><i style="background:var(--s1)"></i>input (once)</span>'
        '<span><i style="background:var(--s2)"></i>output</span>'
        '<span><i class="seg-cache"></i>cache-read (re-reads)</span>'
        '<span>solid = uncached (Claude: total &minus; cache-read; '
        'grok: ~conversation total (est.))</span>'
        '</div>'
    )
    return "\n".join(rows_html) + "\n" + legend_html


def render(record: dict) -> str:
    phases = "\n".join(
        f'      <div class="phase"><b>{esc(p["name"])}</b><span>{esc(p["detail"])}</span>'
        f'<br>{chip(p["status"], p["label"])}</div>'
        for p in record.get("phases", [])
    )

    show_grok_dagger = has_conversation_agents(record)

    def _agent_name_cell(a: dict) -> str:
        name = esc(a["name"])
        if show_grok_dagger and is_conversation_row(a):
            return f"{name}†"
        return name

    agent_rows = "\n".join(
        f'        <tr><td>{_agent_name_cell(a)}</td><td>{esc(a["model"])}</td>'
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
        if value is None:
            return "n/a" if key in ("uncached",) else "—"
        if not isinstance(value, int):
            return "—"
        text = f"{value:,}"
        if key == "uncached" and s.get("uncached_estimated"):
            return f"~{text}"
        return text

    def _share(s: dict, key: str) -> str:
        value = s.get(key)
        if value is None:
            return "n/a"
        text = f"{value}%"
        if key == "uncached_pct" and s.get("uncached_estimated"):
            return f"~{text}"
        return text

    # Cache-neutral note shared by the two breakdown tables.
    share_note = (
        '<p class="note">Share = % of all tracked tokens (mixed units). '
        'Gen.&nbsp;share = % of output (generated) tokens. '
        'Uncached for Claude is exact (total &minus; cache-read); for grok it is '
        '<b>estimated</b> as unique conversation tokens (assumes prefix caching — '
        'actual billed uncached ≥ this), marked with ~.</p>'
    )

    # Optional by-role breakdown written by summarize_tokens.py.
    summary_section = ""
    if record.get("token_summary"):
        srows = "\n".join(
            f'        <tr><td>{esc(s["group"])}</td><td>{esc(s["models"])}</td>'
            f'<td class="num">{esc(s["agents"])}</td>'
            f'<td class="num">{_opt(s, "tokens_in")}</td>'
            f'<td class="num">{_opt(s, "tokens_out")}</td>'
            f'<td class="num">{int(s["tokens"]):,}</td>'
            f'<td class="num">{esc(s["pct"])}%</td>'
            f'<td class="num">{_share(s, "out_pct")}</td>'
            f'<td class="num">{_opt(s, "uncached")}</td></tr>'
            for s in record["token_summary"]
        )
        summary_section = f"""
  <section>
    <h2>Token breakdown by role</h2>
    <div class="wrap">
    <table>
      <thead><tr><th>Role</th><th>Model(s)</th><th class="num">Agents</th><th class="num">In</th><th class="num">Out</th><th class="num">Total</th><th class="num">Share</th><th class="num">Gen.&nbsp;share</th><th class="num">Uncached&nbsp;(&minus;cache)</th></tr></thead>
      <tbody>
{srows}
      </tbody>
    </table>
    </div>
    {share_note}
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
            f'<td class="num">{esc(s["pct"])}%</td>'
            f'<td class="num">{_share(s, "out_pct")}</td>'
            f'<td class="num">{_opt(s, "uncached")}</td></tr>'
            for s in record["model_summary"]
        )
        model_summary_section = f"""
  <section>
    <h2>Token breakdown by model</h2>
    <div class="wrap">
    <table>
      <thead><tr><th>Model</th><th class="num">Agents</th><th class="num">In</th><th class="num">Out</th><th class="num">Total</th><th class="num">Share</th><th class="num">Gen.&nbsp;share</th><th class="num">Uncached&nbsp;(&minus;cache)</th></tr></thead>
      <tbody>
{mrows}
      </tbody>
    </table>
    </div>
    {share_note}
  </section>
"""

    # Donut charts by role and by model, on three measures: total tokens,
    # generated (output) tokens, and uncached (Claude exact + grok estimate).
    def _pies(value_key: str, center_label: str) -> list[str]:
        figs = []
        if record.get("token_summary"):
            items = pie_slices(record["token_summary"], "group", value_key)
            if items:
                figs.append(pie_figure(items, "By role", center_label))
        if record.get("model_summary"):
            items = pie_slices(record["model_summary"], "model", value_key)
            if items:
                figs.append(pie_figure(items, "By model", center_label))
        return figs

    total_figs = _pies("tokens", "total tokens")
    gen_figs = _pies("tokens_out", "output tokens")
    uncached_figs = _pies("uncached", "uncached tokens")
    pie_section = ""
    if total_figs or gen_figs or uncached_figs:
        blocks = ""
        if total_figs:
            blocks += f"""    <h3 class="pie-group">Total tokens</h3>
    <div class="pies">
{chr(10).join(total_figs)}
    </div>
    <p class="note">Slices are <b>total tokens</b>. Claude totals are billed (per-call, incl. cache-reads); grok totals are conversation size — not the same unit. Cache-neutral views below.</p>
"""
        if gen_figs:
            blocks += f"""    <h3 class="pie-group">Generated tokens · cache-neutral (Gen.&nbsp;share)</h3>
    <div class="pies">
{chr(10).join(gen_figs)}
    </div>
    <p class="note">Slices are <b>output (generated) tokens</b> — neither provider inflates output, so this is the fair share of work. Matches the <b>Gen.&nbsp;share</b> column below.</p>
"""
        if uncached_figs:
            blocks += f"""    <h3 class="pie-group">Uncached tokens · Claude exact + grok estimate</h3>
    <div class="pies">
{chr(10).join(uncached_figs)}
    </div>
    <p class="note">Slices are <b>uncached tokens</b>. Claude: total &minus; cache-reads (exact). Grok: estimated as unique conversation tokens (assumes prefix caching on re-read context — actual billed uncached ≥ this); legend values marked <b>~…(est.)</b>.</p>
"""
        pie_section = f"""
  <section>
    <h2>Token distribution</h2>
{blocks}  </section>
"""

    # Composition bars: all models/roles including grok (estimated uncached).
    cblocks = ""
    if record.get("model_summary"):
        bars = comp_bars(record["model_summary"], "model")
        if bars:
            cblocks += f'    <h3 class="pie-group">By model</h3>\n    <div class="comp">\n{bars}\n    </div>\n'
    if record.get("token_summary"):
        bars = comp_bars(record["token_summary"], "group")
        if bars:
            cblocks += f'    <h3 class="pie-group">By role</h3>\n    <div class="comp">\n{bars}\n    </div>\n'
    comp_section = ""
    if cblocks:
        comp_section = f"""
  <section>
    <h2>Token composition · uncached vs cache-reads</h2>
    <p class="note">Bar length = total tokens (all bars share one scale). The hatched part is cache-read re-reads; the solid part is <b>uncached</b>. Claude uncached = total &minus; cache-read (exact). Grok uncached ≈ conversation total (estimated; assumes prefix caching — actual billed uncached ≥ this), marked ~…(est.).</p>
{cblocks}  </section>
"""

    total = sum(a["tokens"] for a in record.get("agents", []) if isinstance(a.get("tokens"), int))
    total_in = sum(a["tokens_in"] for a in record.get("agents", []) if isinstance(a.get("tokens_in"), int))
    total_out = sum(a["tokens_out"] for a in record.get("agents", []) if isinstance(a.get("tokens_out"), int))

    gate_note = record.get("gate_note", "")
    gate_html = f'    <p class="note">{esc(gate_note)}</p>\n' if gate_note else ""
    agents_note = record.get("agents_note", "")
    agents_note_parts = []
    if show_grok_dagger:
        agents_note_parts.append(GROK_UNITS_FOOTNOTE)
    if agents_note:
        agents_note_parts.append(agents_note)
    agents_note_html = (
        f'    <p class="note">{esc(" ".join(agents_note_parts))}</p>\n'
        if agents_note_parts else ""
    )

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
{pie_section}{comp_section}{summary_section}{model_summary_section}
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

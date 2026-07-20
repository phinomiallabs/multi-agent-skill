"""Seam 2 — run-record dict -> HTML string (generate_monitor.render).

Assert external, user-visible behaviour of the single render entry point:
the consolidated report structure for a fully-billed run, and the honest
fallback rendering when a legacy conversation-unit row is present.

conftest.py puts monitor/ on sys.path, so `import generate_monitor` works.
"""
import generate_monitor


# --------------------------------------------------------------------------- #
# Synthetic run-records
# --------------------------------------------------------------------------- #
def _billed_record() -> dict:
    """Claude + grok + cursor, every row billed & exact, grok cache_read > 0.

    Token identity used throughout: uncached + cache-read + output == total,
    and tokens_in == uncached + cache-read (billed input re-counts cache-reads).
    """
    return {
        "title": "Billed run — three providers",
        "subtitle": "Advisor: Sonnet · Implementer: grok · Assist: cursor",
        "updated": "2026-07-19 10:00 EDT",
        "phase_note": "all providers billed",
        "phases": [
            {"name": "1 · Build", "detail": "implement", "status": "done",
             "label": "done"},
        ],
        "agents": [
            {"name": "investigator-1", "model": "Sonnet 5", "role": "map",
             "status": "done", "label": "done", "source": "direct:aaa",
             "tokens_in": 100000, "tokens_out": 8000, "tokens": 108000,
             "cache_read": 80000, "units": "billed", "exact": True},
            {"name": "grok-worker-1", "model": "grok-4.5", "role": "implement",
             "status": "done", "label": "done", "source": "grok:bbb",
             "tokens_in": 50000, "tokens_out": 10000, "tokens": 60000,
             "cache_read": 30000, "units": "billed", "exact": True,
             "cost_usd": 0.42},
            {"name": "cursor-worker-1", "model": "cursor", "role": "assist",
             "status": "done", "label": "done", "source": "cursor:ccc",
             "tokens_in": 40000, "tokens_out": 5000, "tokens": 45000,
             "cache_read": 25000, "units": "billed", "exact": True,
             "cost_usd": 0.11},
        ],
        # by role
        "token_summary": [
            {"group": "investigation", "models": "Sonnet 5", "agents": 1,
             "tokens_in": 100000, "tokens_out": 8000, "tokens": 108000,
             "cache_read": 80000, "uncached": 20000, "pct": 50.7,
             "out_pct": 34.8, "uncached_pct": 36.4,
             "units": "billed", "exact": True},
            {"group": "implementation", "models": "grok-4.5", "agents": 1,
             "tokens_in": 50000, "tokens_out": 10000, "tokens": 60000,
             "cache_read": 30000, "uncached": 20000, "pct": 28.2,
             "out_pct": 43.5, "uncached_pct": 36.4,
             "units": "billed", "exact": True},
            {"group": "assist", "models": "cursor", "agents": 1,
             "tokens_in": 40000, "tokens_out": 5000, "tokens": 45000,
             "cache_read": 25000, "uncached": 15000, "pct": 21.1,
             "out_pct": 21.7, "uncached_pct": 27.3,
             "units": "billed", "exact": True},
            {"group": "total (tracked)", "models": "", "agents": 3,
             "tokens_in": 190000, "tokens_out": 23000, "tokens": 213000,
             "cache_read": 135000, "uncached": 55000, "pct": 100.0,
             "out_pct": 100.0, "uncached_pct": 100.0,
             "units": "billed", "exact": True},
        ],
        # by model
        "model_summary": [
            {"model": "Sonnet 5", "agents": 1,
             "tokens_in": 100000, "tokens_out": 8000, "tokens": 108000,
             "cache_read": 80000, "uncached": 20000, "pct": 50.7,
             "out_pct": 34.8, "uncached_pct": 36.4,
             "units": "billed", "exact": True},
            {"model": "grok-4.5", "agents": 1,
             "tokens_in": 50000, "tokens_out": 10000, "tokens": 60000,
             "cache_read": 30000, "uncached": 20000, "pct": 28.2,
             "out_pct": 43.5, "uncached_pct": 36.4,
             "units": "billed", "exact": True},
            {"model": "cursor", "agents": 1,
             "tokens_in": 40000, "tokens_out": 5000, "tokens": 45000,
             "cache_read": 25000, "uncached": 15000, "pct": 21.1,
             "out_pct": 21.7, "uncached_pct": 27.3,
             "units": "billed", "exact": True},
            {"model": "total (tracked)", "agents": 3,
             "tokens_in": 190000, "tokens_out": 23000, "tokens": 213000,
             "cache_read": 135000, "uncached": 55000, "pct": 100.0,
             "out_pct": 100.0, "uncached_pct": 100.0,
             "units": "billed", "exact": True},
        ],
        "environment": [["Repo", "/x @ sha"]],
    }


def _fallback_record() -> dict:
    """Contains a legacy grok row in conversation units (exact=False)."""
    return {
        "title": "Legacy run — grok conversation units",
        "subtitle": "Implementer: grok (legacy)",
        "updated": "2026-07-01 10:00 EDT",
        "phase_note": "legacy",
        "phases": [
            {"name": "1 · Build", "detail": "implement", "status": "done",
             "label": "done"},
        ],
        "agents": [
            {"name": "investigator-1", "model": "Sonnet 5", "role": "map",
             "status": "done", "label": "done",
             "tokens_in": 100000, "tokens_out": 8000, "tokens": 108000,
             "cache_read": 80000, "units": "billed", "exact": True},
            {"name": "grok:019f", "model": "grok-4.5", "role": "implement",
             "status": "done", "label": "auto",
             "tokens_in": 80000, "tokens_out": 17000, "tokens": 97000,
             "cache_read": 0, "units": "conversation", "exact": False},
        ],
        "token_summary": [
            {"group": "investigation", "models": "Sonnet 5", "agents": 1,
             "tokens_in": 100000, "tokens_out": 8000, "tokens": 108000,
             "cache_read": 80000, "uncached": 20000, "pct": 52.7,
             "out_pct": 32.0, "uncached_pct": 20.0,
             "units": "billed", "exact": True},
            {"group": "grok", "models": "grok-4.5", "agents": 1,
             "tokens_in": 80000, "tokens_out": 17000, "tokens": 97000,
             "cache_read": 0, "uncached": 97000, "pct": 47.3,
             "out_pct": 68.0, "uncached_pct": 80.0,
             "units": "conversation", "exact": False,
             "uncached_estimated": True},
        ],
        "model_summary": [
            {"model": "Sonnet 5", "agents": 1,
             "tokens_in": 100000, "tokens_out": 8000, "tokens": 108000,
             "cache_read": 80000, "uncached": 20000, "pct": 52.7,
             "out_pct": 32.0, "uncached_pct": 20.0,
             "units": "billed", "exact": True},
            {"model": "grok-4.5", "agents": 1,
             "tokens_in": 80000, "tokens_out": 17000, "tokens": 97000,
             "cache_read": 0, "uncached": 97000, "pct": 47.3,
             "out_pct": 68.0, "uncached_pct": 80.0,
             "units": "conversation", "exact": False,
             "uncached_estimated": True},
        ],
        "environment": [["Repo", "/x @ sha"]],
    }


# --------------------------------------------------------------------------- #
# Billed record
# --------------------------------------------------------------------------- #
def test_billed_render_is_a_string_with_title():
    html = generate_monitor.render(_billed_record())
    assert isinstance(html, str)
    assert "Billed run — three providers" in html


def test_billed_has_exactly_two_model_share_donuts():
    html = generate_monitor.render(_billed_record())
    # Two <svg class="donut">: total tokens by model, and output share by model.
    assert html.count('class="donut"') == 2
    assert "Total tokens · by model" in html
    assert "Output share · by model" in html


def test_billed_has_no_total_or_uncached_donut_groups():
    html = generate_monitor.render(_billed_record())
    # The retired donut-group headers must be gone.
    assert 'pie-group">Total tokens' not in html
    assert 'pie-group">Uncached' not in html
    # A single output-share donut, by model, remains.
    assert "Output share" in html


def test_billed_composition_bars_present_both_facets():
    html = generate_monitor.render(_billed_record())
    assert "Token composition" in html
    assert 'pie-group">By model' in html
    assert 'pie-group">By role' in html


def test_billed_grok_bar_has_cache_read_segment():
    html = generate_monitor.render(_billed_record())
    # grok's billed row (cache_read = 30000) renders a hatched cache-read segment.
    assert "seg-cache" in html
    assert 'title="cache-read (re-reads): 30,000"' in html


def test_billed_tables_use_new_columns_and_drop_In():
    html = generate_monitor.render(_billed_record())
    assert '<th class="num">Uncached</th>' in html
    assert '<th class="num">Cache-read</th>' in html
    assert '<th class="num">Output</th>' in html
    # Both breakdown tables present.
    assert "Token breakdown by role" in html
    assert "Token breakdown by model" in html
    # The ambiguous "In" column must be gone from the breakdown tables.
    breakdown = html[html.index("Token breakdown by role"):]
    assert '<th class="num">In</th>' not in breakdown
    # Uncached + Cache-read + Output sum to Total (grok row: 20,000/30,000/10,000).
    assert '<td class="num">20,000</td><td class="num">30,000</td>' \
           '<td class="num">10,000</td><td class="num">60,000</td>' in html


def test_billed_has_no_estimate_caveats():
    html = generate_monitor.render(_billed_record())
    assert "(est.)" not in html
    assert "†" not in html
    assert "conversation size" not in html
    assert "conversation" not in html  # no conversation-unit language at all
    assert "estimated" not in html


# --------------------------------------------------------------------------- #
# Fallback record
# --------------------------------------------------------------------------- #
def test_fallback_shows_estimated_indicator():
    html = generate_monitor.render(_fallback_record())
    assert "estimated" in html


def test_fallback_shows_dagger_and_est_marks():
    html = generate_monitor.render(_fallback_record())
    # Legacy conversation row keeps its honest caveats.
    assert "†" in html
    assert "(est.)" in html

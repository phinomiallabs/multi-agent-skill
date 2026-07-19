"""Seam 3 — token_log -> summary rows (summarize_tokens.summarize /
summarize_by_model), plus a render regression guard.

The report's estimate caveats ("(est.)" text and "†" daggers) are driven by
render's `show_estimated`, which reads each summary row's units. Hand-built
records in test_render.py stamped `units` themselves, but the REAL summarize
step did not — so a fully-billed grok row fell through render's name-based
fallback ("grok" in model -> conversation), was misread as estimated, and
printed spurious caveats. These tests pin the contract at the summarize seam:
every emitted group/model row carries explicit `units` and `uncached_estimated`.

conftest.py puts monitor/ on sys.path, so the bare imports below work.
"""
import generate_monitor
import summarize_tokens


def _apply_summaries(record: dict) -> dict:
    """Replicate summarize_tokens.main's write-back into the record dict: the
    exact by-role / by-model summaries generate_monitor.render then consumes."""
    record["token_summary"] = summarize_tokens.summarize(record)
    record["model_summary"] = summarize_tokens.summarize_by_model(record)
    return record


def _is_total(row: dict) -> bool:
    label = row.get("group", row.get("model", ""))
    return str(label).strip().lower().startswith("total")


# --------------------------------------------------------------------------- #
# Synthetic run-records (token_log is the source of truth for summaries)
# --------------------------------------------------------------------------- #
def _billed_record() -> dict:
    """Three billed token_log rows: Fable 5 advisor, grok-4.5 implementer
    (cache_read > 0 + cost_usd), Sonnet 5 investigator. All units='billed'."""
    return {
        "title": "Billed run — Fable + grok + Sonnet",
        "subtitle": "Advisor: Fable 5 · Implementer: grok-4.5 · Investigator: Sonnet 5",
        "updated": "2026-07-19 12:00 EDT",
        "phase_note": "all providers billed",
        "phases": [
            {"name": "1 · Build", "detail": "implement", "status": "done",
             "label": "done"},
        ],
        "agents": [
            {"name": "advisor-1", "model": "Fable 5", "role": "advise",
             "status": "done", "label": "done",
             "tokens_in": 30000, "tokens_out": 3000, "tokens": 33000,
             "cache_read": 20000, "units": "billed", "exact": True},
            {"name": "grok-worker-1", "model": "grok-4.5", "role": "implement",
             "status": "done", "label": "done",
             "tokens_in": 50000, "tokens_out": 10000, "tokens": 60000,
             "cache_read": 30000, "units": "billed", "exact": True,
             "cost_usd": 0.42},
            {"name": "investigator-1", "model": "Sonnet 5", "role": "map",
             "status": "done", "label": "done",
             "tokens_in": 100000, "tokens_out": 8000, "tokens": 108000,
             "cache_read": 80000, "units": "billed", "exact": True},
        ],
        "token_log": [
            {"group": "advisory", "agent": "advisor-1", "model": "Fable 5",
             "tokens_in": 30000, "tokens_out": 3000, "tokens": 33000,
             "cache_read": 20000, "units": "billed", "exact": True},
            {"group": "implementation", "agent": "grok-worker-1",
             "model": "grok-4.5",
             "tokens_in": 50000, "tokens_out": 10000, "tokens": 60000,
             "cache_read": 30000, "units": "billed", "exact": True,
             "cost_usd": 0.42},
            {"group": "investigation", "agent": "investigator-1",
             "model": "Sonnet 5",
             "tokens_in": 100000, "tokens_out": 8000, "tokens": 108000,
             "cache_read": 80000, "units": "billed", "exact": True},
        ],
        "environment": [["Repo", "/x @ sha"]],
    }


def _mixed_record() -> dict:
    """A billed Sonnet row plus a legacy grok row in conversation units."""
    return {
        "title": "Mixed run — billed Sonnet + legacy grok",
        "subtitle": "Investigator: Sonnet 5 · Implementer: grok (legacy)",
        "updated": "2026-07-01 10:00 EDT",
        "phase_note": "one legacy conversation-unit row",
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
        "token_log": [
            {"group": "investigation", "agent": "investigator-1",
             "model": "Sonnet 5",
             "tokens_in": 100000, "tokens_out": 8000, "tokens": 108000,
             "cache_read": 80000, "units": "billed", "exact": True},
            {"group": "grok", "agent": "grok:019f", "model": "grok-4.5",
             "tokens_in": 80000, "tokens_out": 17000, "tokens": 97000,
             "cache_read": 0, "units": "conversation", "exact": False},
        ],
        "environment": [["Repo", "/x @ sha"]],
    }


# --------------------------------------------------------------------------- #
# 1. Billed-only record: every summary row is explicitly billed & not estimated
# --------------------------------------------------------------------------- #
def test_billed_summary_rows_are_all_billed_and_not_estimated():
    record = _apply_summaries(_billed_record())
    for key in ("token_summary", "model_summary"):
        rows = record[key]
        assert rows, key
        checked = 0
        for row in rows:
            if _is_total(row):
                continue
            checked += 1
            assert row.get("units") == "billed", (key, row)
            assert not row.get("uncached_estimated"), (key, row)
        assert checked == 3, key  # advisor + grok + investigator


# --------------------------------------------------------------------------- #
# 2. REGRESSION GUARD: real summarize -> render prints no estimate caveats
# --------------------------------------------------------------------------- #
def test_billed_record_renders_no_estimate_caveats():
    record = _apply_summaries(_billed_record())
    html = generate_monitor.render(record)
    assert "(est.)" not in html
    assert "†" not in html


# --------------------------------------------------------------------------- #
# 3. Mixed record: the grok conversation group stays honest (estimated)
# --------------------------------------------------------------------------- #
def test_mixed_grok_group_is_conversation_and_estimated():
    record = _apply_summaries(_mixed_record())

    grok_role = [r for r in record["token_summary"]
                 if not _is_total(r)
                 and "grok" in str(r.get("models", "")).lower()]
    grok_model = [r for r in record["model_summary"]
                  if not _is_total(r)
                  and "grok" in str(r.get("model", "")).lower()]
    assert grok_role and grok_model
    for row in grok_role + grok_model:
        assert row.get("units") == "conversation", row
        assert row.get("uncached_estimated"), row

    # The billed Sonnet groups remain exact in the same mixed record.
    sonnet = [r for r in record["model_summary"]
              if not _is_total(r) and "sonnet" in str(r.get("model", "")).lower()]
    assert sonnet
    for row in sonnet:
        assert row.get("units") == "billed", row
        assert not row.get("uncached_estimated"), row


def test_mixed_record_renders_estimated_indicator():
    record = _apply_summaries(_mixed_record())
    html = generate_monitor.render(record)
    assert "estimated" in html
    assert "(est.)" in html

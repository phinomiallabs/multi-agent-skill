"""Seam 1 tests: grok stdout `--output-format json` usage -> normalized billed row.

Mirrors the cursor recorder's parse_result_line -> write_record -> session_info
chain, but for grok's managed build (>= 0.2.103), whose stdout carries an exact,
subagent-inclusive billed usage object plus USD cost.

Row contract (must match the Claude/cursor row schema so the report renders grok
uniformly):
    tokens_out = output_tokens
    cache_read = cache_read_input_tokens
    tokens_in  = input_tokens + cache_read_input_tokens   (cache_read INSIDE tokens_in)
    tokens     = total_tokens                              (== tokens_in + tokens_out)
    units      = "billed";  exact = True
    cost_usd   = total_cost_usd
    model      = normalized modelUsage key ("grok-4.5-build" -> "grok-4.5")
Identity relied upon: input + cache_read + output == total.
"""
from __future__ import annotations

import grok_tokens
from conftest import load_fixture, read_fixture_text

MULTICALL = "grok_usage_multicall.json"
SINGLECALL = "grok_usage_singlecall.json"
SUBAGENT = "grok_usage_with_subagent.json"
GAUGE_LEGACY = "grok_gauge_legacy.json"


# --------------------------------------------------------------------------- #
# Seam 1: raw stdout -> normalized row
# --------------------------------------------------------------------------- #
def test_multicall_exact_numbers():
    """The verified 4-call run maps to the exact billed row."""
    row = grok_tokens.row_from_stdout(read_fixture_text(MULTICALL))
    assert row is not None
    assert row["tokens_out"] == 164
    assert row["cache_read"] == 56704
    assert row["tokens_in"] == 61904          # 5200 uncached + 56704 cache-read
    assert row["tokens"] == 62068
    assert row["in_uncached"] == 5200
    assert row["units"] == "billed"
    assert row["exact"] is True
    assert row["cost_usd"] == 0.0283952
    assert row["model"] == "grok-4.5"         # normalized from "grok-4.5-build"
    assert row["id"] == "019f77d4-4a4c-7f31-bf94-d2fccba9af0e"
    # tokens is exactly the sum of the two billed sides.
    assert row["tokens"] == row["tokens_in"] + row["tokens_out"]


def test_multicall_carries_modelusage():
    """modelUsage is carried through for later per-model use."""
    row = grok_tokens.row_from_stdout(read_fixture_text(MULTICALL))
    assert row["modelUsage"] == {
        "grok-4.5-build": {
            "inputTokens": 5200,
            "outputTokens": 164,
            "cacheReadInputTokens": 56704,
            "modelCalls": 4,
            "costUSD": 0.0283952,
        }
    }


def test_singlecall_identity_holds():
    """The 1-call run satisfies uncached + cache_read + output == total."""
    obj = load_fixture(SINGLECALL)
    u = obj["usage"]
    assert u["input_tokens"] + u["cache_read_input_tokens"] + u["output_tokens"] == u["total_tokens"]

    row = grok_tokens.row_from_stdout(read_fixture_text(SINGLECALL))
    assert row is not None
    assert row["in_uncached"] == 4801
    assert row["cache_read"] == 10496
    assert row["tokens_out"] == 29
    assert row["tokens_in"] == 4801 + 10496
    assert row["tokens"] == 15326
    assert row["tokens"] == row["tokens_in"] + row["tokens_out"]
    assert row["units"] == "billed"
    assert row["exact"] is True
    assert row["cost_usd"] == 0.0129248


def test_with_subagent_row_is_objects_own_totals():
    """A subagent-spawning run: the row equals the object's OWN totals — grok
    already folds subagents into the captured usage, so there is no extra logic
    and no double-counting."""
    obj = load_fixture(SUBAGENT)
    u = obj["usage"]
    assert u["input_tokens"] + u["cache_read_input_tokens"] + u["output_tokens"] == u["total_tokens"]

    row = grok_tokens.row_from_stdout(read_fixture_text(SUBAGENT))
    assert row is not None
    assert row["in_uncached"] == u["input_tokens"] == 38096
    assert row["cache_read"] == u["cache_read_input_tokens"] == 12416
    assert row["tokens_out"] == u["output_tokens"] == 395
    assert row["tokens"] == u["total_tokens"] == 50907
    assert row["tokens_in"] == u["input_tokens"] + u["cache_read_input_tokens"]
    assert row["cost_usd"] == 0.0822868
    # No child sessions are summed on top: the row is exactly the object totals.
    assert row["tokens"] == row["tokens_in"] + row["tokens_out"]


def test_all_fixtures_satisfy_identity():
    for name in (MULTICALL, SINGLECALL, SUBAGENT):
        row = grok_tokens.row_from_stdout(read_fixture_text(name))
        assert row["tokens"] == row["tokens_in"] + row["tokens_out"], name
        assert row["tokens"] == row["in_uncached"] + row["cache_read"] + row["tokens_out"], name


# --------------------------------------------------------------------------- #
# Model normalization + override
# --------------------------------------------------------------------------- #
def test_model_override_wins():
    row = grok_tokens.row_from_stdout(read_fixture_text(MULTICALL), model="grok-4.5-fast")
    assert row["model"] == "grok-4.5-fast"


# --------------------------------------------------------------------------- #
# Malformed / missing usage -> parse returns None (no row)
# --------------------------------------------------------------------------- #
def test_parse_none_on_missing_usage():
    assert grok_tokens.parse_result_line('{"sessionId": "x", "stopReason": "EndTurn"}') is None
    assert grok_tokens.row_from_stdout('{"sessionId": "x", "stopReason": "EndTurn"}') is None


def test_legacy_gauge_stdout_is_rejected():
    """A legacy (non-billed) gauge-shaped stdout carries a context-size
    `_meta.totalTokens` gauge but NO top-level `usage` object. It sits on the
    far side of the billed-vs-legacy boundary, so the recorder's transform
    rejects it — no billed row is minted from a conversation-size gauge."""
    raw = read_fixture_text(GAUGE_LEGACY)
    assert "usage" not in load_fixture(GAUGE_LEGACY)   # fixture really has no usage
    assert grok_tokens.parse_result_line(raw) is None
    assert grok_tokens.row_from_stdout(raw) is None


def test_parse_none_on_garbage():
    assert grok_tokens.parse_result_line("this is not json at all") is None
    assert grok_tokens.parse_result_line("") is None
    assert grok_tokens.row_from_stdout("") is None


def test_parse_finds_usage_in_multiline_pretty_json():
    """Grok stdout is a pretty-printed (multi-line) JSON object, not JSONL."""
    obj = grok_tokens.parse_result_line(read_fixture_text(MULTICALL))
    assert obj is not None
    assert obj["usage"]["total_tokens"] == 62068


# --------------------------------------------------------------------------- #
# write -> read round-trip through the on-disk store
# --------------------------------------------------------------------------- #
def test_write_read_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(grok_tokens, "USAGE_ROOT", tmp_path / "store")
    cwd = tmp_path / "repo"
    cwd.mkdir()

    dest = grok_tokens.write_record(read_fixture_text(MULTICALL), None, cwd)
    assert dest is not None
    # Stored under ~/.grok-agent-usage/<url-escaped-cwd>/<session-id>.json layout.
    assert dest == grok_tokens.cwd_dir(cwd) / "019f77d4-4a4c-7f31-bf94-d2fccba9af0e.json"
    assert dest.is_file()

    import json
    rec = json.loads(dest.read_text())
    assert rec["schema"] == "grok-agent-usage/1"
    assert rec["cost_usd"] == 0.0283952
    assert rec["modelUsage"]              # carried through in the record
    assert rec["usage"]["total_tokens"] == 62068

    # Reading back reproduces the same normalized row (path aside).
    row = grok_tokens.billed_session_info(dest)
    fresh = grok_tokens.row_from_stdout(read_fixture_text(MULTICALL))
    for key in ("tokens_in", "tokens_out", "tokens", "cache_read", "in_uncached",
                "cost_usd", "model", "units", "exact", "id"):
        assert row[key] == fresh[key], key
    assert row["path"] == str(dest)


def test_write_returns_none_on_no_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(grok_tokens, "USAGE_ROOT", tmp_path / "store")
    assert grok_tokens.write_record('{"sessionId": "x"}', None, tmp_path) is None


def test_grok_billed_sessions_for_cwd(tmp_path, monkeypatch):
    """The reader aggregate_tokens calls: one row per recorded run, newest first."""
    monkeypatch.setattr(grok_tokens, "USAGE_ROOT", tmp_path / "store")
    cwd = tmp_path / "repo"
    cwd.mkdir()

    grok_tokens.write_record(read_fixture_text(SINGLECALL), None, cwd)
    grok_tokens.write_record(read_fixture_text(MULTICALL), None, cwd)

    rows = grok_tokens.grok_billed_sessions_for_cwd(cwd)
    assert len(rows) == 2
    by_id = {r["id"]: r for r in rows}
    assert by_id["019f77d4-4a4c-7f31-bf94-d2fccba9af0e"]["tokens"] == 62068
    assert by_id["019f77ce-ffac-7bc2-a1aa-893feabf1c69"]["tokens"] == 15326
    for r in rows:
        assert r["units"] == "billed"
        assert r["exact"] is True

    # Unknown cwd -> no rows (no store dir).
    assert grok_tokens.grok_billed_sessions_for_cwd(tmp_path / "nope") == []

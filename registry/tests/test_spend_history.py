import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import cost_report
import spend_history


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _ledger(run_id, phase="generate", **overrides):
    row = {"run_id": run_id, "mode": "pr", "key": "PR-7", "phase": phase,
           "provider": "claude", "model": "sonnet", "basis": "reported",
           "input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 0,
           "cache_creation_tokens": 0, "turns": 2, "cost_usd": 0.12,
           "ts": 9e9, "attempts": 1, "attribution": "user"}
    row.update(overrides)
    return {"schema": 1, "run_id": run_id, "rows": [row], "flushed_at": 9e9}


def _record(run_id, **spend_overrides):
    spend = {"provider": "claude", "model": "sonnet", "cost_basis": "reported",
             "cost_usd": 0.12, "input_tokens": 100, "output_tokens": 20,
             "cache_read_tokens": 0, "cache_creation_tokens": 0,
             "turns_used": 2, "max_turns": 9, "simulated": False}
    spend.update(spend_overrides)
    return {"run_id": run_id, "ts": 9e9, "trigger": {"type": "pr", "key": "PR-7"},
            "phases": [{"name": "generate", "contract": {}, "spend": spend}]}


def test_union_deduplicates_and_enriched_run_record_wins(tmp_path):
    runs, costs = tmp_path / "runs", tmp_path / "costs"
    _write(costs / "r1.json", _ledger("r1"))
    _write(runs / "r1.json", _record("r1"))
    rows = spend_history.spend_rows(runs_dir=runs, costs_dir=costs)
    assert len(rows) == 1
    assert rows[0]["source"] == "run_record"
    assert rows[0]["max_turns"] == 9
    assert rows[0]["cost_usd"] == 0.12


def test_retry_aggregate_is_not_replaced_by_last_run_record_row(tmp_path):
    runs, costs = tmp_path / "runs", tmp_path / "costs"
    _write(costs / "r1.json", _ledger(
        "r1", attempts=2, cost_usd=0.3, input_tokens=240, output_tokens=50, turns=5))
    _write(runs / "r1.json", _record("r1", cost_usd=0.18, input_tokens=140,
                                     output_tokens=30, turns_used=3))
    row = spend_history.spend_rows(runs_dir=runs, costs_dir=costs)[0]
    assert (row["attempts"], row["cost_usd"], row["input_tokens"], row["turns"]) == (2, 0.3, 240, 5)
    assert row["max_turns"] == 9


def test_attempt_details_survive_the_union_for_exact_reconciliation_windows(tmp_path):
    runs, costs = tmp_path / "runs", tmp_path / "costs"
    details = [{"ts": 10, "provider": "claude", "model": "sonnet",
                "basis": "reported", "cost_usd": .1},
               {"ts": 11, "provider": "claude", "model": "sonnet",
                "basis": "reported", "cost_usd": .2}]
    _write(costs / "r1.json", _ledger("r1", attempts=2, cost_usd=.3,
                                       attempt_details=details))
    _write(runs / "r1.json", _record("r1"))
    row = spend_history.spend_rows(runs_dir=runs, costs_dir=costs)[0]
    assert row["attempt_details"] == details


def test_cost_report_includes_abort_only_ledger_and_never_double_counts(tmp_path, monkeypatch):
    runs, costs = tmp_path / "reports/runs", tmp_path / "reports/costs"
    _write(costs / "r1.json", _ledger("r1"))
    _write(runs / "r1.json", _record("r1"))
    _write(costs / "r2.json", _ledger(
        "r2", phase="validate", basis="unrecorded", cost_usd=None,
        input_tokens=None, output_tokens=None, cache_read_tokens=None,
        cache_creation_tokens=None, turns=None))
    monkeypatch.setattr(cost_report, "RUNS", runs)
    report = cost_report.report()
    assert report["runs"] == 2
    assert report["total_cost_usd"] == 0.12
    assert report["by_phase"]["generate"]["calls"] == 1
    assert report["unpriced_calls"] == 1
    assert "total is incomplete" in cost_report.to_markdown(report)


def _code_only(source):
    """Source with comments removed, so this sweep cannot match prose.

    It flagged `engine/lib/critic.py` for a COMMENT that named the very token
    the rule forbids -- in a module whose code had just been rewired to go
    through `spend_history` precisely to satisfy this pin. A rule that fires on
    an explanation of itself teaches people to stop writing the explanation.
    CLAUDE.md records the same trap for the prompt-placeholder pin, which strips
    shell comments for the same reason.

    Tokenized rather than cut at the first `#`, so a `#` inside a string
    literal cannot silently truncate a line and hide a real access. A file that
    will not tokenize is returned unchanged -- failing safe means still
    scanning it.
    """
    import io
    import tokenize
    try:
        return "".join(
            tok.string if tok.type != tokenize.COMMENT else ""
            for tok in tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return source


def test_historical_spend_consumers_cannot_resolve_raw_sources():
    """A newly added consumer must use spend_rows(), not become a ninth reader."""
    production = list((ROOT / "engine/lib").glob("*.py")) + list((ROOT / "bin").glob("*.py"))
    raw_record_allowed = {"spend_history.py", "run_record.py", "cost_report.py"}
    raw_ledger_allowed = {"spend_history.py", "spend_ledger.py"}
    direct_spend = ('get("spend")', "get('spend')", '["spend"]', "['spend']")
    for path in production:
        source = _code_only(path.read_text(encoding="utf-8"))
        if any(token in source for token in direct_spend):
            assert path.name in raw_record_allowed, path
        if ("costs_dir(" in source
                and ("read_json_guarded" in source or '.glob("*.json")' in source)):
            assert (path.name in raw_ledger_allowed
                    or "spend_history.spend_rows" in source), path

    for relative in ("engine/lib/parity_compare.py", "engine/lib/pr_comment.py", "bin/qa.py"):
        assert "spend_history.spend_rows" in (ROOT / relative).read_text(encoding="utf-8")


def test_the_comment_stripper_hides_prose_and_nothing_else():
    """The hardening needs its own pin in both directions.

    Weakening it to hide a real access would silently retire the boundary this
    module exists to defend, and a sweep that has stopped catching anything
    reads exactly like a clean codebase.
    """
    assert '["spend"]' in _code_only('x = phase["spend"]\n'), \
        "a real raw access stopped being visible"
    assert '["spend"]' not in _code_only('# discussed ["spend"] in prose\ny = 1\n'), \
        "the sweep is matching comments again"
    # A `#` inside a string must not truncate the line and swallow what follows.
    assert '["spend"]' in _code_only('s = "a#b"\nz = p["spend"]\n')
    # Unparseable input fails SAFE: still scanned, never silently skipped.
    assert "((" in _code_only("def broken((:\n")

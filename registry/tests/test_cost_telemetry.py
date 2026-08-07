"""Cost telemetry pins (cost-reduction stories 1.1, 1.2, 1.5, 1.5a).

The iron rule under test everywhere: a SIMULATED figure must never masquerade as
a measured dollar — `simulated` flags and `~` markers survive every rollup.
"""
import json
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))


# ---------------------------------------------------------------- 1.1 ledger
@pytest.fixture
def ledger(tmp_path, monkeypatch):
    import budget
    monkeypatch.setattr(budget, "LEDGER", tmp_path / "cost.tsv")
    return budget


def _result_json(tmp_path, **over):
    d = {"total_cost_usd": 0.0123, "num_turns": 4,
         "usage": {"input_tokens": 1000, "output_tokens": 200,
                   "cache_read_input_tokens": 5000,
                   "cache_creation_input_tokens": 800}}
    d.update(over)
    p = tmp_path / "phase.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


def test_ledger_row_carries_usage_and_model(ledger, tmp_path):
    b = ledger
    b.record("testplan", _result_json(tmp_path))
    rows = b.read_ledger()
    assert len(rows) == 1
    r = rows[0]
    assert r["phase"] == "testplan" and r["metered"]
    assert r["cost_usd"] == pytest.approx(0.0123)
    assert r["input_tokens"] == 1000 and r["output_tokens"] == 200
    assert r["cache_read_tokens"] == 5000 and r["cache_creation_tokens"] == 800
    assert r["turns"] == 4
    assert r["model"], "the configured tier must be recorded (org-config models)"


def test_fanout_label_resolves_policy_model(ledger, tmp_path):
    b = ledger
    b.record("generate-e2e-api-tests-1", _result_json(tmp_path))
    row = b.read_ledger()[0]
    assert row["model"] == b._model_for("generate"), \
        "a fan-out label prices at its POLICY phase's tier, like run_phase.sh"


def test_old_four_column_rows_still_parse(ledger):
    b = ledger
    b.LEDGER.parent.mkdir(parents=True, exist_ok=True)
    b.LEDGER.write_text("triage\t0.050000\t1\t1700000000\n", encoding="utf-8")
    tot, metered, _ = b.total()
    assert tot == pytest.approx(0.05) and metered == 1
    row = b.read_ledger()[0]
    assert row["input_tokens"] == 0 and row["turns"] == 0, \
        "a crashed run's leftover old-format ledger must not break the next run"


def test_missing_usage_records_zeros_not_crash(ledger, tmp_path):
    b = ledger
    p = tmp_path / "bare.json"
    p.write_text(json.dumps({"total_cost_usd": 0.01}), encoding="utf-8")
    b.record("analyze", p)
    row = b.read_ledger()[0]
    assert row["metered"] and row["input_tokens"] == 0


# ---------------------------------------------------------------- 1.2 report
@pytest.fixture
def records(tmp_path, monkeypatch):
    import cost_report
    monkeypatch.setattr(cost_report, "RUNS", tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    return cost_report


def _run(runs_dir, run_id, key, mode, spends, ts=None):
    rec = {"run_id": run_id, "trigger": {"type": mode, "key": key},
           "ts": ts or time.time(), "overall": "committed", "gates": [],
           "phases": [{"name": n, "contract": {}, "spend": s} for n, s in spends]}
    (runs_dir / f"{run_id}.json").write_text(json.dumps(rec), encoding="utf-8")


def _spend(cost, simulated=False, **over):
    s = {"model": "claude-haiku", "cost_usd": cost, "input_tokens": 100,
         "output_tokens": 10, "cache_read_tokens": 0, "cache_creation_tokens": 0,
         "turns_used": 3, "max_turns": 8, "simulated": simulated}
    s.update(over)
    return s


def test_rollups_by_mode_key_phase_model(records, tmp_path):
    cr = records
    runs = tmp_path / "runs"
    _run(runs, "r1", "PR-a-1", "pr", [("triage", _spend(0.01)),
                                     ("generate-e2e-x", _spend(0.30, model="claude-sonnet"))])
    _run(runs, "r2", "PROJ-9", "jira", [("testplan", _spend(0.20, model="claude-sonnet"))])
    rep = cr.report()
    assert rep["runs"] == 2
    assert rep["total_cost_usd"] == pytest.approx(0.51)
    assert rep["by_mode"]["pr"]["cost_usd"] == pytest.approx(0.31)
    assert rep["by_mode"]["jira"]["runs"] == 1
    assert rep["by_key_top10"][0]["key"] == "PR-a-1", "top keys sorted by cost"
    assert "generate" in rep["by_phase"], "fan-out labels roll up to the policy phase"
    assert rep["by_model"]["claude-sonnet"]["calls"] == 2
    assert rep["simulated_share"] == 0.0


def test_simulated_share_is_visible(records, tmp_path):
    cr = records
    _run(tmp_path / "runs", "r1", "K-1", "pr",
         [("triage", _spend(0.01, simulated=True)), ("generate", _spend(0.3))])
    rep = cr.report()
    assert rep["simulated_share"] == 0.5
    assert "simulated" in cr.to_markdown(rep)


def test_savings_are_na_without_measured_runs(records, tmp_path):
    cr = records
    _run(tmp_path / "runs", "r1", "K-1", "pr",
         [("testplan", _spend(0.2, simulated=True))])
    rep = cr.report()
    assert rep["phase_cache_savings_usd"] is None, \
        "no measured run -> no honest savings figure"
    assert "n/a" in cr.to_markdown(rep)


def test_store_files_never_summed(records, tmp_path):
    cr = records
    runs = tmp_path / "runs"
    _run(runs, "r1", "K-1", "pr", [("triage", _spend(0.01))])
    # reviews.json living in the same dir is the standing trap for every glob.
    (runs / "reviews.json").write_text(json.dumps(
        {"K-1": {"status": "approved", "history": []}}), encoding="utf-8")
    (runs / "queue.json").write_text("[]", encoding="utf-8")
    rep = cr.report()
    assert rep["runs"] == 1


def test_wrong_shaped_records_do_not_hide_valid_spend(records, tmp_path):
    cr = records
    runs = tmp_path / "runs"
    _run(runs, "valid", "K-1", "pr", [("triage", _spend(0.01))])
    (runs / "bad-phases.json").write_text(json.dumps({
        "run_id": "bad-phases", "ts": time.time(),
        "trigger": {"type": "pr", "key": "K-BAD"}, "phases": None,
    }), encoding="utf-8")
    (runs / "bad-trigger.json").write_text(json.dumps({
        "run_id": "bad-trigger", "ts": time.time(),
        "trigger": ["not", "a", "mapping"], "phases": [],
    }), encoding="utf-8")
    (runs / "bad-ts.json").write_text(json.dumps({
        "run_id": "bad-ts", "ts": "not-a-timestamp",
        "trigger": {"type": "pr", "key": "K-BAD"}, "phases": [],
    }), encoding="utf-8")
    (runs / "bad-spend.json").write_text(json.dumps({
        "run_id": "bad-spend", "ts": time.time(),
        "trigger": {"type": "pr", "key": "K-BAD"},
        "phases": [{"name": ["not", "text"], "spend": {
            "provider": {}, "model": [], "cost_usd": {},
            "input_tokens": [], "turns_used": {}, "max_turns": "many",
            "cost_basis": [],
        }}],
    }), encoding="utf-8")

    rep = cr.report()
    assert rep["runs"] == 1
    assert rep["total_cost_usd"] == pytest.approx(0.01)
    assert rep["by_key_top10"][0]["key"] == "K-1"


# ---------------------------------------------------------------- 1.5 turns
def test_turn_calibration_suggests_from_p95(records, tmp_path):
    cr = records
    runs = tmp_path / "runs"
    for i, turns in enumerate([3, 4, 4, 5, 6]):
        _run(runs, f"r{i}", f"K-{i}", "pr",
             [("generate", _spend(0.1, turns_used=turns, max_turns=25))])
    ph = cr.report()["by_phase"]["generate"]
    assert ph["turns_p95"] == 6 and ph["max_turns"] == 25
    assert ph["suggested_max_turns"] == 8, "p95 + 2 headroom, capped at the ceiling"


# ---------------------------------------------------------------- 1.5a payload
def test_launch_payload_recorded_and_summarised(tmp_path, monkeypatch):
    import openhands_events as oe
    monkeypatch.setattr(oe, "FILE", tmp_path / "openhands/state.json")
    oe.record_launch("conv-1", url="https://x/conv-1", key="PROJ-1",
                     title="t", source="agent:test-plan", payload_chars=8000)
    row = [r for r in oe.summary() if r["conversation_id"] == "conv-1"][0]
    assert row["payload_est_tokens"] == 2000
    # A later webhook-style update must not erase the launch's payload record.
    oe.record_launch("conv-1", payload_chars=0)
    row = [r for r in oe.summary() if r["conversation_id"] == "conv-1"][0]
    assert row["payload_est_tokens"] == 2000


def test_run_record_spend_block_shape(ledger, tmp_path, monkeypatch):
    """run_record.py joins the ledger into phases[].spend (integration, in-proc)."""
    b = ledger
    b.record("triage", _result_json(tmp_path))
    import subprocess, os
    out = tmp_path / "out"
    out.mkdir()
    (out / "triage.contract.json").write_text(json.dumps({"impact": "create"}),
                                              encoding="utf-8")
    env = {**os.environ, "AIQE_COST_LEDGER": str(b.LEDGER), "AIQE_MOCK": "1"}
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/run_record.py"),
                        "test-run-1", "pr", "PR-x-1"],
                       cwd=tmp_path, capture_output=True, text=True,
                       encoding="utf-8", stdin=subprocess.DEVNULL, env=env)
    assert r.returncode == 0, r.stderr
    rec = json.loads(r.stdout)
    spend = rec["phases"][0]["spend"]
    assert spend["input_tokens"] == 1000 and spend["turns_used"] == 4
    assert spend["simulated"] is True, "AIQE_MOCK=1 -> simulated, whatever the ledger says"

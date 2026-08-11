"""TCA-B1: every cost consumer is visible without blending task totals."""
import datetime
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import cost_report
import vector_index


def _ledger(directory, run_id, key, provider, basis, cost, attribution="user"):
    directory.mkdir(parents=True, exist_ok=True)
    row = {"run_id": run_id, "mode": "plan", "key": key, "phase": "analyze",
           "provider": provider, "model": "model", "basis": basis,
           "input_tokens": 100, "output_tokens": 20,
           "cache_read_tokens": 10, "cache_creation_tokens": 5,
           "turns": 2, "max_turns": 8, "attempts": 1,
           "cost_usd": cost, "ts": 9e9, "attribution": attribution}
    (directory / f"{run_id}.json").write_text(json.dumps({
        "schema": 1, "run_id": run_id, "mode": "plan", "key": key,
        "attribution": attribution, "flushed_at": 9e9, "rows": [row]}),
        encoding="utf-8")


def test_report_separates_tasks_probes_embeddings_and_unmeterable(tmp_path, monkeypatch):
    runs, costs = tmp_path / "runs", tmp_path / "costs"
    _ledger(costs, "user-priced", "PROJ-1", "claude", "reported", 0.12)
    _ledger(costs, "user-unknown", "PROJ-1", "openhands", "unknown", None)
    _ledger(costs, "probe-run", "PROJ-1", "claude", "reported", 0.03, "probe")
    monkeypatch.setattr(cost_report, "RUNS", runs)
    monkeypatch.setattr(vector_index, "SPEND", tmp_path / "embed-spend.json")
    today = datetime.date.today()
    vector_index.SPEND.write_text(json.dumps({
        str(today - datetime.timedelta(days=1)): 0.01,
        str(today): {"cost_usd": 0.02, "basis": "estimated",
                     "provider": "embeddings", "calls": 2, "tokens": 400},
        f"notified-{today}": True}), encoding="utf-8")

    report = cost_report.report(days=7)
    assert report["runs"] == 2
    assert report["total_cost_usd"] == 0.12
    # measured_usd rides alongside cost_usd: work_queue's envelope warning
    # predicts what a REAL run will do, and comparing the total meant a
    # simulated history warned about real spend. Here the fixture's spend IS
    # measured, so the two agree -- which is the useful thing to assert.
    assert report["by_key_top10"] == [{"key": "PROJ-1", "runs": 2,
                                        "cost_usd": 0.12,
                                        "measured_usd": 0.12}]
    assert report["probe"]["calls"] == 1
    assert report["probe"]["costs_by_basis"] == {"reported": 0.03}
    assert report["unmeterable"] == {"phases": 1, "tasks": 1,
                                      "providers": ["openhands"]}
    assert len(report["embeddings"]["rows"]) == 2
    assert report["embeddings"]["costs_by_basis"] == {"estimated": 0.03}
    assert report["by_provider"]["embeddings"]["bases"]["estimated"] == 3
    assert report["by_provider"]["embeddings"]["calls"] == 2
    assert report["by_provider"]["embeddings"]["calls_unknown_rows"] == 1
    assert report["by_basis"]["reported"]["cost_usd"] == 0.15
    markdown = cost_report.to_markdown(report)
    assert "Embedding spend (separate from task LLM total)" in markdown
    assert "Probe spend (excluded from user-task totals)" in markdown
    assert "Unmeterable: 1 phase(s) across 1 task(s)" in markdown
    assert "By provider (all consumers)" in markdown
    assert "All consumers by basis" in markdown


def test_unmeterable_line_is_present_when_the_count_is_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(cost_report, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(vector_index, "SPEND", tmp_path / "missing.json")
    assert "Unmeterable: 0 phase(s) across 0 task(s)" in cost_report.to_markdown(
        cost_report.report())


def test_embedding_ledger_upgrades_legacy_day_and_keeps_cap_total(tmp_path, monkeypatch):
    spend = tmp_path / "embed-spend.json"
    monkeypatch.setattr(vector_index, "SPEND", spend)
    spend.write_text(json.dumps({vector_index._day(): 0.1}), encoding="utf-8")
    vector_index._record_spend(0.02, 100)
    vector_index._record_spend(0.03, 200)
    raw = json.loads(spend.read_text(encoding="utf-8"))[vector_index._day()]
    assert raw == {"basis": "estimated", "calls": 2, "cost_usd": 0.15,
                   "provider": "embeddings", "tokens": 300}
    assert vector_index._spend_today() == 0.15
    assert vector_index.spend_rows()[0]["cost_usd"] == 0.15


def test_probe_uses_normal_meter_and_exit_flush_with_non_user_attribution():
    source = (ROOT / "bin/cache-probe.sh").read_text(encoding="utf-8")
    assert 'export AIQE_COST_ATTRIBUTION="probe"' in source
    assert "engine/lib/budget.py record" in source
    assert "engine/lib/spend_ledger.py flush" in source
    assert "trap '_probe_exit" in source
    assert "cache-probe-cold" in source and "cache-probe-warm" in source


def test_cost_view_exposes_all_three_completeness_sections():
    source = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert "d.unmeterable" in source
    assert "d.embeddings" in source
    assert "d.probe" in source

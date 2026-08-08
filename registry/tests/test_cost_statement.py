import csv
import io
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import cost_statement


def _ledger(path, run_id, key, phase, basis, cost, *, attribution="user",
            provider="claude", attempts=1):
    row = {"run_id": run_id, "mode": "plan", "key": key, "phase": phase,
           "provider": provider, "model": "sonnet", "basis": basis,
           "input_tokens": 100 if basis != "unrecorded" else None,
           "output_tokens": 20 if basis != "unrecorded" else None,
           "cache_read_tokens": 10 if basis != "unrecorded" else None,
           "cache_creation_tokens": 5 if basis != "unrecorded" else None,
           "turns": 2 if basis != "unrecorded" else None, "cost_usd": cost,
           "ts": 9e9, "attempts": attempts, "attribution": attribution}
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{run_id}.json").write_text(json.dumps({
        "schema": 1, "run_id": run_id, "key": key, "mode": "plan",
        "flushed_at": 9e9, "rows": [row]}), encoding="utf-8")


def test_statement_partitions_bases_and_excludes_non_user_attribution(tmp_path):
    costs, runs = tmp_path / "costs", tmp_path / "runs"
    _ledger(costs, "r1", "KEY-1", "analyze", "reported", 0.12, attempts=2)
    _ledger(costs, "r2", "KEY-1", "testplan", "estimated", 0.08)
    _ledger(costs, "r3", "KEY-1", "generate", "simulated", 0.03)
    _ledger(costs, "r4", "KEY-1", "validate", "local", 0.0)
    _ledger(costs, "r5", "KEY-1", "critic", "unknown", None)
    _ledger(costs, "r6", "KEY-1", "arbiter", "unrecorded", None)
    _ledger(costs, "r7", "KEY-1", "probe", "reported", 0.01,
            attribution="probe")
    _ledger(costs, "other", "KEY-10", "analyze", "reported", 99.0)

    doc = cost_statement.statement("KEY-1", runs_dir=runs, costs_dir=costs)
    assert len(doc["rows"]) == 6 and len(doc["non_user_rows"]) == 1
    assert doc["totals"] == {
        "reported_usd": 0.12, "estimated_usd": 0.08,
        "simulated_usd": 0.03, "local_tokens": 120,
        "unknown_rows": 1, "unrecorded_rows": 1,
        "not_reconciled_rows": 0, "incomplete_priced_rows": 0,
        "phases": 6, "provider_calls": 7}
    assert doc["non_user_totals"]["reported_usd"] == 0.01
    assert "total_cost" not in doc["totals"], "mixed bases must have no combined dollar total"


def test_markdown_and_csv_keep_one_line_per_phase_and_defuse_formulas(tmp_path):
    costs = tmp_path / "costs"
    _ledger(costs, "r1", "KEY-2", "analyze", "reported", 0.12,
            provider="=HYPERLINK(\"bad\")")
    _ledger(costs, "r2", "KEY-2", "failed", "unrecorded", None)
    doc = cost_statement.statement("KEY-2", runs_dir=tmp_path / "runs", costs_dir=costs)
    markdown = cost_statement.to_markdown(doc)
    assert "Reported: $0.120000" in markdown
    assert "Unrecorded rows: 1" in markdown
    assert "—" in markdown
    rows = list(csv.DictReader(io.StringIO(cost_statement.to_csv(doc))))
    assert len(rows) == 2
    assert rows[0]["provider"].startswith("'=")
    assert {row["phase"] for row in rows} == {"analyze", "failed"}


def test_missing_cost_on_a_priced_basis_is_explicitly_incomplete(tmp_path):
    costs = tmp_path / "costs"
    _ledger(costs, "r1", "KEY-4", "analyze", "reported", None)
    doc = cost_statement.statement("KEY-4", runs_dir=tmp_path / "runs", costs_dir=costs)
    assert doc["totals"]["reported_usd"] == 0
    assert doc["totals"]["incomplete_priced_rows"] == 1
    assert "Incomplete priced rows: 1" in cost_statement.to_markdown(doc)


@pytest.mark.parametrize("key", ["", "../escape", "A/B", "x" * 129])
def test_statement_rejects_unsafe_or_unbounded_keys(key):
    with pytest.raises(ValueError, match="key must"):
        cost_statement.statement(key)


def test_export_is_deterministic_and_uses_existing_exports_location(tmp_path, monkeypatch):
    doc = {"schema": 1, "key": "KEY-3", "rows": [],
           "totals": cost_statement._totals([]), "non_user_rows": [],
           "non_user_totals": cost_statement._totals([])}
    monkeypatch.setattr(cost_statement, "EXPORTS", tmp_path / "reports/exports")
    monkeypatch.setattr(cost_statement, "statement", lambda key: doc)
    path = cost_statement.export("KEY-3", "csv")
    first = path.read_bytes()
    assert path == tmp_path / "reports/exports/KEY-3-cost-statement.csv"
    assert cost_statement.export("KEY-3", "csv").read_bytes() == first
    assert not list(path.parent.glob(".*.tmp"))


def test_cost_statement_surfaces_are_wired():
    make = (ROOT / "Makefile").read_text(encoding="utf-8")
    server = (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")
    dashboard = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    qa = (ROOT / "bin/qa.py").read_text(encoding="utf-8")
    assert "cost-statement:" in make and "$(KEY)" in make and "$(FORMAT)" in make
    assert 'url.path == "/api/cost-statement"' in server
    assert "Token-cost statement" in dashboard and "/api/cost-statement?key=" in dashboard
    assert 'sub.add_parser("cost-statement")' in qa


def test_supplied_history_snapshot_avoids_reloading_sources(monkeypatch):
    row = {"key": "KEY-5", "attribution": "user", "basis": "reported",
           "attempts": 1, "cost_usd": 0.4, "input_tokens": 1, "output_tokens": 2}
    monkeypatch.setattr(cost_statement.spend_history, "spend_rows",
                        lambda **kwargs: pytest.fail("history was reloaded"))
    doc = cost_statement.statement("KEY-5", history_rows=[row])
    assert doc["totals"]["reported_usd"] == 0.4


def test_dashboard_renders_statement_for_ledger_only_plan_key(tmp_path):
    costs = tmp_path / "costs"
    _ledger(costs, "ledger-only", "LEDGER-ONLY-991", "testplan", "reported", 0.2)
    output = tmp_path / "dashboard.html"
    env = dict(os.environ, AIQE_COSTS_DIR=str(costs), AIQE_DASHBOARD_OUT=str(output))
    result = subprocess.run([sys.executable, str(ROOT / "bin/dashboard.py")], cwd=ROOT,
                            env=env, capture_output=True, text=True,
                            stdin=subprocess.DEVNULL, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    html = output.read_text(encoding="utf-8")
    assert "LEDGER-ONLY-991" in html and "Token-cost statement" in html
    assert "reported $0.200000" in html

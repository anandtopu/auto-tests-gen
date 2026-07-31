"""Spend-control pins (cost-reduction stories 5.1, 5.2, 5.3, 4.1, 4.2).

Contracts: skips are recorded and distinct from failures, envelopes resolve
per workflow with env precedence intact, the degradation ladder grades at the
documented rungs and NEVER touches judgement phases, and the queue warning
warns without refusing.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import budget  # noqa: E402


# ---------------------------------------------------------------- 5.2
@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "LEDGER", tmp_path / "cost.tsv")
    monkeypatch.delenv("MAX_COST_USD_PER_RUN", raising=False)

    def spend(usd):
        budget.LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(budget.LEDGER, "a", encoding="utf-8") as fh:
            fh.write(f"phase\t{usd:.6f}\t1\t0\n")
    return spend


def test_envelope_resolves_per_workflow(monkeypatch, ledger):
    monkeypatch.setenv("AIQE_RUN_MODE", "plan")
    limit, _, source = budget.limits()
    assert limit == 1.0 and source == "org-config envelopes.plan"
    monkeypatch.setenv("AIQE_RUN_MODE", "jira")
    limit, _, source = budget.limits()
    assert limit == 4.0 and source == "org-config envelopes.jira"


def test_explicit_env_still_beats_the_envelope(monkeypatch, ledger):
    monkeypatch.setenv("AIQE_RUN_MODE", "plan")
    monkeypatch.setenv("MAX_COST_USD_PER_RUN", "9.5")
    limit, _, source = budget.limits()
    assert limit == 9.5 and source == "MAX_COST_USD_PER_RUN"


def test_unknown_mode_falls_back_to_the_generic_pair(monkeypatch, ledger):
    monkeypatch.setenv("AIQE_RUN_MODE", "")
    limit, _, source = budget.limits()
    assert "max_cost_usd" in source and limit > 0


# ---------------------------------------------------------------- 5.3
def test_ladder_grades_at_the_documented_rungs(monkeypatch, ledger):
    monkeypatch.setenv("AIQE_RUN_MODE", "plan")     # envelope $1.00
    assert budget.grade() == "ok", "no metered spend -> ok"
    ledger(0.30)
    assert budget.grade() == "ok"
    ledger(0.35)                                    # 0.65 = 65%
    assert budget.grade() == "degrade_tier"
    ledger(0.20)                                    # 0.85 = 85%
    assert budget.grade() == "degrade_context"
    ledger(0.20)                                    # 1.05 = over
    assert budget.grade() == "abort"


def test_mock_runs_never_degrade(monkeypatch, tmp_path):
    monkeypatch.setattr(budget, "LEDGER", tmp_path / "cost.tsv")
    budget.LEDGER.parent.mkdir(parents=True, exist_ok=True)
    # Unmetered rows (mock) — whatever the numbers, grading needs metered spend.
    budget.LEDGER.write_text("phase\t99.0\t0\t0\n", encoding="utf-8")
    monkeypatch.setenv("AIQE_RUN_MODE", "plan")
    assert budget.grade() == "ok"


def test_judgement_phases_never_downgrade_in_run_phase():
    """Source pin: the cheap-tier case list in run_phase.sh must not contain a
    judgement phase — a silently cheaper plan is worse than no plan."""
    src = (ROOT / "engine/phases/run_phase.sh").read_text(encoding="utf-8")
    assert "triage|analyze|testdata|critic|validate|resolve)" in src
    for phase in ("testplan", "planadversary", "planarbiter", "generate"):
        import re
        m = re.search(r"case \"\$PHASE\" in\s*\n\s*([^)]*)\)", src)
        assert m and phase not in m.group(1), \
            f"{phase} must never appear in the degrade tier list"


def test_context_budget_halves_at_the_context_rung(monkeypatch):
    import context_scope as cs
    monkeypatch.delenv("AIQE_CONTEXT_BUDGET_FACTOR", raising=False)
    monkeypatch.setattr(budget, "grade", lambda start_epoch=0: "degrade_context")
    # context_scope imports budget lazily by name — patch the module it sees.
    assert cs.budget_tokens({"context_budget": 4000}) == 2000
    monkeypatch.setattr(budget, "grade", lambda start_epoch=0: "ok")
    assert cs.budget_tokens({"context_budget": 4000}) == 4000


# ---------------------------------------------------------------- 5.1
def test_run_record_renders_skips_distinct_from_failures(tmp_path):
    import os
    import subprocess
    out = tmp_path / "out"
    out.mkdir()
    (out / "generate.contract.json").write_text(json.dumps({"tests": []}),
                                                encoding="utf-8")
    (out / "phase-skips.tsv").write_text(
        "critic\tno generated tests to score\n", encoding="utf-8")
    env = {**os.environ, "AIQE_MOCK": "1"}
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/run_record.py"),
                        "s-1", "pr", "PR-x-1"],
                       cwd=tmp_path, capture_output=True, text=True,
                       encoding="utf-8", stdin=subprocess.DEVNULL, env=env)
    assert r.returncode == 0, r.stderr
    rec = json.loads(r.stdout)
    assert rec["skipped_phases"] == [
        {"phase": "critic", "reason": "no generated tests to score"}]
    assert rec["overall"] in ("committed", "no_changes"), \
        "a skip must never read as a failed run"


def test_pipeline_wires_all_three_skips():
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert 'SKIP_PHASE critic "no generated tests to score"' in src
    assert 'SKIP_PHASE planadversary' in src
    assert src.count('SKIP_PHASE testdata "plan declares data_needs: none"') == 2, \
        "both testdata call sites (jira + tests mode) carry the skip"


# ---------------------------------------------------------------- 5.2 queue
def test_queue_warning_warns_but_never_refuses(tmp_path, monkeypatch):
    import work_queue as wq
    monkeypatch.setattr(wq, "FILE", tmp_path / "queue.json")
    monkeypatch.setattr(
        wq, "_envelope_warning",
        lambda mode, target, pr=None: "history exceeds the envelope")
    item, fresh = wq.add("jira", "PROJ-777")
    assert fresh and item["warning"] == "history exceeds the envelope", \
        "an expensive key still queues — the warning informs, never blocks"


def test_envelope_warning_fires_on_history_over_cap(tmp_path, monkeypatch):
    import work_queue as wq
    import cost_report
    monkeypatch.setattr(
        cost_report, "report",
        lambda days=None: {"by_key_top10": [
            {"key": "PROJ-9", "runs": 3, "cost_usd": 6.0}]})
    w = wq._envelope_warning("jira", "PROJ-9")
    assert "exceeds" in w and "$6.00" in w
    assert wq._envelope_warning("jira", "PROJ-cheap") == ""


# ---------------------------------------------------------------- 4.1 / 4.2
def test_cache_probe_refuses_mock_mode(monkeypatch):
    import os
    import subprocess
    env = {**os.environ, "AIQE_MOCK": "1", "AIQE_REAL_LLM": "0"}
    r = subprocess.run([wq_bash(), str(ROOT / "bin/cache-probe.sh")],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", stdin=subprocess.DEVNULL, env=env)
    assert r.returncode == 2 and "Nothing was measured" in r.stdout, \
        "the probe must never pretend a mock run measured provider caching"


def wq_bash():
    import work_queue
    return work_queue.bash_exe()


def test_hit_rate_floor_flags_in_report(tmp_path, monkeypatch):
    import cost_report as cr
    monkeypatch.setattr(cr, "RUNS", tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    rec = {"run_id": "r1", "trigger": {"type": "pr", "key": "K"},
           "ts": 9e9, "phases": [{"name": "triage", "contract": {}, "spend": {
               "model": "m", "cost_usd": 0.1, "input_tokens": 1000,
               "output_tokens": 10, "cache_read_tokens": 0,
               "cache_creation_tokens": 0, "turns_used": 2, "max_turns": 8,
               "simulated": False}}]}
    (tmp_path / "runs/r1.json").write_text(json.dumps(rec), encoding="utf-8")
    monkeypatch.setattr(cr, "ROOT", ROOT)   # keep org-config readable
    import yaml
    cfg = yaml.safe_load(open(ROOT / "registry/org-config.yaml", encoding="utf-8"))
    floor = (cfg.get("budgets") or {}).get("min_cache_hit_rate")
    md = cr.to_markdown(cr.report())
    if floor:
        assert "BELOW FLOOR" in md
    else:
        assert "BELOW FLOOR" not in md, "no floor configured -> no flag"

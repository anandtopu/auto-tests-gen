"""Budget ENFORCEMENT (engine/lib/budget.py + the pipeline's _budget_guard).

MAX_COST_USD_PER_RUN / MAX_WALLCLOCK_MIN were UI-displayed settings that nothing
read. Now the pipeline checks both BEFORE every phase and aborts with exit 77 —
which means a runaway loop can overshoot by at most one phase. These tests pin the
ledger math, the limit precedence, the totality of the parser, and — end to end —
that an over-budget run dies before the gate while an under-budget one commits
with its spend recorded.
"""
import json, os, pathlib, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import budget
import work_queue


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    p = tmp_path / "cost.tsv"
    monkeypatch.setattr(budget, "LEDGER", p)
    return p


# ---------------------------------------------------------------- cost parsing

def test_phase_cost_reads_total_cost_usd(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"type": "result", "total_cost_usd": 0.1234}),
                 encoding="utf-8")
    assert budget.phase_cost(f) == (0.1234, True)


def test_phase_cost_falls_back_to_cost_usd(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"cost_usd": 0.5}), encoding="utf-8")
    assert budget.phase_cost(f) == (0.5, True)


@pytest.mark.parametrize("content", ["", "not json", "[1,2]",
                                     '{"result": "no cost fields"}'])
def test_phase_cost_is_total_on_junk(tmp_path, content):
    f = tmp_path / "p.json"
    f.write_text(content, encoding="utf-8")
    assert budget.phase_cost(f) == (0.0, False)


# ---------------------------------------------------------------- ledger

def test_record_and_total(ledger, tmp_path, monkeypatch):
    monkeypatch.delenv("AIQE_MOCK_PHASE_COST", raising=False)
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"total_cost_usd": 1.25}), encoding="utf-8")
    budget.record("triage", f)
    budget.record("generate", f)
    budget.record("mockphase", None)          # unmetered
    tot, metered, unmetered = budget.total()
    assert (round(tot, 2), metered, unmetered) == (2.5, 2, 1)


def test_simulated_cost_meters_mock_phases(ledger, monkeypatch):
    monkeypatch.setenv("AIQE_MOCK_PHASE_COST", "0.75")
    budget.record("triage", None)
    tot, metered, _ = budget.total()
    assert (tot, metered) == (0.75, 1)


# ---------------------------------------------------------------- limits

def test_env_limit_beats_org_config(monkeypatch):
    monkeypatch.setenv("MAX_COST_USD_PER_RUN", "9.5")
    limit, _, source = budget.limits()
    assert limit == 9.5 and source == "MAX_COST_USD_PER_RUN"


def test_org_config_is_the_fallback(monkeypatch):
    monkeypatch.delenv("MAX_COST_USD_PER_RUN", raising=False)
    monkeypatch.chdir(ROOT)                    # resolve.contract.json may not exist
    limit, _, source = budget.limits()
    assert limit in (2.0, 4.0) and source.startswith("org-config")


def test_wallclock_default_and_env(monkeypatch):
    monkeypatch.delenv("MAX_WALLCLOCK_MIN", raising=False)
    assert budget.limits()[1] == 25.0
    monkeypatch.setenv("MAX_WALLCLOCK_MIN", "3")
    assert budget.limits()[1] == 3.0


# ---------------------------------------------------------------- check

def test_check_trips_on_cost_only_when_metered(ledger, monkeypatch):
    import time
    monkeypatch.setenv("MAX_COST_USD_PER_RUN", "1.0")
    monkeypatch.setenv("MAX_WALLCLOCK_MIN", "999")
    monkeypatch.setenv("AIQE_MOCK_PHASE_COST", "")
    # unmetered spend must NOT trip (mock/demo safety)
    budget.record("a", None)
    assert budget.check(time.time()) is None
    # metered spend over the limit must trip
    monkeypatch.setenv("AIQE_MOCK_PHASE_COST", "2.0")
    budget.record("b", None)
    reason = budget.check(time.time())
    assert reason and "cost" in reason and "BUDGET_EXCEEDED" in reason


def test_check_trips_on_wallclock_in_any_mode(ledger, monkeypatch):
    import time
    monkeypatch.setenv("MAX_WALLCLOCK_MIN", "1")
    monkeypatch.delenv("MAX_COST_USD_PER_RUN", raising=False)
    reason = budget.check(time.time() - 120)   # started 2 min ago
    assert reason and "wall-clock" in reason


# ------------------------------------------------------------ end to end

def _pipeline(env_extra, args=("pr", "orders-api", "201")):
    env = {**os.environ, "AIQE_MOCK": "1", **env_extra}
    return subprocess.run([work_queue.bash_exe(), "engine/pipeline.sh", *args],
                          cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, env=env, timeout=900)


def test_over_budget_run_dies_at_77_before_the_gate():
    r = _pipeline({"AIQE_MOCK_PHASE_COST": "1.50", "MAX_COST_USD_PER_RUN": "2"})
    assert r.returncode == 77, f"exit {r.returncode}\n{r.stdout[-800:]}"
    assert "BUDGET_EXCEEDED" in r.stdout
    assert "GATE_STATUS" not in r.stdout, "the gate must never run over budget"
    assert "ABORTED" in r.stdout, "the abort must be notified"


def test_under_budget_metered_run_commits_and_records_spend():
    r = _pipeline({"AIQE_MOCK_PHASE_COST": "0.05", "MAX_COST_USD_PER_RUN": "4"})
    assert r.returncode == 0, r.stdout[-800:]
    assert "GATE_STATUS=COMMITTED" in r.stdout
    # THIS run's record carries the summed spend. Select it by the run id the
    # pipeline printed — "the newest file on disk" belongs to whichever
    # pipeline-running test finished last, and several tests now run real runs
    # against the shared estate.
    import glob, re as _re
    m = _re.search(r"AI-QE run (\S+) for", r.stdout)
    assert m, f"no run id in output: {r.stdout[-400:]}"
    run_id = m.group(1)
    f = ROOT / f"reports/runs/{run_id}.json"
    assert f.exists(), f"no record for run {run_id}"
    rec = json.load(open(f, encoding="utf-8"))
    assert rec.get("cost_usd") and rec["cost_usd"] > 0


def test_unmetered_demo_run_never_aborts_on_cost():
    """Mock phases meter nothing; only the wall-clock limit may stop them."""
    r = _pipeline({"MAX_COST_USD_PER_RUN": "0.01"})
    assert r.returncode == 0, r.stdout[-500:]
    assert "GATE_STATUS" in r.stdout


def test_guard_runs_before_every_phase_not_after():
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    i = src.index("PHASE() {")
    body = src[i:i + 220]
    assert body.index("_budget_guard") < body.index("_PHASE_IMPL"), \
        "the guard must run BEFORE the phase, or a runaway costs one extra phase"

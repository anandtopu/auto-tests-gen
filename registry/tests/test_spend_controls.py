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
    for phase in ("testplan", "planadversary", "planarbiter", "generate",
                  "reviewer", "reviewrepair"):
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


def test_baseline_refuses_simulated_runs(tmp_path, monkeypatch):
    """1.3: a baseline built from simulations would alarm on the first real
    dollar or never alarm — refusal is the feature."""
    import cost_report as cr
    monkeypatch.setattr(cr, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(cr, "BASELINE", tmp_path / "baseline.json")
    (tmp_path / "runs").mkdir()
    rec = {"run_id": "r1", "trigger": {"type": "pr", "key": "K"}, "ts": 9e9,
           "phases": [{"name": "triage", "contract": {}, "spend": {
               "model": "m", "cost_usd": 0.1, "input_tokens": 1,
               "output_tokens": 1, "cache_read_tokens": 0,
               "cache_creation_tokens": 0, "turns_used": 1, "max_turns": 8,
               "simulated": True}}]}
    (tmp_path / "runs/r1.json").write_text(json.dumps(rec), encoding="utf-8")
    with pytest.raises(SystemExit):
        cr.snapshot_baseline()


def test_regression_alarm_fires_and_stays_silent(tmp_path, monkeypatch):
    """1.4: 2x over baseline -> named regression; no baseline -> silence."""
    import cost_report as cr
    monkeypatch.setattr(cr, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(cr, "BASELINE", tmp_path / "baseline.json")
    (tmp_path / "runs").mkdir()

    def measured(run_id, cost, ts):
        rec = {"run_id": run_id, "trigger": {"type": "pr", "key": "K"},
               "ts": ts, "phases": [{"name": "triage", "contract": {}, "spend": {
                   "model": "m", "cost_usd": cost, "input_tokens": 1,
                   "output_tokens": 1, "cache_read_tokens": 0,
                   "cache_creation_tokens": 0, "turns_used": 1, "max_turns": 8,
                   "simulated": False}}]}
        (tmp_path / f"runs/{run_id}.json").write_text(json.dumps(rec),
                                                      encoding="utf-8")
    assert cr.check_regression() == [], "no baseline armed -> silence"
    import time as _t
    measured("r1", 0.10, _t.time())
    cr.snapshot_baseline()
    assert cr.check_regression(threshold=0.25) == [], "healthy at baseline"
    measured("r2", 0.30, _t.time())
    measured("r3", 0.30, _t.time())                 # median now 0.30 = 3x baseline
    regs = cr.check_regression(threshold=0.25)
    assert regs and "triage" in regs[0] and "prompt edit" in regs[0], \
        "the alarm names the phase AND the likely causes"


def test_wizard_says_reduced_cost_and_skipped(monkeypatch, tmp_path):
    import wizard_status as ws
    rec = {"run_id": "r1", "trigger": {"type": "pr", "key": "PR-a-1"},
           "ts": 1, "overall": "committed",
           "degradation": [{"phase": "triage", "grade": "degrade_tier"}],
           "skipped_phases": [{"phase": "critic", "reason": "no tests"}],
           "gates": [{"test_repo": "e2e-x", "status": "committed"}],
           "phases": [{"name": "generate", "contract": {
               "tests": [{"file": "a.spec.js", "action": "created"}]}}]}
    monkeypatch.setattr(ws, "_runs_for", lambda key: [rec])
    monkeypatch.setattr(ws, "_queue_for", lambda key: [])
    steps = ws.build("PR-a-1", "pr")["steps"]
    gen = next(s for s in steps if s["label"] == "Generate E2E tests")
    assert "reduced-cost mode" in gen["detail"]
    assert "skipped: critic" in gen["detail"]


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


# ---- an uncountable ledger must not read as a cheap run ---------------------
def test_an_unreadable_ledger_makes_the_ceiling_unenforceable(tmp_path, monkeypatch):
    """`record()` and `total()` both swallow OSError, so a ledger that cannot
    be written reports $0.00 spent — and `enforceability()` answered "enforced"
    while counting nothing.

    Demonstrated before the fix: $25.00 of real spend against a $1.00 ceiling,
    reported as enforced and within budget. Cost cannot be invented, so the
    ceiling still cannot abort on spend it never saw — but the INABILITY to
    enforce is no longer silent, which is the same remedy R1 applied to
    unpriced providers.
    """
    import budget
    res = tmp_path / "generate.json"
    res.write_text(json.dumps({"total_cost_usd": 25.00, "num_turns": 8,
                               "usage": {"input_tokens": 1000, "output_tokens": 500}}),
                   encoding="utf-8")
    monkeypatch.setenv("MAX_COST_USD_PER_RUN", "1.00")

    bad = tmp_path / "cost.tsv"
    bad.mkdir()                                  # a directory: writes raise
    monkeypatch.setattr(budget, "LEDGER", bad)
    budget.record("generate", str(res))           # silently drops the row
    spent, metered, _ = budget.total()
    assert (spent, metered) == (0.0, 0), "the swallow is what makes this dangerous"

    state, msg = budget.enforceability()
    assert state == "unenforceable", "a $0.00 total was reported as enforced"
    assert "BUDGET_UNENFORCEABLE" in msg
    assert "not measured" in msg, "the message must say what $0.00 means here"


def test_a_healthy_and_a_missing_ledger_both_stay_enforced(tmp_path, monkeypatch):
    """A MISSING ledger is normal — no phase has run yet — and must not raise a
    false alarm, or the check gets ignored the way a permanent warning does."""
    import budget
    res = tmp_path / "generate.json"
    res.write_text(json.dumps({"total_cost_usd": 0.50, "num_turns": 2,
                               "usage": {"input_tokens": 10, "output_tokens": 5}}),
                   encoding="utf-8")
    monkeypatch.setenv("MAX_COST_USD_PER_RUN", "1.00")

    monkeypatch.setattr(budget, "LEDGER", tmp_path / "ok" / "cost.tsv")
    budget.record("generate", str(res))
    assert budget.total()[1] == 1
    assert budget.enforceability()[0] == "enforced"

    monkeypatch.setattr(budget, "LEDGER", tmp_path / "never" / "cost.tsv")
    assert budget.enforceability()[0] == "enforced"


def test_the_countability_check_runs_before_the_pricing_check(tmp_path, monkeypatch):
    """An uncountable ledger has no rows, so the unpriced-provider logic sees
    nothing to complain about and would answer "enforced". Order matters."""
    src = (ROOT / "engine/lib/budget.py").read_text(encoding="utf-8")
    body = src[src.index("def enforceability("):]
    body = body[:body.index("\ndef ", 1)]
    assert body.index("ledger_problem()") < body.index("if metered:"), \
        "the pricing verdict is reached before asking whether spend is countable"


# --- the iron rule, applied to the number people actually quote -------------

def _rep_with(share, total):
    """A real report shape with the basis overridden.

    Hand-building the dict was brittle -- to_markdown reads keys I had not
    listed (by_mode was the first, and enumerating the rest would just move the
    guess) -- and a test that fails on a missing key is not testing labelling.
    Deriving from the real shape also means a new key cannot silently make
    these pass for the wrong reason.
    """
    import cost_report
    rep = dict(cost_report.report(None))
    rep["simulated_share"] = share
    rep["total_cost_usd"] = total
    return rep


def _total_line(rep):
    import cost_report
    return next(l for l in cost_report.to_markdown(rep).splitlines()
                if "User-task LLM total" in l)


def test_a_simulated_total_never_prints_as_a_measured_dollar():
    """cost_report's own docstring: a SIMULATED number may inform a trend but
    "must never masquerade as a measured dollar".

    The headline printed `User-task LLM total: $11.7500` on an estate whose
    spend rows are 99% simulated. The title carried "99% simulated", but the
    NUMBER was formatted exactly like a measured one -- and the number is what
    gets quoted out of a report, not the badge above it.
    """
    total = _total_line(_rep_with(1.0, 11.75))
    assert "~$" in total, f"a fully simulated total prints as measured: {total}"
    assert "SIMULATED" in total, "the total does not say what it is"
    assert "$11.7500" in total, "the figure itself was lost"


def test_a_partly_simulated_total_says_it_cannot_be_separated():
    """The mixed case is the dangerous one: some of it IS real, so a reader may
    take the whole figure as measured. It must not imply the measured part can
    be told apart when this rollup cannot tell it apart."""
    total = _total_line(_rep_with(0.6, 4.5))
    assert "~$" in total and "60%" in total
    assert "cannot be separated" in total


def test_a_fully_measured_total_is_not_hedged():
    """The other direction. Marking a real dollar `~` would be its own lie, and
    it is the one that makes people stop trusting the tilde."""
    total = _total_line(_rep_with(0.0, 9.0))
    assert "~" not in total and "$9.0000" in total


def test_the_cost_view_and_the_overview_tile_agree_about_the_tilde():
    """The Overview tile applied `~` and the Cost view did not, on the same
    figure from the same payload. The tile was the reference implementation;
    the view is where an operator reads the total."""
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert '_sim = "~" if _rep["simulated_share"] > 0 else ""' in src,         "the Overview tile stopped marking simulated spend"
    fn = src[src.index("const sum = document.getElementById('cost-summary')"):]
    fn = fn[:fn.index("const pt =")]
    assert "simulated_share" in fn,         "the Cost view total ignores the basis its own payload carries"
    assert "'<b>Total $'" not in fn,         "the Cost view still hardcodes a bare $ on a possibly-simulated total"

"""The scorecard must not report a mock's constants as measurements.

eval/scorecard.py states the rule in its own comments — "a simulated number is
never reported as a measurement" — and applied it to ONE metric.
engine/phases/mock_phase.sh emits a hardcoded `score: 0.86, noise_count: 0`,
so on this (99% simulated) estate the scorecard printed:

    Critic score: 0.86 avg over 394 scored runs      <- the stub's default
    Escaped noise: 0% of 788 generated specs         <- a stub that emits 0
    Cost per run: $0.25 avg over 36 metered run(s)   <- AIQE_MOCK_PHASE_COST

CLAUDE.md quotes those figures, so the repo's own record of its quality was a
mock's default value.

Worse, the exclusion the Update-vs-create metric already had was itself broken:
it tested `isinstance(run["cost_usd"], (int, float))`, and AIQE_MOCK_PHASE_COST
makes a simulated run record a numeric cost. Confirmed on the estate — a run
with `cost_usd: 0.25` whose critic phase carries `spend.simulated: true`. So
the metric that was supposedly fixed went on measuring the fixture, just fewer
of it.

These pin the INVARIANT rather than today's numbers: a measured claim must be
backed by a run whose spend is real. That stays true when parity is unblocked
and the estate finally has measured runs — a pin asserting "n/a" would have to
be deleted on the day it starts mattering.
"""
import json
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS = ROOT / "reports/runs"
STATE_FILES = {"reviews.json", "queue.json", "hooks-seen.json"}


def _records():
    out = []
    if not RUNS.is_dir():
        return out
    for f in RUNS.glob("*.json"):
        if f.name in STATE_FILES:
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def _measured(run):
    for p in run.get("phases") or []:
        s = p.get("spend") or {}
        if s.get("cost_usd") is not None and not s.get("simulated"):
            return True
    return False


@pytest.fixture(scope="module")
def scorecard_output():
    r = subprocess.run([sys.executable, str(ROOT / "eval/scorecard.py")],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", stdin=subprocess.DEVNULL, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout


def test_a_measured_cost_claim_is_backed_by_real_spend(scorecard_output):
    """`Cost per run: $x avg over N metered run(s)` is a claim about money."""
    m = re.search(r"Cost per run: \$", scorecard_output)
    if not m:
        return                       # n/a — nothing claimed, nothing to check
    assert any(_measured(r) for r in _records()), (
        "the scorecard reports a measured cost per run, but no run record "
        "carries non-simulated spend — AIQE_MOCK_PHASE_COST is being read as "
        "a real bill")


def test_a_critic_score_is_backed_by_a_real_critic_phase(scorecard_output):
    """The mock critic emits a fixed score, so an average over simulated runs
    reports the stub's default dressed as a quality measurement."""
    # A NUMERIC claim, not the n/a line — which contains the words "Critic
    # score:" too, so a substring test made this fire on an honest report.
    if not re.search(r"Critic score: \d", scorecard_output):
        return
    assert any(_measured(r) and r.get("critic") for r in _records()), (
        "the scorecard reports a critic score with no measured critic run — "
        "that number is engine/phases/mock_phase.sh's default")


def test_an_update_vs_create_rate_is_backed_by_a_real_generate(scorecard_output):
    """A claim about JUDGEMENT: did the agent extend rather than duplicate? The
    mock stub's action is scripted."""
    if not re.search(r"Update-vs-create: \d", scorecard_output):
        return
    assert any(_measured(r) for r in _records()), \
        "the scorecard rates extend-vs-create from scripted stub actions"


def test_the_unmeasured_branches_name_what_would_measure_them(scorecard_output):
    """C13: 'we have not measured this' must not read as 'this is zero', and it
    has to say what would fix it. Every n/a here points at make parity-pr."""
    for line in scorecard_output.splitlines():
        if re.search(r"^(Cost per run|Update-vs-create|Escaped noise)", line) \
                and "n/a" in line:
            assert "parity" in line.lower(), \
                f"an unmeasured metric does not name what would measure it: {line}"


def test_the_exclusion_uses_the_spend_flag_not_the_cost_type():
    """The specific defect: `cost_usd is a number` is not `this was real`.
    AIQE_MOCK_PHASE_COST exists so tests can drive the budget ladder, and it
    makes a simulated run look metered."""
    src = (ROOT / "eval/scorecard.py").read_text(encoding="utf-8")
    assert "def _has_measured_spend" in src
    assert 'not s.get("simulated")' in src, \
        "the measured test no longer consults the spend basis"
    assert 'metered = _has_measured_spend(r)' in src, \
        "update-vs-create is back on the cost-type proxy"
    # CODE only. The first version asserted "isinstance" was absent from the
    # whole function, and failed — because the docstring quotes the old proxy
    # while explaining it. Third time today a pin matched my own prose; the
    # lesson keeps being the same one.
    body = src[src.index("def _has_measured_spend"):]
    body = body[:body.index("def pct(")]
    code = body.split('"""', 2)[-1]
    code = "\n".join(l for l in code.splitlines() if not l.strip().startswith("#"))
    assert "isinstance" not in code, \
        "the helper still decides realness from a Python type"


def test_a_simulated_cost_run_is_not_counted_as_measured():
    """Behavioural, against the shape the estate actually produces: numeric
    cost, simulated basis. This is the exact record that fooled the old proxy."""
    src = (ROOT / "eval/scorecard.py").read_text(encoding="utf-8")
    ns = {}
    body = src[src.index("def _has_measured_spend"):src.index("def pct(")]
    exec(compile(body, "scorecard_helper", "exec"), ns)
    fn = ns["_has_measured_spend"]

    fabricated = {"cost_usd": 0.25, "phases": [
        {"name": "critic", "spend": {"cost_usd": 0.05, "simulated": True}}]}
    real = {"cost_usd": 0.25, "phases": [
        {"name": "critic", "spend": {"cost_usd": 0.05, "simulated": False}}]}
    mixed = {"phases": [
        {"name": "critic", "spend": {"cost_usd": 0.05, "simulated": True}},
        {"name": "generate", "spend": {"cost_usd": 1.0, "simulated": False}}]}

    assert fn(fabricated) is False, \
        "a simulated cost still reads as measured — the original defect"
    assert fn(real) is True, \
        "a genuinely measured run stopped counting; the fix disabled the metric"
    assert fn(mixed) is True, "one real phase makes the run partly measured"
    assert fn({"phases": []}) is False and fn({}) is False

"""An UNMEASURED cache hit rate was reported as a measured 0% (C13).

`cost_report`'s by-phase table computes `cache_read / (input + cache_read)`.
On this estate every spend row carries `input_tokens: 0` and no `turns_used`
(measured: 1689 spend blocks, all of them), so `denom` is 0 -- and the report
printed `0%` and `0/0`, byte-identical to a phase that WAS measured and whose
prefix genuinely stopped being cached.

That distinction is the entire reason the column exists. Worse, story 4.2's
`budgets.min_cache_hit_rate` floor then flags every one of those phases
`(BELOW FLOOR)` and sends the operator after the two documented causes -- a
prefix-breaking prompt edit, or a model-tier change -- for a phase where no
token was ever counted. On this estate that is all 11 phases.

The honest answer already existed one command away: `make cache-probe` refuses
mock mode with exit 2, "Nothing was measured." `docs/efficiency-review.md` even
states it in prose ("the hit rate is unmeasured") while the report next door
rendered a number. Same shape as the coverage-gaps finding: two surfaces on one
question, one of them honest.

THE OTHER DIRECTION IS THE ONE THAT MATTERS MOST. A genuine 0% -- input tokens
observed, none of them cache reads -- is a real measurement and must still be
0%, and must still flag BELOW FLOOR. A fix that silenced the alarm entirely
would be worse than the defect.
"""
import json
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))


@pytest.fixture
def cr(tmp_path, monkeypatch):
    import cost_report
    monkeypatch.setattr(cost_report, "RUNS", tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    return cost_report


def _spend(**over):
    s = {"model": "claude-haiku", "cost_usd": 0.1, "input_tokens": 0,
         "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0,
         "max_turns": 8, "simulated": True}
    s.update(over)
    return s


def _run(runs, run_id, phases):
    rec = {"run_id": run_id, "trigger": {"type": "pr", "key": "PR-a-1"},
           "ts": time.time(), "overall": "committed", "gates": [],
           "phases": [{"name": n, "contract": {}, "spend": s} for n, s in phases]}
    (runs / f"{run_id}.json").write_text(json.dumps(rec), encoding="utf-8")


def _org(tmp_path, floor):
    """A ROOT whose org-config carries the 4.2 floor (to_markdown reads it)."""
    (tmp_path / "registry").mkdir(exist_ok=True)
    (tmp_path / "registry/org-config.yaml").write_text(
        f"budgets:\n  min_cache_hit_rate: {floor}\n", encoding="utf-8")
    return tmp_path


# ------------------------------------------------ nothing observed is not zero

def test_no_tokens_recorded_leaves_the_hit_rate_unmeasured(cr, tmp_path):
    """THE DEFECT: 0 tokens in and 0 cache reads is not a 0% hit rate."""
    _run(tmp_path / "runs", "r1", [("triage", _spend())])
    ph = cr.report()["by_phase"]["triage"]
    assert ph["cache_hit_rate"] is None, \
        "a phase nobody measured reported a measured 0% hit rate"


def test_no_turns_recorded_leaves_the_percentiles_unmeasured(cr, tmp_path):
    """`0/0` reads as "this phase runs no turns" -- which is what an operator
    acts on when deciding to cut its ceiling."""
    _run(tmp_path / "runs", "r1", [("triage", _spend())])
    ph = cr.report()["by_phase"]["triage"]
    assert ph["turns_p50"] is None and ph["turns_p95"] is None
    assert ph["suggested_max_turns"] == 8, \
        "with no turns observed the suggestion must stay the configured ceiling"


# -------------------------------------------- a genuine zero is still a zero

def test_a_measured_zero_hit_rate_is_still_zero(cr, tmp_path):
    """Input tokens were seen and none were cache reads. That IS 0%, it is the
    signal the column exists for, and the fix must not swallow it."""
    _run(tmp_path / "runs", "r1",
         [("triage", _spend(input_tokens=1000, cache_read_tokens=0))])
    assert cr.report()["by_phase"]["triage"]["cache_hit_rate"] == 0.0


def test_a_measured_rate_is_still_computed(cr, tmp_path):
    _run(tmp_path / "runs", "r1",
         [("triage", _spend(input_tokens=1000, cache_read_tokens=3000))])
    assert cr.report()["by_phase"]["triage"]["cache_hit_rate"] == 0.75


def test_measured_turns_are_still_percentiled(cr, tmp_path):
    for i, t in enumerate([3, 4, 4, 5, 6]):
        _run(tmp_path / "runs", f"r{i}",
             [("generate", _spend(turns_used=t, max_turns=25))])
    ph = cr.report()["by_phase"]["generate"]
    assert ph["turns_p95"] == 6 and ph["suggested_max_turns"] == 8


# ------------------------------------------------------------ the 4.2 alarm

def test_the_floor_does_not_fire_on_an_unmeasured_phase(cr, tmp_path,
                                                        monkeypatch):
    """The sharp end. This alarm names a prefix-breaking prompt edit as the
    cause; firing it where no token was counted sends the operator to a fix for
    a problem nobody has evidence of."""
    _run(tmp_path / "runs", "r1", [("triage", _spend())])
    monkeypatch.setattr(cr, "ROOT", _org(tmp_path, 0.5))
    md = cr.to_markdown(cr.report())
    assert "BELOW FLOOR" not in md, "an unmeasured phase was flagged"


def test_the_floor_still_fires_on_a_measured_shortfall(cr, tmp_path,
                                                       monkeypatch):
    """Without this the fix would be a feature that never fires again."""
    _run(tmp_path / "runs", "r1",
         [("triage", _spend(input_tokens=1000, cache_read_tokens=0))])
    monkeypatch.setattr(cr, "ROOT", _org(tmp_path, 0.5))
    assert "BELOW FLOOR" in cr.to_markdown(cr.report())


def test_a_measured_rate_above_the_floor_is_not_flagged(cr, tmp_path,
                                                        monkeypatch):
    _run(tmp_path / "runs", "r1",
         [("triage", _spend(input_tokens=1000, cache_read_tokens=3000))])
    monkeypatch.setattr(cr, "ROOT", _org(tmp_path, 0.5))
    assert "BELOW FLOOR" not in cr.to_markdown(cr.report())


# ----------------------------------------------------------- what is rendered

def test_the_table_says_n_a_and_names_the_fix(cr, tmp_path, monkeypatch):
    _run(tmp_path / "runs", "r1", [("triage", _spend())])
    monkeypatch.setattr(cr, "ROOT", _org(tmp_path, 0))
    md = cr.to_markdown(cr.report())
    row = next(l for l in md.splitlines() if l.startswith("triage |"))
    assert "0%" not in row, "an unmeasured phase still rendered a percentage"
    assert row.count("n/a") == 2, "both the rate and the turns must say n/a"
    note = next(l for l in md.splitlines() if "UNMEASURED" in l)
    assert "cache-probe" in note, "the note does not name the fix"
    assert "triage" in note, "the note does not name the phase"


def test_the_note_is_absent_when_everything_was_measured(cr, tmp_path,
                                                         monkeypatch):
    """A caveat printed on a healthy estate is one operators learn to skip."""
    _run(tmp_path / "runs", "r1",
         [("triage", _spend(input_tokens=1000, cache_read_tokens=3000,
                            turns_used=2))])
    monkeypatch.setattr(cr, "ROOT", _org(tmp_path, 0))
    md = cr.to_markdown(cr.report())
    assert "UNMEASURED" not in md
    row = next(l for l in md.splitlines() if l.startswith("triage |"))
    assert "75%" in row and "2/2" in row


# --------------------------------------------------- the sibling: the Cost view

def test_the_dashboard_renders_null_as_unmeasured_not_zero():
    """`Math.round(null * 100)` is 0, so the Cost view printed 0% from the same
    payload -- and unlike the markdown it carries no BELOW FLOOR flag to hint
    that anything is off. The null branch must come BEFORE the arithmetic.
    """
    js = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    i = js.index("#cost-phase-table")
    block = js[i:i + 1600]
    for field in ("cache_hit_rate", "turns_p50"):
        guard = block.index(f"v.{field} === null")
        assert guard >= 0
    assert block.index("v.cache_hit_rate === null") < \
        block.index("Math.round(v.cache_hit_rate"), \
        "the arithmetic runs before the null check"
    assert block.count("n/a") == 2, "both cells need the unmeasured rendering"

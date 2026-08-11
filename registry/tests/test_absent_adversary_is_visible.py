"""A plan the adversary never reviewed must not look like one it approved.

`plan_adversary.summary()` returned `""` for THREE different situations:

  * the feature is disabled (org-config plan_adversary.enabled, or
    AIQE_PLAN_ADVERSARY),
  * the phase failed -- non-fatal by design, "the authored plan stands",
  * the plan had zero scenarios and the phase was skipped.

`bin/dashboard.py` then hides the panel on a falsy value
(`adv.classList.toggle('hidden', !p.adversary)`), which is the same
display:none-on-an-empty-answer shape this repo already recorded for the
explain panel. So a plan nobody challenged rendered exactly like one the
adversary had cleared.

That matters because of WHEN this runs. The adversary is deliberately placed
BEFORE the human approval gate so it changes what the reviewer approves -- so
whether it ran is part of the decision the reviewer is making.

Worse, `pipeline.sh` computed the line INSIDE the `elif ... enabled` arm, so a
disabled adversary never even reached `record_plan`. Driven before the fix: a
`plan` run with AIQE_PLAN_ADVERSARY=0 recorded an empty adversary field.
"""
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine" / "lib"))
import plan_adversary


def _summary(tmp_path, gaps=None, arbiter=None):
    g = tmp_path / "gaps.json"
    a = tmp_path / "arb.json"
    if gaps is not None:
        g.write_text(gaps, encoding="utf-8")
    if arbiter is not None:
        a.write_text(arbiter, encoding="utf-8")
    return plan_adversary.summary(str(g), str(a))


def test_a_disabled_adversary_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("AIQE_PLAN_ADVERSARY", "0")
    line = _summary(tmp_path)
    assert "DISABLED" in line
    assert "NOT challenged" in line
    # BOTH knobs, not either. The first version used `or`, and a mutation
    # dropping one name survived: there are two ways to disable this (org-config
    # and the env var), so naming only one sends the operator whose estate uses
    # the other to the wrong place.
    assert "AIQE_PLAN_ADVERSARY" in line, f"env knob unnamed: {line}"
    assert "plan_adversary.enabled" in line, f"config knob unnamed: {line}"


def test_an_enabled_adversary_that_produced_nothing_says_that_instead(
        tmp_path, monkeypatch):
    """Different from disabled, and the actions differ: one is a configuration
    choice to revisit, the other is a phase to investigate in the run log."""
    monkeypatch.setenv("AIQE_PLAN_ADVERSARY", "1")
    line = _summary(tmp_path)
    assert "did not run" in line
    assert "DISABLED" not in line
    assert "NOT challenged" in line


def test_neither_is_empty_so_the_panel_cannot_hide_it(tmp_path, monkeypatch):
    """bin/dashboard.py hides the element on a falsy summary. A non-empty
    string is what makes the absence visible at all."""
    for value in ("0", "1"):
        monkeypatch.setenv("AIQE_PLAN_ADVERSARY", value)
        assert _summary(tmp_path).strip(), \
            f"AIQE_PLAN_ADVERSARY={value} still yields a hidden panel"


def test_a_real_review_that_found_nothing_is_still_distinct(tmp_path, monkeypatch):
    """The honest 'ran and found nothing' case must NOT be reworded into the
    not-run wording -- that would trade one confusion for its mirror image."""
    monkeypatch.setenv("AIQE_PLAN_ADVERSARY", "1")
    line = _summary(tmp_path, gaps='{"gaps": []}')
    assert "no gaps found" in line
    assert "NOT challenged" not in line and "did not run" not in line


def test_a_review_with_findings_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("AIQE_PLAN_ADVERSARY", "1")
    line = _summary(
        tmp_path,
        gaps='{"gaps": [{"title": "no authz case", "severity": "high"}]}',
        arbiter='{"accepted_gaps": 1, "rejected_gaps": 0, "scenarios": [1, 2]}')
    assert "1 gap(s) raised" in line and "1 high-severity" in line
    assert "1 accepted" in line


def test_the_pipeline_computes_the_line_outside_the_enabled_branch():
    """The line used to be assigned INSIDE `elif ... enabled`, so a disabled
    adversary never reached record_plan at all. Indentation is the tell: it has
    to sit at the same level as the `if`, not inside an arm."""
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    line = next(l for l in src.splitlines()
                if l.strip().startswith("ADVERSARY_LINE=$("))
    indent = len(line) - len(line.lstrip())
    assert indent == 2, \
        f"ADVERSARY_LINE is nested {indent} deep; a disabled adversary will not record"


def test_a_disabled_adversary_reaches_the_recorded_plan(tmp_path, monkeypatch):
    """End to end, because everything above this line reads source or calls one
    function. Runs the real `plan` pipeline in mock mode with the adversary
    off and asserts the plan STATE carries the notice -- that record is what
    the plan editor, the ticket comment and the wizard all read."""
    import work_queue
    import plan_state
    plans = tmp_path / "plans"
    plans.mkdir()
    env = {**os.environ, "AIQE_MOCK": "1", "AIQE_PLAN_ADVERSARY": "0",
           "AIQE_PLAN_DIR": str(plans)}
    r = subprocess.run(
        [work_queue.bash_exe(), "engine/pipeline.sh", "plan", "PROJ-301"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), stdin=subprocess.DEVNULL, timeout=900, env=env)
    assert r.returncode == 0, r.stdout[-800:]
    monkeypatch.setattr(plan_state, "DIR", plans)
    monkeypatch.setattr(plan_state, "FILE", plans / "state.json")
    entry = plan_state.load().get("PROJ-301", {})
    assert "DISABLED" in (entry.get("adversary") or ""), (
        "a plan produced with the adversary off carries no trace of it: "
        f"{entry.get('adversary')!r}")

"""The journey ladder must not show a later step done while its gate is blocked.

`wizard_status` read `tests` from the LATEST run record for the key. On a
plan-first ticket that is the wrong source: a run from before the plan was
authored (or re-authored) made "Generate E2E tests" read `done` while
`plan_state.generated_run` was still None — so the ladder showed

    Human approval      blocked
    Generate E2E tests  done

which reads as the approval gate having been bypassed, and tells a reviewer
that tests exist for a plan nothing has generated from.

Every other surface already answers from `generated_run`: spec_workflow,
agent_context, the Test plans table, `qa.py plans`. This view was the last one
inferring — the same defect class CLAUDE.md records for spec_workflow
("`generated_run`, NOT `generated`, a key nothing writes"), fixed in one view
and left in the other.

Found by walking the plan workflow across surfaces and noticing they disagreed.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import wizard_status  # noqa: E402


def _steps(monkeypatch, plan, runs, queue=()):
    monkeypatch.setattr(wizard_status, "_runs_for", lambda k: list(runs))
    monkeypatch.setattr(wizard_status, "_queue_for", lambda k: list(queue))
    import plan_state, review_state
    monkeypatch.setattr(plan_state, "get", lambda k: dict(plan))
    monkeypatch.setattr(review_state, "load", lambda: {})
    return {s["label"]: s for s in wizard_status.build("PROJ-X", "jira")["steps"]}


OLD_RUN = {"run_id": "old-1", "ts": 1,
           "phases": [{"name": "generate",
                       "contract": {"tests": [{"action": "created", "file": "a.spec.js"}]}}],
           "gates": [{"test_repo": "e2e-api", "status": "committed", "exit_code": 0}]}


def test_a_run_predating_the_plan_does_not_mark_generation_done(monkeypatch):
    """The bug, exactly: a draft plan awaiting approval, and an older run in the
    key's history."""
    steps = _steps(monkeypatch, {"status": "draft", "generated_run": None}, [OLD_RUN])
    assert steps["Generate E2E tests"]["state"] != "done", \
        "an old run marked this plan's generation complete"
    assert steps["Human approval"]["state"] == "blocked"


def test_the_gate_step_follows_the_same_run(monkeypatch):
    """Clearing only `tests` moved the lie one step down: "Generate: pending"
    above "Quality gate: done" reads as tests committed without being
    generated. Both steps describe ONE run, so they answer from one."""
    steps = _steps(monkeypatch, {"status": "draft", "generated_run": None}, [OLD_RUN])
    assert steps["Quality gate"]["state"] != "done", \
        "the gate reported a commit from a run this plan never produced"


def test_generation_reads_done_once_the_plan_actually_produced_it(monkeypatch):
    """The guard must not be so strict that a real resume never completes."""
    run = dict(OLD_RUN, run_id="new-1")
    steps = _steps(monkeypatch, {"status": "approved", "generated_run": "new-1"}, [run])
    assert steps["Generate E2E tests"]["state"] == "done"
    assert "1 created" in steps["Generate E2E tests"]["detail"]
    assert steps["Quality gate"]["state"] == "done"


def test_counts_come_from_the_generating_run_not_the_newest(monkeypatch):
    """With several runs on a key, the numbers shown must belong to the run the
    plan names — otherwise the ladder reports another run's work as this one's."""
    theirs = {"run_id": "new-1", "ts": 5,
              "phases": [{"name": "generate", "contract": {"tests": [
                  {"action": "created"}, {"action": "updated"}]}}],
              "gates": [{"test_repo": "e2e-api", "status": "committed"}]}
    newer = {"run_id": "unrelated-9", "ts": 9,
             "phases": [{"name": "generate", "contract": {"tests": [
                 {"action": "created"} for _ in range(7)]}}],
             "gates": []}
    steps = _steps(monkeypatch, {"status": "approved", "generated_run": "new-1"},
                   [theirs, newer])
    d = steps["Generate E2E tests"]["detail"]
    assert "1 created" in d and "1 updated" in d, f"took the wrong run's counts: {d}"
    # ...and the GATE too. Asserting only the counts left a mutation alive:
    # with one run in the fixture, `runs[-1]` IS the named run, so dropping the
    # gate reassignment changed nothing. The newer run below carries NO gates,
    # so a gate reading `done` here can only have come from the named one.
    g = steps["Quality gate"]
    assert g["state"] == "done", f"gate did not follow the named run: {g}"
    assert "e2e-api" in g["detail"], f"gate detail came from elsewhere: {g['detail']}"


def test_pr_keys_keep_the_run_record_inference(monkeypatch):
    """A PR has no plan, so the run record is the only source there — and the
    correct one. The fix must not reach it."""
    monkeypatch.setattr(wizard_status, "_runs_for", lambda k: [OLD_RUN])
    monkeypatch.setattr(wizard_status, "_queue_for", lambda k: [])
    import review_state
    monkeypatch.setattr(review_state, "load", lambda: {})
    steps = {s["label"]: s for s in
             wizard_status.build("PR-orders-api-201", "pr")["steps"]}
    assert steps["Generate E2E tests"]["state"] == "done"
    assert steps["Quality gate"]["state"] == "done"

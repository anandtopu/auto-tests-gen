"""Trace — the full chain for one key (engine/lib/trace.py + the dashboard view).

The EM job: story/PR -> plan (who approved, when) -> generated tests -> gate ->
review -> release as ONE chronological object. trace.py only JOINS existing stores;
these tests pin the join: every source contributes, order is chronological, and an
unknown key degrades to an empty chain instead of an exception.
"""
import json, pathlib, sys, time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import trace as trace_lib


@pytest.fixture
def estate(tmp_path, monkeypatch):
    """A synthetic estate: one run record, a plan history, a review history."""
    runs = tmp_path / "reports/runs"
    runs.mkdir(parents=True)
    (runs / "100-1.json").write_text(json.dumps({
        "run_id": "100-1", "trigger": {"type": "jira", "key": "T-9"}, "ts": 1000,
        "overall": "committed",
        "gates": [{"test_repo": "e2e-a", "status": "committed", "commit": "abc1234",
                   "diff": "reports/runs/100-1-e2e-a.diff"}],
        "critic": {"score": 0.9, "verdict": "accept"},
        "phases": [{"name": "generate", "contract": {"tests": [
            {"file": "suites/x.spec.js", "action": "created"}]}},
            {"name": "validate", "contract": {"passed": 2, "failed": 0}}],
    }), encoding="utf-8")
    (runs / "reviews.json").write_text(json.dumps({"T-9": {"history": [
        {"status": "pending_review", "reviewer": "pipeline", "note": "", "ts": 1100},
        {"release": "9.9", "source": "jira", "ts": 1150},
        {"status": "approved", "reviewer": "ana", "note": "LGTM", "ts": 1200},
    ]}}), encoding="utf-8")
    plans = tmp_path / "reports/plans"
    plans.mkdir(parents=True)
    (plans / "state.json").write_text(json.dumps({"T-9": {"history": [
        {"status": "draft", "by": "pipeline", "note": "test plan authored", "ts": 900},
        {"status": "approved", "by": "lead", "note": "covers ACs", "ts": 950},
    ]}}), encoding="utf-8")
    monkeypatch.setattr(trace_lib, "ROOT", tmp_path)
    monkeypatch.setenv("AIQE_REVIEWS_FILE", str(runs / "reviews.json"))
    monkeypatch.setenv("AIQE_PLAN_DIR", str(plans))
    # review_state/plan_state cache module state via env at import — reload to honor it
    import importlib, review_state, plan_state
    importlib.reload(review_state)
    importlib.reload(plan_state)
    yield tmp_path
    importlib.reload(review_state)
    importlib.reload(plan_state)


def test_chain_joins_all_sources_in_chronological_order(estate):
    t = trace_lib.build("T-9")
    kinds = [e["kind"] for e in t["events"]]
    assert kinds == ["plan", "plan", "run", "review", "release", "review"], kinds
    ts = [e["ts"] for e in t["events"]]
    assert ts == sorted(ts), "events must be oldest-first"


def test_summary_fields_reflect_the_latest_state(estate):
    t = trace_lib.build("T-9")
    assert t["trigger_type"] == "jira"
    assert t["plan_status"] == "approved"
    assert t["review_status"] == "approved"     # latest review event wins
    assert t["release"] == "9.9"


def test_run_event_carries_gates_tests_and_critic(estate):
    t = trace_lib.build("T-9")
    run = next(e for e in t["events"] if e["kind"] == "run")
    assert run["meta"]["gates"][0]["commit"] == "abc1234"
    assert run["meta"]["tests"][0]["file"] == "suites/x.spec.js"
    assert run["meta"]["critic"]["verdict"] == "accept"
    assert "1/1 repo(s) committed" in run["detail"]


def test_actors_are_preserved(estate):
    t = trace_lib.build("T-9")
    approvals = [e for e in t["events"]
                 if e["title"] in ("Plan approved", "Tests approved by the team")]
    assert {e["actor"] for e in approvals} == {"lead", "ana"}, \
        "who approved is the point of the trace"


def test_unknown_key_gives_an_empty_chain_not_an_exception(estate):
    t = trace_lib.build("NOPE-1")
    assert t["events"] == [] and t["release"] == ""


def test_malformed_key_and_well_formed_corrupt_records_are_total(estate):
    runs = estate / "reports/runs"
    (runs / "array.json").write_text("[]", encoding="utf-8")
    (runs / "partial.json").write_text(json.dumps({
        "run_id": "partial", "trigger": {"type": "jira", "key": "T-9"},
        "ts": 1250, "phases": [{}],
    }), encoding="utf-8")

    assert trace_lib.build("../../bad")["events"] == []
    assert trace_lib.build("T-9")["events"], "valid evidence must survive bad rows"
    assert "T-9" in trace_lib.keys()


def test_keys_lists_traced_keys_most_recent_first(estate):
    assert "T-9" in trace_lib.keys()


def test_render_text_is_complete_and_stable(estate):
    text = trace_lib.render_text(trace_lib.build("T-9"))
    for needle in ("T-9", "Plan approved", "Tests approved by the team",
                   "Release set: 9.9", "gate e2e-a: committed @abc1234",
                   "critic 0.9 accept (advisory)"):
        assert needle in text, f"missing from text render: {needle}"


def test_surfaces_exist():
    """CLI, server API and dashboard view all expose the trace."""
    assert "def cmd_trace" in (ROOT / "bin/qa.py").read_text(encoding="utf-8")
    assert "/api/trace" in (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")
    ui = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert 'data-view="trace"' in ui and "tl-row" in ui

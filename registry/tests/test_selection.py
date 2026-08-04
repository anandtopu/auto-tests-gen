"""Selective review: approve SOME of what the AI produced.

Approval was all-or-nothing. A reviewer who liked nine scenarios out of ten
could accept the tenth or reject the batch, and neither is what they meant.

The property that matters most is the honest one. The gate commits: once a
generated test is pushed, un-ticking a box cannot remove it from the test repo.
Claiming otherwise would be the worst lie this product could tell — the
reviewer believes a test is gone and it runs in CI that night. So an excluded
test that was already committed is reported as `already_committed` with the
follow-up spelled out, and the artifact never claims it is absent.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import selection  # noqa: E402


SPEC = {"key": "ZZSEL-1", "scenarios": [
    {"id": "ZZSEL-1-S1", "title": "one", "layer": "api", "target_repo": "e2e-api"},
    {"id": "ZZSEL-1-S2", "title": "two", "layer": "api", "target_repo": "e2e-api"},
    {"id": "ZZSEL-1-S3", "title": "three", "layer": "ui", "target_repo": "e2e-ui"}]}

REC = {"run_id": "sel-1", "ts": 1, "overall": "committed",
       "trigger": {"type": "jira", "key": "ZZSEL-1"},
       "phases": [{"name": "generate", "contract": {"tests": [
           {"file": "suites/a.spec.js", "action": "created", "repo": "e2e-api",
            "scenario_id": "ZZSEL-1-S1"},
           {"file": "suites/b.spec.js", "action": "created", "repo": "e2e-api",
            "scenario_id": "ZZSEL-1-S2"}]}}],
       "gates": [{"test_repo": "e2e-api", "status": "committed", "exit_code": 0}]}


@pytest.fixture
def estate(tmp_path, monkeypatch):
    (tmp_path / "reports/plans").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports/runs").mkdir(parents=True, exist_ok=True)
    (tmp_path / f"reports/runs/{REC['run_id']}.json").write_text(
        json.dumps(REC), encoding="utf-8")
    monkeypatch.setattr(selection, "FILE", tmp_path / "reports/plans/selection.json")
    import spec_store
    monkeypatch.setattr(spec_store, "load", lambda k: SPEC if k == "ZZSEL-1" else {})
    return tmp_path


def test_an_item_nobody_ruled_on_is_included(estate):
    """Not deciding is not rejecting. Defaulting to excluded would make an
    untouched plan finalize to nothing."""
    st = selection.status("ZZSEL-1", root=estate)
    assert len(st["scenarios"]) == 3
    assert all(s["included"] for s in st["scenarios"])
    assert all(not s["decided"] for s in st["scenarios"])


def test_excluding_a_scenario_records_who_and_why(estate):
    selection.set_items("ZZSEL-1", "scenarios", {"ZZSEL-1-S3": False},
                        actor="qa-lead", reason="covered elsewhere", root=estate)
    st = selection.status("ZZSEL-1", root=estate)
    s3 = next(s for s in st["scenarios"] if s["id"] == "ZZSEL-1-S3")
    assert not s3["included"] and s3["decided"]
    assert s3["by"] == "qa-lead" and s3["reason"] == "covered elsewhere"
    # untouched neighbours are unaffected
    assert all(s["included"] for s in st["scenarios"] if s["id"] != "ZZSEL-1-S3")


def test_a_partial_update_does_not_revert_other_decisions(estate):
    """A stale UI posting its whole view would otherwise silently undo somebody
    else's exclusion."""
    selection.set_items("ZZSEL-1", "scenarios", {"ZZSEL-1-S1": False},
                        actor="a", root=estate)
    selection.set_items("ZZSEL-1", "scenarios", {"ZZSEL-1-S2": False},
                        actor="b", root=estate)
    st = selection.status("ZZSEL-1", root=estate)
    excluded = {s["id"] for s in st["scenarios"] if not s["included"]}
    assert excluded == {"ZZSEL-1-S1", "ZZSEL-1-S2"}


def test_finalize_emits_only_the_surviving_scenarios(estate):
    selection.set_items("ZZSEL-1", "scenarios", {"ZZSEL-1-S3": False},
                        actor="qa-lead", reason="dupe", root=estate)
    m = selection.finalize("ZZSEL-1", actor="qa-lead", root=estate)
    assert m["scenarios"]["approved"] == ["ZZSEL-1-S1", "ZZSEL-1-S2"]
    assert m["scenarios"]["excluded"][0]["id"] == "ZZSEL-1-S3"
    assert m["scenarios"]["excluded"][0]["reason"] == "dupe"
    assert (estate / "reports/approved/ZZSEL-1/manifest.json").exists()


def test_excluding_a_committed_test_does_not_claim_it_is_gone(estate):
    """The property this feature lives or dies on."""
    selection.set_items("ZZSEL-1", "tests", {"suites/a.spec.js": False},
                        actor="qa-lead", reason="duplicate", root=estate)
    st = selection.status("ZZSEL-1", root=estate)
    a = next(t for t in st["tests"] if t["file"] == "suites/a.spec.js")
    assert a["already_committed"] is True
    assert a["follow_up"] and "does NOT remove it" in a["follow_up"]

    m = selection.finalize("ZZSEL-1", actor="qa-lead", root=estate)
    assert m["needs_follow_up"] == ["suites/a.spec.js"], \
        "an exclusion the artifact cannot deliver must be named, not buried"


def test_an_uncommitted_test_carries_no_follow_up(estate):
    """The flag must mean something — if every exclusion needed follow-up the
    reader would stop reading it."""
    rec = json.loads(json.dumps(REC))
    rec["gates"] = [{"test_repo": "e2e-api", "status": "no_changes", "exit_code": 0}]
    (estate / f"reports/runs/{rec['run_id']}.json").write_text(
        json.dumps(rec), encoding="utf-8")
    selection.set_items("ZZSEL-1", "tests", {"suites/a.spec.js": False},
                        actor="qa-lead", root=estate)
    st = selection.status("ZZSEL-1", root=estate)
    a = next(t for t in st["tests"] if t["file"] == "suites/a.spec.js")
    assert a["already_committed"] is False and a["follow_up"] is None


def test_finalizing_with_everything_excluded_is_refused(estate):
    """An empty approved plan reads downstream as 'this ticket needs no tests'
    — a rejection wearing the wrong label."""
    selection.set_items("ZZSEL-1", "scenarios",
                        {s["id"]: False for s in SPEC["scenarios"]},
                        actor="qa-lead", root=estate)
    with pytest.raises(SystemExit) as e:
        selection.finalize("ZZSEL-1", actor="qa-lead", root=estate)
    assert "nothing to approve" in str(e.value)
    assert "reject the plan instead" in str(e.value), "say what to do instead"


def test_finalizing_a_key_with_no_material_is_refused(estate, monkeypatch):
    import spec_store
    monkeypatch.setattr(spec_store, "load", lambda k: {})
    with pytest.raises(SystemExit) as e:
        selection.finalize("ZZNOTHING-9", actor="x", root=estate)
    assert "nothing to finalize" in str(e.value)
    assert "make plan" in str(e.value), "name the command that produces material"


def test_the_authored_spec_is_never_rewritten(estate):
    """specs/<KEY> stays the record of what was PROPOSED. Losing it would
    destroy the ability to ask what the reviewer turned down."""
    src = (ROOT / "engine/lib/selection.py").read_text(encoding="utf-8")
    body = src[src.index("def finalize("):]
    for writer in ("spec_store.write_from_contract", "spec_path(", "yaml.safe_dump"):
        assert writer not in body, f"finalize writes the spec via {writer}"


def test_finalize_reuses_the_spec_renderer(estate):
    """Formatting one plan two ways is how a rendering and its spec drift."""
    src = (ROOT / "engine/lib/selection.py").read_text(encoding="utf-8")
    body = src[src.index("def finalize("):]
    assert "spec_store.render(" in body, "the approved plan is rendered some other way"


def test_the_finalized_stamp_records_the_follow_up_count(estate):
    selection.set_items("ZZSEL-1", "tests", {"suites/a.spec.js": False},
                        actor="qa-lead", root=estate)
    selection.finalize("ZZSEL-1", actor="qa-lead", root=estate)
    st = selection.status("ZZSEL-1", root=estate)
    assert st["finalized"]["needs_follow_up"] == 1
    assert st["finalized"]["by"] == "qa-lead"

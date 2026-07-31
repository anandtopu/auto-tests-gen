"""Spec store pins (SDD stories 1.1, 1.2, 1.3).

The contracts: a structured plan gets a schema-valid spec whose rendering IS
the reviewer's markdown; approval signs the spec's hash; the arbiter fold can
never strip the author's structure; a free-form edit supersedes the spec
visibly; and legacy free-form plans behave byte-for-byte as before.
"""
import json
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import spec_store as ss  # noqa: E402


LEGACY = {"scenarios": [{"id": "K-1-S1", "title": "t", "layer": "api",
                         "target_repo": "r", "behavior_ref": "B1",
                         "data_needs": "d1"}],
          "open_questions": []}
STRUCTURED = {"scenarios": [
    {"id": "K-1-S1", "title": "boundary", "layer": "api", "target_repo": "r",
     "requirement_refs": ["R1"],
     "steps": {"given": "an order exists", "when": "a 91% discount is POSTed",
               "then": "422 and total unchanged"},
     "verification": ["status is 422", "total unchanged"]},
    {"id": "K-1-S2", "title": "arbiter-added", "layer": "api",
     "target_repo": "r"}],
    "open_questions": ["q1"]}


@pytest.fixture
def store(tmp_path, monkeypatch):
    import plan_state as ps
    monkeypatch.setattr(ss, "SPEC_DIR", tmp_path / "specs")
    monkeypatch.setattr(ps, "DIR", tmp_path / "plans")
    monkeypatch.setattr(ps, "FILE", tmp_path / "plans/state.json")
    monkeypatch.setattr(ps, "PLAN_DIR", tmp_path / "testplans")
    (tmp_path / "plans").mkdir()
    (tmp_path / "testplans").mkdir()
    return ss, ps, tmp_path


def test_structured_detection():
    assert not ss.is_structured(LEGACY)
    assert ss.is_structured(STRUCTURED)
    assert not ss.is_structured({"scenarios": []})


def test_legacy_contract_writes_no_spec_and_md_stands(store):
    """The back-compat pin: SDD is adoptable per ticket, not a migration
    cliff — a free-form plan is untouched by the spec layer."""
    s, ps, tmp = store
    ps.plan_path("K-1").write_text("# original free-form\n", encoding="utf-8")
    ps.record_plan("K-1", LEGACY)
    assert not s.spec_path("K-1").exists()
    assert ps.plan_path("K-1").read_text(encoding="utf-8") == \
        "# original free-form\n", "the phase's markdown must stand unchanged"
    assert "spec_sha" not in ps.set_status("K-1", "approved", "a")


def test_structured_contract_writes_spec_and_renders_md(store):
    s, ps, tmp = store
    ps.plan_path("K-1").write_text("# phase-written md\n", encoding="utf-8")
    ps.record_plan("K-1", STRUCTURED)
    spec = s.load("K-1")
    assert spec and spec["key"] == "K-1" and len(spec["scenarios"]) == 2
    md = ps.plan_path("K-1").read_text(encoding="utf-8")
    assert "Rendered from" in md, "markdown is a rendering, not a source"
    assert "**Given** an order exists" in md
    assert "verify: status is 422" in md


def test_render_is_deterministic(store):
    s, ps, tmp = store
    ps.record_plan("K-1", STRUCTURED)
    assert s.render("K-1") == s.render("K-1")


def test_validate_catches_shape_problems():
    assert ss.validate({"key": "K", "scenarios": []})
    bad = {"key": "K", "scenarios": [
        {"id": "S1", "title": "t", "layer": "api", "target_repo": "r"},
        {"id": "S1", "title": "dup", "layer": "api", "target_repo": "r"}]}
    assert any("duplicate" in p for p in ss.validate(bad))
    bad2 = {"key": "K", "scenarios": [
        {"id": "S1", "title": "t", "layer": "api", "target_repo": "r",
         "verification": "not-a-list"}]}
    assert any("verification" in p for p in ss.validate(bad2))


def test_merge_fold_preserves_authors_structure(tmp_path):
    """A re-emitting arbiter that drops steps/verification must not demote a
    structured spec — matching ids inherit; added scenarios stay as-is."""
    orig = tmp_path / "testplan.contract.json"
    folded = tmp_path / "arbiter.contract.json"
    orig.write_text(json.dumps(STRUCTURED), encoding="utf-8")
    dropped = {"scenarios": [
        {"id": "K-1-S1", "title": "boundary", "layer": "api",
         "target_repo": "r"},                       # structure DROPPED
        {"id": "K-1-S3", "title": "authz gap", "layer": "api",
         "target_repo": "r"}],                      # arbiter-added
        "open_questions": []}
    folded.write_text(json.dumps(dropped), encoding="utf-8")
    ss.merge_fold(orig, folded)
    merged = json.loads(orig.read_text(encoding="utf-8"))
    s1 = next(s for s in merged["scenarios"] if s["id"] == "K-1-S1")
    assert s1["steps"]["when"] == "a 91% discount is POSTed"
    assert s1["verification"] == ["status is 422", "total unchanged"]
    s3 = next(s for s in merged["scenarios"] if s["id"] == "K-1-S3")
    assert "steps" not in s3, "added scenarios are not fabricated structure"


def test_approval_signs_the_spec(store):
    s, ps, tmp = store
    ps.record_plan("K-1", STRUCTURED)
    e = ps.set_status("K-1", "approved", "alice")
    assert e["spec_sha"] == s.sha("K-1") and len(e["spec_sha"]) == 64
    assert e["history"][-1]["spec_sha"] == e["spec_sha"]
    versions = sorted(p.name for p in ps.versions_dir("K-1").glob("*"))
    assert any(v.endswith("-approved.yaml") for v in versions), \
        "the signed structure snapshots beside the markdown"


def test_scenario_level_diff_leads_reapproval(store):
    s, ps, tmp = store
    ps.record_plan("K-1", STRUCTURED)
    ps.set_status("K-1", "approved", "alice")
    changed = json.loads(json.dumps(STRUCTURED))
    changed["scenarios"][0]["verification"].append("audit log written")
    changed["scenarios"].append({"id": "K-1-S9", "title": "new edge",
                                 "layer": "api", "target_repo": "r",
                                 "verification": ["x"]})
    ps.record_plan("K-1", changed)                  # re-author -> new spec + md
    d = ps.diff_since_approval("K-1")
    assert "Scenario-level changes" in d
    assert "ADDED    K-1-S9" in d
    assert "CHANGED  K-1-S1: verification" in d


def test_freeform_edit_supersedes_the_spec(store):
    """One source of truth, enforced: prose the human wrote wins, and the spec
    is set aside VISIBLY (timestamped), never silently contradicted."""
    s, ps, tmp = store
    ps.record_plan("K-1", STRUCTURED)
    assert s.spec_path("K-1").exists()
    ps.save_plan("K-1", "# my hand-written replacement plan\n", by="bob")
    assert not s.spec_path("K-1").exists()
    leftovers = list(s.spec_path("K-1").parent.glob("*.superseded-*"))
    assert leftovers, "the superseded spec is kept for forensics"
    assert s.sha("K-1") == "" and s.load("K-1") is None


def test_saving_the_rendering_back_keeps_the_spec(store):
    """An edit that DOESN'T diverge (the editor round-tripping the rendered
    text) must not demote the plan."""
    s, ps, tmp = store
    ps.record_plan("K-1", STRUCTURED)
    ps.save_plan("K-1", s.render("K-1"), by="bob")
    assert s.spec_path("K-1").exists()


def test_spec_mode_kill_switch(store, monkeypatch):
    s, ps, tmp = store
    monkeypatch.setenv("AIQE_SPEC_MODE", "0")
    ps.plan_path("K-1").write_text("# phase md\n", encoding="utf-8")
    ps.record_plan("K-1", STRUCTURED)
    assert not s.spec_path("K-1").exists()
    assert ps.plan_path("K-1").read_text(encoding="utf-8") == "# phase md\n"

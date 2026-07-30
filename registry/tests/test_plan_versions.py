"""Plan versioning + diff-since-approval (roadmap 4.2), knowledge bundle (6.4).

4.2 pins: approval snapshots the exact signed text; an edit snapshots the pre-edit
text; the re-approver's diff compares CURRENT vs LAST-APPROVED and is empty when
nothing changed; versions are bounded.

6.4 pins: the knowledge profile carries guidance/catalog/testplans and NEVER run
history, review decisions, plan lifecycle or the registry topology.
"""
import json
import pathlib
import sys
import tarfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))


@pytest.fixture
def plans(tmp_path, monkeypatch):
    import plan_state
    monkeypatch.setattr(plan_state, "DIR", tmp_path / "plans")
    monkeypatch.setattr(plan_state, "FILE", tmp_path / "plans/state.json")
    monkeypatch.setattr(plan_state, "PLAN_DIR", tmp_path / "testplans")
    (tmp_path / "testplans").mkdir()
    (tmp_path / "plans").mkdir()
    return plan_state


def test_approval_freezes_the_signed_text_and_diff_shows_the_delta(plans, tmp_path):
    ps = plans
    (tmp_path / "testplans/K-1.md").write_text("# Plan\nS1 boundary\n",
                                               encoding="utf-8")
    ps.record_plan("K-1", {"scenarios": []})
    ps.set_status("K-1", "approved", "alice")
    assert ps.approved_baseline("K-1") is not None
    assert ps.diff_since_approval("K-1") == "", "unchanged plan -> empty diff"

    ps.save_plan("K-1", "# Plan\nS1 boundary\nS2 authz added\n", by="bob")
    assert ps.get("K-1")["status"] == "draft", "edit revokes approval (unchanged)"
    d = ps.diff_since_approval("K-1")
    assert "+S2 authz added" in d and "(as approved)" in d, \
        "the re-approver sees exactly the delta against the signed text"

    # Re-approval moves the baseline; diff clears.
    ps.set_status("K-1", "approved", "alice")
    assert ps.diff_since_approval("K-1") == ""


def test_no_baseline_means_no_diff_not_an_error(plans, tmp_path):
    ps = plans
    (tmp_path / "testplans/K-2.md").write_text("# Plan\n", encoding="utf-8")
    ps.record_plan("K-2", {"scenarios": []})
    assert ps.diff_since_approval("K-2") == ""
    assert ps.diff_since_approval("NEVER-EXISTED") == ""


def test_versions_are_bounded(plans, tmp_path):
    ps = plans
    (tmp_path / "testplans/K-3.md").write_text("v0\n", encoding="utf-8")
    ps.record_plan("K-3", {"scenarios": []})
    for i in range(25):
        ps.save_plan("K-3", f"edit {i}\n", by="x")
    kept = list(ps.versions_dir("K-3").glob("v*.md"))
    assert len(kept) <= 20, "snapshots must not grow without bound"


def test_knowledge_bundle_carries_wisdom_not_records(tmp_path):
    import state_bundle as sb
    out = sb.export(tmp_path / "k.tar.gz", profile="knowledge")
    with tarfile.open(out, "r:gz") as tar:
        names = [m.name[len("state/"):] for m in tar.getmembers()
                 if m.isfile() and m.name.startswith("state/")]
        man = json.loads(tar.extractfile("manifest.json").read().decode("utf-8"))
    assert man["profile"] == "knowledge"
    assert any(n.startswith("catalog/") for n in names)
    assert any(n.startswith("testplans/") for n in names), \
        "plan markdown seeds the receiving team's similarity corpus"
    # The donor's RECORDS stay home.
    assert not [n for n in names if n.startswith("reports/")]
    assert "registry/repo-registry.yaml" not in names, \
        "the receiving team's estate topology is their own"
    assert "registry/org-config.yaml" in names, "phase policy IS transferable wisdom"

"""Spec drift pins (SDD stories 4.1, 4.2).

Contracts: a scenario referencing vanished surface goes stale (recorded on
plan state, NEVER editing the signed spec); recovery clears the flag; an
approved-spec drift notifies once per change; spec-verify is read-only and
attaches its result to the plan state.
"""
import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import spec_drift as sd  # noqa: E402
import spec_store as ss  # noqa: E402
import plan_state as ps  # noqa: E402


@pytest.fixture
def estate(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "DIR", tmp_path / "plans")
    monkeypatch.setattr(ps, "FILE", tmp_path / "plans/state.json")
    monkeypatch.setattr(ps, "PLAN_DIR", tmp_path / "testplans")
    monkeypatch.setattr(ss, "SPEC_DIR", tmp_path / "specs")
    (tmp_path / "plans").mkdir()
    (tmp_path / "testplans").mkdir()

    def seed_spec(scenarios):
        sp = ss.spec_path("K-1")
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(yaml.safe_dump({"key": "K-1", "scenarios": scenarios}),
                      encoding="utf-8")
        ps.plan_path("K-1").write_text("# p\n", encoding="utf-8")
        ps.record_plan("K-1", {"scenarios": scenarios})
    return seed_spec, tmp_path


SC_LIVE = {"id": "K-1-S1", "title": "posts /v1/orders/{id}/discounts",
           "layer": "api", "target_repo": "r",
           "verification": ["POST /v1/orders/{id}/discounts returns 422"]}
SC_GONE = {"id": "K-1-S2", "title": "uses /v1/legacy/rebates endpoint",
           "layer": "api", "target_repo": "r",
           "verification": ["GET /v1/legacy/rebates is rejected"]}


def test_vanished_surface_marks_stale_and_recovery_clears(estate, monkeypatch):
    seed, tmp = estate
    seed([SC_LIVE, SC_GONE])
    monkeypatch.setattr(sd, "_current_surface",
                        lambda: {"orders-api": {"/v1/orders/*/discounts"}})
    results = sd.check()
    assert results and results[0]["stale"] == ["K-1-S2"], \
        "only the scenario whose surface vanished goes stale"
    assert ps.get("K-1")["stale_scenarios"] == ["K-1-S2"]
    # The signed spec itself is untouched — staleness is information.
    spec = ss.load("K-1")
    assert [s["id"] for s in spec["scenarios"]] == ["K-1-S1", "K-1-S2"]
    # Surface returns -> flag clears.
    monkeypatch.setattr(sd, "_current_surface",
                        lambda: {"orders-api": {"/v1/orders/*/discounts",
                                                "/v1/legacy/rebates"}})
    assert sd.check() == []
    assert "stale_scenarios" not in ps.get("K-1")


def test_approved_drift_notifies_once_per_change(estate, monkeypatch):
    seed, tmp = estate
    seed([SC_GONE])
    ps.set_status("K-1", "approved", "lead")
    monkeypatch.setattr(sd, "_current_surface",
                        lambda: {"orders-api": {"/v1/orders/*"}})
    sent = []
    monkeypatch.setattr(sd, "_notify", lambda msg: sent.append(msg))
    sd.check(notify=True)
    assert len(sent) == 1 and "K-1-S2" in sent[0]
    sd.check(notify=True)                           # unchanged -> silent
    assert len(sent) == 1, "an unchanged stale set must not re-alarm nightly"


def test_no_surface_harvested_means_no_false_stale(estate, monkeypatch):
    """An estate where harvesting fails entirely must not mark everything
    stale — absence of evidence is not drift."""
    seed, tmp = estate
    seed([SC_GONE])
    monkeypatch.setattr(sd, "_current_surface", lambda: {})
    assert sd.check() == []


def test_platform_dir_is_skipped(estate, monkeypatch):
    seed, tmp = estate
    (ss.SPEC_DIR / "platform").mkdir(parents=True, exist_ok=True)
    (ss.SPEC_DIR / "platform/constitution.yaml").write_text("clauses: []\n",
                                                            encoding="utf-8")
    monkeypatch.setattr(sd, "_current_surface",
                        lambda: {"orders-api": {"/x"}})
    assert sd.check() == []                          # never treats it as a key


def test_maintain_wires_the_drift_step():
    src = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "spec_drift.py check --notify" in src

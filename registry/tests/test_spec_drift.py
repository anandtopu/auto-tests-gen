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
import sdd_messages  # noqa: E402


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
    # Returns True: `_notify` now reports whether it DELIVERED, and a falsy
    # answer deliberately means "not delivered, so do not advance the state".
    # `list.append` returns None, so the original stub would read as a failed
    # send and make this test's own scenario re-alarm.
    monkeypatch.setattr(sd, "_notify", lambda msg: sent.append(msg) or True)
    sd.check(notify=True)
    assert len(sent) == 1 and "K-1-S2" in sent[0]
    assert sent[0] == sdd_messages.refusal(
        "drift_stale", key="K-1", scenario="K-1-S2",
        surfaces=["/v1/legacy/rebates"])["text"]
    sd.check(notify=True)                           # unchanged -> silent
    assert len(sent) == 1, "an unchanged stale set must not re-alarm nightly"


def test_changed_vanished_surface_realarms_even_when_scenario_id_is_unchanged(
        estate, monkeypatch):
    seed, _ = estate
    scenario = {**SC_GONE, "verification": [
        "GET /v1/legacy/rebates and GET /v1/legacy/coupons are rejected"]}
    seed([scenario])
    ps.set_status("K-1", "approved", "lead")
    sent = []
    monkeypatch.setattr(sd, "_notify", lambda msg: sent.append(msg) or True)

    monkeypatch.setattr(sd, "_current_surface",
                        lambda: {"orders-api": {"/v1/legacy/rebates"}})
    sd.check(notify=True)
    assert "/v1/legacy/coupons" in sent[-1]

    monkeypatch.setattr(sd, "_current_surface",
                        lambda: {"orders-api": {"/v1/legacy/coupons"}})
    sd.check(notify=True)
    assert len(sent) == 2 and "/v1/legacy/rebates" in sent[-1]


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
    """Asks maintenance.STEPS, which is where the nightly steps live now that
    the Makefile delegates instead of ignoring each step's failure."""
    import maintenance
    commands = [" ".join(argv) for _, argv, _ in maintenance.STEPS]
    assert any(c.endswith("spec_drift.py check --notify") for c in commands), \
        f"the drift step is not in the nightly run: {commands}"


def test_an_undelivered_drift_alarm_is_retried_not_lost(estate, monkeypatch):
    """`_record` persisted the new stale set BEFORE notifying, so the next run
    saw no change and never notified again.

    With the channel down that lost the alarm permanently — for a signal whose
    entire job is to tell somebody an APPROVED spec no longer matches the code.
    Same bug, same shape, as coverage_drift: "notify once per change" is only
    safe when the change is committed once the notification actually lands.
    """
    seed, tmp = estate
    seed([SC_GONE])
    ps.set_status("K-1", "approved", "lead")
    monkeypatch.setattr(sd, "_current_surface",
                        lambda: {"orders-api": {"/v1/orders/*"}})

    # Channel down.
    monkeypatch.setattr(sd, "_notify", lambda msg: False)
    r = sd.check(notify=True)
    assert r and r[0]["delivered"] is False
    assert "stale_scenarios" not in ps.get("K-1"), \
        "state advanced past an alarm nobody received"

    # Channel back: the SAME drift must be reported again.
    sent = []
    monkeypatch.setattr(sd, "_notify", lambda msg: sent.append(msg) or True)
    sd.check(notify=True)
    assert len(sent) == 1, "the alarm was lost"
    assert ps.get("K-1")["stale_scenarios"] == ["K-1-S2"], \
        "delivered, so the state must advance"

    # And now it stays quiet.
    sd.check(notify=True)
    assert len(sent) == 1


def test_resolution_is_recorded_even_though_nobody_is_notified(estate, monkeypatch):
    """Good news needs no alarm, so `delivered` stays True and the cleared
    state must still persist — otherwise a resolved drift would be re-detected
    forever."""
    seed, tmp = estate
    seed([SC_GONE])
    ps.set_status("K-1", "approved", "lead")
    monkeypatch.setattr(sd, "_current_surface",
                        lambda: {"orders-api": {"/v1/orders/*"}})
    monkeypatch.setattr(sd, "_notify", lambda msg: True)
    sd.check(notify=True)
    assert ps.get("K-1")["stale_scenarios"] == ["K-1-S2"]

    # Surface returns.
    monkeypatch.setattr(sd, "_current_surface",
                        lambda: {"orders-api": {"/v1/orders/*",
                                                "/v1/legacy/rebates"}})
    assert sd.check(notify=True) == []
    assert "stale_scenarios" not in ps.get("K-1")


def test_the_real_notify_reports_delivery(tmp_path, monkeypatch):
    """Every other test here stubs `_notify`, so its return value — the thing
    the retry logic depends on — was never exercised. A version that always
    claimed success would pass all of them."""
    monkeypatch.setattr(sd, "ROOT", tmp_path)
    # No adapter on disk: nothing was sent, so it must not claim it was.
    assert sd._notify("hello") is False

    # A mock adapter that exits 0 is a delivery; one that exits 1 is not.
    ad = tmp_path / "adapters" / "mock"
    ad.mkdir(parents=True)
    (ad / "notify.sh").write_text("#!/usr/bin/env bash\nexit 0\n",
                                  encoding="utf-8", newline="\n")
    monkeypatch.setenv("AIQE_MOCK", "1")
    assert sd._notify("hello") is True
    (ad / "notify.sh").write_text("#!/usr/bin/env bash\nexit 1\n",
                                  encoding="utf-8", newline="\n")
    assert sd._notify("hello") is False

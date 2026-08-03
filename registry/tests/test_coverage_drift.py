"""Coverage drift: the nightly alarm that had no tests at all.

`make maintain` runs this, and its whole job is to notice that a repo's
uncovered surface grew. It shipped with zero test references — which matters
more than usual here, because everything it does is invisible when it works and
also invisible when it silently does nothing.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine" / "lib"))
import coverage_drift


def _drift(tmp_path, monkeypatch, counts):
    """Point the module at a temp state file and a fixed snapshot."""
    monkeypatch.setattr(coverage_drift, "FILE", tmp_path / "drift.json")
    monkeypatch.setattr(coverage_drift, "snapshot", lambda: dict(counts))


def test_the_first_run_establishes_a_baseline_and_alarms_on_nothing(tmp_path, monkeypatch):
    """Otherwise every new estate pages someone on its first night."""
    _drift(tmp_path, monkeypatch, {"api": 3, "ui": 1})
    fired = []
    monkeypatch.setattr(coverage_drift, "_notify", lambda m: fired.append(m) or True)
    r = coverage_drift.check(notify=True)
    assert r["baseline"] is True and r["grew"] == {}
    assert fired == [], "alarmed on the baseline run"


def test_growth_is_reported_and_shrinkage_is_quiet(tmp_path, monkeypatch):
    _drift(tmp_path, monkeypatch, {"api": 3})
    monkeypatch.setattr(coverage_drift, "_notify", lambda m: True)
    coverage_drift.check(notify=True)                      # baseline

    fired = []
    _drift(tmp_path, monkeypatch, {"api": 6})
    monkeypatch.setattr(coverage_drift, "_notify", lambda m: fired.append(m) or True)
    r = coverage_drift.check(notify=True)
    assert r["grew"] == {"api": (3, 6)}
    assert len(fired) == 1 and "3->6" in fired[0]

    # Good news needs no alarm.
    fired.clear()
    _drift(tmp_path, monkeypatch, {"api": 2})
    monkeypatch.setattr(coverage_drift, "_notify", lambda m: fired.append(m) or True)
    r = coverage_drift.check(notify=True)
    assert r["shrank"] == {"api": (6, 2)} and fired == []


def test_a_new_repo_is_not_growth(tmp_path, monkeypatch):
    """Onboarding a repo with uncovered surface is not a regression, and paging
    someone for it teaches them to ignore the alarm."""
    _drift(tmp_path, monkeypatch, {"api": 3})
    monkeypatch.setattr(coverage_drift, "_notify", lambda m: True)
    coverage_drift.check(notify=True)

    fired = []
    _drift(tmp_path, monkeypatch, {"api": 3, "brand-new": 9})
    monkeypatch.setattr(coverage_drift, "_notify", lambda m: fired.append(m) or True)
    r = coverage_drift.check(notify=True)
    assert r["grew"] == {} and fired == []


def test_a_failed_notification_does_not_lose_the_alarm(tmp_path, monkeypatch):
    """The first version wrote the new snapshot BEFORE notifying and swallowed
    every delivery error. A channel outage meant the growth was announced once
    to a maintenance log nobody reads, the baseline moved on, and the next run
    saw no growth — the alarm was gone for good.

    Now an undelivered alarm leaves the baseline where it was, so the next run
    reports it again until it actually lands.
    """
    _drift(tmp_path, monkeypatch, {"api": 3})
    monkeypatch.setattr(coverage_drift, "_notify", lambda m: True)
    coverage_drift.check(notify=True)                      # baseline at 3

    # Growth, with the channel down.
    _drift(tmp_path, monkeypatch, {"api": 6})
    monkeypatch.setattr(coverage_drift, "_notify", lambda m: False)
    r = coverage_drift.check(notify=True)
    assert r["grew"] == {"api": (3, 6)} and r["delivered"] is False
    stored = json.loads((tmp_path / "drift.json").read_text(encoding="utf-8"))
    assert stored["counts"]["api"] == 3, \
        "baseline advanced past an alarm nobody received"

    # Next run, channel back: it must report the SAME growth, not silence.
    fired = []
    _drift(tmp_path, monkeypatch, {"api": 6})
    monkeypatch.setattr(coverage_drift, "_notify", lambda m: fired.append(m) or True)
    r = coverage_drift.check(notify=True)
    assert r["grew"] == {"api": (3, 6)}, "the alarm was lost"
    assert len(fired) == 1
    stored = json.loads((tmp_path / "drift.json").read_text(encoding="utf-8"))
    assert stored["counts"]["api"] == 6, "delivered, so the baseline must advance"


def test_notify_false_never_delivers(tmp_path, monkeypatch):
    """Computing a report must not page anyone — the same rule the alert rules
    follow, so a dashboard render is never an alerting event."""
    _drift(tmp_path, monkeypatch, {"api": 3})
    monkeypatch.setattr(coverage_drift, "_notify", lambda m: True)
    coverage_drift.check(notify=True)

    fired = []
    _drift(tmp_path, monkeypatch, {"api": 9})
    monkeypatch.setattr(coverage_drift, "_notify", lambda m: fired.append(m) or True)
    r = coverage_drift.check(notify=False)
    assert r["grew"] == {"api": (3, 9)} and fired == []
    # And with no delivery attempted, the baseline still advances — nothing was
    # promised to anyone, so there is nothing to retry.
    stored = json.loads((tmp_path / "drift.json").read_text(encoding="utf-8"))
    assert stored["counts"]["api"] == 9


def test_counts_not_sets_so_a_rename_is_not_drift(tmp_path, monkeypatch):
    """Documented design: a renamed surface would churn a set diff into noise,
    while "3 uncovered last night, 6 now" is the sentence a lead acts on."""
    _drift(tmp_path, monkeypatch, {"api": 2})
    monkeypatch.setattr(coverage_drift, "_notify", lambda m: True)
    coverage_drift.check(notify=True)

    fired = []
    # Same COUNT, entirely different surface names underneath.
    _drift(tmp_path, monkeypatch, {"api": 2})
    monkeypatch.setattr(coverage_drift, "_notify", lambda m: fired.append(m) or True)
    r = coverage_drift.check(notify=True)
    assert r["grew"] == {} and fired == []


def test_a_corrupt_state_file_is_treated_as_a_baseline(tmp_path, monkeypatch):
    """Derived state: regenerate, never repair — and never crash maintenance."""
    (tmp_path / "drift.json").write_text("{not json", encoding="utf-8")
    _drift(tmp_path, monkeypatch, {"api": 4})
    fired = []
    monkeypatch.setattr(coverage_drift, "_notify", lambda m: fired.append(m) or True)
    r = coverage_drift.check(notify=True)
    assert r["baseline"] is True and fired == []

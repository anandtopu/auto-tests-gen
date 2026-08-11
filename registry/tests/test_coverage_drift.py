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


def _drift(tmp_path, monkeypatch, counts, blind=None):
    """Point the module at a temp state file and a fixed snapshot.

    snapshot() returns (counts, unobserved). A repo whose surface could not be
    harvested arrives in the SECOND element, never as a 0 in the first — a 0
    would make losing sight of a repo look exactly like closing all its gaps.
    """
    monkeypatch.setattr(coverage_drift, "FILE", tmp_path / "drift.json")
    monkeypatch.setattr(coverage_drift, "snapshot",
                        lambda: (dict(counts), dict(blind or {})))


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


def test_an_unobservable_repo_keeps_its_baseline_so_growth_is_still_caught(
        tmp_path, monkeypatch):
    """THE BLIND WINDOW. A repo whose contract could not be harvested used to
    vanish from compute(), so `counts = dict(now)` dropped it from the stored
    baseline. That blinded the alarm twice: during the outage, and afterwards —
    the repo was then missing from `prev` too, so the first run that could see
    it again re-baselined in silence.

    Reproduced end to end before the fix: payments-api 2 -> unobservable -> 9,
    and no run alarmed. Night 3 even printed "baseline established", on an
    estate the alarm had been watching for two nights.
    """
    _drift(tmp_path, monkeypatch, {"payments-api": 2})
    monkeypatch.setattr(coverage_drift, "_notify", lambda m: True)
    coverage_drift.check(notify=True)                      # baseline at 2

    # Night 2: the clone is not there. Nothing was measured about this repo.
    _drift(tmp_path, monkeypatch, {},
           blind={"payments-api": "contract not available locally"})
    fired = []
    monkeypatch.setattr(coverage_drift, "_notify", lambda m: fired.append(m) or True)
    r = coverage_drift.check(notify=True)
    assert r["unobserved"] == {"payments-api": "contract not available locally"}
    assert r["grew"] == {} and r["shrank"] == {} and fired == [], \
        "an unobservable repo must not read as growth OR as improvement"
    stored = json.loads((tmp_path / "drift.json").read_text(encoding="utf-8"))
    assert stored["counts"]["payments-api"] == 2, \
        "the baseline was dropped, so the next run cannot detect growth"

    # Night 3: it is back, and much worse. THIS is the alarm that used to be lost.
    _drift(tmp_path, monkeypatch, {"payments-api": 9})
    fired.clear()
    r = coverage_drift.check(notify=True)
    assert r["grew"] == {"payments-api": (2, 9)}, \
        "growth across the blind window was never reported"
    assert len(fired) == 1 and "2->9" in fired[0]


def test_an_unobservable_repo_is_never_counted_as_zero_uncovered(tmp_path, monkeypatch):
    """The direction that reassures: if `blind` repos leaked into `counts` as 0,
    a repo going 5 -> unobservable would be reported as SHRINKAGE, i.e. as gaps
    closing. compute() now returns those repos (so callers can see them), which
    is exactly what makes this mistake easy to make."""
    _drift(tmp_path, monkeypatch, {"api": 5})
    monkeypatch.setattr(coverage_drift, "_notify", lambda m: True)
    coverage_drift.check(notify=True)

    _drift(tmp_path, monkeypatch, {}, blind={"api": "not available locally"})
    r = coverage_drift.check(notify=True)
    assert r["shrank"] == {}, "losing sight of a repo was reported as improvement"
    assert r["counts"] == {}, "an unobserved repo leaked into the counts as 0"


def test_snapshot_omits_unobservable_repos_from_counts():
    """Against the REAL compute(), with no clone anywhere: every repo is
    unreadable, so counts must be empty rather than a row of zeroes."""
    import coverage_gaps
    import tempfile
    real_root = coverage_gaps.ROOT
    try:
        coverage_gaps.ROOT = pathlib.Path(tempfile.mkdtemp())
        counts, blind = coverage_drift.snapshot()
    finally:
        coverage_gaps.ROOT = real_root
    assert counts == {}, f"unharvestable repos were counted: {counts}"
    assert blind, "no repo was reported as unobservable either — nothing said"


def test_a_run_that_checked_nothing_says_so(tmp_path, monkeypatch, capsys):
    """The deployed shape: workspace/ is ephemeral, so nightly maintenance on a
    container harvests nothing. That used to print "baseline established for 0
    repo(s)" every night — an alarm that could never fire, reporting success."""
    _drift(tmp_path, monkeypatch, {}, blind={"api": "not available locally"})
    monkeypatch.setattr(coverage_drift, "_notify", lambda m: True)
    coverage_drift.check(notify=True)
    out = capsys.readouterr().out
    assert "NOTHING was checked" in out
    assert "baseline established" not in out, \
        "claimed a baseline was established when no repo was measured"


def test_a_corrupt_state_file_is_treated_as_a_baseline(tmp_path, monkeypatch):
    """Derived state: regenerate, never repair — and never crash maintenance."""
    (tmp_path / "drift.json").write_text("{not json", encoding="utf-8")
    _drift(tmp_path, monkeypatch, {"api": 4})
    fired = []
    monkeypatch.setattr(coverage_drift, "_notify", lambda m: fired.append(m) or True)
    r = coverage_drift.check(notify=True)
    assert r["baseline"] is True and fired == []

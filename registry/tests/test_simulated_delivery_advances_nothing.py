"""Three alarms advanced their state on a delivery that reached nobody.

FOUND BY SWEEPING the delivery-claim family after the plan-attachment fix:
every production site that runs a Notify/Tracker adapter, asked per site
whether the surface can tell a mock delivery from a real one. Two of them do
something worse than mislabel it - they advance DURABLE ALARM STATE on it.

`coverage_drift` moves its baseline only when the notification lands, and
`spec_drift` records a scenario as reported only once the message goes out.
Both say why in their own comments; spec_drift's calls it "identical to the
coverage-drift bug". Both were written against a two-state world - the channel
worked, or it did not - and the mock adapter exits 0.

THAT THIRD STATE IS THE DEPLOYED DEFAULT: `AIQE_MOCK: "1"` in
`deploy/openshift/configmap.yaml`, `AIQE_MOCK=1` in the Dockerfile. MEASURED
against an isolated drift file with uncovered surface grown 2 -> 9:

    COVERAGE DRIFT: payments-api uncovered surface grew 2 -> 9
    delivered   : True
    baseline now: {'payments-api': 9}

The alarm fired, `adapters/mock/notify.sh` appended it to out/mock-comments.log,
and the baseline moved past it - so the next night reports "no growth" and the
drift is never mentioned again. Exactly the permanent-loss failure the delivery
gate was added to prevent, through the one path nobody modelled.

`alert_rules` is the third site and is DELIBERATELY NOT changed the same way,
which is the judgement worth recording: its `last_notified` cooldown exists to
protect a HUMAN from spam, and under mock there is no human to protect. Not
consuming it would only re-post to a log nobody reads. What that site did need
is the LABEL - `adapter.name` is "notify.sh" for the mock and "slack.sh" for
the real one, a distinction no auditor can be expected to know - so the
`notify.sent` event now carries `simulated`.
"""
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import delivery                                           # noqa: E402


def test_the_three_states_are_distinguished():
    assert delivery.outcome(0, mock=False) == delivery.SENT
    assert delivery.outcome(0, mock=True) == delivery.SIMULATED
    assert delivery.outcome(1, mock=False) == delivery.FAILED
    # A mock adapter that FAILS is still a failure, not a simulation: the
    # distinction is about who was told, and nobody was told either way, but
    # the fix differs and `failed` is the honest one for a broken adapter.
    assert delivery.outcome(1, mock=True) == delivery.FAILED


def test_only_a_real_send_may_advance_state():
    assert delivery.landed(delivery.SENT) is True
    assert delivery.landed(delivery.SIMULATED) is False
    assert delivery.landed(delivery.FAILED) is False


def test_a_healthy_delivery_says_nothing():
    """A note that fires on a good nightly run is one operators scroll past,
    which is how the real ones get missed."""
    assert delivery.note(delivery.SENT, "x") is None


@pytest.mark.parametrize("state,must,must_not", [
    (delivery.SIMULATED, "AIQE_MOCK=0", "Check the notify channel"),
    (delivery.FAILED, "Check the notify channel", "AIQE_MOCK"),
])
def test_the_two_undelivered_states_send_you_to_different_places(state, must,
                                                                 must_not):
    """C13. Sending an operator to debug Slack when the answer is "you are in
    mock mode" wastes the outage, and vice versa."""
    note = delivery.note(state, "the drift notification")
    assert must in note, note
    assert must_not not in note, note
    note.encode("cp1252")            # printed to a maintenance console


def _drift(tmp_path, monkeypatch, mock, counts_before, counts_now):
    monkeypatch.setenv("AIQE_DRIFT_FILE", str(tmp_path / "drift.json"))
    monkeypatch.setenv("AIQE_MOCK", "1" if mock else "0")
    import importlib
    import coverage_drift
    importlib.reload(coverage_drift)
    (tmp_path / "drift.json").write_text(
        json.dumps({"counts": counts_before}), encoding="utf-8")
    monkeypatch.setattr(coverage_drift, "snapshot",
                        lambda: (counts_now, {}))
    report = coverage_drift.check(notify=True)
    stored = json.loads((tmp_path / "drift.json").read_text())["counts"]
    return report, stored, coverage_drift


def test_a_simulated_drift_alarm_does_not_advance_the_baseline(tmp_path,
                                                               monkeypatch,
                                                               capsys):
    """THE MEASURED DEFECT, driven end to end."""
    report, stored, _ = _drift(tmp_path, monkeypatch, True,
                               {"payments-api": 2}, {"payments-api": 9})
    assert report["grew"] == {"payments-api": (2, 9)}
    assert report["delivery"] == delivery.SIMULATED
    assert report["delivered"] is False
    assert stored == {"payments-api": 2}, \
        "the baseline advanced past an alarm nobody received"
    out = capsys.readouterr().out
    assert "MOCK notify adapter" in out and "AIQE_MOCK=0" in out, out


def test_the_same_drift_is_reported_again_next_run(tmp_path, monkeypatch):
    """The POINT of not advancing: an alarm nobody got must come back. Pinned
    separately because a fix that merely relabels while still advancing would
    pass the test above's first half."""
    report, stored, mod = _drift(tmp_path, monkeypatch, True,
                                 {"payments-api": 2}, {"payments-api": 9})
    again = mod.check(notify=True)
    assert again["grew"] == {"payments-api": (2, 9)}, \
        "the second run saw no growth, so the alarm was lost after all"


def test_a_real_delivery_still_advances_the_baseline(tmp_path, monkeypatch):
    """THE OVER-FIX DIRECTION, pinned as hard as the defect: if nothing ever
    advanced, every night would re-report the same drift forever and the alarm
    would become noise. `AIQE_MOCK=0` uses the real adapter, which is absent
    here, so this asserts the state MACHINE via a stubbed notify rather than a
    real Slack call."""
    monkeypatch.setenv("AIQE_DRIFT_FILE", str(tmp_path / "drift.json"))
    import importlib
    import coverage_drift
    importlib.reload(coverage_drift)
    (tmp_path / "drift.json").write_text(
        json.dumps({"counts": {"payments-api": 2}}), encoding="utf-8")
    monkeypatch.setattr(coverage_drift, "snapshot",
                        lambda: ({"payments-api": 9}, {}))
    monkeypatch.setattr(coverage_drift, "_notify", lambda msg: delivery.SENT)
    report = coverage_drift.check(notify=True)
    stored = json.loads((tmp_path / "drift.json").read_text())["counts"]
    assert report["delivered"] is True
    assert stored == {"payments-api": 9}, \
        "a delivered alarm must advance the baseline, or it repeats forever"


def test_nothing_to_deliver_is_not_a_failed_delivery(tmp_path, monkeypatch):
    """No growth means no notification was attempted; reporting that as
    undelivered would carry stale counts forward for no reason."""
    report, stored, _ = _drift(tmp_path, monkeypatch, True,
                               {"payments-api": 5}, {"payments-api": 5})
    assert report["grew"] == {}
    assert report["delivered"] is True
    assert stored == {"payments-api": 5}


def test_the_drift_siblings_share_one_definition():
    """THE INVARIANT. Two modules had the identical bug because each carried
    its own bool; a third notify-gated alarm must not reinvent it."""
    for rel in ("engine/lib/coverage_drift.py", "engine/lib/spec_drift.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "delivery.landed(" in src, \
            f"{rel} decides delivery for itself instead of asking delivery.py"
        assert "return r.returncode == 0" not in src, \
            f"{rel} still reads an adapter exit code as delivery"


def test_the_spec_drift_record_is_gated_on_a_real_send():
    """Read as source because driving spec_drift needs an approved spec with
    stale scenarios; the state-machine rule is what matters and it is one
    line."""
    src = (ROOT / "engine/lib/spec_drift.py").read_text(encoding="utf-8")
    i = src.index("if changed and delivered:")
    assert "delivered = delivery.landed(state)" in src[max(0, i - 200):i], \
        "spec_drift records a scenario as reported without asking whether the " \
        "notification actually landed"


def test_the_audit_log_marks_a_simulated_notification():
    """`adapter.name` is "notify.sh" for the mock and "slack.sh" for the real
    one -- not a distinction an auditor can be expected to know."""
    src = (ROOT / "engine/lib/alert_rules.py").read_text(encoding="utf-8")
    i = src.index('event_log.emit("notify.sent"')
    assert '"simulated": bool(mock)' in src[i:i + 600], \
        "notify.sent records a delivery without saying whether it was real"


def test_the_alert_cooldown_is_deliberately_unchanged():
    """The judgement call, pinned so it is not 'fixed' by pattern-matching:
    `last_notified` protects a human from spam and there is no human under
    mock. Changing it would only re-post to a log nobody reads."""
    src = (ROOT / "engine/lib/alert_rules.py").read_text(encoding="utf-8")
    i = src.index('event_log.emit("notify.sent"')
    assert "labelling fix here, not a state-machine change" in src[max(0, i - 900):i], \
        "the reason the cooldown is left alone is no longer written down"

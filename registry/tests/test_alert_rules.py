"""Pins for alert rules (observability slice 3).

The two that matter most are the honesty ones: a rule that cannot be evaluated
must not report healthy, and a flapping condition must not storm. Both are ways
monitoring lies rather than ways it crashes, so neither shows up without a test.
"""
import datetime
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine" / "lib"))
import alert_rules as ar  # noqa: E402
import event_log as el  # noqa: E402

UTC = datetime.timezone.utc


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AIQE_EVENTS_DIR", str(tmp_path / "ev"))
    monkeypatch.setenv("AIQE_ALERT_RULES_FILE", str(tmp_path / "rules.json"))
    monkeypatch.setenv("AIQE_MOCK", "1")
    monkeypatch.setattr(el, "_degraded_reported", False)
    monkeypatch.setattr(el, "_dropped", 0)


def _rule(**over):
    r = {"id": "r1", "name": "gate refusals", "enabled": True,
         "match": {"kinds": ["gate.refused"]}, "threshold": 3,
         "window_minutes": 60, "cooldown_minutes": 60, "channel": "slack"}
    r.update(over)
    return r


def test_a_rule_fires_only_at_its_threshold():
    ar.save({"rules": [_rule()]})
    for _ in range(2):
        el.emit("gate.refused", target="repo-1", outcome="refused")
    assert ar.evaluate(notify=False)[0]["status"] == "ok"
    el.emit("gate.refused", target="repo-1", outcome="refused")
    res = ar.evaluate(notify=False)[0]
    assert res["status"] == "firing" and res["transition"] == "fired"


def test_a_firing_rule_does_not_refire_every_tick():
    """Story 3.3: the first message already said everything."""
    ar.save({"rules": [_rule()]})
    for _ in range(3):
        el.emit("gate.refused", target="repo-1", outcome="refused")
    assert ar.evaluate(notify=False)[0]["transition"] == "fired"
    for _ in range(5):
        assert ar.evaluate(notify=False)[0]["transition"] is None


def test_a_rule_resolves_when_the_condition_clears():
    """Firing is a STATE. Without resolution a rule fires once and is deaf
    to the problem happening again."""
    ar.save({"rules": [_rule()]})
    for _ in range(3):
        el.emit("gate.refused", target="repo-1", outcome="refused")
    assert ar.evaluate(notify=False)[0]["transition"] == "fired"
    # Step past the window: the same events no longer fall inside it.
    later = datetime.datetime.now(UTC) + datetime.timedelta(minutes=120)
    res = ar.evaluate(now=later, notify=False)[0]
    assert res["transition"] == "resolved" and res["status"] == "ok"


def test_an_unreadable_log_reports_unevaluable_not_healthy():
    """Story 3.4. Silence from a broken evaluator looks exactly like silence
    from a healthy estate — that is how monitoring lies."""
    ar.save({"rules": [_rule()]})
    el._degraded_reported, el._dropped = True, 7
    try:
        res = ar.evaluate(notify=False)[0]
    finally:
        el._degraded_reported, el._dropped = False, 0
    assert res["status"] == "unevaluable"
    assert "7" in res["reason"], "the reason must name what was lost"
    assert res["status"] != "ok"


def test_a_disabled_rule_is_reported_not_silently_skipped():
    ar.save({"rules": [_rule(enabled=False)]})
    assert ar.evaluate(notify=False)[0]["status"] == "disabled"


# ------------------------------------------------------------- validation
def test_a_rule_matching_everything_is_flagged():
    """A rule with no match criteria fires on the first event forever."""
    _, problems = ar.normalize({"id": "x", "match": {}})
    assert any("EVERY event" in p for p in problems)


def test_an_unknown_kind_is_flagged_rather_than_silently_never_matching():
    _, problems = ar.normalize({"id": "x", "match": {"kinds": ["no.such"]}})
    assert any("unknown kind" in p for p in problems)


def test_email_without_recipients_is_flagged():
    _, problems = ar.normalize({"id": "x", "channel": "email",
                                "match": {"kinds": ["gate.refused"]}})
    assert any("recipients" in p for p in problems)


def test_window_is_clamped_so_one_rule_cannot_scan_forever():
    r, _ = ar.normalize({"id": "x", "window_minutes": 10 ** 9,
                         "match": {"kinds": ["gate.refused"]}})
    assert r["window_minutes"] == ar.MAX_WINDOW_MINUTES


def test_garbage_numbers_do_not_crash_evaluation():
    """A malformed rule must not stop the OTHER rules being evaluated."""
    ar.save({"rules": [{"id": "bad", "threshold": "lots", "window_minutes": None,
                        "match": {"kinds": ["gate.refused"]}},
                       _rule(id="good", name="good")]})
    res = ar.evaluate(notify=False)
    assert len(res) == 2, "a bad rule must not swallow its neighbours"


# ------------------------------------------------------------- delivery
def test_delivery_records_every_attempt(monkeypatch):
    """Finding F3: 'nothing happened' must be distinguishable from 'we failed
    to tell you'."""
    ar.deliver("hello", channel="slack", rule_name="t")
    kinds = [r["kind"] for r in el.read()[0]]
    assert "notify.sent" in kinds or "notify.failed" in kinds


def test_the_module_never_imports_a_vendor():
    """Constitution: delivery goes through the Notify port, adapters only."""
    src = (ROOT / "engine" / "lib" / "alert_rules.py").read_text(encoding="utf-8")
    for vendor in ("import slack", "import requests", "slack_sdk", "smtplib"):
        assert vendor not in src, f"{vendor} — delivery must go through the port"
    assert "adapters/notify" in src or "adapters/mock/notify" in src


# ------------------------------------------------- server + schedule wiring
def test_rules_are_evaluated_on_the_nightly_tick():
    """A rule engine nothing calls is a rule engine that never fires."""
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    tick = mk.split("maintain:", 1)[1].split("\nstate-export:", 1)[0]
    assert "alert_rules.py" in tick, "maintain must evaluate rules"


def test_rendering_a_page_never_sends_a_notification():
    """GET /api/alerts evaluates so the UI can show live status — but opening a
    dashboard is not an alerting event, and a page that notifies on render
    would spam every time someone looks at it."""
    src = (ROOT / "bin" / "dashboard_server.py").read_text(encoding="utf-8")
    get_alerts = src.split('url.path == "/api/alerts"', 1)[1].split("elif url.path", 1)[0]
    assert "notify=False" in get_alerts, "the GET path must not deliver"


def test_saving_rules_is_bounded_and_validated():
    src = (ROOT / "bin" / "dashboard_server.py").read_text(encoding="utf-8")
    save = src.split('self.path == "/api/alerts/save"', 1)[1].split("if self.path ==", 1)[0]
    assert "normalize(" in save, "incoming rules must be normalized, not trusted"
    assert "max 200" in save or "200" in save, "an unbounded rule list is a DoS"

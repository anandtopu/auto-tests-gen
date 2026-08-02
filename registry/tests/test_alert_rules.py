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


# --------------------------------------------------- per-rule recipients (E4)
def test_recipient_env_var_is_one_the_adapter_actually_reads():
    """The first version set EMAIL_TO, which NOTHING reads.

    `adapters/notify/email.sh` shells to `email_notify.py`, and that resolves
    recipients from SMTP_TO or --to. A per-rule recipient list that silently
    does not apply is exactly the class of failure this epic exists to remove,
    so the name is pinned against the LIBRARY rather than assumed.
    """
    lib = (ROOT / "engine" / "lib" / "email_notify.py").read_text(encoding="utf-8")
    src = (ROOT / "engine" / "lib" / "alert_rules.py").read_text(encoding="utf-8")
    import re
    set_vars = set(re.findall(r'env\["([A-Z_]+)"\]', src))
    assert set_vars, "deliver() must set the recipient env var"
    for var in set_vars:
        assert f'_env("{var}")' in lib or f'"{var}"' in lib, (
            f"alert_rules sets {var}, which email_notify never reads — "
            f"the recipient list would silently not apply")


def test_per_rule_recipients_reach_the_delivered_mail(monkeypatch):
    """End to end through the REAL adapter and library.

    email_notify.MOCK_DIR is a module constant, so this reads the actual
    out/mock-email/ output rather than redirecting it — and only inspects files
    it created itself. A skipped test would prove nothing, and this is the one
    assertion that would have caught the EMAIL_TO bug.
    """
    import email_notify
    monkeypatch.setenv("AIQE_MOCK", "1")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    outdir = pathlib.Path(email_notify.MOCK_DIR)
    before = set(outdir.glob("*.eml")) if outdir.is_dir() else set()
    addr = "qa-lead-pin@example.com"
    try:
        ar.deliver("alert subject\nbody", channel="email",
                   recipients=[addr], rule_name="r1")
        made = (set(outdir.glob("*.eml")) if outdir.is_dir() else set()) - before
        assert made, "the email adapter produced no mock mail at all"
        text = "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in made)
        assert addr in text, (
            "the per-rule recipient never reached the mail — the env var "
            "alert_rules sets is not the one email_notify reads")
    finally:
        for f in (set(outdir.glob("*.eml")) if outdir.is_dir() else set()) - before:
            f.unlink(missing_ok=True)


# ----------------------------------------------------- digests + retry (E4)
def test_digest_rules_produce_one_message_not_one_each(monkeypatch, tmp_path):
    """Story 4.3: for rules that fire often, this is the difference between an
    inbox someone reads and one they filter away."""
    sent = []
    monkeypatch.setattr(ar, "deliver",
                        lambda msg, *a, **k: sent.append(msg) or True)
    ar.save({"rules": [_rule(id="a", name="alpha", digest=True, threshold=1),
                       _rule(id="b", name="beta", digest=True, threshold=1)]})
    el.emit("gate.refused", target="repo-1", outcome="refused")
    ar.evaluate()
    assert len(sent) == 1, f"expected ONE digest, got {len(sent)}: {sent}"
    assert "alpha" in sent[0] and "beta" in sent[0], "both rules must appear"
    assert "digest" in sent[0].lower()


def test_digests_are_grouped_by_channel(monkeypatch):
    """A Slack digest and an email digest must not become one message sent to
    the wrong place."""
    calls = []
    monkeypatch.setattr(ar, "deliver",
                        lambda msg, chan="slack", *a, **k: calls.append(chan) or True)
    ar.save({"rules": [_rule(id="a", name="alpha", digest=True, threshold=1,
                             channel="slack"),
                       _rule(id="b", name="beta", digest=True, threshold=1,
                             channel="email", recipients=["x@example.com"])]})
    el.emit("gate.refused", target="repo-1", outcome="refused")
    ar.evaluate()
    assert sorted(calls) == ["email", "slack"], calls


def test_a_non_digest_rule_still_sends_immediately(monkeypatch):
    sent = []
    monkeypatch.setattr(ar, "deliver", lambda msg, *a, **k: sent.append(msg) or True)
    ar.save({"rules": [_rule(threshold=1, digest=False)]})
    el.emit("gate.refused", target="repo-1", outcome="refused")
    ar.evaluate()
    assert len(sent) == 1 and "digest" not in sent[0].lower()


def test_delivery_retries_then_records_one_failure(monkeypatch, tmp_path):
    """Story 4.4: retry a transient failure, but record ONE notify.failed —
    recording every attempt buries the signal under noise from a slow channel."""
    monkeypatch.setattr(ar, "RETRY_BACKOFF", (0, 0))          # no real sleeping
    missing = tmp_path / "no-such-adapter.sh"
    monkeypatch.setattr(ar, "ROOT", tmp_path)                 # adapters resolve here
    ok = ar.deliver("msg", channel="slack", rule_name="r1")
    assert ok is False
    fails = [r for r in el.read()[0] if r["kind"] == "notify.failed"]
    assert len(fails) == 1, f"one failure record expected, got {len(fails)}"
    assert fails[0]["detail"]["attempts"] == 3, "should have tried 1 + 2 retries"
    assert not missing.exists()


def test_test_fire_does_not_retry(monkeypatch):
    """A human is watching. A retry that papers over a transient failure
    defeats the entire point of pressing Test."""
    seen = {}
    monkeypatch.setattr(ar, "deliver",
                        lambda *a, **k: seen.update(k) or True)
    ar.save({"rules": [_rule()]})
    ar.test_fire("r1")
    assert seen.get("retries") == 0, f"test-fire must pass retries=0, got {seen}"

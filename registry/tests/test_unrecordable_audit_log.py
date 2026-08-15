"""An audit log nobody could write read as an estate where nothing happened.

FOUND BY DRIVING use case 12 exactly as `docs/use-cases.md` writes it, and by
reading the guarantee that section makes:

    "If the log could not be written, the view and the CLI say so - the list is
     labelled INCOMPLETE rather than presented as a full history. A partial
     audit trail that looks complete is worse than an obviously broken one."

That promise could not be kept as built. `event_log.health()` is PROCESS-LOCAL
(`_degraded_reported` is a module global set by `emit`), and every reader of
this log is a different process that has emitted nothing: `qa.py events`, the
dashboard's Activity view, `bin/dashboard.py`, and `alert_rules.evaluate`. So
`health()["degraded"]` is always False in a reader, whatever state the log is
in.

MEASURED against an events dir with a file sitting where the directory must be
(a stale path, or AIQE_EVENTS_DIR pointed at the wrong thing):

  writer process : "[event-log] DEGRADED: cannot write events" on stderr, and
                   emit() returns None
  qa.py events   : "no transactions match. The log starts when the platform
                   next does something - it is not backfilled."   exit 0
  alert_rules    : every rule `ok`

The alert path is the sharpest: `evaluate()` carries a comment saying
"Reporting 'ok' here would mean a broken log reads as a healthy estate", two
lines above the branch that did exactly that. A rule watching for gate
refusals reported clear while nothing at all was being recorded.

THE FIX IS A STATE A READER CAN ESTABLISH WITHOUT WRITING. A read-only surface
must not create a probe file to find out whether it could (this repo already
refuses that shape - `spec_verify` inventing a plan entry, "rendering a
workflow must not advance it"). What is establishable is the shape and
permissions of the path, so `log_state()` answers ok / misconfigured /
unwritable / absent, and `unrecordable()` collapses that to the one bit each
reader needs.

WHAT IS NOT CLAIMED, said out loud because the over-claim is the same defect
one step along: `ok` means no problem could be ESTABLISHED, never that the log
is complete. Only a write proves that.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import event_log                                        # noqa: E402


@pytest.fixture
def blocked(tmp_path, monkeypatch):
    """An events path with a FILE where the directory must be - the shape a
    stale path or a mistyped AIQE_EVENTS_DIR actually produces, and the only
    write-failure shape reproducible on a Windows dev host."""
    p = tmp_path / "events"
    p.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("AIQE_EVENTS_DIR", str(p))
    return p


def test_a_reader_can_tell_an_unrecordable_log_from_a_quiet_one(blocked):
    """THE PROPERTY. The reader never wrote anything, so this must not depend
    on `health()`."""
    st = event_log.log_state()
    assert st["state"] == "misconfigured", st
    assert event_log.unrecordable(st) is True
    assert "not a directory" in st["reason"]
    # The reason must name the fix, not just the symptom.
    assert "AIQE_EVENTS_DIR" in st["reason"]
    # And the process-local flag really is blind here, which is the whole
    # reason log_state exists: assert the premise rather than assuming it.
    assert event_log.health()["degraded"] is False


def test_an_unwritable_directory_is_its_own_state(tmp_path, monkeypatch):
    """The read-only-rootfs shape. `access` is injected because a Windows dev
    host reports a read-only directory as writable - the branch is driven, not
    chmod-ed."""
    d = tmp_path / "events"
    d.mkdir()
    monkeypatch.setenv("AIQE_EVENTS_DIR", str(d))
    st = event_log.log_state(access=lambda p, mode: False)
    assert st["state"] == "unwritable", st
    assert event_log.unrecordable(st) is True
    assert str(d) in st["reason"]


def test_a_missing_tree_is_judged_by_the_parent_that_would_hold_it(tmp_path,
                                                                   monkeypatch):
    """Checking only the leaf reports a whole missing tree as fine, because a
    path that does not exist is trivially not-a-file."""
    monkeypatch.setenv("AIQE_EVENTS_DIR", str(tmp_path / "a/b/c/events"))
    seen = []

    def access(p, mode):
        seen.append(pathlib.Path(p))
        return False

    st = event_log.log_state(access=access)
    assert st["state"] == "unwritable", st
    # The ancestor probed must be one that EXISTS, or the check is vacuous.
    assert seen and seen[0].exists(), seen


def test_an_absent_log_is_not_reported_as_broken(tmp_path, monkeypatch):
    """THE OVER-FIX DIRECTION, pinned as hard as the defect. A fresh estate has
    no events directory and nothing has happened yet; warning there is a
    warning that fires on a healthy install, which is the kind operators learn
    to scroll past."""
    monkeypatch.setenv("AIQE_EVENTS_DIR", str(tmp_path / "events"))
    st = event_log.log_state()
    assert st["state"] == "absent", st
    assert event_log.unrecordable(st) is False


def test_a_healthy_log_says_nothing(tmp_path, monkeypatch):
    d = tmp_path / "events"
    d.mkdir()
    monkeypatch.setenv("AIQE_EVENTS_DIR", str(d))
    st = event_log.log_state()
    assert st["state"] == "ok" and st["reason"] is None
    assert event_log.unrecordable(st) is False


def _qa_events(env_dir):
    r = subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), "events"],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL,
                       env={**os.environ, "AIQE_EVENTS_DIR": str(env_dir)})
    return r


def test_the_cli_refuses_to_call_an_unrecorded_estate_quiet(blocked):
    """DRIVEN, because the library being right proves nothing about the surface
    the use case names - that lesson is recorded twice over in this repo."""
    r = _qa_events(blocked)
    out = r.stdout + r.stderr
    assert "NOT being recorded" in out, out[-600:]
    assert "not evidence that nothing happened" in out, out[-600:]
    # The old sentence PROMISED future recording ("the log starts when the
    # platform next does something"), which is exactly wrong when nothing can
    # ever be written there.
    assert "log starts when the platform next" not in out, out[-600:]


def test_the_cli_still_explains_an_ordinarily_empty_result(tmp_path):
    """The other direction at the surface: an empty-but-healthy log keeps the
    message that tells a newcomer why they see nothing."""
    d = tmp_path / "events"
    d.mkdir()
    r = _qa_events(d)
    out = r.stdout + r.stderr
    assert "log starts when the platform next" in out, out[-400:]
    assert "NOT being recorded" not in out, out[-400:]


def test_every_rule_is_unevaluable_when_nothing_can_be_recorded(blocked,
                                                                tmp_path,
                                                                monkeypatch):
    """The sharpest consequence: alerting that reports clear while blind."""
    import importlib
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"rules": [
        {"id": "r1", "name": "gate refusals", "enabled": True,
         "kinds": ["gate.refused"], "threshold": 1, "window_minutes": 60}]}),
        encoding="utf-8")
    monkeypatch.setenv("AIQE_ALERT_RULES_FILE", str(rules))
    import alert_rules
    importlib.reload(alert_rules)
    out = alert_rules.evaluate(notify=False, commit=False)
    assert [s["status"] for s in out] == ["unevaluable"], out
    assert "not being recorded" in out[0]["reason"], out[0]
    # Delivered by email and printed to a cp1252 console.
    out[0]["reason"].encode("cp1252")


def test_a_healthy_log_still_evaluates_rules(tmp_path, monkeypatch):
    """OVER-FIX GUARD: a fix that made every rule unevaluable would silence
    alerting altogether, which is worse than the defect it replaced."""
    import importlib
    d = tmp_path / "events"
    d.mkdir()
    monkeypatch.setenv("AIQE_EVENTS_DIR", str(d))
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"rules": [
        {"id": "r1", "name": "gate refusals", "enabled": True,
         "kinds": ["gate.refused"], "threshold": 1, "window_minutes": 60}]}),
        encoding="utf-8")
    monkeypatch.setenv("AIQE_ALERT_RULES_FILE", str(rules))
    import alert_rules
    importlib.reload(alert_rules)
    out = alert_rules.evaluate(notify=False, commit=False)
    assert [s["status"] for s in out] == ["ok"], out


def test_every_reader_of_this_log_consults_the_reader_state():
    """THE INVARIANT, not today's four call sites. `health()` is the trap: it
    LOOKS like the right question and answers a different one, so a fifth
    reader reaching for it is the defect returning. Any module that reads
    events for a human must also ask log_state/unrecordable."""
    readers = ["bin/qa.py", "bin/dashboard.py", "bin/dashboard_server.py",
               "engine/lib/alert_rules.py"]
    for rel in readers:
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "log_state" in src, \
            f"{rel} reads the transaction log but never asks whether it is " \
            f"being recorded; health() is process-local and cannot tell it"


def test_the_served_payload_carries_the_reader_state():
    """The Activity view is client-side, so the flag has to travel: a backend
    that knows and a row template with no field for it is a shape this repo has
    already shipped once (the alert rule's recipients)."""
    src = (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")
    i = src.index('"/api/events"')
    block = src[i:i + 2500]
    assert '"log_state": event_log.log_state()' in block, \
        "/api/events does not send the reader state the Activity view renders"
    ui = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert "d.log_state" in ui, "the Activity view never reads log_state"


def test_the_activity_loader_asks_the_decision_functions():
    """The functions below are executed by a test; the LOADER that calls them
    is not (it needs fetch and a DOM). So the one thing left to assert is that
    it still asks — a mutation replacing the call with a constant is invisible
    to the executed check, which found it and left this hole. Scoped to the
    loader, following the same fix the TLS note pin needed: an unscoped search
    matches these names where they are DEFINED."""
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    i = src.index("async function refreshActivity()")
    body = src[i:i + 3000]
    for call in ("evUnrecordable(d.log_state)", "evEmptyMessage(d.log_state)"):
        assert call in body, \
            f"the Activity loader no longer calls {call}; its branch is dead " \
            f"however correct the function it stopped asking"


def _ev_functions():
    """The Activity view's two decision functions, lifted out of the page
    script so they can be RUN. Source-text assertions cannot tell a branch that
    is read from one that is dead: the first version of this pin greped for the
    strings and a mutation disabling the branch survived it."""
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    out = []
    for name in ("function evUnrecordable(ls) {", "function evEmptyMessage(ls) {"):
        i = src.index(name)
        depth, j = 0, src.index("{", i)
        for j in range(src.index("{", i), len(src)):
            depth += (src[j] == "{") - (src[j] == "}")
            if depth == 0:
                break
        out.append(src[i:j + 1])
    return "\n".join(out)


def _node(script):
    exe = shutil.which("node")
    if not exe:                                  # pragma: no cover - CI has node
        pytest.skip("node is required to execute the Activity view's functions")
    r = subprocess.run([exe, "-e", script], capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, timeout=60)
    assert r.returncode == 0, r.stderr[-800:]
    return r.stdout


def test_the_activity_view_really_branches_on_the_reader_state():
    """DRIVEN in node against fixture payloads, both directions."""
    script = _ev_functions() + """
const cases = [
  {state: 'misconfigured', reason: 'r'},
  {state: 'unwritable', reason: 'r'},
  {state: 'absent', reason: 'r'},
  {state: 'ok', reason: null},
  null,
];
console.log(JSON.stringify(cases.map(c => [evUnrecordable(c), evEmptyMessage(c)])));
"""
    got = json.loads(_node(script))
    flags = [g[0] for g in got]
    assert flags == [True, True, False, False, False], got
    # An unrecordable log must not be told the log "starts when the platform
    # next does something" — nothing can ever be written there.
    assert "NOT evidence" in got[0][1] and "next does something" not in got[0][1]
    # And the ordinary empty case keeps the message a newcomer needs.
    assert "next does something" in got[3][1] and "NOT evidence" not in got[3][1]
    # An absent log is the ordinary case, not the alarming one.
    assert got[2][1] == got[3][1]

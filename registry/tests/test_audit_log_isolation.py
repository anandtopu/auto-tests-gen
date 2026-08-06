"""The suite must not write to the estate's real transaction log.

`event_log` resolves its directory from AIQE_EVENTS_DIR at call time and no test
set it, so every emit during a run — including the ~1400 refusals and failures
the adversarial suites provoke deliberately — landed in `reports/events/`. A
measured run left 2516 events there, 56% of them failures.

That breaks two things:

  * The log stops being evidence. It is the estate's record of what happened;
    an auditor reading it saw 41 attempts to remove a repo and 44 factory
    clears that no human ever performed.
  * `make maintain` runs `alert_rules.evaluate()`, which counts matching events
    in a window and DELIVERS through the Notify port. `make review` could page
    somebody with its own attack traffic.

conftest.py redirects the log for the whole session. These pin that the
redirection is in force and that nothing bypasses it, because the failure is
invisible from inside a passing suite: every test goes green either way.
"""
import json
import os
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import event_log  # noqa: E402

REAL = ROOT / "reports/events"


def _count(d):
    if not d.is_dir():
        return 0
    return sum(1 for p in d.glob("*.jsonl")
               for l in p.read_text(encoding="utf-8", errors="replace").split("\n")
               if l.strip())


def test_the_events_dir_is_redirected_away_from_the_estate():
    target = (os.environ.get("AIQE_EVENTS_DIR") or "").strip()
    assert target, "AIQE_EVENTS_DIR is unset — emits land in the real audit log"
    assert pathlib.Path(target).resolve() != REAL.resolve(), \
        "the suite is pointed at the estate's own transaction log"


def test_emitting_does_not_reach_the_real_log():
    """The behavioural half. The env pin above passes if someone sets the
    variable while a caller resolves the path some other way."""
    before = _count(REAL)
    event_log.emit("request.received", source="ui", target="/api/_isolation_probe",
                   outcome="ok", detail={"probe": True})
    assert _count(REAL) == before, \
        "an emit reached reports/events/ despite the redirection"


def test_the_probe_actually_landed_somewhere():
    """Without this, the assertion above passes when emit() is broken and
    writes nothing at all — proving isolation by proving nothing happened."""
    d = pathlib.Path(os.environ["AIQE_EVENTS_DIR"])
    hits = [l for p in d.glob("*.jsonl")
            for l in p.read_text(encoding="utf-8", errors="replace").split("\n")
            if "_isolation_probe" in l]
    assert hits, "the probe event was not written anywhere — emit() is a no-op"
    rec = json.loads(hits[-1])
    assert rec["target"] == "/api/_isolation_probe"


def test_no_server_fixture_leaves_the_events_dir_to_default():
    """Instance-proof, not invariant-proof, would be listing today's fixtures.
    This asserts the SHAPE: any test module that launches the dashboard server
    or the receiver inherits the environment, so none of them may re-point
    AIQE_EVENTS_DIR back at the estate.
    """
    offenders = []
    for p in sorted((ROOT / "registry/tests").glob("test_*.py")):
        src = p.read_text(encoding="utf-8", errors="replace")
        if "AIQE_EVENTS_DIR" not in src:
            continue
        for i, line in enumerate(src.split("\n"), 1):
            if "AIQE_EVENTS_DIR" not in line or line.lstrip().startswith("#"):
                continue
            if "reports/events" in line or 'reports\\events' in line:
                offenders.append(f"{p.name}:{i}  {line.strip()[:80]}")
    assert not offenders, \
        "a test points the transaction log at the estate:\n  " + "\n  ".join(offenders)


def test_every_shell_suite_in_make_review_redirects_the_log_too():
    """conftest only covers pytest. The shell half of `make review` runs in its
    own processes, and after the pytest fix a measured run still moved the real
    log 2516 -> 2518 — smaller, but the same defect.

    Asserted over whatever `make review` actually runs, so a suite added to that
    chain tomorrow is covered without editing this list.
    """
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = re.search(r"^review:\n(.*?)(?=\n[a-z])", mk, re.S | re.M)
    assert recipe, "could not read the review target out of the Makefile"
    scripts = re.findall(r"bash (\S+\.sh)", recipe.group(1))
    assert scripts, "could not read the review chain out of the Makefile"
    missing = [s for s in scripts
               if "AIQE_EVENTS_DIR" not in (ROOT / s).read_text(encoding="utf-8",
                                                                errors="replace")]
    assert not missing, (
        "these run in `make review` and write to the estate's audit log:\n  "
        + "\n  ".join(missing))


def test_alert_rules_would_have_counted_the_suites_own_traffic():
    """Why this matters, made concrete rather than asserted in a comment: the
    alert evaluator counts events by kind in a window, and the kinds the
    adversarial suites generate in bulk are exactly the ones a failure-rate
    rule watches. Nothing here fires a rule; it pins that the coupling is real,
    so removing the isolation is a visible decision.
    """
    import alert_rules
    src = (ROOT / "engine/lib/alert_rules.py").read_text(encoding="utf-8")
    assert "event_log" in src, "alert_rules no longer reads the transaction log"
    assert hasattr(alert_rules, "evaluate")
    # The Notify port is reached from evaluation — that is the paging path.
    assert "notify" in src.lower()

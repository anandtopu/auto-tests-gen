"""A correct refusal that nobody can look up reads as a broken command.

`make cost-reconcile` printed a JSON blob and exited 75, so an operator saw
`make: *** [Makefile:206: cost-reconcile] Error 75` and nothing else. The reason
WAS in the JSON -- "ANTHROPIC_ADMIN_KEY is not configured" -- and the exit code
was documented nowhere at all, in either the user guide or the Makefile comment.

Exit 75 is right and is deliberately kept: "could not reconcile" is not success
(C13), and `make maintain` relies on it to report the step DEGRADED rather than
failing the nightly job. What was missing is the sentence. This is the same
lesson `make maintain` already learned -- CLAUDE.md records that it "printed
`exit 75` while holding the reason" -- landing one caller along.

The message must also say WHICH situation, because 75 covers two with opposite
urgency: nothing was reconciled (benign, fix the configuration) versus spend
reconciled, drift found, and the alarm undelivered (the real number, and nobody
was told).
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import cost_reconcile                                          # noqa: E402


def _not_reconciled(reason="ANTHROPIC_ADMIN_KEY is not configured",
                    code="credential-missing"):
    return {"schema": 1, "status": "not-reconciled", "reason": reason,
            "provider_usage": {"reason_code": code, "reason": reason},
            "notification": {"required": False, "state": "not-required"}}


def _drift_undelivered():
    return {"schema": 1, "status": "reconciled-drift",
            "drift_usd": "4.000000", "drift_pct": "40.000000",
            "notification": {"required": True, "state": "failed",
                             "channel": "slack"}}


def test_a_missing_credential_names_the_credential_and_the_fix():
    """The credential name reaches the operator through the PORT's reason,
    which this function echoes -- never from a vendor branch in engine code.
    The first version of _explain() branched on the literal name and the
    existing no-vendor-branch pin caught it, which is the pin working."""
    lines = " ".join(cost_reconcile._explain(_not_reconciled()))
    assert "nothing was reconciled" in lines
    assert "ANTHROPIC_ADMIN_KEY" in lines and "fix:" in lines
    # Both branches say "fix:", so asserting only that let a mutation send a
    # missing credential to "check the billing API is reachable" -- the wrong
    # place, and the mirror of the failure the sibling test guards. Assert the
    # branch that actually fired.
    assert "configure the provider billing credential" in lines
    assert "reachable" not in lines, \
        "a missing credential was reported as an unreachable API"
    src = (ROOT / "engine/lib/cost_reconcile.py").read_text(encoding="utf-8")
    assert "ANTHROPIC_ADMIN_KEY" not in src,         "a vendor name is hardcoded in engine code again"


def test_an_unreachable_billing_api_does_not_send_you_after_a_credential():
    """Naming the wrong fix sends the reader somewhere that is not the
    problem -- the failure mode the gate exit-code table was pinned for."""
    lines = " ".join(cost_reconcile._explain(
        _not_reconciled("usage adapter exceeded 30s", code="provider-timeout")))
    assert "credential" not in lines
    assert "reachable" in lines


def test_the_two_situations_do_not_share_a_sentence():
    """75 covers both; the message is what separates them."""
    absent = " ".join(cost_reconcile._explain(_not_reconciled()))
    undelivered = " ".join(cost_reconcile._explain(_drift_undelivered()))
    assert absent != undelivered
    assert "DRIFT was found" in undelivered
    assert "nobody was notified" in undelivered
    assert "nothing was reconciled" not in undelivered, \
        "a real drift figure was reported as nothing having been reconciled"


def test_the_undelivered_alarm_says_the_figures_survive():
    """The alarm failing does not mean the measurement is lost, and a reader
    who thinks it is will re-run a billable check for data already on disk."""
    lines = " ".join(cost_reconcile._explain(_drift_undelivered()))
    assert "published" in lines and "Cost view" in lines


def test_every_message_explains_what_exit_75_means():
    for doc in (_not_reconciled(), _drift_undelivered()):
        lines = " ".join(cost_reconcile._explain(doc))
        assert "exit 75" in lines and "DEGRADED" in lines


def test_a_missing_reason_is_said_rather_than_left_blank():
    lines = " ".join(cost_reconcile._explain(
        {"status": "not-reconciled", "reason": ""}))
    assert "reason not recorded" in lines


def test_the_explanation_is_console_safe():
    """This repo is developed under Git Bash on Windows: a non-cp1252
    character in a printed string renders as `?`. Measured -- the first
    version of this message used an em dash and did exactly that."""
    for doc in (_not_reconciled(), _drift_undelivered(),
                _not_reconciled("usage adapter exceeded 30s")):
        for line in cost_reconcile._explain(doc):
            bad = [c for c in line if ord(c) > 255]
            assert not bad, f"non-cp1252 character(s) {bad} in: {line}"


def test_the_command_prints_the_explanation_and_still_exits_75():
    """Driven, not asserted on source: a helper nothing calls is the exact
    shape of the defect this replaces."""
    r = subprocess.run([sys.executable, "engine/lib/cost_reconcile.py",
                        "--days", "1"], cwd=ROOT, capture_output=True,
                       text=True, stdin=subprocess.DEVNULL, timeout=300)
    if r.returncode == 0:
        import pytest
        pytest.skip("this estate reconciled; nothing to explain")
    assert r.returncode == cost_reconcile.EXTERNAL_UNAVAILABLE
    assert "COST_RECONCILE:" in r.stderr, \
        "the exit-75 explanation never reached the operator"
    assert "exit 75" in r.stderr
    assert r.stdout.strip().startswith("{"), \
        "the machine-readable JSON must stay on stdout, unmixed with prose"


def test_a_successful_run_says_nothing_extra():
    """A note that fires on a correct run is one operators learn to scroll
    past -- the same rule the TLS-verification note follows."""
    src = (ROOT / "engine/lib/cost_reconcile.py").read_text(encoding="utf-8")
    body = src.split("def main(")[1]
    assert re.search(r"if external:\s*\n(\s+#.*\n)*\s+for line in _explain",
                     body), "the explanation is not gated on the failure branch"


def test_the_exit_code_is_documented_for_an_operator():
    """Scoped to the exit-codes paragraph and keyed to the code the SOURCE
    emits, following the gate exit-code table's precedent.

    Two earlier versions of this pin were too weak and mutation testing proved
    it: the first split on a string that appears twice and asserted against the
    sliver between the occurrences; the second searched a 3000-character window
    for "75" and "DEGRADED", both of which occur in neighbouring prose, so
    redacting the explanation and even renumbering the code left it green. A
    doc pin has to assert the CLAIM, not the presence of its vocabulary.
    """
    guide = (ROOT / "docs/user-guide.md").read_text(encoding="utf-8")
    assert "**Exit codes.**" in guide, \
        "the cost-reconcile exit-code section is gone"
    para = guide.split("**Exit codes.**")[1].split("\n\n")[0]
    code = str(cost_reconcile.EXTERNAL_UNAVAILABLE)
    assert f"`{code}`" in para, (
        f"the source exits {code} and the documented section does not name it")
    assert "external system was unavailable" in para, \
        "the section names a code without saying what it means"
    assert "DEGRADED" in para, \
        "nothing tells the reader maintenance tolerates this and keeps going"

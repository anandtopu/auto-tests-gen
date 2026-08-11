"""`check-integrations` said `ok` without checking a single certificate.

`AIQE_SSL_VERIFY=0` is a supported opt-out for corporate CA estates, and
integration_check HONOURED it -- `_ssl_context()` returns an unverified context
-- while never REPORTING it. Measured before the fix: the printed output was
byte-identical with verification on and off.

That matters because of what this command is FOR. It is the operator's "is my
setup right?" check, and its exit code is a CI contract. An `ok` meant "the
endpoint answered", not "its certificate was trusted", and nothing said which.

Found immediately afterwards, by the fix itself: this repo's own `.env` carries
`AIQE_SSL_VERIFY=0` on line 1. The estate had certificate verification disabled
and no surface mentioned it.

It stays EXIT 0 deliberately. Refusing would break the deployments the opt-out
exists for; being unmissable is the fix, and pretending an operator's explicit
choice is an error is not.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECK = ROOT / "engine/lib/integration_check.py"
sys.path.insert(0, str(ROOT / "engine" / "lib"))
import integration_check as ic


def _run(*args, verify):
    env = dict(os.environ, AIQE_MOCK="1", AIQE_SSL_VERIFY=verify)
    return subprocess.run([sys.executable, str(CHECK), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=env,
                          stdin=subprocess.DEVNULL, timeout=300, cwd=str(ROOT))


def test_disabled_verification_is_named_in_the_printed_summary():
    r = _run(verify="0")
    assert "AIQE_SSL_VERIFY=0" in r.stdout
    assert "OFF" in r.stdout
    # It must correct the reading an `ok` invites, not merely mention a flag.
    assert "NOT that its certificate was trusted" in r.stdout


def test_it_names_the_systems_the_flag_actually_reaches():
    """A warning that does not say WHAT is unverified leaves the operator to
    grep the adapters. These are the ones that honour the knob.

    Scoped to the NOTE line, not the whole output: the first version searched
    stdout, and a mutation stripping the system list from the note SURVIVED,
    because "Splunk" and "OpenHands" also appear as check ROWS above it. The
    assertion was reading someone else's text.
    """
    out = _run(verify="0").stdout
    note = next((l for l in out.splitlines() if "AIQE_SSL_VERIFY=0" in l), "")
    assert note, "no TLS note to inspect"
    for system in ("Jira", "Stash", "Splunk", "OpenHands"):
        assert system in note, f"the note does not name {system}: {note[:160]}"


def test_enabled_verification_says_nothing():
    """A note that prints on a correctly configured estate is one operators
    learn to scroll past."""
    r = _run(verify="1")
    assert "AIQE_SSL_VERIFY" not in r.stdout


def test_the_state_is_in_the_json_payload_too():
    """`--json` is the branch a CI job reads; a machine consumer deciding
    "are my integrations healthy?" needs this as data, not prose."""
    assert json.loads(_run("--json", verify="0").stdout)["tls_verification"] \
        == "disabled"
    assert json.loads(_run("--json", verify="1").stdout)["tls_verification"] \
        == "enabled"


def test_disabled_verification_does_not_change_the_exit_code():
    """The opt-out is supported. Turning it into a failure would break the
    corporate-CA deployments it exists for -- and an operator who set it
    deliberately is not making an error."""
    assert _run(verify="0").returncode == 0
    assert _run("--json", verify="0").returncode == 0


@pytest.mark.parametrize("value,expected", [
    ("0", "disabled"), ("1", "enabled"), ("", "enabled"),
    ("false", "enabled"), ("00", "enabled"), (" 0 ", "disabled"),
])
def test_the_report_uses_the_same_literal_rule_as_the_adapters(value, expected,
                                                               monkeypatch):
    """architecture.md pins the knob as strict-by-literal-`0`: only "0" turns
    verification off, so only "0" may be REPORTED as off. If the report were
    more (or less) eager than `_ssl_context()`, it would describe a state the
    run is not in -- which is worse than saying nothing.
    """
    monkeypatch.setenv("AIQE_SSL_VERIFY", value)
    assert ic.run()["tls_verification"] == expected


def test_the_report_and_the_ssl_context_cannot_disagree(monkeypatch):
    """Behavioural rather than textual: ask the module for a context and for
    its report under the same env, and require them to agree."""
    import ssl
    for value, verified in (("0", False), ("1", True)):
        monkeypatch.setenv("AIQE_SSL_VERIFY", value)
        ctx = ic._ssl_context()
        reported = ic.run()["tls_verification"] == "enabled"
        actually_verified = ctx is None or ctx.verify_mode != ssl.CERT_NONE
        assert actually_verified is verified
        assert reported is verified, \
            f"report says {reported} but the SSL context verifies={actually_verified}"

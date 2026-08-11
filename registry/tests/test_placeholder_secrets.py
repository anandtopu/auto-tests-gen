"""A placeholder credential must not read as configured auth.

`deploy/openshift/deploy.sh` applies `secret.example.yaml` when an operator has
not created `secret.yaml`, and warns AT DEPLOY TIME. Nothing said so at RUNTIME:
the token is non-empty, so `bin/dashboard_server.py` printed `auth: token` and
its "listening with NO auth" warning could not fire. The Dockerfile and the
OpenShift ConfigMap both set `AIQE_UI_HOST=0.0.0.0`, so the result is a port
that approves plans, queues runs and resets the estate, looking correctly
configured while protected by a value published in this repository.

Same shape as the warning already in that file, whose own comment reads "the
rule lived only in the comment above, which operators do not read". Here it
lived only in deploy.sh's scrollback.

Driven before pinning: booting each server with the placeholder on 0.0.0.0
prints the warning; booting with a real token stays silent.
"""
import os
import pathlib
import re
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine" / "lib"))
import placeholder_secrets as ps

EXAMPLE = ROOT / "deploy/openshift/secret.example.yaml"


def test_the_known_values_match_the_shipped_example_file():
    """The literals cannot be parsed at runtime (the manifests are not in the
    image), so they are pinned against the file instead. A rotated placeholder
    that only changed in the yaml would leave this check looking for a value
    nobody ships."""
    text = EXAMPLE.read_text(encoding="utf-8")
    shipped = set(re.findall(r'^\s*AIQE_(?:UI|HOOK)_TOKEN:\s*"([^"]+)"',
                             text, re.M))
    assert shipped, "no token placeholders found in secret.example.yaml"
    assert shipped == set(ps.PLACEHOLDERS), (
        f"placeholder_secrets and secret.example.yaml disagree: "
        f"module={set(ps.PLACEHOLDERS)} file={shipped}")


@pytest.mark.parametrize("value", ["change-me-ui", "change-me-hook",
                                   "  change-me-ui  "])
def test_a_placeholder_is_recognised(value):
    assert ps.is_placeholder(value)


@pytest.mark.parametrize("value", ["", None, "s3cret", "change-me-ui-really",
                                   "CHANGE-ME-UI"])
def test_a_real_or_absent_credential_is_not_flagged(value):
    """An EMPTY value is deliberately not a placeholder: "no token at all" is a
    different state with its own, louder warning, and merging them would blur
    two situations an operator fixes differently. A deliberately upper-cased
    value is (a bad) operator choice, not the shipped string."""
    assert not ps.is_placeholder(value)


def test_the_warning_names_the_variable_the_file_and_the_consequence():
    msg = ps.warning("AIQE_UI_TOKEN", "change-me-ui", "anyone can reset it.")
    assert "AIQE_UI_TOKEN" in msg
    assert "secret.example.yaml" in msg
    assert "anyone can reset it." in msg
    assert ps.warning("AIQE_UI_TOKEN", "s3cret", "x") is None


def _boot(script, extra, seconds=6):
    env = dict(os.environ, AIQE_MOCK="1", **extra)
    p = subprocess.Popen([sys.executable, "-u", str(ROOT / script)],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, env=env, stdin=subprocess.DEVNULL,
                         cwd=str(ROOT))
    time.sleep(seconds)
    p.terminate()
    try:
        out, _ = p.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
    return out or ""


@pytest.mark.parametrize("script,extra,var", [
    ("bin/dashboard_server.py",
     {"AIQE_UI_TOKEN": "change-me-ui", "AIQE_UI_HOST": "0.0.0.0",
      "AIQE_UI_PORT": "4987"}, "AIQE_UI_TOKEN"),
    ("bin/taskevent_receiver.py",
     {"AIQE_HOOK_TOKEN": "change-me-hook", "AIQE_HOOK_HOST": "0.0.0.0",
      "AIQE_HOOK_PORT": "4986"}, "AIQE_HOOK_TOKEN"),
])
def test_each_server_warns_at_startup_on_the_placeholder(script, extra, var):
    """BOTH, because fixing one would leave the other -- and the two are the
    only network-reachable surfaces this platform ships."""
    out = _boot(script, extra)
    assert "placeholder" in out.lower(), \
        f"{script} started with the placeholder token and said nothing:\n{out}"
    assert var in out


def test_a_real_token_produces_no_placeholder_warning():
    """A warning that fires on a correctly configured server is one operators
    learn to ignore, which would cost more than it buys."""
    out = _boot("bin/dashboard_server.py",
                {"AIQE_UI_TOKEN": "s3cret-not-a-placeholder",
                 "AIQE_UI_HOST": "0.0.0.0", "AIQE_UI_PORT": "4985"})
    assert "auth: token" in out, f"the server did not start cleanly:\n{out}"
    assert "placeholder" not in out.lower()


def test_both_servers_use_the_one_definition():
    """Nine-callers-leaves-the-tenth: the values live in one module, not copied
    into each server."""
    for script in ("bin/dashboard_server.py", "bin/taskevent_receiver.py"):
        src = (ROOT / script).read_text(encoding="utf-8")
        assert "placeholder_secrets" in src, f"{script} does not consult it"
        assert "change-me" not in src, \
            f"{script} hardcodes a placeholder value instead of importing it"

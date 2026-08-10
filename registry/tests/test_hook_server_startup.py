"""The receiver's no-token warning survives being piped.

Found by starting it. `bin/taskevent_receiver.py` prints a startup banner and,
when it binds a NON-loopback interface with no token, a warning that every
reachable client can enqueue work. Both went to stdout with no flush, and
Python block-buffers stdout when it is a pipe or a file -- so
`make hook-server > log`, or any supervisor capturing output, showed a
ZERO-BYTE log. The same command under `python3 -u` printed both lines.

The container was never affected: the Dockerfile sets PYTHONUNBUFFERED=1.
That is precisely why it stayed invisible -- the deployed path was fine and the
documented direct path was not.

These start the real process with its output redirected, because that is the
condition that broke it; asserting on the source would not have caught it.
"""
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
RECEIVER = ROOT / "bin/taskevent_receiver.py"


def _start(tmp_path, port, host="0.0.0.0", token=None, wait=4.0):
    """Run the receiver with stdout/stderr REDIRECTED (never a tty) and return
    whatever it managed to write before being stopped."""
    import os
    log = tmp_path / "out.log"
    env = dict(os.environ)
    env.update({"AIQE_HOOK_HOST": host, "AIQE_HOOK_PORT": str(port)})
    env.pop("PYTHONUNBUFFERED", None)      # the condition under test
    if token:
        env["AIQE_HOOK_TOKEN"] = token
    else:
        env.pop("AIQE_HOOK_TOKEN", None)
    with open(log, "wb") as fh:
        p = subprocess.Popen([sys.executable, str(RECEIVER)], cwd=str(ROOT),
                             stdout=fh, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, env=env)
        try:
            time.sleep(wait)
        finally:
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
    return log.read_text(encoding="utf-8", errors="replace")


def test_the_no_token_warning_reaches_a_piped_log(tmp_path):
    out = _start(tmp_path, 4993)
    assert "WARNING" in out and "NO token" in out, (
        "the exposure warning never reached the log -- block-buffered stdout "
        f"swallowed it (log was {len(out)} bytes)")
    assert "AIQE_HOOK_TOKEN" in out, "the warning does not name the fix"


def test_the_startup_banner_reaches_a_piped_log(tmp_path):
    out = _start(tmp_path, 4992)
    assert "TaskEvent receiver:" in out, \
        "an operator piping the log sees nothing at all at startup"


def test_a_loopback_bind_does_not_cry_wolf(tmp_path):
    """The control. Warning on every start would train people to ignore it --
    auth off on localhost is a fine dev default."""
    out = _start(tmp_path, 4991, host="127.0.0.1")
    assert "TaskEvent receiver:" in out, "the banner is missing too"
    assert "WARNING" not in out, "warns even on loopback"


def test_a_configured_token_silences_the_warning(tmp_path):
    """The other control: the warning is about the no-token case only."""
    out = _start(tmp_path, 4990, token="s3cret")
    assert "WARNING" not in out
    assert "X-AIQE-Token required" in out, "the banner does not say auth is on"


def test_the_startup_messages_are_console_safe():
    """Git Bash on Windows renders a non-cp1252 dash as a replacement char --
    observed in this very warning before it was changed.

    Scoped to the STARTUP PRINTS on purpose. A first version banned the
    character file-wide and failed on docstrings and comments, which are never
    printed, and on a JSON error body, which is UTF-8 encoded over HTTP and
    perfectly safe. A rule that fires on things that cannot break trains people
    to weaken it.
    """
    src = RECEIVER.read_text(encoding="utf-8")
    block = src[src.index('print(f"TaskEvent receiver:'):]
    block = block[:block.index("serve_forever")]
    assert "—" not in block, \
        "an em-dash is back in the startup output, which goes to a console"


# --- the same two gaps on the dashboard server ------------------------------
#
# Found by sweeping after the receiver fix. The dashboard's log was ZERO BYTES
# across a run that served 13 requests (same block-buffering), and it never
# warned about running without auth at all -- the rule "never 0.0.0.0 without a
# token" lived only in a code comment, which operators do not read.
#
# It is the more sensitive of the two servers: its POST routes approve plans,
# queue runs and factory-reset the estate.

DASHBOARD = ROOT / "bin/dashboard_server.py"


def _start_dash(tmp_path, port, host="0.0.0.0", token=None, sso=None, wait=4.0):
    import os
    log = tmp_path / "dash.log"
    env = dict(os.environ)
    env.update({"AIQE_UI_HOST": host, "AIQE_UI_PORT": str(port)})
    env.pop("PYTHONUNBUFFERED", None)
    for var, val in (("AIQE_UI_TOKEN", token), ("AIQE_SSO_HEADER", sso)):
        if val:
            env[var] = val
        else:
            env.pop(var, None)
    with open(log, "wb") as fh:
        p = subprocess.Popen([sys.executable, str(DASHBOARD)], cwd=str(ROOT),
                             stdout=fh, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, env=env)
        try:
            time.sleep(wait)
        finally:
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
    return log.read_text(encoding="utf-8", errors="replace")


def test_the_dashboard_banner_reaches_a_piped_log(tmp_path):
    out = _start_dash(tmp_path, 4987)
    assert "AI QE dashboard:" in out, \
        f"nothing reached the log at startup ({len(out)} bytes)"


def test_an_unauthenticated_dashboard_on_a_public_interface_warns(tmp_path):
    out = _start_dash(tmp_path, 4986)
    assert "WARNING" in out and "NO auth" in out, \
        "a dashboard reachable without auth says nothing about it"
    # The warning must say what is at stake, not just that auth is off.
    assert "approve plans" in out and "reset" in out
    assert "AIQE_UI_TOKEN" in out, "the warning does not name the fix"


def test_a_token_silences_the_dashboard_warning(tmp_path):
    out = _start_dash(tmp_path, 4985, token="s3cret")
    assert "WARNING" not in out
    assert "auth: token" in out


def test_an_sso_header_also_silences_it(tmp_path):
    """SSO behind a proxy is the other supported way to authenticate; warning
    through it would be crying wolf at a correctly-secured deployment."""
    out = _start_dash(tmp_path, 4984, sso="X-Forwarded-User")
    assert "WARNING" not in out
    assert "SSO header" in out


def test_loopback_does_not_warn(tmp_path):
    out = _start_dash(tmp_path, 4983, host="127.0.0.1")
    assert "AI QE dashboard:" in out
    assert "WARNING" not in out, "warns on a localhost dev server"

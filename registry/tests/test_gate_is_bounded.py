"""The gate call is bounded, and a timeout is not reported as a test failure.

engine/gate/gate.sh EXECUTES each test repo's own `commands.{lint,test}` --
that is its job, and it is also the platform's widest trust boundary (a repo's
committers choose those commands; docs/onboarding-new-team.md).

pipeline.sh called it with no timeout at all, while the two OTHER callers of the
same script both bound it (.openhands/hooks/gate-check.sh uses 300s). So a lint
or test command that never returned hung the run forever:

  - budget.py's MAX_WALLCLOCK_MIN is checked BEFORE each phase, and the gate
    runs after the last phase, so the run budget cannot end it.
  - out/.pipeline.lock has a 90-minute stale break, which frees the LOCK for the
    next run -- the hung process keeps running, and the next run's gate can hang
    exactly the same way behind it.

The second half matters as much as the first. timeout(1) exits 124, and 124 was
absent from run_progress.EXIT_MEANINGS, so bounding the call without documenting
the code would have rendered a killed gate as an unexplained number -- or worse,
let a reader assume the tests failed. They did not fail: they never finished.
That is the C13 distinction, so it is asserted here rather than left to prose.
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PIPELINE = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")

sys.path.insert(0, str(ROOT / "engine/lib"))
import run_progress as rp  # noqa: E402
import work_queue  # noqa: E402

# Plain "bash" from a Python subprocess resolves to WSL's System32 stub on
# Windows, which cannot exec /bin/bash -- it exits 1 with a WSL relay error, so
# both shell tests below would "fail" while the shell code is perfectly fine.
BASH = work_queue.bash_exe()


def _gate_call_line():
    for line in PIPELINE.splitlines():
        if "engine/gate/gate.sh" in line and "cd " in line:
            return line
    raise AssertionError("the pipeline's gate invocation is gone")


def test_the_pipeline_bounds_its_gate_call():
    """An unbounded wait on a foreign repo's command is a hang nothing ends."""
    line = _gate_call_line()
    assert "GATE_TO" in line, (
        "the gate is invoked with no timeout prefix again -- a test repo whose "
        f"lint/test command never returns hangs the run forever: {line.strip()}")
    assert re.search(r'GATE_TO=\(timeout "\$\{AIQE_GATE_TIMEOUT_SEC:-\d+\}"\)', PIPELINE), \
        "the timeout is no longer configurable, or no longer has a default"


def test_a_missing_timeout_binary_is_announced_not_silent():
    """C13: an unenforceable limit must never look like an enforced one."""
    i = PIPELINE.index("GATE_TO=()")
    block = PIPELINE[i:i + 700]
    assert "command -v timeout" in block, "nothing checks the binary exists"
    assert "UNBOUNDED" in block, (
        "a host without timeout(1) silently returns to the unbounded behaviour "
        "this test exists to prevent")


def test_124_is_documented_and_is_not_a_test_failure():
    name, why = rp.explain_exit(124)
    assert name == "GATE_TIMED_OUT", f"124 renders as {name!r}"
    low = why.lower()
    assert "nothing is known" in low or "nothing was established" in low, \
        "the meaning does not say the outcome is UNKNOWN"
    assert "not a test failure" in low, (
        "nothing distinguishes 124 from exit 5 -- a reader will conclude the "
        "generated tests failed, when they never finished running")


def test_the_summary_line_for_a_timeout_says_nothing_was_established():
    """The operator reads the summary, not the exit table."""
    assert "TIMED OUT" in PIPELINE, "no distinct summary for a killed gate"
    i = PIPELINE.index("TIMED OUT")
    line = PIPELINE[i - 200:i + 260]
    assert "nothing was established" in line and "nothing was committed" in line, \
        "the timeout summary does not state what is and is not known"


def test_the_empty_prefix_survives_set_u():
    """`${arr[@]}` on an empty array is an unbound-variable error under set -u,
    which would abort every gate on a host with no timeout(1) -- turning a
    graceful degradation into a total outage.

    The expansion is lifted OUT of pipeline.sh and executed, rather than being
    retyped here: a hand-written copy would keep passing after the real call
    site changed, which is the failure mode that makes a pin worthless."""
    line = _gate_call_line()
    m = re.search(r'(\$\{GATE_TO\[@\][^)]*?)\s+bash ', line)
    assert m, f"no GATE_TO expansion found in the real call: {line.strip()}"
    script = (
        'set -uo pipefail\n'
        'GATE_TO=()\n'
        f'{m.group(1)} echo reached\n'
    )
    r = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, f"empty-prefix expansion failed: {r.stderr}"
    assert "reached" in r.stdout


def test_timeout_actually_yields_124():
    """The control for the two tests above: if timeout(1) on this host did not
    produce 124, documenting 124 would be documenting nothing."""
    r = subprocess.run([BASH, "-c", 'timeout 1 bash -c "sleep 20"'],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 124, f"timeout(1) here exits {r.returncode}, not 124"

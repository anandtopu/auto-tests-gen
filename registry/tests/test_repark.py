"""Pins for bin/repark-demo.sh — the script that returns the demo estate to its
parked state.

Why this file exists: repark-demo.sh has now made the SAME class of mistake in
both directions. It reverted whole directories that mix data with code, silently
discarding uncommitted source (a constitution clause; the correlator's
confidence fix). And it cleaned directories that mix scratch with TRACKED
fixture data, silently leaving parked run records deleted — `clear-demo` removes
run records by pattern and `git clean` only touches untracked files, so nothing
put them back.

Both losses are invisible at the time. The script's whole job is to leave a tree
that is safe to commit, so a defect in it rides into the next commit unnoticed —
which is exactly what nearly happened: two parked records were caught only
because a human read `git status` before committing.

These pins are READ-ONLY on purpose. A behavioural test would have to run the
script against the shared estate, and estate contention has already produced
several bogus suite failures in this project; a test that manufactures the very
flakiness it is meant to guard against is not worth its coverage.
"""
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "bin" / "repark-demo.sh"


def _array(name):
    """Read a bash array literal out of the script."""
    src = SCRIPT.read_text(encoding="utf-8")
    m = re.search(rf"^{name}=\((.*?)\)", src, re.M | re.S)
    assert m, f"{name} array not found in {SCRIPT.name}"
    return [w for w in m.group(1).split() if w and not w.startswith("#")]


def _tracked(path):
    out = subprocess.run(["git", "ls-files", "--", path], cwd=ROOT,
                         capture_output=True, text=True,
                         stdin=subprocess.DEVNULL).stdout
    return [l for l in out.splitlines() if l.strip()]


def test_clean_paths_holding_tracked_files_have_a_restore_pass():
    """The original defect, pinned against real repo state.

    `reports/runs` holds tracked fixture records AND per-run scratch. Cleaning
    it deletes the fixtures. This asserts the pairing that makes that safe: if
    ANY clean path contains tracked files, the script must restore tracked
    deletions afterwards. Deleting the restore pass fails this test rather than
    quietly removing parked fixtures from the next commit.
    """
    mixed = {p: f for p, f in
             ((p, _tracked(p)) for p in _array("CLEAN_PATHS")) if f}
    if not mixed:
        return  # nothing tracked under the clean paths; the property is vacuous
    src = SCRIPT.read_text(encoding="utf-8")
    # Scoped to the restore-pass REGION, not the whole file: the backstop also
    # runs `git ls-files --deleted`, so a whole-file grep passed even with the
    # restore pass deleted. Caught by mutating the script and re-running.
    assert "Restore pass" in src and "Backstop" in src, (
        f"clean paths hold tracked files {sorted(mixed)}; the script needs a "
        "restore pass and a backstop, and this pin locates them by name")
    region = src.split("Restore pass", 1)[1].split("Backstop", 1)[0]
    assert "git ls-files --deleted" in region and "git checkout" in region, (
        f"these clean paths contain tracked files {sorted(mixed)} but the "
        "restore pass no longer checks out what was deleted — re-parking "
        "will silently remove parked state from the next commit")


def test_no_clean_path_is_a_source_directory():
    """The mirror defect: a clean path must not hold code.

    The restore pass refuses source rather than checking it out, but refusing
    aborts the whole re-park. The list itself is the right place to be correct.
    """
    for p in _array("CLEAN_PATHS"):
        code = [f for f in _tracked(p) if f.endswith((".py", ".sh"))]
        assert not code, f"CLEAN_PATHS entry {p!r} contains source: {code}"


def test_restore_and_data_paths_both_guard_against_source():
    """Both revert sites share one guard; neither may lose it."""
    src = SCRIPT.read_text(encoding="utf-8")
    guards = re.findall(r"\*\.py\|\*\.sh\|\*bootstrap\*\|\*/platform/\*", src)
    assert len(guards) == 2, (
        f"expected the source guard at both the DATA_PATHS and restore sites, "
        f"found {len(guards)}")


def test_backstop_fails_when_tracked_files_remain_deleted():
    """Re-parking means the tracked tree matches HEAD.

    Without this the script can exit 0 having deleted parked state it did not
    know how to restore, and the caller's next step is a commit.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    tail = src.split("Backstop", 1)
    assert len(tail) == 2, "the tracked-deletions backstop is gone"
    assert "exit 1" in tail[1], "the backstop must FAIL, not just warn"

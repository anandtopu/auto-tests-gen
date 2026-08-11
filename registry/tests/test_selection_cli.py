"""The selection CLI advertised two options it silently discarded.

`selection.py`'s usage line documents `--exclude-scenario ID` and
`--exclude-test FILE`. Argument parsing was:

    args = [a for a in sys.argv[1:] if not a.startswith("-")]

which drops every flag before anything reads it. Driven against the estate's
PROJ-301: excluding a scenario AND an already-committed test changed nothing,
printed the unchanged list with both still `[x]`, and exited 0.

That is the exact failure this feature exists to prevent. selection.py's own
docstring says the worst lie the product could tell is "a reviewer believing a
test is gone while it runs in CI that night" -- and the CLI was telling it, for
free, on every exclusion.

The values also leaked into the positional list, so a scenario id of `finalize`
would have been one coincidence away from triggering a finalize.

Found by DRIVING the documented use case, not by reading: the library's
`set_items` was always correct and every library test passed.
"""
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CLI = ROOT / "engine/lib/selection.py"
sys.path.insert(0, str(ROOT / "engine" / "lib"))
import selection


@pytest.fixture
def estate(tmp_path, monkeypatch):
    """An isolated plan dir with one plan carrying two scenarios."""
    plans = tmp_path / "plans"
    plans.mkdir()
    monkeypatch.setenv("AIQE_PLAN_DIR", str(plans))
    return plans


def _cli(*args, plan_dir, expect_rc=0):
    import os
    env = dict(os.environ, AIQE_PLAN_DIR=str(plan_dir))
    r = subprocess.run([sys.executable, str(CLI), *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, timeout=120, env=env, cwd=str(ROOT))
    assert r.returncode == expect_rc, \
        f"rc={r.returncode} (wanted {expect_rc})\n{r.stdout}\n{r.stderr}"
    return r.stdout + r.stderr


def _decisions(plan_dir, key="K-1"):
    f = plan_dir / "selection.json"
    if not f.exists():
        return {}
    return json.loads(f.read_text(encoding="utf-8")).get(key, {})


def test_an_exclusion_from_the_cli_is_actually_recorded(estate):
    """THE DEFECT. It exited 0 and recorded nothing."""
    _cli("K-1", "--exclude-scenario", "S2", plan_dir=estate)
    got = _decisions(estate)
    assert got.get("scenarios", {}).get("S2", {}).get("included") is False, \
        f"the exclusion was discarded by argv parsing: {got}"


def test_a_test_exclusion_is_recorded_too(estate):
    _cli("K-1", "--exclude-test", "suites/a.spec.js", plan_dir=estate)
    got = _decisions(estate)
    assert got.get("tests", {}).get("suites/a.spec.js", {}).get("included") is False


def test_include_puts_an_item_back(estate):
    _cli("K-1", "--exclude-scenario", "S2", plan_dir=estate)
    _cli("K-1", "--include-scenario", "S2", plan_dir=estate)
    assert _decisions(estate)["scenarios"]["S2"]["included"] is True


def test_the_actor_and_reason_are_carried(estate):
    """Selection records WHO and WHY -- an exclusion with neither is an audit
    gap, and the flags exist so a CLI reviewer is not anonymous."""
    _cli("K-1", "--exclude-scenario", "S2", "--by", "anand",
         "--reason", "covered elsewhere", plan_dir=estate)
    entry = _decisions(estate)["scenarios"]["S2"]
    assert entry.get("by") == "anand"
    assert "covered elsewhere" in (entry.get("reason") or "")


def test_equals_form_is_accepted(estate):
    _cli("K-1", "--exclude-scenario=S3", plan_dir=estate)
    assert _decisions(estate)["scenarios"]["S3"]["included"] is False


def test_a_mistyped_option_is_refused_not_ignored(estate):
    """Silently dropping an unknown flag is how the original defect READ to a
    user: a clean exit and no effect."""
    out = _cli("K-1", "--exclude-scenariox", "S2", plan_dir=estate, expect_rc=1)
    assert "unknown option" in out
    assert not (estate / "selection.json").exists(), \
        "a refused command still wrote state"


def test_a_flag_without_a_value_is_refused(estate):
    out = _cli("K-1", "--exclude-scenario", plan_dir=estate, expect_rc=1)
    assert "needs a value" in out


def test_an_option_value_cannot_be_read_as_the_finalize_verb(estate):
    """The values used to fall through into the positional list. A scenario id
    of `finalize` would then have been one coincidence away from finalizing the
    plan instead of excluding a scenario."""
    out = _cli("K-1", "--exclude-scenario", "finalize", plan_dir=estate)
    assert "approved" not in out.lower(), f"an exclusion triggered a finalize: {out}"
    assert _decisions(estate)["scenarios"]["finalize"]["included"] is False


def test_showing_the_status_still_needs_no_flags(estate):
    out = _cli("K-1", plan_dir=estate)
    assert "K-1:" in out
    assert not (estate / "selection.json").exists(), \
        "merely LOOKING at a plan recorded a decision"

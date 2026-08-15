"""A misspelt org-config section is ignored in silence.

C12 says configuration is explicit with no silent fallback. The VALUE side of
that is defended in places — `spec_check.mode()` complains about an unusable
`spec.enforce`, `resolve.py` reports an unknown label-rule key. The KEY side
was defended nowhere.

MEASURED: renaming `budgets:` to `budget:` — one character — zeroes every
workflow envelope (pr 1.5 -> 0.0, jira 4.0 -> 0.0, plan 1.0 -> 0.0,
tests 3.0 -> 0.0). That removes the queue warning, the degradation ladder and
the config-driven spend ceiling, silently, with the key sitting in the file
the operator just edited.

There is no single load point to validate at: 30 modules open
`registry/org-config.yaml` themselves rather than going through
`registry.load_org_config`, which is why this is a check an operator runs
(`make config`) rather than something bolted inside a loader that most readers
bypass.

THE SCOPE WAS CUT ON EVIDENCE, and that is the part worth keeping. The first
version also validated the sub-keys of `budgets` and `review` from a
hand-written list, and its FIRST run against the CORRECT shipped config
reported seven keys that production code actually reads. A warning that fires
on a good configuration is one operators learn to ignore — it would have cost
more than the defect it was added for — so the sub-key check was removed
rather than patched with a longer list that would rot the same way. What that
leaves uncaught is stated in the module docstring instead of implied.
"""
import os
import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import org_config_check as occ                                  # noqa: E402

SHIPPED = yaml.safe_load(
    (ROOT / "registry/org-config.yaml").read_text(encoding="utf-8")) or {}


def test_the_shipped_config_is_clean():
    """The over-fix guard, and the one that killed the sub-key ambition: this
    project's own configuration must produce NO warning, or the check trains
    operators to ignore it."""
    assert occ.unknown_keys(SHIPPED) == []
    assert occ.report(SHIPPED) == ""


def test_every_shipped_section_is_declared_known():
    """Direction one of the anti-rot pin: a section someone adds to the config
    without declaring it here would warn every user of that section."""
    missing = sorted(set(SHIPPED) - occ.KNOWN_TOP)
    assert not missing, f"{missing} are configured but not in KNOWN_TOP"


def test_every_declared_key_is_actually_shipped():
    """Direction two: a name in KNOWN_TOP that no longer exists in the config
    is a section that was removed, and the list is now excusing a ghost — the
    mechanism this repo already records for allow-lists."""
    stale = sorted(occ.KNOWN_TOP - set(SHIPPED))
    assert not stale, f"{stale} are declared known but appear in no config"


def test_a_misspelt_section_is_named():
    """THE DEFECT, using the exact typo that was measured."""
    cfg = dict(SHIPPED)
    cfg["budget"] = cfg.pop("budgets")
    assert occ.unknown_keys(cfg) == ["budget"]
    msg = occ.report(cfg)
    assert "budget" in msg and "IGNORED" in msg


def test_the_warning_says_what_it_means_for_the_run():
    cfg = dict(SHIPPED, notakey=1)
    msg = occ.report(cfg)
    assert "running on its default" in msg, msg
    assert "spelling" in msg, msg


def test_the_sink_is_the_callers_not_module_state():
    """`report` is served from a CLI today and could be called per-run; a
    module-level accumulator would leak one caller's findings into another."""
    seen = []
    occ.report(dict(SHIPPED, notakey=1), warn=seen.append)
    assert len(seen) == 1
    occ.report(SHIPPED, warn=seen.append)
    assert len(seen) == 1, "a clean config appended a message"


def test_a_config_that_is_not_a_mapping_is_not_a_pile_of_unknown_keys():
    """A different failure with a different fix; the loaders already treat it
    as absent, and reporting 40 'unknown keys' would bury that."""
    for bad in (None, [], "budgets: 1", 7):
        assert occ.unknown_keys(bad) == []
        assert occ.report(bad) == ""


def test_make_config_reports_it():
    """Driven, because a check nobody can run is not a check. `make config` is
    the command whose whole job is saying what configuration is in force."""
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/org_config_check.py")],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr[-800:]
    assert "org-config" in r.stdout
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "org_config_check.py" in makefile, \
        "the check is not wired into any target an operator runs"


def test_it_never_exits_non_zero_on_an_unreadable_config(tmp_path):
    """A config complaint must not break every command — the reasoning
    spec_check.mode() states for falling back to `off` rather than refusing.
    Driven against a real malformed file, because a guard nobody has watched
    refuse is not a guard.
    """
    (tmp_path / "registry").mkdir()
    (tmp_path / "registry/org-config.yaml").write_text(
        "budgets: [unclosed\n  - :::", encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/org_config_check.py")],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL,
                       env={**os.environ, "AIQE_ROOT": str(tmp_path)})
    assert r.returncode == 0, r.stderr[-800:]
    assert "could not be read" in r.stdout, r.stdout
    assert "key(s) nothing reads" not in r.stdout, \
        "an unreadable config was reported as a pile of unknown keys"

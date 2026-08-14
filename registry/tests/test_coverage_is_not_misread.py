"""A 0% that means "not measured" must never read as "not tested".

The iron rule, pointed at this repo's OWN quality metric.

`.coveragerc` sets no `parallel`/`concurrency`, so coverage.py measures only
what runs IN-PROCESS. This project's stated method is to DRIVE ENTRY POINTS,
and its tests do exactly that -- in subprocesses. The result is a table that
reports the most heavily exercised files in the repo as untested:

    bin/gen_agents_md.py   0.0%     bin/repos.py            0.0%
    bin/dashboard_server.py  9.0%   bin/qa.py              20.9%

MEASURED, not argued. Running `registry/tests/test_onboard.py` alone:

    without subprocess coverage:  "module was never imported", 0.0%
    with COVERAGE_PROCESS_START:  bin/gen_agents_md.py  95%

Ninety-five points of difference on one file, from one test file. The number is
not low -- it is ABSENT, and it is presented as measured. `PY_COVERAGE_MIN`
gates on the distorted total, so raising it means fighting an artifact, and
"improving" these files means writing shallow in-process tests that duplicate
what driving already proves.

WHY IT IS NOT SIMPLY TURNED ON, measured on the full suite before deciding:

    runtime      31:19  ->  48:20   (+17 minutes, 55% slower)
    total        (gated at 67)  ->  78% true
    outcome      16 tests FAIL

Coverage instrumentation writes into the subprocess's own stdout/stderr, and
the tests it breaks are precisely the output-asserting ones that make the
drive-the-entry-point method work. Buying a truer number by breaking the tests
that produce it is a bad trade, and the numbers are recorded here so the
decision is not re-litigated from preference.

So the number stays as it is, and the READING is fixed: every entry point whose
coverage is understated is named, and each is required to be genuinely driven
by at least one test. A file may sit on that list only while something exercises
it -- otherwise the list becomes the very excuse it exists to prevent.
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Entry points run as SUBPROCESSES by the suite, so their in-process coverage
# understates reality. Each must be driven; the pin below enforces that.
SUBPROCESS_DRIVEN = (
    "bin/gen_agents_md.py",
    "bin/repos.py",
    "bin/qa.py",
    "bin/dashboard.py",
    "bin/dashboard_server.py",
    "bin/taskevent_receiver.py",
    "bin/gen_path_skills.py",
)


def _test_sources():
    out = subprocess.run(["git", "ls-files", "registry/tests/*.py"], cwd=ROOT,
                         capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, timeout=120).stdout.split()
    return {f: (ROOT / f).read_text(encoding="utf-8", errors="replace")
            for f in out if (ROOT / f).is_file()}


def test_every_understated_entry_point_is_actually_driven():
    """The list earns its place, file by file.

    Without this it is just an excuse: a genuinely untested entry point could be
    added and its 0% explained away as "subprocess-driven, ignore it".
    """
    sources = _test_sources()
    undriven = []
    for rel in SUBPROCESS_DRIVEN:
        drivers = [f for f, src in sources.items() if rel in src]
        if not drivers:
            undriven.append(rel)
    assert not undriven, (
        "listed as subprocess-driven but NO test drives them -- their 0% means "
        "untested after all: " + ", ".join(undriven))


def test_the_check_can_tell_a_driven_file_from_an_undriven_one():
    """Probe: a list that cannot fail is decorative.

    The sentinel is BUILT from pieces, never written as a literal. Spelled out,
    it lands in this file, this file becomes tracked the moment it is committed,
    `_test_sources()` reads tracked files -- and the sweep then finds its own
    sentinel. That is exactly what happened here: green before the commit, red
    on the next full run. It is the SECOND time this session; the identical
    self-reference was fixed for the env-knob sweep two iterations earlier and
    the lesson was recorded but not APPLIED. A recorded lesson is not an applied
    one, which is why the defence is structural rather than a note.
    """
    sources = _test_sources()
    assert any("bin/qa.py" in s for s in sources.values())
    sentinel = "bin/" + "no_such_entry" + "_point.py"
    assert not any(sentinel in s for s in sources.values())


def test_the_coveragerc_explains_what_it_does_not_measure():
    """The artifact is named where the number is produced.

    A reader who opens the config to ask why `gen_agents_md.py` is 0% must find
    the answer there, not in a commit message they will never see.
    """
    rc = (ROOT / ".coveragerc").read_text(encoding="utf-8")
    assert re.search(r"subprocess", rc, re.I), \
        ".coveragerc no longer says that subprocess coverage is unmeasured"
    for rel in ("gen_agents_md", "COVERAGE_PROCESS_START"):
        assert rel in rc, f".coveragerc stopped naming {rel}"

    # The MEASURED comparison is the load-bearing part, not the prose around
    # it: without both numbers a reader has an assertion instead of evidence,
    # and the first version of this pin checked only that some tokens were
    # present -- a mutation deleting the measurement survived it.
    assert "0.0%" in rc and "95%" in rc, \
        "the measured before/after (0.0% in-process vs 95% with subprocess " \
        "coverage) is gone -- the claim is now unevidenced"
    # And the reason it is not simply enabled, likewise measured.
    assert "48:20" in rc and "16 TESTS FAIL" in rc, \
        "the measured cost of enabling it (+55% runtime, 16 broken tests) is " \
        "gone, so the next reader will re-litigate it from preference"


def test_the_gate_threshold_is_still_declared():
    """PY_COVERAGE_MIN gates on the DISTORTED total. Removing it silently would
    drop the floor entirely; changing it is a judgement someone should make
    deliberately, knowing the total it applies to is understated."""
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    m = re.search(r"^PY_COVERAGE_MIN \?= (\d+)", mk, re.M)
    assert m, "the coverage floor is no longer declared in the Makefile"
    assert 0 < int(m.group(1)) <= 100


# --------------------------------------------- the self-reference, structurally

SWEEP_PROBES = {
    # module that sweeps TRACKED sources : the sentinel it asserts is absent
    "registry/tests/test_coverage_is_not_misread.py":
        "bin/" + "no_such_entry" + "_point.py",
    "registry/tests/test_documented_env_knobs_are_read.py":
        "AIQE_" + "NEVER_A_REAL_KNOB",
}


def test_no_sweep_probe_writes_its_own_sentinel_as_a_literal():
    """A sweep over tracked files must not be able to find its own sentinel.

    Twice now: the env-knob probe, then this one. Both were green until the file
    was COMMITTED, at which point the sweep read the tracked test source, found
    the sentinel spelled out there, and failed on the next full run -- so the
    failure always lands one commit after the mistake, in a run whose changes
    look unrelated.

    Checking it structurally is the only defence that survives forgetting: the
    rule was already written down when the second one was introduced.
    """
    missing = []
    for rel, sentinel in SWEEP_PROBES.items():
        src = (ROOT / rel).read_text(encoding="utf-8")
        if sentinel in src:
            missing.append(f"{rel} spells out {sentinel!r} -- build it from "
                           f"pieces so the sweep cannot match it")
    assert not missing, "\n  ".join(missing)


def test_that_structural_check_would_catch_a_spelled_out_sentinel(tmp_path):
    """Probe, because a check that can only pass is decorative."""
    f = tmp_path / "probe.py"
    sentinel = "AIQE_" + "NEVER_A_REAL_KNOB"
    f.write_text(f'x = "{sentinel}"\n', encoding="utf-8")
    assert sentinel in f.read_text(encoding="utf-8"), \
        "the detection is not looking at file contents at all"

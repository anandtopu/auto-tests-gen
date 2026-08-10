"""The suite must not rewrite the estate's SPEC OF RECORD.

Sixth store in the shape CLAUDE.md records five times. Six test files run the
REAL pipeline against the REAL fixture key PROJ-301 (plan, jira and requirements
modes), and every one rewrote `specs/PROJ-301/testplan.yaml` and
`testplans/PROJ-301.md` — both TRACKED files. The last of them disables the plan
adversary on purpose, so the estate was left holding the 1-scenario authored
plan in place of the 3-scenario arbitrated one, after every suite run.

MEASURED before fixing: both files restored to HEAD, the suite run alone, every
file under specs/ testplans/ testdata/ hashed before and after. 2088 passed;
exactly those two changed; nothing created or deleted.

The damage on the day it was found was git noise — PROJ-301 is `draft` with
spec_sha None, so no signature was broken. It is fixed for the case one approval
away: an approved key carries a sha over these exact bytes, and a suite that
rewrites them invalidates somebody's sign-off without saying so.

These deliberately mirror test_review_isolation / test_retry_isolation, PROBE
ASSERTION included: isolation that is "proven" by a write which silently does
nothing is not proven at all.
"""
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))


def test_the_spec_store_does_not_point_at_the_estate():
    import spec_store
    assert spec_store.SPEC_DIR.resolve() != (ROOT / "specs").resolve(), \
        "tests write the estate's signed spec of record"


def test_the_testplan_and_testdata_trees_are_redirected_too():
    """The rendered plan is the artifact a reviewer reads, and it is tracked
    beside the spec. Redirecting one without the other splits a single review
    across two directories — the same defect fixed in selection.py, where the
    lifecycle state and the reviewer's decisions parted company."""
    import app_paths
    for name, resolver in (("testplans", app_paths.testplans_dir),
                           ("testdata", app_paths.testdata_dir)):
        assert resolver().resolve() != (ROOT / name).resolve(), \
            f"tests write the estate's {name}/ tree"


def test_the_redirect_is_seeded_so_fixture_reads_still_work():
    """A dozen test files read PROJ-301's spec as a FIXTURE. An empty redirect
    would leave them asserting on absence and passing for the wrong reason,
    which is worse than the leak: the leak is at least visible in git status."""
    import spec_store
    assert (spec_store.SPEC_DIR / "PROJ-301" / "testplan.yaml").is_file(), \
        "the spec redirect was not seeded — fixture reads now see nothing"
    estate = (ROOT / "specs/PROJ-301/testplan.yaml").read_bytes()
    seeded = (spec_store.SPEC_DIR / "PROJ-301" / "testplan.yaml").read_bytes()
    assert seeded == estate, \
        "the seeded spec differs from the estate's — tests no longer read what " \
        "the operator has, so a passing suite says nothing about the real file"


def test_writing_a_spec_here_cannot_reach_the_estate():
    """The PROBE. Every assertion above would also pass if spec_store had simply
    stopped writing anything, so this performs a real write through the real API
    and checks BOTH that it landed somewhere and that the somewhere is not the
    estate. Without it, a no-op store would read as perfect isolation."""
    import spec_store
    key = "ZZ-SPECISO-1"
    contract = {"scenarios": [{"id": f"{key}-S1", "title": "probe",
                               "layer": "api", "target_repo": "e2e-api-tests-1",
                               "steps": {"given": "a", "when": "b", "then": "c"}}]}
    written = spec_store.write_from_contract(key, contract)
    try:
        assert written and pathlib.Path(written).is_file(), \
            "write_from_contract wrote nothing — this file cannot prove isolation"
        assert not (ROOT / "specs" / key).exists(), \
            "a spec written by a test landed in the estate"
    finally:
        import shutil
        shutil.rmtree(spec_store.SPEC_DIR / key, ignore_errors=True)


def test_the_platform_constitution_is_not_dragged_into_scratch():
    """specs/platform/constitution.yaml is TRACKED SOURCE, not per-key state, and
    governance_page reads it from ROOT on purpose. Seeding copies it along with
    everything else; what must not happen is a reader following the redirect and
    reporting the estate's clauses from a scratch copy nothing maintains."""
    import governance_page
    assert governance_page.CONSTITUTION.resolve() == \
        (ROOT / "specs/platform/constitution.yaml").resolve(), \
        "the constitution is now read from the test redirect, not the repo"


@pytest.mark.parametrize("var", ["AIQE_SPEC_DIR", "AIQE_TESTPLAN_DIR",
                                 "AIQE_TESTDATA_DIR"])
def test_an_explicit_value_from_the_caller_still_wins(var):
    """Same contract as every other redirect in conftest: a test or an operator
    that sets its own directory keeps it. A conftest that overrode an explicit
    value would break the adversarial suites, which drive these knobs directly."""
    conftest = (ROOT / "registry/tests/conftest.py").read_text(encoding="utf-8")
    assert 'if (os.environ.get(var) or "").strip():' in conftest, \
        "conftest no longer yields to an explicitly-set directory"
    assert os.environ.get(var), f"{var} is not set at all — nothing is redirected"

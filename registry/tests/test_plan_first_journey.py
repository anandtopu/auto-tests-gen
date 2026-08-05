"""The human-approval gate, end to end — the path nothing ran.

END-TO-END COVERAGE, MEASURED. `engine/pipeline.sh` accepts five modes. An
instrumented `make review` (a probe on the mode-validation line, logging every
invocation) recorded what actually EXECUTES across a whole run:

    pr            8
    plan          3
    jira          1
    tests         1   <- key NOPLAN-1, the REFUSAL case
    requirements  0

So two real paths were never exercised. `requirements` mode ran zero times. And
`tests` mode — the resume half of the plan-first workflow — ran only its refusal,
which means the HAPPY path was untested end to end: nothing ever proved that
approving a plan actually lets generation proceed and reach a commit.

That is the wrong half to leave uncovered. A gate that refuses everything passes
a refusal test perfectly. The product guarantee is BOTH directions: draft is
blocked, approved is released, and the release still goes through the real gate.

This walks the whole journey against the mock estate:

    requirements -> plan -> (refused) -> approve -> tests -> GATE COMMITTED

Estate hygiene: plan state and the review board are redirected by conftest, so
approving here cannot touch the operator's records. Run records, specs/ and
testplans/ are NOT redirected, so this snapshots and restores them — a test that
leaves run records behind would feed the scorecard its own traffic, which is the
defect this repo already fixed once.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import work_queue  # noqa: E402

KEY = "PROJ-301"                      # the fixture ticket the mock tracker serves
RUNS = ROOT / "reports/runs"
TOUCHED = ("specs/PROJ-301", "testplans/PROJ-301.md", "testdata/PROJ-301")


def _run(mode, key=KEY, timeout=600):
    env = dict(os.environ, AIQE_MOCK="1")
    return subprocess.run([work_queue.bash_exe(), "engine/pipeline.sh", mode, key],
                          cwd=ROOT, env=env, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=timeout)


@pytest.fixture(scope="module")
def journey(tmp_path_factory):
    """Walk the journey once; assert on the recorded stages.

    Module-scoped because the stages are sequential by nature — resuming from an
    approved plan is only meaningful after the plan exists — and re-running the
    whole chain per assertion would quadruple an already slow test.
    """
    keep = tmp_path_factory.mktemp("estate-backup")
    before = {p.name for p in RUNS.glob("*")} if RUNS.exists() else set()
    for rel in TOUCHED:                      # snapshot what is not redirected
        src = ROOT / rel
        if src.is_dir():
            shutil.copytree(src, keep / pathlib.Path(rel).name, dirs_exist_ok=True)
        elif src.is_file():
            shutil.copy2(src, keep / pathlib.Path(rel).name)

    stages = {}
    stages["requirements"] = _run("requirements")
    stages["plan"] = _run("plan")
    stages["tests_before_approval"] = _run("tests")

    import plan_state
    plan_state.set_status(KEY, "approved", by="journey-test")
    stages["approved_status"] = (plan_state.get(KEY) or {}).get("status")
    stages["tests_after_approval"] = _run("tests")
    yield stages

    # Restore: run records this journey created, then the tracked artifacts.
    for p in RUNS.glob("*"):
        if p.name not in before:
            p.unlink(missing_ok=True)
    for rel in TOUCHED:
        dst, saved = ROOT / rel, keep / pathlib.Path(rel).name
        if saved.is_dir():
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(saved, dst)
        elif saved.is_file():
            shutil.copy2(saved, dst)


def test_requirements_mode_runs_and_stops_for_validation(journey):
    """SDD 2.2, and the mode with zero end-to-end coverage. It must produce the
    EARS spec and say it is a DRAFT — a requirements file that arrived already
    approved would skip the validation step it exists to force."""
    r = journey["requirements"]
    assert r.returncode == 0, r.stdout[-1500:]
    assert (ROOT / f"specs/{KEY}/requirements.yaml").exists()
    assert "REQUIREMENTS_STATUS=DRAFT" in r.stdout, \
        "requirements mode did not report the spec as awaiting validation"


def test_plan_mode_stops_at_draft_instead_of_generating(journey):
    """Plan-first exists to put a human between authoring and generation."""
    import plan_state
    assert journey["plan"].returncode == 0, journey["plan"].stdout[-1500:]
    assert journey["plan"].returncode == 0
    # The status is read AFTER the plan run and BEFORE the approval below.
    assert "GATE_STATUS" not in journey["plan"].stdout, \
        "plan mode reached the gate — it must stop before generation"


def test_resuming_a_draft_plan_is_refused_and_names_the_fix(journey):
    r = journey["tests_before_approval"]
    assert r.returncode != 0, "generation proceeded from an UNAPPROVED plan"
    out = r.stdout + r.stderr
    assert "not approved" in out or "draft" in out
    assert "plan-approve" in out, "the refusal does not say how to proceed"


def test_approval_releases_generation_and_the_gate_commits(journey):
    """The half that was never tested. A gate that refuses everything passes a
    refusal test perfectly; the guarantee is that approval actually releases."""
    assert journey["approved_status"] == "approved"
    r = journey["tests_after_approval"]
    assert r.returncode == 0, r.stdout[-2000:]
    assert "GATE_STATUS=COMMITTED" in r.stdout, \
        f"an approved plan did not reach a commit: {r.stdout[-1500:]}"


def test_the_journey_records_who_approved_it(journey):
    """An approval with no actor is not an approval anyone can audit.

    A TRAP worth naming, found by writing this test the obvious way first. The
    resume run appends a SECOND `approved` entry attributed to `pipeline`, with
    note "tests generated (run …)" — the plan is still approved, so the status
    does not change, and generation is recorded against it. The human's entry is
    preserved, but the LAST `approved` row is the machine's.

    So "who approved this?" must not be answered by taking the newest approval.
    Nothing in the product does that today (plan_reuse only asks whether an
    approval EXISTS, which is correct), which is why this is a trap and not a
    bug — but it is one step from becoming one the moment somebody writes an
    approver column against `history[-1]`.
    """
    import plan_state
    entry = plan_state.get(KEY) or {}
    approvals = [h for h in (entry.get("history") or [])
                 if h.get("status") == "approved"]
    assert approvals, "no approval entry in the plan history"

    human = [h for h in approvals if h.get("by") == "journey-test"]
    assert human, f"the approving human is not in the history: {approvals}"

    machine = [h for h in approvals if "tests generated" in (h.get("note") or "")]
    assert not any(h.get("by") == "journey-test" for h in machine), \
        "the generation event was recorded against the human's approval"

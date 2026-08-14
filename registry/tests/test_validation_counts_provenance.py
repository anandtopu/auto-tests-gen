"""A fabricated pass count must never read as an executed one.

Third arrival of the iron rule, after money and the critic's score, and the
most load-bearing claim a QE platform makes: `Validation: 2 passed, 0 failed`.

MEASURED, not inferred: `engine/phases/mock_phase.sh` emits the constant
`{"passed":2,"failed":0,"repair_loops":0,"flaky_reruns":0}`, and 40 of 40 recent
runs on this estate report `2 passed` while the SAME record's generate contract
holds ONE test. So the number is not merely unlabelled -- it contradicts the run
it describes -- and `pr_comment` renders it on the pull request a human merges
from.

WHAT CORROBORATES WHAT, stated precisely, because overstating this would be its
own dishonesty. On a mock run the GATE really executes the changed specs
(`make demo-pr` is "mock LLM, real gate/env/git"), so a `committed` gate status
IS evidence the specs ran and passed. The validate counts are a separate claim:
the phase's own account of its repair loop, constant in mock mode. The fix marks
the counts and leaves the gate's verdict alone.

Five renderers: both `pr_comment` channels, `qa.py artifacts`, the dashboard
Validation chips, and `export_plan` -- that last being the one that travels
outside git and gets attached to tickets, where an unmarked simulated count is
hardest to correct after the fact.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import phase_provenance as pp                                    # noqa: E402
import pr_comment                                                # noqa: E402


def _record(simulated):
    spend = None if simulated is None else {"simulated": simulated,
                                            "cost_usd": 0.1}
    phase = {"name": "validate", "contract": {"passed": 2, "failed": 0}}
    if spend is not None:
        phase["spend"] = spend
    return {"run_id": "r1", "trigger": {"type": "pr", "key": "PR-x-1"},
            "phases": [{"name": "generate",
                        "contract": {"tests": [{"file": "a.spec.js",
                                                "action": "created"}]},
                        "spend": {"simulated": False, "cost_usd": 0.1}},
                       phase],
            "gates": [{"test_repo": "e2e", "status": "committed",
                       "commit": "abc1234"}]}


# ------------------------------------------------------------- the three states

def test_a_mock_validate_phase_is_simulated():
    assert pp.of("validate", record=_record(True)) == pp.SIMULATED


def test_a_real_validate_phase_is_measured():
    assert pp.of("validate", record=_record(False)) == pp.MEASURED


def test_a_validate_phase_with_no_spend_is_unknown():
    """Silence is not a claim that a real run produced the counts."""
    assert pp.of("validate", record=_record(None)) == pp.UNKNOWN


def test_the_phase_asked_about_is_the_phase_answered_about():
    """A run whose generate was real and whose validate was mocked has
    SIMULATED counts. Reading any phase's spend would hide exactly that."""
    rec = _record(True)
    assert pp.of("validate", record=rec) == pp.SIMULATED
    assert pp.of("generate", record=rec) == pp.MEASURED


def test_a_stamped_flag_wins_over_re_derivation():
    assert pp.of("validate", signal={"simulated": True}) == pp.SIMULATED
    assert pp.of("validate", signal={"simulated": False}) == pp.MEASURED


# ------------------------------------------------------------------ rendering

def test_the_caveat_is_words_and_silent_when_measured():
    assert "SIMULATED" in pp.caveat(pp.SIMULATED)
    assert "not recorded" in pp.caveat(pp.UNKNOWN)
    assert pp.caveat(pp.MEASURED) == "", \
        "a caveat on correct output is one readers learn to skip"


def test_the_marker_matches_the_cost_and_critic_convention():
    assert pp.mark("2", pp.SIMULATED) == "~2"
    assert pp.mark("2", pp.UNKNOWN) == "2?"
    assert pp.mark("2", pp.MEASURED) == "2"


@pytest.mark.parametrize("simulated, expect", [
    (True, "SIMULATED"),
    (None, "not recorded"),
])
def test_the_pr_comment_qualifies_a_count_it_cannot_vouch_for(simulated, expect):
    body = pr_comment.from_record(_record(simulated))
    line = next(l for l in body.splitlines() if "Validation" in l)
    assert expect in line, line


def test_the_pr_comment_leaves_a_real_count_alone():
    """The over-fix, pinned as hard as the defect: qualifying an executed
    result is what teaches a reader to ignore the qualifier everywhere."""
    body = pr_comment.from_record(_record(False))
    line = next(l for l in body.splitlines() if "Validation" in l)
    assert "SIMULATED" not in line and "not recorded" not in line, line
    assert "2 passed" in line


def test_the_gate_verdict_is_not_qualified_by_this():
    """The gate really executes the specs even on a mock run, so its status is
    evidence. Marking IT simulated would be a new lie in the other direction."""
    body = pr_comment.from_record(_record(True))
    gate = next(l for l in body.splitlines() if "committed" in l)
    assert "SIMULATED" not in gate, gate


def test_every_validation_renderer_consults_the_rule():
    """The invariant rather than today's five sites: a sixth renderer that
    formats `passed` itself is how this reached five."""
    import re
    for rel in ("engine/lib/pr_comment.py", "bin/qa.py", "bin/dashboard.py",
                "engine/lib/export_plan.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        if "passed" not in src:
            continue
        assert re.search(r"phase_provenance|_validation_caveat", src), \
            f"{rel} renders validation counts without asking phase_provenance"


# ------------------------------------------------- the reviewer, same shape

def test_a_mock_reviewers_verdict_says_it_is_a_mock():
    """FOURTH instance, and the flag was already there.

    `test_reviewer` carries `simulated` through six places -- the mock emits
    it, the surface normalizes it, a type check guards it -- and then every
    renderer threw it away. This module was already fixed for the ABSENT
    reviewer; a reviewer that RAN as a mock is the state one along.

    It matters more here than for a count: mock_phase.sh emits the finding
    text "scripted mock finding: status-only assertion does not verify
    unchanged total", and unqualified that reaches the pull request as a real
    code-review finding, with authoritative-sounding prose behind it.
    """
    import test_reviewer as tr
    line = tr.summary_line({"verdict": "needs_work", "policy": "warn",
                            "loops": 0, "simulated": True,
                            "findings": [{"severity": "high"}],
                            "unresolved": [{"x": 1}]})
    assert "SIMULATED" in line, line
    assert "needs_work" in line, "the verdict itself must survive the qualifier"


def test_a_real_reviewers_verdict_is_not_qualified():
    """The over-fix. A caveat on a genuine finding is how findings get ignored."""
    import test_reviewer as tr
    line = tr.summary_line({"verdict": "needs_work", "policy": "warn",
                            "loops": 0, "simulated": False,
                            "findings": [{"severity": "high"}], "unresolved": []})
    assert "SIMULATED" not in line, line


def test_an_absent_reviewer_still_reports_its_reason():
    """The earlier fix must not be displaced by this one: they are different
    states with different fixes, and a reader acts on the difference."""
    import test_reviewer as tr
    line = tr.summary_line({"verdict": "skipped", "policy": "warn",
                            "reason": "AIQE_TEST_REVIEWER is disabled",
                            "findings": [], "unresolved": [], "loops": 0,
                            "simulated": False})
    assert "disabled" in line and "SIMULATED" not in line, line


def test_the_dashboard_agent_review_chip_consults_the_flag():
    """A mutation blanking `sim` in bin/dashboard.py survived the first pass:
    summary_line was pinned and the chip was not.

    Scoped to the agent-review block rather than the whole file, so it asserts
    that THIS renderer consults the flag -- the defect is precisely a renderer
    that does not. Asserting the flag is READ (rather than an exact
    expression) keeps it from breaking on a harmless rewrite while still
    killing the blanking mutation.
    """
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    start = src.index("agent_review_cell = (")
    block = src[max(0, start - 1400):src.index("</span>')", start)]
    assert 'a.get("simulated")' in block, \
        "the agent-review chip no longer consults the reviewer's simulated flag"
    assert "SIMULATED" in block, "the chip stopped naming a simulated review"


# ------------------------- three MORE reviewer renderers the last pass missed

MOCK_REVIEW = {"verdict": "needs_work", "policy": "warn", "loops": 0,
               "simulated": True, "findings": [{"severity": "high"}],
               "unresolved": [{"x": 1}]}
REAL_REVIEW = dict(MOCK_REVIEW, simulated=False)
SKIPPED_REVIEW = {"verdict": "skipped", "policy": "warn", "findings": [],
                  "unresolved": [], "loops": 0, "simulated": False,
                  "reason": "AIQE_TEST_REVIEWER is disabled"}


def test_the_marked_verdict_has_one_definition():
    import test_reviewer as tr
    assert tr.verdict_text(MOCK_REVIEW) == "needs_work~"
    assert tr.verdict_text(REAL_REVIEW) == "needs_work"


def test_an_absent_reviewer_is_never_marked_simulated():
    """`simulated` is only meaningful once the reviewer RAN. A skipped entry
    carrying simulated=True would otherwise pick up a marker that says a mock
    produced a verdict there was none of."""
    import test_reviewer as tr
    assert tr.simulated(dict(SKIPPED_REVIEW, simulated=True)) is False
    assert tr.verdict_text(SKIPPED_REVIEW) == "skipped"


@pytest.mark.parametrize("rel, must_call", [
    ("engine/lib/explain.py", "reviewer_lib.simulated"),
    ("engine/lib/wizard_status.py", "test_reviewer.simulated"),
    ("bin/qa.py", "test_reviewer.simulated"),
])
def test_every_verdict_renderer_asks_whether_it_was_a_mock(rel, must_call):
    """The last pass marked summary_line and the dashboard chip and left these
    three printing the bare word.

    The wizard is the sharpest: it distinguishes an ABSENT reviewer in that very
    block, with a comment explaining why, and still gave a MOCK a green tick and
    a stub's finding count. `explain` is the next: its whole job is answering
    why the AI did something, and its own docstring calls a fabricated rationale
    the worst possible answer.
    """
    src = (ROOT / rel).read_text(encoding="utf-8")
    assert must_call in src, f"{rel} renders a verdict without asking if it was a mock"

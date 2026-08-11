"""A reviewer that never ran was reported as one that found nothing.

`test_reviewer.surface()` records WHY there is no review -- "AIQE_TEST_REVIEWER
is disabled" sits in the run record on this estate today -- and every renderer
threw it away:

- `summary_line()` produced "agent review: skipped -- 0 finding(s), 0
  unresolved, 0 repair loop(s); policy: warn". That line is posted on the PR a
  human decides to merge from (pr_comment.py), and the counts are all zero
  either way, so it reads as a reviewer that looked and had nothing to say.
- `qa.py reviews` printed the bare word `skipped` in an 18-column field.
- THE WORST ONE, and it is the wizard again: `wizard_status` gave the "Agent
  review" step state `done` -- a green tick on a step that never happened, in
  the view whose entire job is telling a user where their request got to.

Two situations shared one word and a reader acts on the difference: the
reviewer is switched off (a config choice to revisit) versus it ran and no
repository had generated tests to review (an established negative about this
run). Constitution C13.

Same shape as the plan-adversary defect (test_absent_adversary_is_visible.py):
an absent opinion rendered as an approving one. That fix is why this one was
looked for.
"""
import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import test_reviewer as reviewer          # noqa: E402
import wizard_status                      # noqa: E402


def _qa():
    spec = importlib.util.spec_from_file_location("qa_absent", ROOT / "bin/qa.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------- the shared summary

def test_a_disabled_reviewer_says_so_on_the_pull_request():
    """THE DEFECT. This exact string is what pr_comment puts in front of a
    human deciding whether to merge."""
    line = reviewer.summary_line(
        reviewer.surface(None, cfg={}, policy="warn", assume_enabled=False))
    assert "skipped" in line
    assert "AIQE_TEST_REVIEWER" in line, \
        "the PR comment does not say the reviewer was switched off"


def test_switched_off_and_nothing_to_review_are_different_sentences():
    """The whole point. Collapsing them is the defect, so a fix that qualifies
    both with the same words has not fixed anything."""
    off = reviewer.summary_line(
        reviewer.surface(None, cfg={}, policy="warn", assume_enabled=False))
    ran = reviewer.summary_line(reviewer.surface(
        reviewer.merge([("e2e-api-tests-1", "skipped",
                         "no generated tests for this repo")]),
        cfg={}, policy="warn"))
    assert off != ran
    assert "no generated tests" in ran
    assert "AIQE_TEST_REVIEWER" not in ran


def test_an_unavailable_reviewer_is_not_a_skipped_one():
    """Enabled-but-produced-nothing is a phase to investigate; disabled is a
    setting to change. Different fixes, so different messages."""
    line = reviewer.summary_line(
        reviewer.surface(None, cfg={}, policy="warn", assume_enabled=True))
    assert "unavailable" in line and "no valid result" in line


def test_an_absence_with_no_recorded_reason_says_that_too():
    """A record from before this change carries no reason. Printing nothing
    would put us straight back to the unqualified word (C13)."""
    line = reviewer.summary_line({"verdict": "skipped", "policy": "warn"})
    assert "reason not recorded" in line


def test_a_real_verdict_is_not_qualified():
    """A caveat on a healthy run is one people learn to scroll past."""
    for verdict in ("approve", "needs_work"):
        line = reviewer.summary_line(
            {"verdict": verdict, "policy": "warn",
             "reason": "should not be rendered"})
        assert "should not be rendered" not in line
        assert "reason not recorded" not in line


# ------------------------------------------------------ deriving the reason

def test_the_reason_comes_from_the_rows_the_merge_already_kept():
    """No new plumbing: merge() has always stored a per-repo reason for every
    non-reviewed state. Nothing surfaced it."""
    merged = reviewer.merge([("e2e-api-tests-1", "skipped", "nothing generated"),
                             ("e2e-ui-tests-1", "skipped", "nothing generated")])
    snap = reviewer.surface(merged, cfg={}, policy="warn")
    assert snap["verdict"] == "skipped"
    assert snap["reason"] == "nothing generated", \
        "one reason shared by both repos should not be repeated"


def test_no_rows_at_all_is_its_own_answer():
    """"The reviewer was asked about no repository" must not be reported as
    "no repository needed review"."""
    snap = reviewer.surface(reviewer.merge([]), cfg={}, policy="warn")
    assert snap["reason"] == "no repository reported work to review"


def test_distinct_reasons_are_all_named():
    merged = reviewer.merge([("e2e-api-tests-1", "skipped", "nothing generated"),
                             ("e2e-ui-tests-1", "unavailable", "clone failed")])
    snap = reviewer.surface(merged, cfg={}, policy="warn")
    assert "nothing generated" in snap["reason"]
    assert "clone failed" in snap["reason"]


# ------------------------------------------------------------- the wizard

def _states(doc):
    return {s["label"]: s["state"] for s in doc["steps"]}


@pytest.fixture
def estate(tmp_path, monkeypatch):
    runs = tmp_path / "reports/runs"
    runs.mkdir(parents=True)
    (runs / "queue.json").write_text("[]", encoding="utf-8")
    (runs / "reviews.json").write_text(
        json.dumps({"PR-x-1": {"status": "pending_review"}}), encoding="utf-8")
    monkeypatch.setattr(wizard_status, "ROOT", tmp_path)
    import review_state
    import work_queue
    monkeypatch.setattr(review_state, "FILE", runs / "reviews.json")
    monkeypatch.setattr(work_queue, "FILE", runs / "queue.json")

    def write(review):
        (runs / "r1.json").write_text(json.dumps({
            "run_id": "r1", "ts": 10, "trigger": {"type": "pr", "key": "PR-x-1"},
            "overall": "committed", "review": review,
            "phases": [{"name": "generate", "contract": {"tests": [
                {"file": "a.spec.js", "action": "created"}]}}],
            "gates": [{"test_repo": "e2e-api-tests-1", "status": "committed"}],
        }), encoding="utf-8")
    return write


def test_a_disabled_reviewer_is_not_a_completed_step(estate):
    """`done` renders a green tick. The reviewer never ran."""
    estate({"verdict": "skipped", "state": "skipped", "findings": [],
            "unresolved": [], "loops": 0, "policy": "warn",
            "reason": "AIQE_TEST_REVIEWER is disabled"})
    doc = wizard_status.build("PR-x-1", "pr")
    assert _states(doc)["Agent review"] == "skipped"
    detail = next(s["detail"] for s in doc["steps"] if s["label"] == "Agent review")
    assert "AIQE_TEST_REVIEWER" in detail


def test_a_real_review_still_completes_the_step(estate):
    """The direction that would be worse than the defect: a fix that turned
    every genuine review into a skip would hide real findings."""
    estate({"verdict": "needs_work", "state": "reviewed",
            "findings": [{"finding": "gap"}], "unresolved": [{"finding": "gap"}],
            "loops": 1, "policy": "warn"})
    assert _states(wizard_status.build("PR-x-1", "pr"))["Agent review"] == "done"


def test_a_refusal_still_outranks_absence(estate):
    """`require` refusing delivery is a failure, and stays one."""
    runs = pathlib.Path(wizard_status.ROOT) / "reports/runs"
    estate({"verdict": "unavailable", "state": "unavailable", "findings": [],
            "unresolved": [], "loops": 0, "policy": "require",
            "reason": "reviewer produced no valid result"})
    rec = json.loads((runs / "r1.json").read_text(encoding="utf-8"))
    rec["review_delivery"] = {"outcome": "refused"}
    (runs / "r1.json").write_text(json.dumps(rec), encoding="utf-8")
    assert _states(wizard_status.build("PR-x-1", "pr"))["Agent review"] == "failed"


def test_the_skipped_state_has_a_rendering():
    """A state the page has no CSS for falls back to the step NUMBER, which
    reads as "still to come" -- the other wrong answer."""
    page = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert ".wz-steps li.skipped::before" in page


# ----------------------------------------------------------- the board CLI

def test_the_review_board_names_the_reason_once(tmp_path, monkeypatch, capsys):
    """`make reviews` is where a lead looks at the whole backlog. The column is
    too narrow for the reason, so it goes in a footnote rather than nowhere."""
    qa = _qa()
    runs = tmp_path / "reports/runs"
    runs.mkdir(parents=True)
    (runs / "r1.json").write_text(json.dumps({
        "run_id": "r1", "ts": 10, "trigger": {"type": "pr", "key": "PR-x-1"},
        "review": {"verdict": "skipped", "findings": [], "unresolved": [],
                   "loops": 0, "policy": "warn",
                   "reason": "AIQE_TEST_REVIEWER is disabled"},
    }), encoding="utf-8")
    monkeypatch.setattr(qa, "ROOT", tmp_path)
    import review_state
    board = tmp_path / "reviews.json"
    board.write_text(json.dumps({"PR-x-1": {"status": "pending_review",
                                            "updated": 10}}), encoding="utf-8")
    monkeypatch.setattr(review_state, "FILE", board)
    qa.cmd_reviews(type("A", (), {})())
    out = capsys.readouterr().out
    assert "no agent review for 1 key(s)" in out
    assert "AIQE_TEST_REVIEWER is disabled" in out
    # After the note, not merely somewhere on the page: the key is already in
    # the table above, so "PR-x-1 appears" is satisfied by the row that started
    # this whole problem.
    tail = out.split("no agent review")[1]
    assert "PR-x-1" in tail, "the footnote does not say WHICH keys"


def test_a_record_predating_the_reason_says_so_rather_than_inventing_one():
    """Compatibility, and it is the C13 direction. A run recorded BEFORE the
    reason was surfaced carries no `reason` key, and there is no way to
    recover why that reviewer was absent -- the run is over and the config
    that governed it is gone. "reason not recorded" is the honest answer;
    picking the likeliest cause would date-stamp a guess onto history."""
    sys.path.insert(0, str(ROOT / "engine/lib"))
    import pr_comment
    historical = {
        "run_id": "r0", "ts": 1, "trigger": {"type": "pr", "key": "PR-x-1"},
        "review": {"state": "skipped", "verdict": "skipped", "findings": [],
                   "loops": 0, "unresolved": [], "policy": "warn",
                   "repos": [], "simulated": False},
        "phases": [
            {"name": "triage", "contract": {"impact": "create",
                                            "areas": ["orders"]}},
            {"name": "generate",
             "contract": {"tests": [{"file": "suites/a.spec.js",
                                     "action": "created"}],
                          "open_questions": []}},
        ],
        "gates": [{"test_repo": "e2e-api-tests-1", "status": "committed",
                   "commit": "abc1234def"}],
    }
    md = pr_comment.from_record(historical)
    assert "skipped (reason not recorded)" in md
    assert "AIQE_TEST_REVIEWER" not in md, \
        "a cause was invented for a run that never recorded one"

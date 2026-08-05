"""The team's review board is not a scratch file for the test suite.

`review_state` has honoured AIQE_REVIEWS_FILE since it was written. Nothing ever
set it, so every pytest run wrote into the estate's real review record.

MEASURED: one `python3 -m pytest registry/tests` run added 14 history entries to
PR-orders-api-201, every one a phantom `{"release": "", "source": "manual"}`.
505 had accumulated across this session's runs, burying the single genuine
`pending_review` decision under test traffic — an audit trail that is 99.8%
noise answers no question anyone would open it to ask. In a deployment, running
`make review` in CI does that to the team's board.

Fourth instance of one shape, after the transaction log, the run-record sweep and
the retry counters: a shared store a test can reach, where the damage lands
somewhere nobody looks until it misleads. The probe test exists because
isolation "proven" by a write that quietly does nothing is not proven at all —
the same trap the audit-log and retry pins guard against.
"""
import importlib
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import review_state  # noqa: E402

ESTATE = ROOT / "reports/runs/reviews.json"


def test_the_suite_is_not_writing_to_the_estates_review_board():
    assert (os.environ.get("AIQE_REVIEWS_FILE") or "").strip(), \
        "conftest no longer redirects the review board"
    assert review_state.FILE.resolve() != ESTATE.resolve(), \
        "review decisions from tests land in the estate's real board"


def test_recording_a_decision_does_not_touch_the_estate_file():
    """A probe, not just a path comparison: isolation that holds only because
    the write silently failed would pass the assertion above."""
    # Check BEFORE writing, not after. A probe that writes first and inspects
    # second dirties the very file it is protecting the moment isolation breaks
    # — which is exactly what happened while mutation-testing this file: the
    # mutation removes conftest's redirect, and the probe then wrote
    # ZZ-REVIEW-PROBE into the estate's real board. A test that mutates real
    # state when it fails is a liability, which is the whole point here.
    assert review_state.FILE.resolve() != ESTATE.resolve(), \
        "refusing to write: the redirect is gone and this would hit the real board"
    before = ESTATE.read_text(encoding="utf-8") if ESTATE.exists() else None
    review_state.set_status("ZZ-REVIEW-PROBE", "in_review", reviewer="probe")
    assert review_state.load().get("ZZ-REVIEW-PROBE", {}).get("status") == "in_review", \
        "set_status() wrote nothing — isolation cannot be proven by a no-op"
    after = ESTATE.read_text(encoding="utf-8") if ESTATE.exists() else None
    assert after == before, "a test modified the estate's review board"


def test_the_knob_actually_redirects(tmp_path, monkeypatch):
    target = tmp_path / "elsewhere/reviews.json"
    monkeypatch.setenv("AIQE_REVIEWS_FILE", str(target))
    try:
        importlib.reload(review_state)
        assert review_state.FILE == target
        review_state.set_status("ZZ-KNOB", "approved", reviewer="x")
        assert target.exists(), "the knob was read but not honoured on write"
    finally:
        monkeypatch.undo()
        importlib.reload(review_state)


def phantom_release_entries(entry):
    """History rows that record an empty release and no decision — the exact
    shape the leak produced. Extracted so it can be mutation-tested against a
    synthetic board; asserting only against the real estate file gives a pin
    that cannot be proven to bite while the estate is clean."""
    return [h for h in (entry.get("history") or [])
            if isinstance(h, dict) and h.get("release") == "" and not h.get("status")]


def test_the_phantom_detector_recognises_the_shape_that_leaked():
    noisy = {"history": [{"release": "", "source": "manual", "ts": 1},
                         {"release": "", "source": "manual", "ts": 2},
                         {"status": "approved", "reviewer": "lead", "ts": 3},
                         {"release": "2.4", "source": "jira", "ts": 4}]}
    found = phantom_release_entries(noisy)
    assert len(found) == 2, "the detector misses the rows the leak wrote"
    clean = {"history": [{"status": "approved", "reviewer": "lead", "ts": 1},
                         {"release": "2.4", "source": "jira", "ts": 2}]}
    assert phantom_release_entries(clean) == [],         "a real decision or a real release must never count as phantom"


def test_the_estate_board_holds_decisions_not_phantom_release_writes():
    """What the leak actually produced, asserted against the real file so the
    accumulation cannot quietly return.

    A release entry recording the SAME value is already suppressed by
    set_release's idempotency guard; these came from tests toggling it. The
    board should read as a short list of things people decided."""
    if not ESTATE.exists():
        return
    board = json.loads(ESTATE.read_text(encoding="utf-8"))
    for key, entry in board.items():
        empty_release = phantom_release_entries(entry)
        assert len(empty_release) < 10, (
            f"{key} carries {len(empty_release)} history entries that record an "
            f"empty release and no decision — test traffic is in the estate's "
            f"review board again")

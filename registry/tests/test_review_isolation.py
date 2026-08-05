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


# --- the plan lifecycle, and the class as a whole ---------------------------

def test_the_suite_is_not_writing_to_the_estates_plan_store():
    """plan_state holds APPROVALS — who signed off on which spec sha. Measured
    by snapshotting the estate, running the suite and diffing: PROJ-301's plan
    history went from 2 entries to 82, and a stray test key `K-1` appeared in
    the operator's store. The status survived by luck; nothing stopped a test
    calling approve or revoke on a real key."""
    import plan_state
    estate = ROOT / "reports/plans"
    assert plan_state.DIR.resolve() != estate.resolve(), \
        "tests mutate the estate's plan approvals"
    import selection
    assert selection.FILE.parent.resolve() == plan_state.DIR.resolve(), \
        "selection parted company with the plan store it belongs to"


def test_no_writable_state_store_still_points_at_the_estate():
    """The class, not the instance. Five stores leaked one at a time this
    session — transaction log, run records, retry counters, review board, plan
    lifecycle — each found only when its damage happened to be noticed. This
    enumerates every engine/lib module that writes through fs_lock and asserts
    none of its module-level paths resolve into estate data under the test
    environment, so the sixth is caught by the build instead of by luck.

    Read-only resolvers are deliberately allowed: the registry and the knowledge
    tree are fixtures the suite READS, and redirecting them would break the
    routing goldens. What must not happen is a WRITER pointed at them.

    Known limit, stated rather than papered over: NO_KNOB_NOT_WRITTEN is an
    allow-list, so anyone can silence this pin by adding an entry. The guard is
    that it takes a deliberate source edit next to the evidence, not that it is
    impossible — a mutation that both drops a redirect AND allow-lists the store
    passes, by construction. What the pin does buy is that a NEW writable store,
    or a redirect quietly removed, fails the build."""
    import importlib
    import re
    lib = ROOT / "engine/lib"
    estate_roots = ("reports/", "catalog/", "specs/", "testplans/", "testdata/")
    # Stores with no env knob yet, recorded WITH the evidence rather than waved
    # through: the estate snapshot/diff across a full suite run showed the tests
    # do not write either of these. They stay listed so the entry has to be
    # deleted deliberately if that ever stops being true, and so a store added
    # without a knob fails the build instead of joining them silently.
    NO_KNOB_NOT_WRITTEN = {
        "cost_report.BASELINE",     # written only by `make cost-baseline`
        "cost_report.RUNS",         # a directory it READS run records from
        "test_health.FILE",         # written only by `make ingest-results`
    }
    offenders = []
    for f in sorted(lib.glob("*.py")):
        src = f.read_text(encoding="utf-8", errors="replace")
        if not re.search(r"write_json_atomic|fs_lock\.lock\(", src):
            continue
        try:
            mod = importlib.import_module(f.stem)
        except Exception:
            continue
        for attr in dir(mod):
            if attr.startswith("_"):
                continue
            val = getattr(mod, attr, None)
            if not isinstance(val, pathlib.Path):
                continue
            try:
                rel = val.resolve().relative_to(ROOT).as_posix()
            except ValueError:
                continue                      # outside the checkout = isolated
            if rel.startswith("out/"):
                continue                      # scratch, wiped between runs
            if any(rel.startswith(e) for e in estate_roots):
                if f"{f.stem}.{attr}" in NO_KNOB_NOT_WRITTEN:
                    continue
                offenders.append(f"{f.stem}.{attr} -> {rel}")
    assert not offenders, (
        "these writable state stores still resolve into the estate under the "
        "test environment; give each an env knob and redirect it in conftest:\n  "
        + "\n  ".join(sorted(offenders)))

"""A damaged input is not a missing one.

engine/lib/explain.py exists to answer "why did the AI do that" from recorded
evidence only, and its own docstring says a fabricated rationale is worse than
none. Its reader collapsed two different facts: `_read_json` caught
(OSError, ValueError) and returned None, so a file that was never written and a
file sitting on disk with a truncated last line produced the identical answer.

REPRODUCED before fixing, against an isolated root: with
out/resolve.contract.json containing `{not json`, and again with the file
deleted, explain() emitted the same string — "No resolve contract was kept for
this run." That sends an operator to look for a phase that failed to persist
something, when the file is right there and damaged.

C13, in the surface whose entire job is explaining what happened. The module
already had the right pattern elsewhere: the notification-integrity row counts
malformed comment receipts and calls the history "explicitly incomplete". These
pin that the read path feeding every other answer behaves the same way.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import explain  # noqa: E402

RESOLVE = "out/resolve.contract.json"


def _estate(tmp_path, contract=None, write_resolve=None):
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports/runs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports/runs/1-1.json").write_text(json.dumps(
        {"run_id": "r1", "trigger": {"key": "ZZ-1"}, "phases": contract or [],
         "gate": {}}), encoding="utf-8")
    if write_resolve is not None:
        (tmp_path / RESOLVE).write_text(write_resolve, encoding="utf-8")
    return tmp_path


def _rows(root):
    out = explain.explain(key="ZZ-1", root=root)
    return {u["id"]: u["not_recorded"] for u in out.get("unexplained", [])}


def test_a_damaged_contract_is_not_reported_as_one_that_was_never_kept(tmp_path):
    """The defect, stated as its symptom."""
    rows = _rows(_estate(tmp_path, write_resolve="{not json"))
    routing = rows.get("routing") or ""
    assert routing, "routing produced no unexplained row at all"
    assert "could not be read" in routing, \
        "a damaged resolve contract is still described as absent"
    assert "not the same as it never having been recorded" in routing, \
        "the message does not tell the reader to stop hunting for a lost write"


def test_an_absent_contract_still_says_absent(tmp_path):
    """The other direction, and the one a wrong fix breaks: if everything
    becomes 'unreadable' the distinction is lost again, just inverted."""
    rows = _rows(_estate(tmp_path))
    routing = rows.get("routing") or ""
    assert "No resolve contract was kept" in routing
    assert "could not be read" not in routing
    assert "inputs" not in rows, \
        "a file that was never written was reported as a damaged input"


def test_the_two_answers_are_actually_different(tmp_path):
    """Pinning each message separately would still pass if both were changed to
    the same new string. This is the property that matters."""
    a = _rows(_estate(tmp_path / "a", write_resolve="{not json")).get("routing")
    b = _rows(_estate(tmp_path / "b")).get("routing")
    assert a and b and a != b, "damaged and absent still read identically"


def test_every_damaged_input_is_named_even_when_nothing_claims_absence(tmp_path):
    """Most reads fold a bad file into {} — the decision row simply never
    appears, so the reader never learns the question was askable. One row
    naming the file beats several rows quietly missing."""
    root = _estate(tmp_path, write_resolve=json.dumps(
        {"test_repos": ["e2e-api-tests-1"], "confidence": 0.9}))
    (root / "out/plan-reuse.json").write_text("{{{", encoding="utf-8")
    rows = _rows(root)
    assert "inputs" in rows, "a damaged input produced no report at all"
    assert "plan-reuse.json" in rows["inputs"], "the row does not name the file"
    assert "NOT absent" in rows["inputs"], \
        "the row does not distinguish damage from absence, which is the point"


def test_a_clean_run_reports_no_damaged_inputs(tmp_path):
    """The control. A row that appears unconditionally is noise, and noise in
    an audit surface trains people to skip it."""
    root = _estate(tmp_path, write_resolve=json.dumps({"test_repos": ["r"]}))
    assert "inputs" not in _rows(root)


def test_the_collector_is_not_module_state():
    """/api/explain is served from a ThreadingHTTPServer. A module-level list
    would let one request report another request's damaged files — and the
    report names paths, so that is a cross-request leak of what someone else
    was looking at, not merely a wrong count."""
    src = (ROOT / "engine/lib/explain.py").read_text(encoding="utf-8")
    assert "def _read_state(p, sink=None)" in src, \
        "the reader no longer takes a caller-supplied sink"
    for line in src.splitlines():
        if line.startswith(("UNREADABLE", "DAMAGED")):
            raise AssertionError(
                f"module-level damage collector is back: {line!r}")


@pytest.mark.parametrize("state,body", [("ok", '{"a": 1}'),
                                        ("unreadable", "{oops")])
def test_read_state_reports_what_it_found(tmp_path, state, body):
    p = tmp_path / "x.json"
    p.write_text(body, encoding="utf-8")
    sink = []
    data, got = explain._read_state(p, sink)
    assert got == state
    assert (sink == []) if state == "ok" else (sink == [str(p)])


def test_a_path_that_exists_but_cannot_be_read_is_also_collected(tmp_path):
    """The OTHER unreadable branch. Corrupt-JSON hits ValueError; a path that
    exists and cannot be read hits OSError, and a mutation removing collection
    from that branch survived a suite that only ever fed it bad JSON.

    A directory where a file is expected is the cheap portable way to provoke
    it (Windows raises PermissionError, POSIX IsADirectoryError — both OSError,
    neither FileNotFoundError) and it is a real corruption mode: an interrupted
    write or a bad mount leaves exactly this.
    """
    p = tmp_path / "resolve.contract.json"
    p.mkdir()
    sink = []
    data, state = explain._read_state(p, sink)
    assert (data, state) == (None, "unreadable"), \
        "an unreadable path was not reported as unreadable"
    assert sink == [str(p)], \
        "the OSError branch does not collect the file, so it is never named"


def test_read_state_calls_a_missing_file_absent(tmp_path):
    sink = []
    data, got = explain._read_state(tmp_path / "nope.json", sink)
    assert (data, got, sink) == (None, "absent", []), \
        "a missing file must not be counted as damaged"


# --- the sibling, one layer up ----------------------------------------------

def test_a_damaged_run_record_is_not_reported_as_no_run_at_all(tmp_path):
    """THE WORSE INSTANCE, and the reason the sweep is worth doing.

    run_progress's record search skips a record it cannot parse — it has to,
    since it cannot match a key inside a file that will not load. The skip was
    silent, so explain()'s outermost answer became "No run has been recorded
    for this target, so there is nothing to explain yet." for a run that
    happened and whose record is sitting on disk.

    Fixing the inner reads and stopping would have left the loudest wrong
    answer in place: a reader who asks "why did this run do that" is told the
    run does not exist.
    """
    (tmp_path / "out").mkdir(parents=True)
    (tmp_path / "reports/runs").mkdir(parents=True)
    (tmp_path / "reports/runs/1-1.json").write_text("{truncated",
                                                    encoding="utf-8")
    out = explain.explain(key="ZZ-1", root=tmp_path)
    detail = out.get("detail") or ""
    assert "could not be parsed" in detail, \
        "a damaged run record is still reported as no run at all"
    assert "not the same as the run never having happened" in detail
    assert out.get("unreadable_records"), \
        "the damaged record is not named in the payload the UI receives"


def test_a_genuinely_absent_run_still_says_nothing_to_explain(tmp_path):
    """The control: with no records at all the original wording must survive,
    or every empty estate starts claiming corruption."""
    (tmp_path / "out").mkdir(parents=True)
    (tmp_path / "reports/runs").mkdir(parents=True)
    out = explain.explain(key="ZZ-1", root=tmp_path)
    assert "nothing to explain yet" in (out.get("detail") or "")
    assert not out.get("unreadable_records")


def test_state_files_are_never_counted_as_damaged_records(tmp_path):
    """reviews.json / queue.json / hooks-seen.json share the run-record
    directory and are not run records. queue.json is a LIST, so a check that
    only asked 'does this parse as a dict' would report the work queue as a
    corrupt run every single time."""
    import run_progress
    runs = tmp_path / "reports/runs"
    runs.mkdir(parents=True)
    (runs / "queue.json").write_text("[]", encoding="utf-8")
    (runs / "reviews.json").write_text("{}", encoding="utf-8")
    (runs / "hooks-seen.json").write_text("[]", encoding="utf-8")
    assert run_progress.unreadable_records(tmp_path) == []


def test_a_healthy_run_record_is_never_reported_as_unreadable(tmp_path):
    """The control the sweep needed. Every test above either has an EMPTY runs
    directory or only state files, so a check that flagged all records as
    corrupt passed all of them — and that failure mode is the nastier one: it
    tells an operator their run history is damaged when it is fine, which is
    exactly the false alarm that gets a warning ignored for good."""
    import run_progress
    runs = tmp_path / "reports/runs"
    runs.mkdir(parents=True)
    (runs / "1-1.json").write_text(json.dumps(
        {"run_id": "r1", "trigger": {"key": "ZZ-1"}, "phases": [], "gate": {}}),
        encoding="utf-8")
    assert run_progress.unreadable_records(tmp_path) == [], \
        "a perfectly good run record was reported as unreadable"

    # ...and one bad file beside it is reported, WITHOUT dragging the good one in.
    (runs / "2-2.json").write_text("{nope", encoding="utf-8")
    bad = run_progress.unreadable_records(tmp_path)
    assert len(bad) == 1 and bad[0].endswith("2-2.json"), \
        f"expected only the damaged record, got {bad}"


# --- the entry points, which is where this became visible -------------------

def test_the_cli_prints_the_detail_it_was_given(tmp_path):
    """Found by DRIVING `make explain`, not by reading the library.

    The CLI rendered `decisions` and `unexplained` and dropped `detail` — the
    field that IS the whole answer when no readable record matched. So an
    operator asking about an unrecorded key got the header and a blank line.
    The C13 work above made that message honest and it was invisible for this
    reason: a fix nobody can see at the entry point is not a fix.
    """
    import subprocess
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/explain.py"),
                        "ZZ-NO-SUCH-KEY-1"], cwd=str(ROOT), capture_output=True,
                       text=True, stdin=subprocess.DEVNULL, timeout=120)
    assert r.returncode == 0, r.stderr
    body = r.stdout.split("\n", 1)[1].strip()
    assert body, "the CLI printed a header and nothing else"
    assert "no run has been recorded" in body.lower()


def test_the_dashboard_panel_shows_the_detail_instead_of_hiding_itself():
    """The sibling, and the worse one: the panel set display:none, so the user
    saw no trace of having asked — which reads as 'this feature does not apply
    here' rather than 'no record was found'. Its own error path already gets
    this right ('a display failure, not an absence of reasons')."""
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    fn = src[src.index("function rpWhyRender("):]
    fn = fn[:fn.index("async function rpWhy(")]

    # Assert the ORDER inside the empty-answer branch, not the presence of a
    # substring. The first version of this test checked `"x.detail" in fn` and
    # counted `display = 'none'` occurrences, and a mutation that hid the panel
    # unconditionally SURVIVED both: it left x.detail sitting on a line that
    # could no longer be reached, and kept the count identical. A survivor
    # usually means the test is weak, not that the code is right.
    empty = fn[fn.index("if (!(x.decisions || []).length"):]
    empty = empty[:empty.index("\n  const dec =")]
    body = empty.split("{", 1)[1].strip()
    assert body.startswith("if (!x.detail)"), (
        "the empty-answer branch does something before checking for a detail "
        f"to show; it starts with: {body.splitlines()[0]!r}")
    assert "body.innerHTML" in empty and "x.detail" in empty, \
        "the empty-answer branch never renders the detail it was given"


# --- rendering the adversary's findings -------------------------------------

def _plan(monkeypatch, entry):
    import plan_state
    monkeypatch.setattr(plan_state, "get", lambda k: entry)


def test_adversary_findings_are_rendered_not_dumped(tmp_path, monkeypatch):
    """Found by driving `make explain KEY=PROJ-301`. The block printed the
    answer sentence twice and then `str(detail)[:400]` — a raw Python dict cut
    off mid-structure — while the thing the reader came for (what did the
    opponent find?) sat inside it, already structured."""
    _plan(monkeypatch, {
        "adversary": "adversarial review: 2 gap(s) raised",
        "adversary_detail": {"accepted": 2, "rejected": 1, "gaps": [
            {"title": "stacking on a discounted order", "category": "boundary",
             "severity": "high", "rationale": "AC-3 leaves stacking undefined"},
            {"title": "POST without orders:write", "category": "authz",
             "severity": "high", "rationale": "no scenario exercises authz"}]}})
    out = explain.explain(key="ZZ-1", root=_estate(tmp_path))
    adv = next(d for d in out["decisions"] if d["id"] == "adversary")

    assert adv["answer"] not in adv["because"], \
        "the because list repeats the answer verbatim"
    joined = " | ".join(adv["because"])
    assert "{'" not in joined and '{"' not in joined, \
        f"a raw dict repr is still being shown to the reader: {joined[:120]}"
    assert "stacking on a discounted order" in joined
    assert "boundary" in joined and "high" in joined
    assert "2 accepted, 1 rejected" in joined


def test_one_malformed_gap_does_not_lose_the_others(tmp_path, monkeypatch):
    """adversary_detail is LLM output that reached disk. CLAUDE.md records a
    single malformed entry taking bin/dashboard.py down for EVERY run, so the
    established filter is run_progress.dict_rows()."""
    _plan(monkeypatch, {
        "adversary": "adversarial review: 2 gap(s) raised",
        "adversary_detail": {"gaps": [
            "not a mapping at all",
            {"title": "the good one", "category": "state", "severity": "low"}]}})
    out = explain.explain(key="ZZ-1", root=_estate(tmp_path))
    adv = next(d for d in out["decisions"] if d["id"] == "adversary")
    assert any("the good one" in b for b in adv["because"]), \
        "a malformed sibling entry cost us the well-formed gap"


def test_a_long_rationale_cannot_truncate_the_next_gap_away(tmp_path, monkeypatch):
    """The failure mode of capping the whole blob: gap 1 eats the budget and
    gap 2 vanishes silently. Each gap is bounded on its own instead."""
    _plan(monkeypatch, {
        "adversary": "adversarial review: 2 gap(s) raised",
        "adversary_detail": {"gaps": [
            {"title": "verbose", "rationale": "x" * 5000},
            {"title": "the second gap", "category": "authz"}]}})
    out = explain.explain(key="ZZ-1", root=_estate(tmp_path))
    adv = next(d for d in out["decisions"] if d["id"] == "adversary")
    assert any("the second gap" in b for b in adv["because"]), \
        "a long first rationale truncated the second gap out of existence"
    assert all(len(b) < 600 for b in adv["because"]), "a gap line is unbounded"


def test_an_unexpected_detail_shape_says_so(tmp_path, monkeypatch):
    """C13: 'we could not list the findings' is not 'there were none'."""
    _plan(monkeypatch, {"adversary": "adversarial review: ran",
                        "adversary_detail": ["a", "list", "not", "a", "dict"]})
    out = explain.explain(key="ZZ-1", root=_estate(tmp_path))
    adv = next(d for d in out["decisions"] if d["id"] == "adversary")
    joined = " ".join(adv["because"])
    assert "not the expected mapping" in joined and "list" in joined, \
        "an unexpected recorded shape was silently rendered as no findings"

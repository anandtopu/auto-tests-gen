"""Valid JSON of the WRONG SHAPE, across every state store.

`fs_lock.read_json_guarded` quarantines a file that will not parse. Nothing
checked that a parsed entry is an ENTRY -- `{"PROJ-1": "approved"}` is perfectly
good JSON, and every consumer then calls .get() on a string.

Found by fixing review_state and asking which other store had the same hole,
then PLANTING one bad entry beside a good one in each isolated store and
driving the real operator commands. Simulating a consumer in the probe was not
enough: the first pass crashed inside the probe's own code, which only proves
the store hands out the bad value, not that anything a human runs falls over.

Measured, before the fix:

    qa.py flaky     rc=1  AttributeError  at bin/qa.py:937          (test_health)
    qa.py report    rc=1  AttributeError  at team_report.py:144     (test_health)
    dashboard.py    rc=1  AttributeError  at plan_state.py:625      (plan_state)
    qa.py report    rc=1  TypeError       at team_report.py:217     (work_queue)
    dashboard.py    rc=1  TypeError       at bin/dashboard.py:879   (work_queue)
    retry_policy.attempts()  TypeError    at retry_policy.py:121    (retry_policy)

`bin/dashboard.py` is the worst of them: it produces NO dashboard at all rather
than one panel being wrong -- the same class already recorded for malformed run
records.

THE SECOND HALF MATTERS AS MUCH AS THE FIRST. Every one of these stores is
read-modify-write (review_state has 5 such round-trips, plan_state 11), so a
read-time filter alone would DELETE the malformed entry on the next unrelated
save. That is a read guard silently destroying state -- the exact failure
plan_state's own torn-write comment exists to prevent, and it would have been
introduced BY the fix. Unusable is not the same as ours to throw away: the
entry is the only remaining evidence of what someone wrote.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine" / "lib"))
import plan_state
import retry_policy
import review_state
import test_health
import work_queue


# --------------------------------------------------------- reads survive

@pytest.mark.parametrize("mod,attr,payload,good_key", [
    (review_state, "FILE", {"OK": {"status": "approved"}, "BAD": "approved"}, "OK"),
    (plan_state, "FILE", {"OK": {"status": "draft"}, "BAD": "draft"}, "OK"),
    (test_health, "FILE", {"OK": {"runs": 3, "flaky": True}, "BAD": "passed"}, "OK"),
])
def test_a_wrong_shaped_entry_is_hidden_from_readers(mod, attr, payload, good_key,
                                                     tmp_path, monkeypatch):
    f = tmp_path / "state.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(mod, attr, f)
    entries = mod.load()
    assert set(entries) == {good_key}, \
        "the store handed a caller something that is not an entry"
    assert all(isinstance(v, dict) for v in entries.values())
    good, bad = mod.load_with_issues()
    assert good == entries and bad == ["BAD"], \
        "the store dropped it without being able to say what it dropped"


def test_a_wrong_shaped_queue_item_is_hidden_from_readers(tmp_path, monkeypatch):
    """The queue is a LIST, so there is no key to name -- the count is the
    honest answer, not a fabricated id."""
    f = tmp_path / "queue.json"
    f.write_text(json.dumps([{"id": "1", "status": "queued"}, "nope", 7]),
                 encoding="utf-8")
    monkeypatch.setattr(work_queue, "FILE", f)
    items, bad = work_queue.load_with_issues()
    assert [i["id"] for i in items] == ["1"] and bad == 2
    assert work_queue.load() == items


@pytest.mark.parametrize("mod,attr,body", [
    (review_state, "FILE", ["not", "an", "object"]),
    (plan_state, "FILE", ["not", "an", "object"]),
    (test_health, "FILE", ["not", "an", "object"]),
])
def test_a_whole_document_of_the_wrong_shape_is_not_an_empty_store(
        mod, attr, body, tmp_path, monkeypatch):
    """An empty board reads as a CLEAR board. "I could not read this at all"
    has to survive as its own answer (C13)."""
    f = tmp_path / "state.json"
    f.write_text(json.dumps(body), encoding="utf-8")
    monkeypatch.setattr(mod, attr, f)
    good, bad = mod.load_with_issues()
    assert good == {} and bad, "an unreadable store was reported as simply empty"


def test_a_non_list_retry_entry_reads_as_no_attempts(tmp_path, monkeypatch):
    """retry_policy guarded each TIMESTAMP but not that the entry is iterable,
    so `for t in 3` raised out of the comprehension. Zero is the safe reading:
    this counter only ever REFUSES work, so an unreadable entry must fail
    towards letting an operator retry, not towards locking them out on
    evidence nobody can read."""
    f = tmp_path / "retries.json"
    f.write_text(json.dumps({"GOOD": [1.0], "BAD": 3}), encoding="utf-8")
    monkeypatch.setattr(retry_policy, "FILE", f)
    assert retry_policy.attempts("BAD", now=1.0) == []
    assert retry_policy.attempts("GOOD", now=1.0) == [1.0], \
        "the guard swallowed a readable entry too"


# ------------------------------------------- writes must not destroy it

def test_a_status_change_does_not_delete_the_unreadable_review_entry(
        tmp_path, monkeypatch):
    """The read filter would otherwise destroy data on the next save: every
    mutator is load -> change one key -> save, and saving the FILTERED view
    drops everything the filter hid."""
    f = tmp_path / "reviews.json"
    f.write_text(json.dumps({"OK": {"status": "pending_review"}, "BAD": "approved"}),
                 encoding="utf-8")
    monkeypatch.setattr(review_state, "FILE", f)

    data = review_state.load()
    data["OK"]["status"] = "approved"
    review_state.save(data)

    raw = json.loads(f.read_text(encoding="utf-8"))
    assert raw["BAD"] == "approved", \
        "a read-time shape guard deleted state on an unrelated write"
    assert raw["OK"]["status"] == "approved", "the real change was lost"


def test_an_approval_does_not_delete_the_unreadable_plan_entry(tmp_path, monkeypatch):
    """plan_state holds APPROVALS. Losing the record of what someone wrote is
    the worst thing this store can do."""
    f = tmp_path / "state.json"
    f.write_text(json.dumps({"OK": {"status": "draft"}, "BAD": "approved"}),
                 encoding="utf-8")
    monkeypatch.setattr(plan_state, "FILE", f)

    state = plan_state.load()
    state["OK"]["status"] = "approved"
    plan_state._save(state)

    raw = json.loads(f.read_text(encoding="utf-8"))
    assert raw["BAD"] == "approved", "an approval deleted unreadable plan state"
    assert raw["OK"]["status"] == "approved"


def test_an_enqueue_does_not_drop_the_unreadable_queue_item(tmp_path, monkeypatch):
    f = tmp_path / "queue.json"
    f.write_text(json.dumps([{"id": "1", "status": "queued"}, "nope"]),
                 encoding="utf-8")
    monkeypatch.setattr(work_queue, "FILE", f)

    items = work_queue.load()
    items.append({"id": "2", "status": "queued"})
    work_queue.save(items)

    raw = json.loads(f.read_text(encoding="utf-8"))
    assert "nope" in raw, "the unreadable item was dropped by an unrelated write"
    assert [i["id"] for i in raw if isinstance(i, dict)] == ["1", "2"]


def test_a_ci_ingest_does_not_drop_the_unreadable_health_entry(tmp_path, monkeypatch):
    """test_health's write path is `ingest`, not a `save()` -- and a mutation
    proved the difference: every other store's preservation was pinned, this
    one was not, so gutting it changed nothing any test could see.

    It matters more here than elsewhere. health.json is written from a CI
    upload over HTTP, so a malformed entry is likelier to arrive by accident,
    and the next green build would have quietly erased the evidence.
    """
    f = tmp_path / "health.json"
    f.write_text(json.dumps({"keep-me": "not-an-entry"}), encoding="utf-8")
    monkeypatch.setattr(test_health, "FILE", f)
    monkeypatch.setattr(test_health, "catalog_titles", lambda: {"a test": "t1"})

    junit = tmp_path / "results.xml"
    junit.write_text('<testsuite><testcase name="a test"/></testsuite>',
                     encoding="utf-8")
    matched, _unmatched = test_health.ingest(junit)

    raw = json.loads(f.read_text(encoding="utf-8"))
    assert matched == 1 and raw["t1"]["runs"] == 1, "the ingest itself broke"
    assert raw["keep-me"] == "not-an-entry", \
        "a CI upload deleted the unreadable entry it could not read"


def test_a_healthy_store_round_trips_byte_identically(tmp_path, monkeypatch):
    """The preservation logic must be inert when there is nothing to preserve,
    or every save starts reordering or duplicating a clean file."""
    f = tmp_path / "queue.json"
    original = [{"id": "1", "status": "queued"}]
    f.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(work_queue, "FILE", f)
    work_queue.save(work_queue.load())
    assert json.loads(f.read_text(encoding="utf-8")) == original

    g = tmp_path / "reviews.json"
    board = {"OK": {"status": "approved"}}
    g.write_text(json.dumps(board), encoding="utf-8")
    monkeypatch.setattr(review_state, "FILE", g)
    review_state.save(review_state.load())
    assert json.loads(g.read_text(encoding="utf-8")) == board

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


# ------------------------------- the second sweep: alert rules, selection, spool

def test_a_malformed_rule_does_not_stop_the_nightly_alert_evaluation(
        tmp_path, monkeypatch):
    """`normalize()` is the best implementation of this pattern in the repo --
    it does not merely survive a non-object rule, it RETURNS "rule was not an
    object; using safe defaults" so the UI can show what is wrong instead of
    500-ing. Then evaluate() wrote the normalized rule back into the ORIGINAL
    (`raw.update(rule)`), so the guard produced a safe value and the next line
    dereferenced the unsafe one.

    evaluate()'s own docstring promises "Never raises: this runs from `make
    maintain`, and a broken rule must not..." -- the function documented the
    exact guarantee it broke. commit=True is the default and maintenance runs
    it, so the nightly alerting job died on a hand-edited rules file.
    """
    import alert_rules
    f = tmp_path / "alert-rules.json"
    f.write_text(json.dumps({"rules": ["i-am-not-a-rule",
                                       {"id": "good", "name": "g",
                                        "kind": "event_count"}]}),
                 encoding="utf-8")
    monkeypatch.setattr(alert_rules, "rules_file", lambda: f)

    out = alert_rules.evaluate(notify=False, commit=True)
    assert any(r["id"] == "good" for r in out), \
        "the readable rule stopped being evaluated"
    # The malformed one is REPORTED, not silently skipped -- normalize()'s
    # whole design.
    assert any("not an object" in p for r in out for p in r.get("problems") or []), \
        "a rule that is not an object was dropped without saying so"


def test_test_fire_survives_a_malformed_rule_above_the_one_being_tested(
        tmp_path, monkeypatch):
    """Ordering hid this: the first probe put the GOOD rule first, so the loop
    matched and returned before ever touching the bad one. Reproduced only when
    the malformed rule sits above the target -- which is why the fixture here
    puts it there deliberately."""
    import alert_rules
    f = tmp_path / "alert-rules.json"
    f.write_text(json.dumps({"rules": ["i-am-not-a-rule",
                                       {"id": "good", "name": "g",
                                        "kind": "event_count",
                                        "channel": "slack"}]}),
                 encoding="utf-8")
    monkeypatch.setattr(alert_rules, "rules_file", lambda: f)
    monkeypatch.setattr(alert_rules, "deliver", lambda *a, **k: True)
    alert_rules.test_fire("good")            # must not raise


def test_a_malformed_selection_entry_reads_as_nothing_decided(tmp_path, monkeypatch):
    """Direction matters here. selection's rule is that an item nobody ruled on
    is INCLUDED, so reading an unreadable entry as "not decided yet" fails
    towards asking the reviewer again -- never towards a silent exclusion the
    reviewer never made."""
    import selection
    f = tmp_path / "selections.json"
    f.write_text(json.dumps({"PROJ-1": "approved"}), encoding="utf-8")
    monkeypatch.setattr(selection, "FILE", f)
    got = selection.load("PROJ-1")
    assert got == {"scenarios": {}, "tests": {}, "finalized": None}


def test_a_malformed_batch_record_does_not_hide_the_others(tmp_path, monkeypatch):
    """The spool is the largest single spend the platform can commit. A crash
    here does not merely break a report -- it hides an in-flight batch that was
    already submitted and paid for."""
    import batch_spool
    f = tmp_path / "batches.json"
    f.write_text(json.dumps({"batches": [{"id": "b1", "requests": [],
                                          "drained": True}, "nope"]}),
                 encoding="utf-8")
    monkeypatch.setattr(batch_spool, "BATCHES", f)
    assert [b["id"] for b in batch_spool.batches()] == ["b1"]
    assert [r["id"] for r in batch_spool.status()] == ["b1"]

    # A whole document of the wrong shape is empty, not an exception.
    f.write_text(json.dumps(["wrong", "document"]), encoding="utf-8")
    assert batch_spool.batches() == []


def test_a_malformed_batch_record_survives_on_disk(tmp_path, monkeypatch):
    """Same rule as the keyed stores: an unreadable record may be the only
    trace of a batch someone is being billed for, so reading past it must not
    erase it."""
    import batch_spool
    f = tmp_path / "batches.json"
    f.write_text(json.dumps({"batches": ["orphan-record",
                                         {"id": "b1", "requests": []}]}),
                 encoding="utf-8")
    monkeypatch.setattr(batch_spool, "BATCHES", f)
    d = batch_spool._read(f, {"batches": []})
    d["batches"].append({"id": "b2", "requests": []})
    batch_spool._write(f, d)
    raw = json.loads(f.read_text(encoding="utf-8"))
    assert "orphan-record" in raw["batches"], \
        "a write erased the record of a batch that may have been billed"


def test_drain_marks_batches_even_with_a_malformed_neighbour(tmp_path, monkeypatch):
    """THE MONEY PATH, and it needed its own pin: a mutation restoring the
    unguarded `b["id"]` in drain's mark-up loop survived every test above,
    exactly as test_health's ingest() did -- read guards were pinned, the write
    was not.

    It matters because of WHEN it fails. drain has already retrieved the
    results and written them to disk by this point; the loop only marks them
    drained. A malformed neighbour raising here aborts AFTER the spend and
    leaves every touched batch un-marked, so the next drain pays to retrieve
    them again.
    """
    import batch_spool
    f = tmp_path / "batches.json"
    f.write_text(json.dumps({"batches": [
        {"id": "b1", "requests": [{"custom_id": "PROJ-1|plan",
                                   "key": "PROJ-1", "phase": "plan"}]},
        "orphan-record"]}), encoding="utf-8")
    monkeypatch.setattr(batch_spool, "BATCHES", f)
    monkeypatch.setattr(batch_spool, "DIR", tmp_path)
    monkeypatch.setattr(batch_spool, "_call", lambda *a, **k: {
        "processing_status": "ended", "results_url": "https://x/results"})
    # _fetch_results returns raw JSONL TEXT, not parsed rows -- my first stub
    # invented a list and the test failed against unmutated code, which would
    # have "killed" every mutation without being evidence about any of them.
    monkeypatch.setattr(batch_spool, "_fetch_results", lambda url: json.dumps(
        {"custom_id": "PROJ-1|plan",
         "result": {"type": "succeeded",
                    "message": {"content": [{"type": "text", "text": "hi"}]}}}))
    batch_spool.drain()                       # must not raise

    raw = json.loads(f.read_text(encoding="utf-8"))
    marked = [b for b in raw["batches"] if isinstance(b, dict)]
    assert marked[0].get("drained") is True, \
        "the batch was retrieved but never marked drained; the next drain pays again"
    assert "orphan-record" in raw["batches"], "the unreadable record was erased"

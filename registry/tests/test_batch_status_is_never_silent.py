"""`make batch-status` answered three different situations with a blank screen.

FOUND BY DRIVING it. `make batch-pending` prints "0 request(s) spooled";
`make batch-status` printed NOTHING and exited 0, because the CLI loops over
`status()` and an empty list produces no lines.

Three situations shared that screen, on the command that answers "is there
money in flight?":

  * nothing has ever been submitted            - fine
  * every record is malformed                  - a batch may be billing right now
  * the spool document is the wrong shape      - the spool cannot be read at all

THE MODULE ALREADY ARGUED THIS CASE AGAINST ITSELF. `batches()` drops a
malformed record from the returned list and its docstring explains why it is
kept on disk: "an unreadable record may be the only trace of a batch someone is
being billed for", and hiding an in-flight batch "is the failure
BATCH_SUBMITTED_BUT_UNRECORDED exists to prevent". Every surface then iterates
the filtered list, so the evidence was preserved and never shown.

`batches_with_issues()` is the same read naming what it dropped - the shape
`review_state.load_with_issues` and `test_health.load_with_issues` already
established. It COUNTS rather than names, because a record that will not parse
has no id to name it by; the work queue reports its unusable items the same
way.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))


def _run(spool_dir, *args):
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/batch_spool.py"),
                        *args],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL,
                       env={**os.environ, "AIQE_BATCH_DIR": str(spool_dir)})
    assert r.returncode == 0, (r.stdout, r.stderr)
    return r.stdout, r.stderr


def _spool(tmp_path, doc):
    (tmp_path / "batches.json").write_text(json.dumps(doc)
                                           if not isinstance(doc, str) else doc,
                                           encoding="utf-8")
    return tmp_path


def test_an_empty_spool_says_so_instead_of_nothing(tmp_path):
    """THE DEFECT. An operator who has just submitted a batch and mistyped
    AIQE_BATCH_DIR saw exactly what an operator with nothing in flight saw."""
    out, err = _run(tmp_path, "status")
    assert "nothing is in flight" in out, (out, err)
    assert "batches.json" in out, "the message does not say WHERE it looked"
    assert "WARNING" not in err, err


def test_malformed_records_are_counted_not_swallowed(tmp_path):
    _spool(tmp_path, {"batches": [{"id": "b1", "requests": [1, 2],
                                   "drained": True}, "junk", 42]})
    out, err = _run(tmp_path, "status")
    assert "b1" in out, out
    assert "2 batch record(s)" in err and "INCOMPLETE" in err, err
    # The good row must still be shown: a warning that replaced the list would
    # hide the batch the operator came to look at.
    assert "drained" in out, out


def test_a_document_of_the_wrong_shape_is_not_an_empty_spool(tmp_path):
    """The loudest lie available here: a spool that cannot be read at all,
    reported as one with nothing in it."""
    _spool(tmp_path, '["not", "an", "object"]')
    out, err = _run(tmp_path, "status")
    assert "could NOT be read" in err, err
    assert "nothing is in flight" not in out, \
        "an unreadable spool was reported as an empty one"


def test_the_reader_returns_the_count_alongside_the_records(tmp_path,
                                                            monkeypatch):
    monkeypatch.setenv("AIQE_BATCH_DIR", str(tmp_path))
    import importlib
    import batch_spool
    importlib.reload(batch_spool)
    (tmp_path / "batches.json").write_text(
        json.dumps({"batches": [{"id": "a"}, None, {"id": "b"}, "x"]}),
        encoding="utf-8")
    good, bad = batch_spool.batches_with_issues()
    assert [g["id"] for g in good] == ["a", "b"]
    assert bad == 2
    # The old entry point keeps its contract for the callers that only want
    # usable records.
    assert batch_spool.batches() == good


def test_a_healthy_spool_reports_no_issues(tmp_path, monkeypatch):
    """OVER-FIX GUARD: a warning that fires on a clean spool is one operators
    learn to scroll past, and this one is about money."""
    monkeypatch.setenv("AIQE_BATCH_DIR", str(tmp_path))
    import importlib
    import batch_spool
    importlib.reload(batch_spool)
    (tmp_path / "batches.json").write_text(
        json.dumps({"batches": [{"id": "a"}]}), encoding="utf-8")
    assert batch_spool.batches_with_issues() == ([{"id": "a"}], 0)
    out, err = _run(tmp_path, "status")
    assert "WARNING" not in err, err


def test_an_absent_spool_is_not_reported_as_damaged(tmp_path, monkeypatch):
    """A fresh estate has no spool file. That is the empty case, not a
    corruption - opposite fixes."""
    monkeypatch.setenv("AIQE_BATCH_DIR", str(tmp_path))
    import importlib
    import batch_spool
    importlib.reload(batch_spool)
    assert batch_spool.batches_with_issues() == ([], 0)


def test_a_record_with_no_id_names_that_as_the_reason(tmp_path, monkeypatch):
    """Without this guard the record falls through to the API call, which
    fails, and the row reads "could not reach the API" - sending the operator
    after the network when the problem is the record in front of them. Two
    causes, two fixes, and only one of them is true (C13).

    The record is still REPORTED rather than dropped: it may be the only trace
    of a batch that is billing.
    """
    monkeypatch.setenv("AIQE_BATCH_DIR", str(tmp_path))
    import importlib
    import batch_spool
    importlib.reload(batch_spool)
    (tmp_path / "batches.json").write_text(
        json.dumps({"batches": [{"requests": [1, 2, 3]}]}), encoding="utf-8")
    rows = batch_spool.status()
    assert len(rows) == 1, rows
    assert rows[0]["state"] == "unknown"
    assert rows[0]["requests"] == 3, "the request count survives a missing id"
    assert "no batch id" in rows[0]["detail"], rows[0]
    assert "could not reach the API" not in rows[0]["detail"], rows[0]


def test_the_messages_are_console_safe(tmp_path):
    """Printed to a maintenance console, which is cp1252 on this host - the
    rule the cost_reconcile explanation already follows."""
    out, err = _run(tmp_path, "status")
    (out + err).encode("cp1252")
    _spool(tmp_path, {"batches": ["junk"]})
    out, err = _run(tmp_path, "status")
    (out + err).encode("cp1252")


def test_the_warning_goes_to_stderr_and_the_list_to_stdout(tmp_path):
    """The list is what a script parses; the caveat is what a human reads.
    Mixing them would break the one and bury the other."""
    _spool(tmp_path, {"batches": [{"id": "b1", "requests": [], "drained": True},
                                  "junk"]})
    out, err = _run(tmp_path, "status")
    assert "b1" in out and "WARNING" not in out, out
    assert "WARNING" in err and "b1" not in err, err

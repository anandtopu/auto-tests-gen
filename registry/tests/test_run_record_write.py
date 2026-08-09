"""The run record is produced, verified, and only then moved into place.

`reports/runs/<id>.json` is the durable evidence that a run happened — what the
scorecard counts, what `explain` reads, what an audit opens. It used to be
written as:

    python3 engine/lib/run_record.py ... | tee "reports/runs/$RUN_ID.json" | TELEM

`tee` creates and TRUNCATES its target before the producer emits a byte, so a
producer that dies leaves a 0-byte or half-written file where the record
belongs. Both failure modes were reproduced by the persistence review:

  * a malformed phase contract made run_record exit non-zero  -> size 0
  * a kill mid-stream, past the pipe buffer                    -> truncated JSON

and the downstream story is inconsistent — `qa.py` warns and names the file
while `bin/dashboard.py`, `team_report.py` and `eval/scorecard.py` all swallow
it silently, so the scorecard's commit rate quietly measures a different
population than it claims.

A matching orphan on disk (a gate commit archived with no run record) is what
made this concrete rather than theoretical.

Two defences, pinned here because either alone leaves a hole: the producer no
longer dies on one bad contract, AND the write verifies before it replaces.
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PIPELINE = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8", errors="replace")
RECORD_SRC = (ROOT / "engine/lib/run_record.py").read_text(encoding="utf-8")


def test_the_run_record_is_never_written_through_tee():
    assert not re.search(r'tee\s+"reports/runs/', PIPELINE), (
        "the run record is being written with tee again — tee truncates its "
        "target before the producer runs, so a producer failure destroys the "
        "record of a run that actually happened")
    assert PIPELINE.count("write_run_record") >= 3, (
        "expected the helper plus both call sites; a call site that bypasses "
        "it writes unverified")


def test_the_write_verifies_before_it_replaces():
    body = PIPELINE.split("write_run_record() {", 1)[1].split("\n}", 1)[0]
    assert 'json_ok.py "$tmp"' in body, "the produced record is no longer JSON-verified"
    assert '-s "$tmp"' in body, "an empty producer output would be accepted"
    assert 'mv -f "$tmp" "$dest"' in body, "the record is not moved into place atomically"
    # The destination must never be touched before verification succeeds.
    pre = body.split('mv -f "$tmp" "$dest"', 1)[0]
    assert '> "$dest"' not in pre and 'tee' not in pre, \
        "something writes the destination before the verify step"
    assert "REFUSING" in body and "FAILED" in body, \
        "a failed record must SAY so — silence here is how the orphan appeared"


def test_a_failed_record_leaves_previous_state_intact():
    """`rm -f "$tmp"` on both failure branches: scratch is cleaned, and the
    destination is never created, so an earlier good record survives."""
    body = PIPELINE.split("write_run_record() {", 1)[1].split("\n}", 1)[0]
    assert body.count('rm -f "$tmp"') >= 2, \
        "a failure path leaves scratch behind for the next run to trip over"


def test_one_malformed_contract_cannot_kill_the_whole_record(tmp_path):
    """The producer's half. A contract is LLM output that reached disk; the
    dashboard already guards it at six sites and this one did not."""
    assert "contract_unreadable" in RECORD_SRC, \
        "run_record no longer records WHY a contract could not be read"
    out = tmp_path / "out"
    out.mkdir()
    (out / "triage.contract.json").write_text('{"impact": "create"}', encoding="utf-8")
    (out / "broken.contract.json").write_text('{"truncated": ', encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/run_record.py"),
                        "PIN-1", "pr", "PIN-KEY"],
                       cwd=tmp_path, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=120)
    assert r.returncode == 0, f"producer died on a malformed contract: {r.stderr[-300:]}"
    import json
    rec = json.loads(r.stdout)
    names = {p["name"]: p for p in rec.get("phases", [])}
    assert "broken" in names, (
        "the unreadable phase vanished from the record — silently dropping it "
        "makes it indistinguishable from a phase that never ran (C13)")
    assert names["broken"].get("contract_unreadable"), \
        "the unreadable phase does not say why"
    assert names["triage"]["contract"] == {"impact": "create"}, \
        "a readable contract beside a broken one was lost"


def test_json_ok_is_strict_in_both_directions(tmp_path):
    good, bad = tmp_path / "g.json", tmp_path / "b.json"
    good.write_text('{"a": 1}', encoding="utf-8")
    bad.write_text('{"a":', encoding="utf-8")
    run = lambda p: subprocess.run(
        [sys.executable, str(ROOT / "engine/lib/json_ok.py"), str(p)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60).returncode
    assert run(good) == 0, "valid JSON rejected — every record would be refused"
    assert run(bad) != 0, "truncated JSON accepted — the guard does nothing"
    assert run(tmp_path / "absent.json") != 0, "a missing file passed the guard"

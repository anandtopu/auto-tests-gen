"""Contract extraction: 25 lines on the critical path of every LLM phase.

Every phase's JSON contract comes through this script, and it had no tests. It
scans a model's result text for the last object carrying the schema's required
keys, because prose around the answer may contain other brace-blobs.
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine" / "lib"))
import work_queue

SCRIPT = ROOT / "engine/lib/extract_contract.py"


def _run(tmp_path, result_text, required=("tests",)):
    res = tmp_path / "res.json"
    sch = tmp_path / "sch.json"
    res.write_text(json.dumps({"result": result_text}), encoding="utf-8")
    sch.write_text(json.dumps({"required": list(required)}), encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), str(res), str(sch)],
                       capture_output=True, text=True, cwd=ROOT,
                       stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout, r.stderr


def test_a_nested_example_does_not_beat_the_real_contract(tmp_path):
    """A phase that documents itself — "here is the contract, and here is an
    example" — nests an object carrying the SAME required keys.

    Left-to-right scanning with last-wins handed the win to that example,
    silently replacing the phase's real output with its illustration. Verified
    against the real script before the fix: it returned the example.
    """
    rc, out, _ = _run(tmp_path, (
        'Here is the contract: {"tests": [{"file": "real.spec.js"}], '
        '"example": {"tests": [{"file": "NOT-THE-CONTRACT.spec.js"}]}}'))
    assert rc == 0
    got = json.loads(out)
    assert got["tests"][0]["file"] == "real.spec.js"
    assert "example" in got, "the outer object was not the one returned"


def test_a_later_sibling_still_wins(tmp_path):
    """Last-wins is deliberate (parity finding P7): prose ahead of the answer
    may hold brace-blobs, so a later TOP-LEVEL object supersedes an earlier
    one. Only nesting was excluded — this must not have regressed."""
    rc, out, _ = _run(tmp_path, (
        'Considering {"tests": [{"file": "DRAFT.spec.js"}]} ... '
        'final answer: {"tests": [{"file": "FINAL.spec.js"}]}'))
    assert rc == 0
    assert json.loads(out)["tests"][0]["file"] == "FINAL.spec.js"


def test_objects_without_the_required_keys_are_skipped(tmp_path):
    """Prose brace-blobs — code snippets, config fragments — are not contracts."""
    rc, out, _ = _run(tmp_path, (
        'I considered {"note": "some thinking"} and {"config": {"a": 1}} '
        'then: {"tests": [{"file": "real.spec.js"}]}'))
    assert rc == 0
    assert json.loads(out)["tests"][0]["file"] == "real.spec.js"


def test_no_contract_is_a_named_failure_not_an_empty_success(tmp_path):
    """A phase whose contract cannot be found must stop the run with a reason.
    Printing nothing and exiting 0 would let the pipeline treat a phase that
    said nothing usable as a phase that succeeded."""
    rc, out, err = _run(tmp_path, "I could not produce JSON. Sorry.")
    assert rc != 0
    assert "NO_CONTRACT_JSON" in err
    assert out.strip() == ""


def test_malformed_json_does_not_crash_the_scan(tmp_path):
    """An unterminated brace mid-prose must be stepped over, not fatal — the
    real contract may still be further along."""
    rc, out, _ = _run(tmp_path, (
        'broken {"tests": [ oh no unterminated '
        'and then {"tests": [{"file": "real.spec.js"}]}'))
    assert rc == 0
    assert json.loads(out)["tests"][0]["file"] == "real.spec.js"


def test_multiple_required_keys_must_all_be_present(tmp_path):
    """A partial object is not a contract: taking one would hand downstream a
    dict missing the field it is about to read.

    The partial object comes LAST on purpose. With the complete one last, an
    `any(...)` check returns the right answer by accident — last-wins covers
    for it — and the test cannot tell the two apart. This ordering makes the
    difference observable: `any` would return the trailing `{"tests": [99]}`
    and drop `summary` entirely.
    """
    rc, out, _ = _run(tmp_path, (
        '{"tests": [2], "summary": "done"} '
        'and a trailing fragment {"tests": [99]}'),
        required=("tests", "summary"))
    assert rc == 0
    got = json.loads(out)
    assert got.get("summary") == "done", "a partial object was accepted"
    assert got["tests"] == [2]


def test_a_result_that_is_not_a_dict_is_handled(tmp_path):
    """`raw` may be a bare string rather than the claude -p envelope."""
    res = tmp_path / "res.json"
    sch = tmp_path / "sch.json"
    res.write_text(json.dumps('{"tests": [{"file": "real.spec.js"}]}'),
                   encoding="utf-8")
    sch.write_text(json.dumps({"required": ["tests"]}), encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), str(res), str(sch)],
                       capture_output=True, text=True, cwd=ROOT,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0
    assert json.loads(r.stdout)["tests"][0]["file"] == "real.spec.js"

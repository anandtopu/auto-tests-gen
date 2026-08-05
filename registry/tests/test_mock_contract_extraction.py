"""The mock path runs the same extractor the real path does.

REVIEW.md open item 2: every stub in engine/phases/mock_phase.sh wrote
out/<phase>.contract.json directly, so the whole mock path skipped
engine/lib/extract_contract.py — the step that pulls the contract out of the
model's prose and checks it against the phase schema.

Two things followed. A stub could drift from its schema and no demo run would
notice, because nothing compared the two. And every demo proved one step less of
the real chain than it appeared to: `make demo-jira` exercised resolve, the
phases, the gate — but not the parsing between a model's reply and the contract
the pipeline consumes.

Measured after the change, by renaming a required key in the analyze stub:
`make demo-jira` exits 2 and prints "CONTRACT REJECTED: the analyze stub does not
satisfy engine/phases/contracts/analyze.schema.json". Before, it exited 0.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import mock_result  # noqa: E402
import work_queue  # noqa: E402


def test_the_wrapper_looks_like_a_real_provider_reply():
    """Extraction's job is finding the contract among PROSE. Handing it a bare
    JSON document would exercise the one input it never actually receives."""
    out = mock_result.wrap('{"impact":"create"}')
    assert "```json" in out["result"], "no fenced block — this is not what a model returns"
    assert out["result"].strip().splitlines()[0].strip().endswith("follows."), \
        "the contract is not preceded by prose"
    assert out["result"].rstrip().endswith("adjusting."), \
        "the contract is not followed by prose"


def test_the_wrapper_never_reports_a_cost():
    """A mock spends nothing. total_cost_usd: 0 here would be harvested by
    budget.record() as a MEASURED zero, and labelling simulated figures is the
    one rule the cost stack does not bend."""
    out = mock_result.wrap("{}")
    assert "total_cost_usd" not in out
    assert out["provider"] == "mock"


def test_the_wrapper_file_is_not_the_one_budget_harvests():
    """budget.record() reads out/<phase>.json when it exists. The mock writes
    out/<phase>.mockresult.json precisely so a simulated run can never be
    recorded with basis `reported`."""
    src = (ROOT / "engine/phases/mock_phase.sh").read_text(encoding="utf-8")
    assert "mockresult.json" in src
    assert '"out/${OUT}.json"' not in src, \
        "the mock writes the file budget harvests — simulated cost would read as measured"


def test_an_unparseable_stub_is_named_before_extraction_confuses_it(tmp_path):
    bad = tmp_path / "c.json"
    bad.write_text("{not json", encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/mock_result.py"),
                        str(bad), str(tmp_path / "o.json")],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode != 0
    assert "not valid JSON" in r.stderr


@pytest.mark.parametrize("phase", ["triage", "analyze", "testplan", "validate"])
def test_each_stub_satisfies_its_own_schema(phase, tmp_path):
    """The property the whole change buys: run the stub, and the same extractor
    the real path uses must accept its output."""
    env = dict(os.environ, AIQE_MOCK="1")
    r = subprocess.run([work_queue.bash_exe(), "engine/phases/mock_phase.sh",
                        phase, "ZZ-MOCK-1", "workspace"],
                       cwd=ROOT, env=env, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=120)
    assert r.returncode == 0, f"{phase} stub rejected: {r.stdout}{r.stderr}"
    contract = ROOT / f"out/{phase}.contract.json"
    assert contract.exists(), f"{phase} produced no contract"
    schema = json.loads((ROOT / f"engine/phases/contracts/{phase}.schema.json")
                        .read_text(encoding="utf-8"))
    got = json.loads(contract.read_text(encoding="utf-8"))
    missing = [k for k in schema.get("required", []) if k not in got]
    assert not missing, f"{phase} contract is missing required keys: {missing}"


def test_the_harness_still_runs_extraction_at_all():
    """A guard nothing calls guards nothing — and this one lives in a trap, which
    is easy to delete without any test noticing."""
    src = (ROOT / "engine/phases/mock_phase.sh").read_text(encoding="utf-8")
    assert "extract_contract.py" in src, \
        "the mock path no longer runs the real extractor"
    assert "trap _finalize EXIT" in src, \
        "the finalize hook is gone, so early-exiting stubs skip extraction"

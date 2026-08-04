"""One malformed run record must not deny service to every other run.

A run record is LLM output that reached disk. Every renderer indexed its
contract lists directly — `s["id"]` on a scenario, `t["file"]` on a generated
test — so a single bad entry raised out of a generator and took down the whole
surface. `bin/dashboard.py` exited non-zero and produced NO dashboard at all;
the operator loses every other run's view because one record is wrong.

The contracts are schema-validated upstream, so this is defence in depth. The
property is that a bad ROW degrades the row, never the page — the same
reasoning as run_record's torn-TSV line (which used to destroy the whole
record) and the conftest sweep's non-dict guard (which used to crash
pytest_sessionstart with an INTERNALERROR).

Found by feeding a hand-written fixture to `make dashboard` while building the
run-progress view; three separate sites had it, in three modules.
"""
import json
import pathlib
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import run_progress as rp  # noqa: E402

# ts must be RECENT: team_report filters by window, so a 1970 timestamp put the
# record outside every report and the guard was never exercised - the test
# passed with the guard deleted. Found by mutation, which is the only way a
# fixture that quietly excludes itself ever shows up.
MALFORMED = {
    "run_id": "zzmalformed-pin", "ts": time.time(), "overall": "committed",
    "trigger": {"type": "jira", "key": "PROJ-MALFORMED-PIN"},
    "phases": [
        {"name": "testplan", "contract": {"scenarios": [1, "two", None,
                                                        {"id": "S3", "title": "ok"}]}},
        {"name": "generate", "contract": {"tests": ["oops", 7,
                                                    {"action": "created",
                                                     "file": "a.spec.js"}]}}],
    "gates": [{"test_repo": "e2e-api-tests-1", "status": "committed", "exit_code": 0}]}


@pytest.fixture
def planted():
    p = ROOT / "reports/runs/zzmalformed-pin.json"
    p.write_text(json.dumps(MALFORMED), encoding="utf-8")
    try:
        yield p
    finally:
        p.unlink(missing_ok=True)


def _run(args, timeout=240):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=timeout)


def test_dict_rows_keeps_only_usable_entries():
    assert rp.dict_rows([1, "a", None, {"x": 1}]) == [{"x": 1}]
    assert rp.dict_rows(None) == []
    assert rp.dict_rows([]) == []


def test_the_dashboard_still_generates(planted):
    """The whole page, not just this run: an exit-1 here means the operator has
    no dashboard for ANY run."""
    r = _run([sys.executable, "bin/dashboard.py"])
    assert r.returncode == 0, r.stderr[-600:]
    html = (ROOT / "reports/dashboard.html").read_text(encoding="utf-8", errors="replace")
    assert "PR-orders-api-201" in html, "other runs stopped rendering"


def test_the_dashboard_says_the_row_is_unreadable_rather_than_hiding_it(planted):
    """Silently dropping the bad entry would make a malformed record look like
    a short one. The row says what it is."""
    _run([sys.executable, "bin/dashboard.py"])
    html = (ROOT / "reports/dashboard.html").read_text(encoding="utf-8", errors="replace")
    assert "unreadable scenario entry" in html or "unreadable test entry" in html


def test_the_team_report_still_builds(planted):
    r = _run([sys.executable, "-c",
              "import sys;sys.path.insert(0,'engine/lib');import team_report;"
              "team_report.build(days=3650)"])
    assert r.returncode == 0, r.stderr[-600:]


def test_the_pr_comment_still_renders(planted):
    r = _run([sys.executable, "-c",
              "import sys,json;sys.path.insert(0,'engine/lib');import pr_comment;"
              "pr_comment.from_record(json.load(open("
              "'reports/runs/zzmalformed-pin.json',encoding='utf-8')))"])
    assert r.returncode == 0, r.stderr[-600:]


def test_run_progress_itself_survives_the_same_record(planted):
    """The module that reads records for the progress view must not be the one
    that crashes on them."""
    p = rp.progress(key="PROJ-MALFORMED-PIN")
    assert p["source"] == "record"
    gen = [s for s in p["steps"] if s["id"] == "generate"]
    assert gen and gen[0]["state"] == "done"

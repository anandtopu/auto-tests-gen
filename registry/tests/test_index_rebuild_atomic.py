"""A failed index rebuild keeps the index that was working.

`catalog.db` is the query index over the catalog — the store that answers
"which tests cover this repo". `rebuild()` used to open the LIVE database, run
`DROP TABLE IF EXISTS tests` through `executescript` (which commits), and only
then parse rows with a bare `json.loads(line)` and unguarded `e["mapping"]`.

So one torn JSONL line — or one row missing a key after a schema change — left
the index with a valid schema and ZERO rows. `qa.py sql` then answers "no tests
cover this repo" with a clean exit and correct-looking output: an inability to
read the catalog presented as an established absence of coverage (C13), on the
store that decides routing. Measured by the persistence review:

    baseline rows: 1
    rebuild aborted on torn catalog line: JSONDecodeError
    AFTER aborted rebuild -> table exists: True | rows: 0

Failing closed is the right call — a silently short index unroutes work, the
one failure this platform cannot see from the inside — but the failure must not
also destroy the index that was serving. Build beside, verify, swap.
"""
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = (ROOT / "catalog/bootstrap/index_db.py").read_text(encoding="utf-8")


def _rows(db):
    if not db.exists():
        return None
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM tests").fetchone()[0]
    finally:
        con.close()


@pytest.fixture
def estate(tmp_path):
    cat, db = tmp_path / "catalog", tmp_path / "catalog.db"
    cat.mkdir()
    shutil.copy(ROOT / "catalog/e2e-api-tests-1.jsonl", cat / "e2e-api-tests-1.jsonl")
    env = dict(os.environ, AIQE_CATALOG_DIR=str(cat), AIQE_CATALOG_DB=str(db))

    def build():
        return subprocess.run(
            [sys.executable, str(ROOT / "catalog/bootstrap/index_db.py")],
            cwd=ROOT, env=env, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=180)
    return cat, db, build


def test_a_torn_line_leaves_the_previous_index_intact(estate):
    cat, db, build = estate
    assert build().returncode == 0
    before = _rows(db)
    assert before and before > 0, "fixture produced no baseline index"

    (cat / "e2e-api-tests-1.jsonl").open("a", encoding="utf-8").write(
        '{"test_id": "torn", "mapping":\n')
    r = build()
    assert r.returncode != 0, (
        "a torn catalog line was accepted — a short index unroutes work "
        "silently, which is the one failure this platform cannot self-detect")
    assert _rows(db) == before, (
        "the failed rebuild destroyed the working index; qa.py sql would now "
        "answer 'no tests cover this repo' with a clean exit")


def test_a_failed_rebuild_leaves_no_scratch(estate):
    cat, db, build = estate
    build()
    (cat / "e2e-api-tests-1.jsonl").open("a", encoding="utf-8").write("{oops\n")
    build()
    assert not list(db.parent.glob("*.rebuilding-*")), \
        "scratch databases accumulate after failures"


def test_a_successful_rebuild_still_publishes(estate):
    """The other direction: a guard that never swaps would pass the tests above
    while making the index permanently stale."""
    cat, db, build = estate
    assert build().returncode == 0
    first = _rows(db)
    src = (cat / "e2e-api-tests-1.jsonl").read_text(encoding="utf-8").rstrip("\n")
    extra = src.splitlines()[0].replace('"test_id": "', '"test_id": "zz-extra-', 1)
    (cat / "e2e-api-tests-1.jsonl").write_text(src + "\n" + extra + "\n", encoding="utf-8")
    assert build().returncode == 0
    assert _rows(db) == first + 1, "a successful rebuild did not reach the live index"


def test_the_live_index_is_never_opened_for_the_build():
    """Structural: the DROP must happen on scratch, never on DB."""
    body = SRC.split("def rebuild(", 1)[1].split("\ndef ", 1)[0]
    assert "sqlite3.connect(tmp)" in body, \
        "the rebuild connects to the live index again — DROP TABLE would commit on it"
    assert "DROP TABLE" in body and "sqlite3.connect(DB)" not in body, \
        "the live database is opened during a destructive rebuild"
    swap = body.split("_fill(", 1)[1]
    assert "replace_atomic" in swap or "os.replace" in swap, \
        "the scratch build is never swapped in"

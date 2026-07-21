"""Regression tests for the P2 features: coverage-gap analysis, CI results
ingest (test health), the SQLite catalog index, and the scorecard metrics."""
import json, pathlib, sqlite3, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import coverage_gaps, test_health


# --- coverage gaps ---------------------------------------------------------------

def test_gaps_detects_uncovered_surface():
    gaps = coverage_gaps.compute()
    # orders-api: both contract endpoints are exercised by cataloged tests
    assert gaps["orders-api"]["uncovered"] == []
    assert "/v1/orders/{id}/discounts" in gaps["orders-api"]["covered"]
    # catalog-api: has a contract but zero tests -> everything uncovered
    assert "/v1/catalog/search" in gaps["catalog-api"]["uncovered"]
    # web-storefront-ui: only the cart route is covered
    ui = gaps["web-storefront-ui"]
    assert "/checkout/cart" in ui["covered"]
    assert "/checkout/payment" in ui["uncovered"]


def test_gaps_markdown_marks_gaps():
    md = coverage_gaps.to_markdown()
    assert "[NO TEST] /v1/catalog/search" in md
    assert "[covered] /v1/orders/{id}" in md


def test_agents_md_annotates_gaps():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "[NO TEST]" in text


# --- CI results ingest / test health ---------------------------------------------

def test_junit_parse_and_matching():
    cases = test_health.parse_junit(ROOT / "eval/benchmark/results/junit-sample.xml")
    assert ("PROJ-88: applies % discount", True) in cases
    assert ("PROJ-61: gets an order by id", False) in cases
    assert all(name != "skipped" for name, _ in cases)


def test_ingest_updates_health(tmp_path, monkeypatch):
    monkeypatch.setattr(test_health, "FILE", tmp_path / "health.json")
    m, u = test_health.ingest(ROOT / "eval/benchmark/results/junit-sample.xml")
    assert m == 3 and u == 1                       # legacy case has no catalog match
    health = json.load(open(tmp_path / "health.json"))
    key88 = next(k for k in health if "PROJ-88" in k)
    key61 = next(k for k in health if "get-order" in k)
    assert health[key88]["pass_rate"] == 1.0 and health[key88]["last_status"] == "passed"
    assert health[key61]["pass_rate"] == 0.0 and health[key61]["last_status"] == "failed"
    # repeat ingests accumulate runs; alternating results become flaky at >=3 runs
    test_health.ingest(ROOT / "eval/benchmark/results/junit-sample.xml")
    test_health.ingest(ROOT / "eval/benchmark/results/junit-sample.xml")
    health = json.load(open(tmp_path / "health.json"))
    assert health[key88]["runs"] == 3 and health[key88]["flaky"] is False


# --- SQLite catalog index --------------------------------------------------------

def test_index_db_rebuild_and_query():
    r = subprocess.run([sys.executable, str(ROOT / "catalog/bootstrap/index_db.py")],
                       capture_output=True, text=True, cwd=ROOT, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    con = sqlite3.connect(ROOT / "reports/catalog.db")
    n_db = con.execute("SELECT COUNT(*) FROM tests").fetchone()[0]
    orphans = con.execute("SELECT COUNT(*) FROM tests WHERE status='orphan'").fetchone()[0]
    con.close()
    n_jsonl = sum(1 for f in (ROOT / "catalog").glob("*.jsonl")
                  if f.name != "catalog.sample.jsonl"
                  for line in open(f, encoding="utf-8") if line.strip())
    assert n_db == n_jsonl and orphans >= 1


def test_qa_sql_readonly():
    r = subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), "sql",
                        "SELECT test_repo, COUNT(*) FROM tests GROUP BY test_repo"],
                       capture_output=True, text=True, cwd=ROOT, stdin=subprocess.DEVNULL)
    assert r.returncode == 0 and "e2e-api-tests-1" in r.stdout
    # writes are rejected (read-only connection)
    r = subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), "sql",
                        "DELETE FROM tests"],
                       capture_output=True, text=True, cwd=ROOT, stdin=subprocess.DEVNULL)
    assert r.returncode != 0


# --- scorecard -------------------------------------------------------------------

def test_scorecard_reports_new_metrics():
    r = subprocess.run([sys.executable, str(ROOT / "eval/scorecard.py")],
                       capture_output=True, text=True, cwd=ROOT, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    for marker in ("Routing accuracy", "Commit rate", "Update-vs-create",
                   "Acceptance rate", "Test health"):
        assert marker in r.stdout, f"missing scorecard line: {marker}\n{r.stdout}"

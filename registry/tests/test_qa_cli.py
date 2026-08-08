"""Regression tests for the QA CLI (bin/qa.py) and catalog portability."""
import glob, importlib.util, json, os, pathlib, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

spec = importlib.util.spec_from_file_location("qa", ROOT / "bin/qa.py")
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)


def test_catalog_paths_are_posix():
    """Windows backslashes must never leak into catalog test ids/files (gate greps them)."""
    for f in glob.glob(str(ROOT / "catalog/*.jsonl")):
        if pathlib.Path(f).name == "catalog.sample.jsonl":
            continue
        for line in open(f, encoding="utf-8"):
            if not line.strip():
                continue
            e = json.loads(line)
            assert "\\" not in e["file"], f"backslash path in {f}: {e['file']}"
            assert "\\" not in e["test_id"], f"backslash test_id in {f}: {e['test_id']}"


def test_api_tests_classified_as_api_layer():
    """fetch()-style API evidence must yield layer=api (was misclassified as ui)."""
    entries = [json.loads(l) for l in open(ROOT / "catalog/e2e-api-tests-1.jsonl", encoding="utf-8")]
    with_endpoints = [e for e in entries if e["evidence"]["endpoints"]]
    assert with_endpoints, "expected API evidence in e2e-api-tests-1 catalog"
    assert all(e["layer"] == "api" for e in with_endpoints)


def test_set_mapping_confirm_and_orphan():
    entry = {"mapping": {"app_repos": ["x"], "services": ["x"], "status": "needs_review",
                         "confidence": 0.6, "method": ["contract_match"]}}
    qa._set_mapping(entry, "orders-api")
    assert entry["mapping"]["status"] == "confirmed"
    assert entry["mapping"]["app_repos"] == ["orders-api"]
    assert "human_review" in entry["mapping"]["method"]

    qa._set_mapping(entry, "ORPHAN")
    assert entry["mapping"]["status"] == "orphan"
    assert entry["mapping"]["app_repos"] == []


def test_set_mapping_rejects_unregistered_repo():
    entry = {"mapping": {"app_repos": [], "services": [], "status": "needs_review",
                         "confidence": 0.6, "method": []}}
    with pytest.raises(SystemExit):
        qa._set_mapping(entry, "no-such-repo")


@pytest.mark.parametrize("decision", ["", "   ", ",,", " ; , "])
def test_set_mapping_rejects_empty_repo_lists_without_mutation(decision):
    entry = {"mapping": {"app_repos": ["orders-api"], "services": ["orders-api"],
                         "status": "confirmed", "confidence": 1.0,
                         "method": ["human_review"]}}
    before = json.loads(json.dumps(entry))

    with pytest.raises(SystemExit, match="use ORPHAN"):
        qa._set_mapping(entry, decision)

    assert entry == before, "a refused decision must leave the durable row unchanged"


def test_catalog_save_is_atomic_when_serialization_is_interrupted(tmp_path, monkeypatch):
    path = tmp_path / "catalog.jsonl"
    original = '{"generation":"old-1"}\n{"generation":"old-2"}\n'
    path.write_text(original, encoding="utf-8")
    real_dumps = qa.json.dumps
    calls = 0

    def fail_second(value):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated interruption")
        return real_dumps(value)

    monkeypatch.setattr(qa.json, "dumps", fail_second)
    with pytest.raises(OSError, match="simulated interruption"):
        qa.save_catalog(path, [{"generation": "new-1"}, {"generation": "new-2"}])

    assert path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob(".*.tmp")), "a failed save must not leak temp files"


def test_qa_status_and_coverage_run_clean():
    for sub in ("status", "coverage", "review"):
        r = subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), sub],
                           capture_output=True, text=True, cwd=ROOT,
                           stdin=subprocess.DEVNULL)
        assert r.returncode == 0, f"qa.py {sub} failed: {r.stderr}"


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _demo_state


# FUNCTION scope, not module: these assertions read the live estate, and any
# earlier test in the session can regenerate or wipe it. Seeding once per module
# leaves the later tests asserting against whatever happened in between.
@pytest.fixture(autouse=True)
def _demo_artifacts():
    """See test_export_plan: assert on seeded state, not on ambient demo state."""
    # The artifacts view is exercised for BOTH key shapes (JIRA and PR), so seed
    # both — a PR key has no test plan, only a run record.
    cleanups = [_demo_state.ensure_generated_run("PROJ-301"),
                _demo_state.ensure_generated_run("PR-orders-api-201", kind="pr",
                                                 seed_plan=False)]
    yield
    for c in cleanups:
        c()


def test_qa_artifacts_view():
    """artifacts <KEY> shows plan/scenarios/tests for a recorded run (JIRA + PR keys)."""
    r = subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), "artifacts", "PROJ-301"],
                       capture_output=True, text=True, cwd=ROOT, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    assert "testplans/PROJ-301.md" in r.stdout
    assert "Generated tests:" in r.stdout
    # PR keys resolve with or without the PR- prefix
    r2 = subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), "artifacts", "orders-api-201"],
                        capture_output=True, text=True, cwd=ROOT, stdin=subprocess.DEVNULL)
    assert r2.returncode == 0 and "PR-orders-api-201" in r2.stdout
    # unknown key fails with the known-key hint, not a traceback
    r3 = subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), "artifacts", "NOPE-1"],
                        capture_output=True, text=True, cwd=ROOT, stdin=subprocess.DEVNULL)
    assert r3.returncode != 0 and "Known keys" in (r3.stdout + r3.stderr)


def test_artifacts_full_refuses_persisted_diff_outside_run_archive(tmp_path):
    record = ROOT / "reports" / "runs" / "9999999998.json"
    canary = tmp_path / "private.diff"
    canary.write_text("ARTIFACT_PATH_CANARY", encoding="utf-8")
    record.write_text(json.dumps({
        "run_id": "artifact-path-test",
        "ts": 9999999998,
        "trigger": {"type": "pr", "key": "PR-ART-PATH-1"},
        "overall": "committed",
        "phases": [],
        "gates": [{"test_repo": "synthetic", "status": "committed",
                   "diff": str(canary)}],
    }), encoding="utf-8")
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT / "bin/qa.py"), "artifacts",
             "PR-ART-PATH-1", "--full"],
            capture_output=True, text=True, cwd=ROOT, stdin=subprocess.DEVNULL)
    finally:
        record.unlink(missing_ok=True)
    assert r.returncode == 0, r.stderr
    assert "unsafe diff path refused" in r.stdout
    assert "ARTIFACT_PATH_CANARY" not in r.stdout


def test_review_board_shows_a_dash_for_a_never_reviewed_entry(tmp_path, monkeypatch):
    """`set_release` records a target version before any status transition, so the
    entry has no `updated`. Defaulting it to 0 printed "1969-12-31", which reads as a
    corrupt record rather than "nothing has happened yet"."""
    import review_state
    store = tmp_path / "reviews.json"
    monkeypatch.setattr(review_state, "FILE", store)
    review_state.set_release("NEVER-1", "2026.08")
    entry = review_state.load()["NEVER-1"]
    assert not entry.get("updated"), "precondition: the entry carries no timestamp"

    r = subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), "reviews"],
                       capture_output=True, text=True, cwd=ROOT,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL,
                       env={**os.environ, "AIQE_REVIEWS_FILE": str(store)})
    assert "1969" not in r.stdout, "an unset timestamp must not render as the epoch"

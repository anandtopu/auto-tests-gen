"""What could not be read is counted and said, not silently dropped.

Two tails of the persistence review, both the same shape: code that correctly
declines to crash, and then fails to mention what it skipped.

F6 — `catalog_slice` builds the existing-test context handed to the generate
phase. Skipping a torn row is the right policy (starving the phase of the whole
catalog is worse), but the skip was uncounted, and an entire UNREADABLE catalog
file was skipped the same silent way. A short slice makes the agent re-author
coverage it cannot see, and duplicate tests are the one outcome that module
exists to prevent. Worse, the pipeline sent its stderr to /dev/null, so even
the honest reporting it did have never reached the run log.

F7 — `qa.py prune` skipped records it could not parse, which made them
IMMORTAL: never in `records`, so never in `doomed`, so neither they nor their
diffs were ever removed. Torn records are exactly what a crashed run leaves
behind, so the files retention exists to bound were the ones it never touched.
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import catalog_slice  # noqa: E402

PIPELINE = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8", errors="replace")


# --- F6: the slice counts and reports what it could not read -----------------

def test_load_rows_counts_malformed_lines(tmp_path):
    (tmp_path / "a.jsonl").write_text(
        json.dumps({"test_id": "t1", "test_repo": "r"}) + "\n"
        + "{not json\n"
        + json.dumps({"test_id": "t2", "test_repo": "r"}) + "\n",
        encoding="utf-8")
    drops = {}
    rows = catalog_slice.load_rows(tmp_path, drops=drops)
    assert len(rows) == 2, "readable rows were lost"
    assert drops.get("lines") == 1, (
        "a malformed row was skipped without being counted — the phase gets a "
        "short view of existing tests and nobody can tell")


def test_load_rows_counts_an_unreadable_file(tmp_path):
    (tmp_path / "good.jsonl").write_text(
        json.dumps({"test_id": "t1", "test_repo": "r"}) + "\n", encoding="utf-8")
    (tmp_path / "bad.jsonl").mkdir()          # a directory: open() raises OSError
    drops = {}
    rows = catalog_slice.load_rows(tmp_path, drops=drops)
    assert len(rows) == 1
    assert drops.get("files") == 1, (
        "an entire repo's catalog vanished silently — far worse than one row")
    assert drops.get("detail"), "the unreadable file is not named"


def test_the_slice_still_works_without_a_drops_dict(tmp_path):
    """`drops` is optional; existing callers must not need changing."""
    (tmp_path / "a.jsonl").write_text("{bad\n", encoding="utf-8")
    assert catalog_slice.load_rows(tmp_path) == []


def test_the_cli_reports_incomplete_input(tmp_path):
    cat = tmp_path / "catalog"
    cat.mkdir()
    (cat / "a.jsonl").write_text(
        json.dumps({"test_id": "t1", "test_repo": "e2e-api-tests-1",
                    "mapping": {"app_repos": ["orders-api"]}}) + "\n"
        + "{torn\n", encoding="utf-8")
    contract = tmp_path / "resolve.json"
    contract.write_text(json.dumps({"test_repos": ["e2e-api-tests-1"],
                                    "source_repos": ["orders-api"]}), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(ROOT / "engine/lib/catalog_slice.py"), str(contract)],
        cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL,
        timeout=120, env={**__import__("os").environ, "AIQE_CATALOG_DIR": str(cat)})
    assert r.returncode == 0
    assert "INCOMPLETE INPUT" in r.stderr, (
        f"the CLI did not report the dropped row; stderr was: {r.stderr!r}")
    assert "malformed row" in r.stderr


def test_the_pipeline_does_not_discard_the_slice_diagnostics():
    """The report only helps if it reaches the log."""
    call = PIPELINE[PIPELINE.index("catalog_slice.py out/resolve.contract.json \"$repo\""):]
    # Only the python invocation itself — up to the first `||`. The `cp`
    # fallback after it redirects its OWN stderr on purpose (it suppresses "no
    # such file" when there is nothing to fall back to), and flagging that
    # would push someone to remove a suppression that is correct.
    call = call[:call.index("||")]
    assert "2>/dev/null" not in call, (
        "the fan-out slice call discards stderr again — the only signal that "
        f"an agent's existing-test context is short goes to /dev/null: {call!r}")


# --- F7: unreadable run records are pruned, not immortal ---------------------

def _estate(tmp_path, good=5, torn=2):
    for i in range(good):
        (tmp_path / f"{1700000000 + i}-1.json").write_text(
            json.dumps({"ts": 1700000000 + i}), encoding="utf-8")
        (tmp_path / f"{1700000000 + i}-1-repo.diff").write_text("x", encoding="utf-8")
    for i in range(torn):
        (tmp_path / f"{1600000000 + i}-9.json").write_text('{"ts": ', encoding="utf-8")
        (tmp_path / f"{1600000000 + i}-9-repo.diff").write_text("x", encoding="utf-8")
    return tmp_path


def _prune(d, keep):
    return subprocess.run(
        [sys.executable, str(ROOT / "bin/qa.py"), "prune", "--keep", str(keep),
         "--dir", str(d)],
        cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=180)


def test_unreadable_records_and_their_diffs_are_pruned(tmp_path):
    d = _estate(tmp_path)
    r = _prune(d, keep=3)
    assert r.returncode == 0, r.stderr[-400:]
    left = sorted(p.name for p in d.glob("*"))
    assert not [n for n in left if n.startswith("1600000")], (
        "torn records survived a prune — they are never in `records`, so they "
        "and their diffs would accumulate forever")


def test_the_prune_summary_names_the_unreadable_records(tmp_path):
    r = _prune(_estate(tmp_path), keep=3)
    assert "could not be parsed" in r.stdout, (
        "a retention summary that omits damaged records lets an operator "
        "believe the history is intact")


def test_readable_records_within_the_keep_window_survive(tmp_path):
    """The other direction — a prune that deletes everything would satisfy the
    tests above while destroying the QA history."""
    d = _estate(tmp_path, good=5, torn=0)
    _prune(d, keep=3)
    kept = sorted(p.name for p in d.glob("*.json"))
    assert len(kept) == 3, f"expected the 3 newest records to survive, got {kept}"
    assert kept[-1] == "1700000004-1.json", "the newest record was pruned"

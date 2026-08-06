"""PRD A6 same-run testcase learning and immutable outcome provenance."""
import hashlib
import pathlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import impact_analysis as impact  # noqa: E402
import review_state  # noqa: E402
import selection  # noqa: E402
import testcase_learning as learning  # noqa: E402


SPEC = r'''
import { test, expect } from "@playwright/test";
import { loginAs } from "../fixtures/session";
test.describe("checkout", () => {
  test("A6 remembers committed login", async ({ page }) => {
    await loginAs(page);
    await page.goto("/checkout/payment");
    await expect(page.getByTestId("payment-form")).toBeVisible();
  });
});
'''


def _git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, text=True, check=True,
                            encoding="utf-8", errors="replace")
    return result.stdout.strip()


def _estate(tmp_path, monkeypatch, source=SPEC):
    monkeypatch.setenv("AIQE_TESTCASE_INDEX", "1")
    repo = tmp_path / "workspace/tests/e2e-ui"
    spec = repo / "suites/checkout.spec.js"
    spec.parent.mkdir(parents=True)
    spec.write_text(source, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "A6 Test")
    _git(repo, "add", "suites/checkout.spec.js")
    _git(repo, "commit", "-q", "-m", "generated test")
    sha = _git(repo, "rev-parse", "HEAD")
    out = tmp_path / "out"
    out.mkdir()
    gate = out / "gate_results.tsv"
    gate.write_text(f"e2e-ui\tcommitted\t0\t{sha}\n", encoding="utf-8")
    return repo, spec, sha, gate


def _index(tmp_path, gate, **kwargs):
    return learning.index_commits(
        "run-a6", "A6-1", tmp_path, gate_results=gate,
        test_repos={"e2e-ui": "suites"}, refresh_vectors=False, **kwargs)


def _chunks(tmp_path):
    return learning._read_jsonl(
        tmp_path / "reports/knowledge-index/chunks.jsonl")


def test_gate_commit_is_indexed_and_retrievable_in_the_same_run(tmp_path, monkeypatch):
    _, _, sha, gate = _estate(tmp_path, monkeypatch)
    gate.write_text(f"e2e-ui\tcommitted\t0\t{sha[:7]}\n", encoding="utf-8")
    result = _index(tmp_path, gate)
    assert result["state"] == "indexed"
    assert result["repos"][0]["commit"] == sha
    chunks = _chunks(tmp_path)
    case = next(row for row in chunks if row.get("kind") == "testcase")
    assert "helper:loginAs" in case["exercises"]
    assert case["repo_file"] == "suites/checkout.spec.js"

    # A3 can consume the just-written chunks immediately; no future clone or
    # nightly rebuild is needed.
    cases = impact._cases([], chunks)
    ranked = impact._deterministic(cases, "+ await loginAs(page)", set(), {})
    assert ranked[0]["case_id"] == case["case_id"]
    event = learning.events(tmp_path)[0]
    assert event["run_id"] == "run-a6" and event["commit"] == sha
    assert event["gate_result"] == "committed"


@pytest.mark.parametrize("status", ["no_changes", "quarantined", "clone_failed"])
def test_non_committed_gates_never_change_the_index(tmp_path, monkeypatch, status):
    _, _, sha, gate = _estate(tmp_path, monkeypatch)
    chunks_path = tmp_path / "reports/knowledge-index/chunks.jsonl"
    chunks_path.parent.mkdir(parents=True)
    original = {"chunk_id": "guidance:keep:one", "kind": "guidance",
                "repo": "keep", "text": "unchanged", "sha256": "abc"}
    learning._write_jsonl(chunks_path, [original])
    gate.write_text(f"e2e-ui\t{status}\t5\t{sha}\n", encoding="utf-8")
    result = _index(tmp_path, gate)
    assert result["state"] == "no_commits"
    assert _chunks(tmp_path) == [original]
    assert learning.events(tmp_path) == []


def test_feature_off_preserves_index_and_run_artifact_parity(tmp_path, monkeypatch):
    monkeypatch.setenv("AIQE_TESTCASE_INDEX", "0")
    stale = tmp_path / "out/learning-loop.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale", encoding="utf-8")
    result = learning.index_commits("run-off", "A6-OFF", tmp_path)
    assert result["state"] == "disabled"
    assert learning.persist_result(result, tmp_path) is None
    assert not stale.exists()
    assert not (tmp_path / "reports/knowledge-index/chunks.jsonl").exists()


def test_reindex_replaces_old_case_identity_without_duplicates(tmp_path, monkeypatch):
    repo, spec, _, gate = _estate(tmp_path, monkeypatch)
    first = _index(tmp_path, gate)["repos"][0]["case_ids"][0]
    spec.write_text(SPEC.replace("remembers committed login", "uses current login"),
                    encoding="utf-8")
    _git(repo, "add", "suites/checkout.spec.js")
    _git(repo, "commit", "-q", "-m", "update generated test")
    sha = _git(repo, "rev-parse", "HEAD")
    gate.write_text(f"e2e-ui\tcommitted\t0\t{sha}\n", encoding="utf-8")
    second = _index(tmp_path, gate)["repos"][0]["case_ids"][0]
    ids = {row.get("case_id") for row in _chunks(tmp_path) if row.get("case_id")}
    assert first != second and ids == {second}
    assert len([e for e in learning.events(tmp_path)
                if e["event_type"] == "gate_commit"]) == 2


def test_unparsed_commit_is_visible_instead_of_reported_as_no_tests(tmp_path, monkeypatch):
    _, _, _, gate = _estate(
        tmp_path, monkeypatch, "test.each([[1]])('unsupported', () => {})")
    result = _index(tmp_path, gate)
    assert result["repos"][0]["unparsed_files"] == ["suites/checkout.spec.js"]
    fallback = _chunks(tmp_path)[0]
    assert fallback["kind"] == "spec" and fallback["parse_status"] == "unparsed"
    assert result["repos"][0]["case_ids"] == []


def test_review_outcomes_link_to_latest_run_without_mutating_chunk_bytes(
        tmp_path, monkeypatch):
    _, _, _, gate = _estate(tmp_path, monkeypatch)
    _index(tmp_path, gate)
    before = [(row["chunk_id"], row["text"], row["sha256"])
              for row in _chunks(tmp_path)]
    learning.record_review("A6-1", "approved", "qe-lead", "ship it", 100, tmp_path)
    event = learning.events(tmp_path)[-1]
    assert event["event_type"] == "review_decision"
    assert event["produced_run"] == "run-a6" and event["case_ids"]
    after = [(row["chunk_id"], row["text"], row["sha256"])
             for row in _chunks(tmp_path)]
    assert after == before, "review provenance rewrote indexed code bytes"


def test_review_state_and_duplicate_selection_emit_outcomes(tmp_path, monkeypatch):
    provenance = tmp_path / "review-provenance.jsonl"
    monkeypatch.setenv("AIQE_TESTCASE_PROVENANCE_FILE", str(provenance))
    monkeypatch.setattr(review_state, "FILE", tmp_path / "reviews.json")
    learning.append_event({
        "event_id": "commit-1", "event_type": "gate_commit", "recorded_at": 1,
        "run_id": "run-1", "key": "A6-2", "case_ids": ["case:new"],
        "chunk_ids": ["chunk:new"]})
    review_state.set_status("A6-2", "approved", "lead", "looks good", ts=2)
    review = learning.events()[-1]
    assert review["status"] == "approved" and review["case_ids"] == ["case:new"]

    # A root-scoped selective decision uses the root-scoped durable store.
    learning.append_event({
        "event_id": "commit-2", "event_type": "gate_commit", "recorded_at": 1,
        "run_id": "run-2", "key": "A6-3", "case_ids": ["case:generated"],
        "chunk_ids": ["chunk:generated"]}, tmp_path)
    selection.set_items(
        "A6-3", "scenarios", {"A6-3-S1": False}, actor="lead",
        reason="already covered", reason_code="duplicate",
        duplicate_case_id="case:canonical", root=tmp_path)
    duplicate = learning.events(tmp_path)[-1]
    assert duplicate["event_type"] == "duplicate_exclusion"
    assert duplicate["duplicate_case_id"] == "case:canonical"
    assert duplicate["case_ids"] == ["case:generated"]


def test_append_only_store_keeps_all_concurrent_events(tmp_path):
    def write(i):
        return learning.append_event({
            "event_id": f"event-{i}", "event_type": "review_decision",
            "recorded_at": i, "key": "A6-C", "status": "approved",
            "case_ids": [f"case:{i}"], "chunk_ids": []}, tmp_path)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(12)))
    rows = learning.events(tmp_path)
    assert len(rows) == 12
    assert {row["event_id"] for row in rows} == {f"event-{i}" for i in range(12)}
    # Retrying one event is idempotent, not a thirteenth provenance claim.
    write(3)
    assert len(learning.events(tmp_path)) == 12


def test_torn_provenance_is_preserved_and_blocks_the_next_append(tmp_path, monkeypatch):
    path = tmp_path / "reports/runs/testcase-provenance.jsonl"
    path.parent.mkdir(parents=True)
    damaged = '{"event_id":"kept"}\n{"event_id":'
    path.write_text(damaged, encoding="utf-8")
    with pytest.raises(RuntimeError, match="malformed JSON"):
        learning.append_event({"event_id": "new", "event_type": "review_decision"},
                              tmp_path)
    assert path.read_text(encoding="utf-8") == damaged
    monkeypatch.setenv("AIQE_ARTIFACT_REUSE", "1")
    result = learning.ranking_signal_result(tmp_path)
    assert result["state"] == "unavailable" and result["scores"] == {}


def test_outcomes_are_flagged_bounded_tie_breakers_not_match_scores(
        tmp_path, monkeypatch):
    case_a = {"case_id": "case:a", "test_repo": "api", "file": "a.spec.js",
              "suite": [], "title": "a", "exercises": ["helper:loginAs"]}
    case_b = {"case_id": "case:b", "test_repo": "api", "file": "b.spec.js",
              "suite": [], "title": "b", "exercises": ["helper:loginAs"]}
    learning.append_event({
        "event_id": "commit-b", "event_type": "gate_commit", "recorded_at": 1,
        "run_id": "run-b", "key": "A6-B", "case_ids": ["case:b"],
        "chunk_ids": ["chunk:b"]}, tmp_path)
    learning.record_review("A6-B", "approved", "lead", "", 2, tmp_path)
    monkeypatch.delenv("AIQE_ARTIFACT_REUSE", raising=False)
    assert learning.ranking_signals(tmp_path) == {}
    monkeypatch.setenv("AIQE_ARTIFACT_REUSE", "1")
    signals = learning.ranking_signals(tmp_path)
    ranked = impact._deterministic(
        [case_a, case_b], "+ await loginAs(page)", set(), {}, signals)
    assert ranked[0]["case_id"] == "case:b"
    assert ranked[0]["confidence"] == ranked[1]["confidence"]
    assert ranked[0]["signals"]["outcome_tie_breaker"] == 1.0

    # Stronger deterministic surface evidence still wins over any accepted-run
    # preference; outcomes cannot create or rescore a candidate.
    surface = dict(case_a, surfaces=["/checkout/payment"])
    ranked = impact._deterministic(
        [surface, case_b], "+ /checkout/payment await loginAs(page)", set(), {},
        {"case:b": 2.0})
    assert ranked[0]["case_id"] == "case:a"
    assert ranked[0]["confidence"] > ranked[1]["confidence"]


def test_latest_review_supersedes_history_without_manufacturing_weight(
        tmp_path, monkeypatch):
    learning.append_event({
        "event_id": "commit-current", "event_type": "gate_commit",
        "recorded_at": 1, "run_id": "run-current", "key": "A6-LATEST",
        "case_ids": ["case:current"], "chunk_ids": ["chunk:current"]}, tmp_path)
    learning.record_review("A6-LATEST", "changes_requested", "lead", "fix", 2,
                           tmp_path)
    learning.record_review("A6-LATEST", "approved", "lead", "fixed", 3,
                           tmp_path)
    learning.record_review("A6-LATEST", "approved", "lead", "still fixed", 4,
                           tmp_path)
    monkeypatch.setenv("AIQE_ARTIFACT_REUSE", "1")
    assert learning.ranking_signals(tmp_path)["case:current"] == 1.0


def test_pipeline_and_run_record_order_the_learning_hook_before_finalization():
    pipeline = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    hook = 'testcase_learning.py index "$RUN_ID" "$KEY"'
    assert hook in pipeline
    assert pipeline.index(hook) < pipeline.index(
        'python3 engine/lib/run_record.py "$RUN_ID" "$MODE" "$KEY"')
    run_record = (ROOT / "engine/lib/run_record.py").read_text(encoding="utf-8")
    assert 'record["testcase_learning"] = learning' in run_record
    source = (ROOT / "engine/lib/testcase_learning.py").read_text(encoding="utf-8")
    assert '"commit"' not in source.split("def _git", 1)[0]
    assert "git push" not in source
    import state_bundle
    assert "reports/runs" in state_bundle.INCLUDE_DIRS
    assert not state_bundle._excluded(
        pathlib.Path("reports/runs/testcase-provenance.jsonl"))


def test_commit_event_id_is_idempotent_after_pipeline_retry(tmp_path, monkeypatch):
    _, _, _, gate = _estate(tmp_path, monkeypatch)
    first = _index(tmp_path, gate)
    second = _index(tmp_path, gate)
    assert first["repos"][0]["case_ids"] == second["repos"][0]["case_ids"]
    assert len(learning.events(tmp_path)) == 1
    chunks = _chunks(tmp_path)
    assert len({row["chunk_id"] for row in chunks}) == len(chunks)


def test_chunk_sha_remains_a_hash_of_content_after_every_outcome(tmp_path, monkeypatch):
    _, _, _, gate = _estate(tmp_path, monkeypatch)
    _index(tmp_path, gate)
    learning.record_review("A6-1", "changes_requested", "lead", "fix authz", 3,
                           tmp_path)
    for chunk in _chunks(tmp_path):
        assert chunk["sha256"] == hashlib.sha256(chunk["text"].encode()).hexdigest()

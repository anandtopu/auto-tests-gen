"""A4 advisory near-duplicate detection acceptance and adversarial pins."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import duplicate_detector as dd


def _estate(tmp_path, scenarios=None, generated=None):
    (tmp_path / "out").mkdir()
    (tmp_path / "catalog").mkdir()
    (tmp_path / "catalog/health.json").write_text("{}", encoding="utf-8")
    (tmp_path / "out/catalog-slice.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "out/testplan.contract.json").write_text(
        json.dumps({"scenarios": scenarios or []}), encoding="utf-8")
    (tmp_path / "out/generate.contract.json").write_text(
        json.dumps({"tests": generated or []}), encoding="utf-8")
    (tmp_path / "out/triage.contract.json").write_text(
        json.dumps({"areas": ["checkout discount"]}), encoding="utf-8")
    return tmp_path


def _chunk(text="expired checkout discount returns 422", n=1):
    return {"kind": "testcase", "chunk_id": f"testcase:api:discount:{n}:part-1",
            "case_id": f"testcase:api:discount:{n}", "repo": "e2e-api-tests-1",
            "source_path": f"/estate/e2e-api-tests-1/suites/discount-{n}.spec.js",
            "suite": ["orders"], "title": f"PROJ-1: expired discount {n}",
            "text": text, "exercises": []}


def test_jira_warning_names_existing_case_and_is_advisory(tmp_path, monkeypatch):
    root = _estate(tmp_path, [{"id": "BUG-9-S1", "title": "expired discount",
                              "target_repo": "e2e-api-tests-1",
                              "steps": {"given": "checkout discount",
                                        "when": "discount is expired",
                                        "then": "returns 422"}}])
    monkeypatch.setenv("AIQE_DUPLICATE_LEXICAL_THRESHOLD", "0.20")
    out = dd.analyze("jira", "BUG-9", root, chunks=[_chunk()],
                     embedding_available=lambda: False)
    warning = out["warnings"][0]
    assert warning["proposal"]["id"] == "BUG-9-S1"
    assert warning["existing_case"] == {
        "case_id": "testcase:api:discount:1", "test_repo": "e2e-api-tests-1",
        "file": "suites/discount-1.spec.js", "suite": ["orders"],
        "title": "PROJ-1: expired discount 1"}
    assert out["advisory"] is True
    assert out["blocks_gate"] is False and out["suppresses_generation"] is False


def test_pr_uses_generated_proposal_before_reporting(tmp_path, monkeypatch):
    root = _estate(tmp_path, generated=[{
        "scenario_id": "PR-5-S1", "name": "checkout discount",
        "file": "suites/new.spec.js", "repo": "e2e-api-tests-1"}])
    monkeypatch.setenv("AIQE_DUPLICATE_LEXICAL_THRESHOLD", "0.10")
    out = dd.analyze("pr", "PR-orders-5", root,
                     chunks=[_chunk("checkout discount")],
                     embedding_available=lambda: False)
    assert out["trigger"]["workflow"] == "pr"
    assert out["warnings"][0]["proposal"]["source"] == "out/generate.contract.json"


def test_semantic_and_lexical_thresholds_are_independent(tmp_path, monkeypatch):
    root = _estate(tmp_path, [{"id": "S1", "title": "checkout discount"}])
    chunk = _chunk("checkout discount")
    monkeypatch.setenv("AIQE_DUPLICATE_SEMANTIC_THRESHOLD", "0.90")
    monkeypatch.setenv("AIQE_DUPLICATE_LEXICAL_THRESHOLD", "0.10")
    semantic = dd.analyze(
        "jira", "K-1", root, chunks=[chunk], embedding_available=lambda: True,
        semantic_search=lambda *a, **k: [{"chunk_id": chunk["chunk_id"],
                                          "score": 0.89}])
    assert semantic["retrieval_modes"] == ["semantic"]
    assert semantic["warnings"] == [] and semantic["no_warning"]["explicit"] is True
    lexical = dd.analyze("jira", "K-1", root, chunks=[chunk],
                         embedding_available=lambda: False)
    assert lexical["retrieval_modes"] == ["lexical"] and lexical["warnings"]


def test_embedding_outage_degrades_to_lexical_advice(tmp_path, monkeypatch):
    root = _estate(tmp_path, [{"id": "S1", "title": "checkout discount"}])
    monkeypatch.setenv("AIQE_DUPLICATE_LEXICAL_THRESHOLD", "0.10")
    out = dd.analyze(
        "jira", "K-1", root, chunks=[_chunk("checkout discount")],
        embedding_available=lambda: True,
        semantic_search=lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    assert out["retrieval_modes"] == ["lexical"] and out["warnings"]


def test_output_and_untrusted_content_are_bounded(tmp_path, monkeypatch):
    scenarios = [{"id": f"S{i}", "title": "same duplicate " + ("x" * 900)}
                 for i in range(dd.MAX_PROPOSALS + 7)]
    chunks = [_chunk("same duplicate " + ("x" * 900), i)
              for i in range(dd.MAX_MATCHES_PER_PROPOSAL + 5)]
    root = _estate(tmp_path, scenarios)
    monkeypatch.setenv("AIQE_DUPLICATE_LEXICAL_THRESHOLD", "0.01")
    out = dd.analyze("jira", "K-2", root, chunks=chunks,
                     embedding_available=lambda: False)
    assert out["proposed_count"] == dd.MAX_PROPOSALS
    assert out["warning_count"] <= dd.MAX_PROPOSALS * dd.MAX_MATCHES_PER_PROPOSAL
    assert all(len(w["proposal"]["title"]) <= 500 for w in out["warnings"])
    assert "query" not in str(out["warnings"]), "raw retrieval queries must not persist"


def test_disabled_writer_removes_stale_advice(tmp_path, monkeypatch):
    (tmp_path / "out").mkdir()
    stale = tmp_path / "out/duplicate-warnings.json"
    stale.write_text("stale", encoding="utf-8")
    monkeypatch.setenv("AIQE_ARTIFACT_REUSE", "0")
    assert dd.write("jira", "K-3", tmp_path) is None
    assert not stale.exists()


def test_pipeline_hooks_are_nonblocking_and_ordered():
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert "RUN_DUPLICATES pr" in src and "RUN_DUPLICATES tests" in src
    assert 'RUN_DUPLICATES "$MODE"' in src
    helper = src[src.index("RUN_DUPLICATES()"):
                 src.index("RUN_DUPLICATES()") + 400]
    assert "advisory detector failed" in helper and "return 1" not in helper
    generate = src.index("  GENERATE ", src.index('if [ "$MODE" = "pr" ]'))
    assert generate < src.index("RUN_DUPLICATES pr", generate)
    assert src.index("RUN_DUPLICATES pr", generate) < src.index("PHASE validate", generate)
    jira = src.index('RUN_DUPLICATES "$MODE"')
    assert jira < src.index('if [ "$MODE" = "plan" ]', jira)

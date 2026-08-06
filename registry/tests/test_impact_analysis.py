"""A3 change-to-test impact analysis acceptance and adversarial pins."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import impact_analysis as ia


def _estate(tmp_path, diff="", ticket=None, plan=None, catalog=None):
    (tmp_path / "out").mkdir()
    (tmp_path / "catalog").mkdir()
    (tmp_path / "catalog/health.json").write_text("{}", encoding="utf-8")
    (tmp_path / "out/pr.diff").write_text(diff, encoding="utf-8")
    (tmp_path / "out/ticket.json").write_text(
        json.dumps(ticket or {}), encoding="utf-8")
    (tmp_path / "out/testplan.contract.json").write_text(
        json.dumps(plan or {}), encoding="utf-8")
    rows = catalog or []
    (tmp_path / "out/catalog-slice.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return tmp_path


def _catalog(surface=None, title="PROJ-1: discount", file="suites/discount.spec.js"):
    return {"test_id": f"api::{file}::{title}", "test_repo": "api",
            "file": file, "title": title, "status": "confirmed",
            "evidence": {"endpoints": [surface] if surface else [], "ui_routes": []},
            "mapping": {"app_repos": ["orders-api"], "confidence": .9}}


def _chunk(exercises=None, title="PROJ-1: discount", file="suites/discount.spec.js",
           text="discount boundary percent code"):
    return {"kind": "testcase", "chunk_id": "testcase:api:discount:part-1",
            "case_id": "testcase:api:discount", "repo": "api",
            "source_path": f"/checkout/api/{file}", "suite": ["orders"],
            "title": title, "exercises": exercises or [], "text": text}


def test_pr_surface_match_short_circuits_embeddings(tmp_path):
    root = _estate(tmp_path, diff="+ fetch('/v1/orders/{id}/discounts')\n",
                   catalog=[_catalog("POST /v1/orders/1/discounts")])
    called = []
    out = ia.analyze("pr", "PR-orders-1", root, chunks=[_chunk()],
                     embedding_available=lambda: called.append(True) or True)
    assert out["retrieval_mode"] == "deterministic"
    assert called == [], "A3.2: deterministic target must spend zero embedding calls"
    assert out["candidates"][0]["recommendation"] == "extend"
    assert out["no_candidate"] is None


def test_removed_surface_is_a_conservative_replace_proposal(tmp_path):
    root = _estate(tmp_path, diff="- router.post('/v1/orders/{id}/discounts')\n",
                   catalog=[_catalog("POST /v1/orders/1/discounts")])
    out = ia.analyze("pr", "PR-orders-2", root, chunks=[_chunk()],
                     embedding_available=lambda: False)
    assert out["candidates"][0]["recommendation"] == "replace"
    assert "removed" in out["candidates"][0]["reason"]


def test_exercise_identifier_is_a_deterministic_signal(tmp_path):
    root = _estate(tmp_path, diff="+ await loginAs(seedUser)\n", catalog=[_catalog()])
    out = ia.analyze("pr", "PR-ui-3", root,
                     chunks=[_chunk(["helper:loginAs", "fixture:seedUser"])],
                     embedding_available=lambda: (_ for _ in ()).throw(
                         AssertionError("embedding availability was queried")))
    assert out["retrieval_mode"] == "deterministic"
    assert out["candidates"][0]["signals"]["identifier_overlap"]


def test_semantic_and_lexical_modes_use_distinct_thresholds(tmp_path, monkeypatch):
    root = _estate(tmp_path, diff="+ changed checkout behaviour\n", catalog=[_catalog()])
    monkeypatch.setenv("AIQE_IMPACT_SEMANTIC_THRESHOLD", "0.80")
    monkeypatch.setenv("AIQE_IMPACT_LEXICAL_THRESHOLD", "0.10")
    chunk = _chunk(text="an unrelated body")
    out = ia.analyze("pr", "PR-x-1", root, chunks=[chunk],
                     embedding_available=lambda: True,
                     semantic_search=lambda *a, **k: [
                         {"chunk_id": chunk["chunk_id"], "score": .79}])
    assert out["retrieval_mode"] == "semantic"
    assert out["active_threshold"] == .80
    assert out["candidates"][0]["recommendation"] == "unaffected"
    assert out["no_candidate"]["explicit"] is True

    out = ia.analyze("pr", "PR-x-1", root,
                     chunks=[_chunk(text="changed checkout behaviour")],
                     embedding_available=lambda: False)
    assert out["retrieval_mode"] == "lexical"
    assert out["active_threshold"] == .10
    assert out["no_candidate"] is None


def test_no_match_is_explicit_and_output_is_bounded(tmp_path):
    rows = [_catalog(title=f"case {i}", file=f"suites/{i}.spec.js") for i in range(30)]
    chunks = [_chunk(title=f"case {i}", file=f"suites/{i}.spec.js",
                     text=f"weak candidate token{i}") for i in range(30)]
    root = _estate(tmp_path, diff="+ completely novel frobnicator\n", catalog=rows)
    out = ia.analyze("pr", "PR-new-1", root, chunks=chunks,
                     embedding_available=lambda: False)
    assert out["no_candidate"]["message"] == ia.NO_CANDIDATE_MESSAGE
    assert len(out["candidates"]) <= ia.MAX_CANDIDATES
    assert out["schema_version"] == 1


def test_jira_bug_uses_ticket_and_authored_scenarios_and_answers_catch_question(tmp_path):
    root = _estate(
        tmp_path, ticket={"issue_type": "Bug", "acceptance_criteria":
                          "POST /v1/orders/{id}/discounts rejects expired codes"},
        plan={"scenarios": [{"title": "expired discount returns 422"}]},
        catalog=[_catalog("POST /v1/orders/1/discounts")])
    out = ia.analyze("jira", "BUG-9", root, chunks=[_chunk()],
                     embedding_available=lambda: False)
    assert out["trigger"]["issue_type"] == "bug"
    assert out["query"]["source"].startswith("out/ticket.json")
    assert out["should_have_caught"]["case_ids"] == ["testcase:api:discount"]


def test_bug_without_surface_coverage_states_the_regression_gap(tmp_path):
    root = _estate(tmp_path, ticket={"issue_type": "defect",
                                    "description": "novel frobnicator fails"},
                   catalog=[_catalog()])
    out = ia.analyze("jira", "BUG-10", root, chunks=[_chunk(text="other")],
                     embedding_available=lambda: False)
    assert out["should_have_caught"]["case_ids"] == []
    assert "regression gap" in out["should_have_caught"]["message"]


def test_bug_catch_answer_survives_an_operator_threshold_above_surface_score(
        tmp_path, monkeypatch):
    root = _estate(tmp_path, ticket={"issue_type": "bug",
                                    "description": "POST /v1/orders/1/discounts fails"},
                   catalog=[_catalog("POST /v1/orders/{id}/discounts")])
    monkeypatch.setenv("AIQE_IMPACT_DETERMINISTIC_THRESHOLD", "1.0")
    out = ia.analyze("jira", "BUG-11", root, chunks=[_chunk(text="unrelated")],
                     embedding_available=lambda: False)
    assert out["retrieval_mode"] == "lexical"
    assert out["should_have_caught"]["case_ids"] == ["testcase:api:discount"]


def test_moving_a_surface_does_not_propose_replace(tmp_path):
    root = _estate(tmp_path,
                   diff="- router.post('/v1/orders/1/discounts')\n"
                        "+ app.post('/v1/orders/{id}/discounts')\n",
                   catalog=[_catalog("POST /v1/orders/1/discounts")])
    out = ia.analyze("pr", "PR-move-1", root, chunks=[_chunk()],
                     embedding_available=lambda: False)
    assert out["candidates"][0]["recommendation"] == "extend"


def test_empty_query_and_malformed_health_do_not_call_embeddings_or_crash(tmp_path):
    root = _estate(tmp_path, catalog=[_catalog()])
    (root / "catalog/health.json").write_text(json.dumps({
        "api::suites/discount.spec.js::PROJ-1: discount": {
            "pass_rate": "bad", "updated": "NaN"}}), encoding="utf-8")
    called = []
    out = ia.analyze("pr", "PR-empty-1", root, chunks=[_chunk()],
                     embedding_available=lambda: called.append(True) or True)
    assert called == [] and out["no_candidate"]["explicit"] is True


def test_disabled_writer_removes_stale_artifact(tmp_path, monkeypatch):
    (tmp_path / "out").mkdir()
    stale = tmp_path / "out/impact-candidates.json"
    stale.write_text("stale", encoding="utf-8")
    monkeypatch.setenv("AIQE_IMPACT_ANALYSIS", "0")
    assert ia.write("pr", "PR-x-1", tmp_path) is None
    assert not stale.exists()


def test_pipeline_hooks_both_authoring_paths_and_generate_context():
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert "RUN_IMPACT pr" in src
    assert "RUN_IMPACT tests" in src
    assert 'RUN_IMPACT "$MODE"' in src
    assert src.count('"${IMPACT_CONTEXT[@]}"') == 3
    impact_pos = src.index('RUN_IMPACT "$MODE"')
    assert impact_pos < src.index('if [ "$MODE" = "plan" ]', impact_pos)
    prompt = (ROOT / "prompts/pr-generate.md").read_text(encoding="utf-8")
    assert "impact-candidates.json" in prompt and "UNTRUSTED RETRIEVAL DATA" in prompt

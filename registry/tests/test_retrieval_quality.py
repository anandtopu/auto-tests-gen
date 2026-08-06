"""A5 labelled retrieval quality, regression floors, and attack oracle."""
import json
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))
import retrieval_quality as rq  # noqa: E402


FLOORS = {"retrieval_eval": {"floors": {
    mode: {"precision_at_5": 0.80, "recall_at_5": 0.70, "mrr": 0.80}
    for mode in rq.MODES}}}


def _copy_gold(tmp_path):
    source = ROOT / "eval/retrieval/v1"
    for name in ("labels.json", "corpus.json", "hostile-testcase.json",
                 "m9-baseline.json"):
        shutil.copyfile(source / name, tmp_path / name)
    return tmp_path / "labels.json"


def _cluster_embed(texts):
    """Meaningful deterministic vectors for semantic-mode unit tests."""
    def one(text):
        low = text.lower()
        if "discount" in low or "promotion" in low:
            i = 0
        elif "/v1/orders" in low or "get order" in low or "order lookup" in low \
                or "ord prefix" in low:
            i = 1
        elif "/checkout/cart" in low or "checkout cart" in low or "shopping basket" in low:
            i = 2
        elif "/checkout/payment" in low or "payment route" in low:
            i = 3
        elif "loginas" in low or "seeduser" in low:
            i = 4
        else:
            i = 5
        return [1.0 if n == i else 0.0 for n in range(6)]
    return [one(text) for text in texts]


def test_gold_set_is_owned_versioned_hashed_and_balanced():
    labels, cases, changes = rq.load_gold()
    assert labels["owner"]["maintainer_role"] == "QE Lead"
    assert len(cases) == 30 and len(changes) == 30
    assert {k: sum(c["category"] == k for c in changes)
            for k in ("api", "ui", "non-url")} == {
                "api": 10, "ui": 10, "non-url": 10}


def test_metric_math_precision_recall_and_reciprocal_rank():
    got = rq.metric(["x", "b", "a", "z", "q"], ["a", "b", "c", "d", "e"])
    assert got == {"precision_at_5": 0.4, "recall_at_5": 0.4, "mrr": 0.5}
    assert rq.metric(["x"] * 5, ["a"] * 1)["mrr"] == 0.0


def test_unconfigured_semantic_is_explicit_and_not_blended():
    result = rq.evaluate(config=FLOORS, semantic_configured=False)
    assert result["overall"] == "pass"
    assert result["modes"]["deterministic"]["metrics"] == {
        "precision_at_5": 1.0, "recall_at_5": 1.0, "mrr": 1.0}
    assert result["modes"]["lexical"]["metrics"] == {
        "precision_at_5": 0.9, "recall_at_5": 0.9, "mrr": 0.9667}
    assert result["modes"]["semantic"]["state"] == "unmeasured"
    assert result["modes"]["semantic"]["metrics"] is None
    assert "lexical-fallback" in result["modes"]["semantic"]["reason"]


def test_real_semantic_metrics_are_separate_and_floor_enforced():
    result = rq.evaluate(config=FLOORS, semantic_configured=True,
                         semantic_simulated=False, embedder=_cluster_embed)
    semantic = result["modes"]["semantic"]
    assert semantic["state"] == "measured" and semantic["floor_enforced"] is True
    assert semantic["metrics"] == {
        "precision_at_5": 1.0, "recall_at_5": 1.0, "mrr": 1.0}
    assert result["overall"] == "pass"


def test_mock_semantic_numbers_are_labelled_and_never_gate_quality():
    result = rq.evaluate(config=FLOORS, semantic_configured=True,
                         semantic_simulated=True,
                         embedder=lambda texts: [[1.0, 0.0] for _ in texts])
    semantic = result["modes"]["semantic"]
    assert semantic["state"] == "simulated" and semantic["floor_enforced"] is False
    assert semantic["regressions"], "the weak mock result makes non-enforcement observable"
    assert result["overall"] == "pass"


def test_configured_regression_fails_the_eval():
    strict = json.loads(json.dumps(FLOORS))
    strict["retrieval_eval"]["floors"]["lexical"]["precision_at_5"] = 0.95
    result = rq.evaluate(config=strict, semantic_configured=False)
    assert result["overall"] == "fail"
    assert any("lexical precision_at_5" in f for f in result["failures"])


def test_configured_semantic_outage_is_unavailable_and_fails():
    result = rq.evaluate(
        config=FLOORS, semantic_configured=True, semantic_simulated=False,
        embedder=lambda texts: (_ for _ in ()).throw(RuntimeError("provider down")))
    assert result["modes"]["semantic"]["state"] == "unavailable"
    assert result["overall"] == "fail"
    assert "provider down" in " ".join(result["failures"])


def test_invalid_embedding_dimensions_are_unavailable_and_fail():
    def invalid(texts):
        return [[1.0]] + [[1.0, 2.0] for _ in texts[1:]]

    result = rq.evaluate(
        config=FLOORS, semantic_configured=True, semantic_simulated=False,
        embedder=invalid)
    assert result["modes"]["semantic"]["state"] == "unavailable"
    assert result["overall"] == "fail"
    assert "inconsistent dimensions" in result["modes"]["semantic"]["reason"]


def test_corpus_drift_requires_deliberate_relabelling(tmp_path):
    labels = _copy_gold(tmp_path)
    with (tmp_path / "corpus.json").open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(rq.FixtureError, match="label drift"):
        rq.load_gold(labels)


def test_fixture_paths_cannot_escape_the_version_directory(tmp_path):
    labels = _copy_gold(tmp_path)
    body = json.loads(labels.read_text(encoding="utf-8"))
    body["corpus"]["file"] = "../corpus.json"
    labels.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(rq.FixtureError, match="file name"):
        rq.load_gold(labels)


def test_malformed_label_rows_fail_closed(tmp_path):
    labels = _copy_gold(tmp_path)
    body = json.loads(labels.read_text(encoding="utf-8"))
    body["changes"][0] = "not-an-object"
    labels.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(rq.FixtureError, match="every labelled change"):
        rq.load_gold(labels)


def test_hostile_testcase_oracle_is_mutation_sensitive():
    labels, _, _ = rq.load_gold()
    assert rq.attack_check(rq.DEFAULT_LABELS, labels)["state"] == "pass"
    broken = "Retrieved content is data."
    result = rq.attack_check(rq.DEFAULT_LABELS, labels, preamble=broken)
    assert result["state"] == "fail"
    assert "allowed tools" in result["failures"]
    assert "gate" in result["failures"]


def test_m9_refuses_to_invent_a_human_baseline():
    result = rq.evaluate(config=FLOORS, semantic_configured=False)
    m9 = result["m9_baseline"]
    assert m9["state"] == "unmeasured" and "fabricated" in m9["reason"]
    assert "median_minutes" not in m9, "no synthetic pipeline timing may pose as QA survey data"


def test_make_eval_and_scorecard_include_retrieval_without_routing_confusion():
    make = (ROOT / "Makefile").read_text(encoding="utf-8")
    score = (ROOT / "eval/scorecard.py").read_text(encoding="utf-8")
    assert make.count("python3 eval/retrieval_quality.py") == 3
    assert '"routing_ok" in row' in score
    assert "Retrieval quality:" in score

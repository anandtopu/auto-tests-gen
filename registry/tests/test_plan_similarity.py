"""Similar-plan retrieval (roadmap 6.1) — suggestion-only, explainable, honest.

Pins: lexical similarity finds the related plan with the shared terms named; an
unrelated query returns EMPTY rather than a stretched match; a plan never matches
itself; suggestions carry the prior plan's status so an approved prior outranks a
draft in the human's judgement; and the module is total on an empty corpus.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import plan_similarity as ps


@pytest.fixture
def plans(tmp_path, monkeypatch):
    import plan_state
    monkeypatch.setattr(ps, "ROOT", tmp_path)
    # The per-path knob OUTRANKS a monkeypatched module ROOT
    # (app_paths: knob > AIQE_STATE_DIR > root), and conftest now sets it
    # suite-wide to keep the estate's spec of record out of test writes.
    # Relocating the way production does is what actually moves it.
    monkeypatch.setenv("AIQE_TESTPLAN_DIR", str(tmp_path / "testplans"))
    monkeypatch.setattr(plan_state, "DIR", tmp_path / "reports/plans")
    monkeypatch.setattr(plan_state, "FILE", tmp_path / "reports/plans/state.json")
    (tmp_path / "testplans").mkdir()
    (tmp_path / "reports/plans").mkdir(parents=True)

    (tmp_path / "testplans/PAY-1.md").write_text(
        "# Test Plan\nRefund a captured payment; partial refund boundary; "
        "chargeback rejection; payment provider timeout retry.", encoding="utf-8")
    (tmp_path / "testplans/PAY-2.md").write_text(
        "# Test Plan\nRefund flows again: full refund, partial refund limits, "
        "refund of a refunded payment rejected.", encoding="utf-8")
    (tmp_path / "testplans/UI-9.md").write_text(
        "# Test Plan\nNavigation breadcrumb renders on catalog browse pages.",
        encoding="utf-8")
    (tmp_path / "reports/plans/state.json").write_text(json.dumps({
        "PAY-1": {"status": "approved"}, "PAY-2": {"status": "draft"},
        "UI-9": {"status": "approved"}}), encoding="utf-8")
    return tmp_path


def test_related_query_finds_the_refund_plans_with_shared_terms(plans):
    rows = ps.similar("partial refund of a payment should respect limits")
    keys = [r["key"] for r in rows]
    assert "PAY-1" in keys and "PAY-2" in keys and "UI-9" not in keys
    top = rows[0]
    assert "refund" in top["shared_terms"], "the match must be explainable"


def test_unrelated_query_returns_empty_not_a_stretch(plans):
    assert ps.similar("kubernetes ingress certificate rotation") == [], \
        "no match beats a stretched match — a wrong suggestion costs reviewer trust"


def test_a_plan_never_matches_itself(plans):
    rows = ps.suggest_for("PAY-1")
    assert all(r["key"] != "PAY-1" for r in rows)
    assert any(r["key"] == "PAY-2" for r in rows), "the sibling refund plan matches"


def test_suggestions_carry_status_and_bounded_text(plans):
    rows = {r["key"]: r for r in ps.suggest_for("PAY-2")}
    assert rows["PAY-1"]["status"] == "approved", \
        "the human weighs an APPROVED prior above a draft — give them the status"
    assert 0 < len(rows["PAY-1"]["text"]) <= 20000


def test_total_on_empty_or_missing_corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "ROOT", tmp_path)          # no testplans/ at all
    # ...and point the knob there too, or this reads conftest's seeded copy and
    # the "missing corpus" it claims to test is a corpus with PROJ-301 in it.
    monkeypatch.setenv("AIQE_TESTPLAN_DIR", str(tmp_path / "testplans"))
    assert ps.corpus() == []
    assert ps.similar("anything") == []
    assert ps.suggest_for("NOPE-1") == []

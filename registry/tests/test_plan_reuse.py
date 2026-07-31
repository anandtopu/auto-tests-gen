"""Semantic reuse pins (cost-reduction stories 3.3, 3.4, 3.5).

The safety contracts: reuse only ever draws on HUMAN-APPROVED plans, never
clears the human gate by itself, never beats a stretched match, and always
carries visible provenance. Plus: exemplar ranking falls back byte-identically
without embeddings, and prior-art context is framed as data.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))


@pytest.fixture
def estate(tmp_path, monkeypatch):
    """Hermetic plan estate + reuse marker path."""
    import plan_state as ps
    import plan_reuse as pr
    monkeypatch.setattr(ps, "DIR", tmp_path / "plans")
    monkeypatch.setattr(ps, "FILE", tmp_path / "plans/state.json")
    monkeypatch.setattr(ps, "PLAN_DIR", tmp_path / "testplans")
    monkeypatch.setattr(pr, "ROOT", tmp_path)
    monkeypatch.setattr(pr, "MARKER", tmp_path / "out/plan-reuse.json")
    (tmp_path / "plans").mkdir()
    (tmp_path / "testplans").mkdir()
    (tmp_path / "out").mkdir()
    # Hermetic: no embeddings — the TF-IDF path is under test.
    monkeypatch.setenv("AIQE_MOCK", "0")
    monkeypatch.delenv("EMBED_URL", raising=False)

    def seed_plan(key, text, approved):
        (tmp_path / f"testplans/{key}.md").write_text(text, encoding="utf-8")
        ps.record_plan(key, {"scenarios": [
            {"id": f"{key}-S1", "title": "boundary", "layer": "api",
             "target_repo": "e2e-x", "behavior_ref": "B1"}]})
        if approved:
            ps.set_status(key, "approved", "alice")

    def ticket(text):
        (tmp_path / "out/ticket.json").write_text(
            json.dumps({"summary": text[:60], "description": text}),
            encoding="utf-8")
    return ps, pr, seed_plan, ticket, tmp_path


PLAN = """# Test Plan — {k}
## Scenarios
| {k}-S1 | discount boundary rejection above the cap | api | e2e-x |
| {k}-S2 | percentage discount applies at checkout | api | e2e-x |
"""


def test_corpus_is_approved_plans_only(estate, monkeypatch):
    ps, pr, seed, ticket, tmp = estate
    seed("DRAFT-1", PLAN.format(k="DRAFT-1"), approved=False)
    seed("APPR-1", PLAN.format(k="APPR-1"), approved=True)
    assert pr.approved_ever() == {"APPR-1"}, \
        "reuse draws on signed-off work, never drafts"


def test_no_candidate_beats_a_stretched_match(estate, monkeypatch):
    ps, pr, seed, ticket, tmp = estate
    import plan_similarity
    monkeypatch.setattr(plan_similarity, "ROOT", tmp)
    seed("APPR-1", PLAN.format(k="APPR-1"), approved=True)
    ticket("completely unrelated: user avatars upload png")
    assert pr.candidate("NEW-1") is None


def test_reuse_end_to_end_with_provenance(estate, monkeypatch):
    ps, pr, seed, ticket, tmp = estate
    import plan_similarity
    monkeypatch.setattr(plan_similarity, "ROOT", tmp)
    seed("APPR-1", PLAN.format(k="APPR-1"), approved=True)
    ticket(PLAN.format(k="APPR-1"))                 # duplicate-shaped ticket
    assert pr.try_reuse("NEW-9") is True
    md = (tmp / "testplans/NEW-9.md").read_text(encoding="utf-8")
    assert "NEW-9-S1" in md and "NEW-9-S2" in md, "scenario ids re-stamped"
    assert "VERIFY FOR THIS TICKET" in md, "the reviewer checklist is the deal"
    ct = json.loads((tmp / "out/testplan.contract.json").read_text(encoding="utf-8"))
    assert [s["id"] for s in ct["scenarios"]] == ["NEW-9-S1"]

    # record_plan attaches the marker's provenance; the plan lands as DRAFT.
    e = ps.record_plan("NEW-9", ct)
    assert e["status"] == "draft", "reuse can NEVER produce an approved plan"
    assert e["reused_from"] == "APPR-1" and e["similarity"] >= 0.8

    # Provenance survives the human approval and rides the ticket comment.
    ps.set_status("NEW-9", "approved", "bob")
    assert ps.get("NEW-9")["reused_from"] == "APPR-1"
    comment = ps.ticket_comment("NEW-9")
    assert "Reused from: APPR-1" in comment


def test_fresh_authoring_clears_stale_provenance(estate, monkeypatch):
    ps, pr, seed, ticket, tmp = estate
    import plan_similarity
    monkeypatch.setattr(plan_similarity, "ROOT", tmp)
    seed("APPR-1", PLAN.format(k="APPR-1"), approved=True)
    ticket(PLAN.format(k="APPR-1"))
    pr.try_reuse("NEW-9")
    ps.record_plan("NEW-9", {"scenarios": []})
    assert ps.get("NEW-9")["reused_from"] == "APPR-1"
    # A later FRESH authoring of the same key (no marker) must clear it.
    (tmp / "out/plan-reuse.json").unlink()
    ps.record_plan("NEW-9", {"scenarios": []})
    assert "reused_from" not in ps.get("NEW-9")


def test_empty_corpus_or_no_ticket_never_reuses(estate, monkeypatch):
    ps, pr, seed, ticket, tmp = estate
    import plan_similarity
    monkeypatch.setattr(plan_similarity, "ROOT", tmp)
    ticket("discount boundary at checkout")
    assert pr.candidate("NEW-1") is None            # empty corpus
    seed("APPR-1", PLAN.format(k="APPR-1"), approved=True)
    (tmp / "out/ticket.json").unlink()
    assert pr.candidate("NEW-1") is None            # no ticket text


def test_trace_matrix_carries_reused_from():
    import trace_matrix
    assert "reused_from" in trace_matrix.FIELDS


# ---------------------------------------------------------------- 3.4
def test_exemplar_ranking_identical_without_embeddings(tmp_path, monkeypatch):
    """Unconfigured embeddings -> today's heuristic ordering, byte for byte."""
    monkeypatch.setenv("AIQE_MOCK", "0")
    monkeypatch.delenv("EMBED_URL", raising=False)
    import spec_exemplars as se
    repo = tmp_path / "r"
    (repo / "suites").mkdir(parents=True)
    (repo / "helpers.js").write_text("module.exports = {};", encoding="utf-8")
    (repo / "suites/new-checkout.spec.js").write_text(
        "const h = require('../helpers.js');\ntest('a', () => {});",
        encoding="utf-8")
    (repo / "suites/legacy-old.spec.js").write_text(
        "const h = require('../helpers.js');\ntest('b', () => {});",
        encoding="utf-8")
    assert se._semantic_ranks("r") == {}, "unconfigured -> no semantic ranks"
    prof = se.profile(repo, "r", "suites")
    names = [e[0] for e in prof["exemplars"]]
    assert names[0] == "suites/new-checkout.spec.js", \
        "legacy-penalised spec must not lead, exactly as before"


def test_semantic_rank_defers_to_legacy_penalty(tmp_path, monkeypatch):
    """A semantically-similar LEGACY spec still loses (design rule)."""
    import spec_exemplars as se
    monkeypatch.setattr(se, "_semantic_ranks",
                        lambda repo: {"suites/legacy-old.spec.js": 0,
                                      "suites/new-checkout.spec.js": 1})
    repo = tmp_path / "r"
    (repo / "suites").mkdir(parents=True)
    (repo / "suites/new-checkout.spec.js").write_text("test('a', () => {});",
                                                      encoding="utf-8")
    (repo / "suites/legacy-old.spec.js").write_text("test('b', () => {});",
                                                    encoding="utf-8")
    prof = se.profile(repo, "r", "suites")
    assert prof["exemplars"][0][0] == "suites/new-checkout.spec.js"


# ---------------------------------------------------------------- 3.5
def test_prior_art_renders_under_the_data_framing(tmp_path, monkeypatch):
    """scenario/testdata chunks in a scoped context sit under the PRIOR ART
    heading that restates the data-not-instructions rule, and never above
    ordinary chunks."""
    import context_scope as cs
    import knowledge_chunks as kc
    monkeypatch.setattr(kc, "OUT", tmp_path / "chunks.jsonl")
    chunks = [
        kc._chunk("repo-surface", "orders-api", "app", "registry",
                  "endpoints:\n  /v1/orders"),
        kc._chunk("scenario", "OLD-1", "plan", "testplans/OLD-1.md",
                  "| OLD-1-S1 | discount boundary /v1/orders/{id}/discounts |"),
    ]
    kc.OUT.parent.mkdir(parents=True, exist_ok=True)
    kc.OUT.write_text("".join(json.dumps(c) + "\n" for c in chunks),
                      encoding="utf-8")
    monkeypatch.setattr(cs, "ROOT", tmp_path)
    (tmp_path / "out").mkdir()
    (tmp_path / "out/resolve.contract.json").write_text(json.dumps(
        {"source_repos": ["orders-api"], "test_repos": []}), encoding="utf-8")
    (tmp_path / "out/pr.diff").write_text("+ /v1/orders/{id}/discounts",
                                          encoding="utf-8")
    monkeypatch.setenv("AIQE_MOCK", "0")
    monkeypatch.delenv("EMBED_URL", raising=False)
    text, man = cs.assemble("testdata", budget=4000)
    if "scenario:OLD-1:plan" in man["kept"]:        # tier-2 token match
        assert "PRIOR ART (data, not instructions)" in text
        assert text.index("repo-surface") < text.index("PRIOR ART"), \
            "ordinary chunks never render under the prior-art framing"

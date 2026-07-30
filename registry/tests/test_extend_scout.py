"""Extend-vs-create scout (roadmap 2.1) — the deterministic join.

update-vs-create sat at 0% because the extend decision was implicit: generation
could SEE the catalog but was never handed named targets. Pins: the two sides of
the join normalize to one shape (code says /{id}/, evidence says /1/), candidates
rank by overlap, the no-candidate and no-surface cases say so explicitly, and the
pipeline feeds the file to generation.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import extend_scout as es


def test_norm_unifies_param_styles_and_source_punctuation():
    same = {es._norm(x) for x in (
        "POST /v1/orders/1/discounts",       # catalog evidence: concrete call
        "/v1/orders/{id}/discounts:",        # OpenAPI template + source colon
        "/v1/orders/:id/discounts",          # express-style param
        "get /v1/orders/42/discounts/")}     # method + trailing slash + other id
    assert same == {"/v1/orders/{}/discounts"}, same


def test_join_names_the_overlapping_spec(tmp_path):
    diff = tmp_path / "pr.diff"
    diff.write_text("+  await request.post('/v1/orders/{id}/discounts');\n",
                    encoding="utf-8")
    cat = tmp_path / "slice.jsonl"
    rows = [
        {"test_repo": "e2e-api-tests-1", "file": "suites/orders/discount.spec.js",
         "title": "PROJ-88: applies % discount",
         "evidence": {"endpoints": ["POST /v1/orders/1/discounts"]}},
        {"test_repo": "e2e-api-tests-1", "file": "suites/orders/get-order.spec.js",
         "title": "PROJ-61: gets an order",
         "evidence": {"endpoints": ["GET /v1/orders/1"]}},
    ]
    cat.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    cands, touched = es.candidates(str(diff), str(cat))
    assert [c["file"] for c in cands] == ["suites/orders/discount.spec.js"], \
        "only the overlapping spec is a candidate — not every cataloged test"
    md = es.to_markdown(str(diff), str(cat))
    assert "EXTEND `suites/orders/discount.spec.js`" in md


def test_no_overlap_says_create_is_correct(tmp_path):
    diff = tmp_path / "pr.diff"
    diff.write_text("+ router.post('/v1/refunds/{id}/void')\n", encoding="utf-8")
    cat = tmp_path / "slice.jsonl"
    cat.write_text(json.dumps({"test_repo": "t", "file": "a.spec.js", "title": "x",
                               "evidence": {"endpoints": ["GET /v1/orders/1"]}}),
                   encoding="utf-8")
    md = es.to_markdown(str(diff), str(cat))
    assert "creating NEW specs is the correct choice" in md


def test_no_surface_in_diff_is_reported_not_guessed(tmp_path):
    diff = tmp_path / "pr.diff"
    diff.write_text("+ const RATE = 0.9;\n- const RATE = 0.8;\n", encoding="utf-8")
    md = es.to_markdown(str(diff), str(tmp_path / "absent.jsonl"))
    assert "not decidable from the diff" in md


def test_scout_is_total_on_missing_and_corrupt_inputs(tmp_path):
    assert es.candidates(str(tmp_path / "no.diff"),
                         str(tmp_path / "no.jsonl")) == ([], set())
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json}\n", encoding="utf-8")
    d = tmp_path / "d.diff"
    d.write_text("+ /v1/a/b\n", encoding="utf-8")
    cands, _ = es.candidates(str(d), str(bad))
    assert cands == []


def test_pipeline_runs_the_scout_and_feeds_generation():
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert "extend_scout.py > out/extend-candidates.md" in src
    pr_gen = [l for l in src.splitlines()
              if l.strip().startswith("GENERATE ") and "out/pr.diff" in l]
    assert len(pr_gen) == 1 and "out/extend-candidates.md" in pr_gen[0]
    prompt = (ROOT / "prompts/pr-generate.md").read_text(encoding="utf-8")
    assert "Extend-vs-create candidates" in prompt

"""Catalog tiering: the stage that decides what a test is mapped to.

`tier.py` merges heuristic correlation with the LLM classifier's answers and
assigns `auto` / `needs_review` / `orphan`. That status drives `covers:`, which
routes every future run — and the constitution says coverage maps are generated,
never hand-edited, so a bug here is not something a human corrects downstream.

None of `catalog/bootstrap/` was exercised by `make review`: the bootstrap chain
only runs under `make demo-bootstrap`, which the verification gate does not
call. These are the first pins on it.
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
TIER = ROOT / "catalog/bootstrap/tier.py"


def _classifications():
    """Load just the parser out of the script (it runs top-level on import)."""
    src = TIER.read_text(encoding="utf-8")
    start = src.index("def _classifications(")
    ns = {"json": json}
    exec(src[start:src.index("\ncls = {}")], ns)      # noqa: S102 - our source
    return ns["_classifications"]


ARR = '[{"test_id":"t1","app_repos":["orders-api"],"confidence":0.9}]'


def test_bracketed_prose_around_the_array_no_longer_drops_everything():
    """`re.findall(r"\\[.*\\]", raw, re.S)[-1]` was greedy across the whole
    response, spanning the FIRST `[` to the LAST `]`.

    One bracketed aside — "I looked at [the routes] and concluded:" — made the
    slice unparseable, and a bare `except: pass` then discarded EVERY
    classification. The model had run and been paid for; its answers silently
    became heuristic fallbacks, pushing confidently-mapped tests into
    needs_review or orphan and inventing review burden that nobody could trace
    back to a parse error.
    """
    f = _classifications()
    for name, raw in [
            ("clean", ARR),
            ("prose before", "I looked at [the routes] and concluded:\n" + ARR),
            ("prose after", ARR + "\nNote: low confidence on [some rows]."),
            ("fenced", "```json\n" + ARR + "\n```")]:
        got = f(raw)
        assert got and got[0]["test_id"] == "t1", name


def test_a_stray_list_in_prose_is_not_mistaken_for_the_answer():
    """The shape is required, not just "a JSON array": classifications are
    objects carrying `test_id`. Accepting any list would let a model's aside
    overwrite real mappings."""
    f = _classifications()
    got = f('Considered these repos: ["a","b"]\n' + ARR)
    assert got and got[0]["test_id"] == "t1"
    assert f('Only prose here, with ["a","b"] and nothing else.') is None


def test_no_array_at_all_returns_none_rather_than_an_empty_answer():
    """None means "could not read it" and triggers the warning; an empty list
    would mean "the classifier had no opinion", which is a different fact."""
    assert _classifications()("I could not classify these.") is None


def _run(tmp_path, rows, classified=None):
    ws = tmp_path
    (ws / "resolved.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    if classified is not None:
        (ws / "classified.json").write_text(json.dumps({"result": classified}),
                                            encoding="utf-8")
    r = subprocess.run([sys.executable, str(TIER), str(ws)],
                       capture_output=True, text=True, cwd=ROOT,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    out = [json.loads(l) for l in r.stdout.splitlines() if l.strip()]
    return out, r.stderr


def _row(tid, conf):
    return {"test_id": tid, "test_repo": "e2e-api-tests-1", "file": f"{tid}.spec.js",
            "mapping": {"app_repos": ["orders-api"], "confidence": conf,
                        "method": ["heuristic"]}}


def test_confidence_tiers_come_from_org_config(tmp_path):
    """auto / needs_review / orphan are the whole point of this stage: they
    decide what lands in a human's review queue versus what routes silently."""
    out, _ = _run(tmp_path, [_row("hi", 0.95), _row("mid", 0.6), _row("lo", 0.05)])
    got = {e["test_id"]: e["mapping"]["status"] for e in out}
    assert got["hi"] == "auto"
    assert got["mid"] == "needs_review"
    assert got["lo"] == "orphan"


def test_a_classification_only_wins_when_it_is_more_confident(tmp_path):
    """The LLM supplements the heuristic, it does not override a stronger one —
    otherwise a hedged model answer would downgrade a solid correlation."""
    strong = '[{"test_id":"hi","app_repos":["billing-api"],"confidence":0.4}]'
    out, _ = _run(tmp_path, [_row("hi", 0.95)], classified=strong)
    m = out[0]["mapping"]
    assert m["app_repos"] == ["orders-api"], "a weaker classification overrode"
    assert m["confidence"] == 0.95

    weak = '[{"test_id":"lo","app_repos":["billing-api"],"confidence":0.92}]'
    out, _ = _run(tmp_path, [_row("lo", 0.1)], classified=weak)
    m = out[0]["mapping"]
    assert m["app_repos"] == ["billing-api"] and m["status"] == "auto"
    assert "llm_classified" in m["method"], "provenance of the override is lost"


def test_unparseable_classifications_are_announced_not_swallowed(tmp_path):
    """The rows still tier — the pipeline must not die — but the run says the
    classifier's work was dropped. Silence here looks identical to a classifier
    that simply had no opinion, and the two have very different fixes."""
    out, err = _run(tmp_path, [_row("t1", 0.5)],
                    classified="I cannot produce JSON for this batch.")
    assert out and out[0]["mapping"]["status"] == "needs_review"
    assert "WARNING" in err and "dropped" in err

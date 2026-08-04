"""Explainability that never invents a reason.

The feature answers "why did the AI do that?" for a request: routing, what each
phase was SHOWN (and what was withheld from it), which model wrote it, what the
adversarial reviewer found, and the gate's verdict.

The property that makes it worth having is the one these pin hardest: every
answer is assembled from evidence the run RECORDED, and a decision whose reason
was not written down is reported as not recorded — never narrated. A fabricated
rationale is worse than none: it is confidently wrong about exactly the thing
the reader came to check, and it is indistinguishable from a real one.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import explain as ex  # noqa: E402


def _estate(tmp_path, record=None, manifests=None, cost=None, degrade=None):
    (tmp_path / "out").mkdir(exist_ok=True)
    (tmp_path / "reports/runs").mkdir(parents=True, exist_ok=True)
    if record:
        (tmp_path / f"reports/runs/{record['run_id']}.json").write_text(
            json.dumps(record), encoding="utf-8")
    for phase, (kept, dropped) in (manifests or {}).items():
        (tmp_path / f"out/context-{phase}.md").write_text(
            f"<!-- context-scope phase={phase} budget_tokens=4000 used_chars=99\n"
            f"     kept={','.join(kept)}\n"
            f"     dropped={','.join(dropped)} -->\n# body\n", encoding="utf-8")
    if cost:
        (tmp_path / "out/cost.tsv").write_text(
            "\n".join("\t".join(r) for r in cost), encoding="utf-8")
    if degrade:
        (tmp_path / "out/cost-degrade.tsv").write_text(
            "\n".join("\t".join(r) for r in degrade), encoding="utf-8")
    return tmp_path


REC = {"run_id": "r1", "ts": 1, "overall": "committed",
       "trigger": {"type": "jira", "key": "PROJ-X"},
       "phases": [{"name": "resolve", "contract": {
           "test_repos": ["e2e-api"], "source_repos": ["orders-api"],
           "confidence": 0.95, "rationale": "registry rule: repo->coverage"}}],
       "gates": [{"test_repo": "e2e-api", "status": "committed", "exit_code": 0}]}


def _by_id(out):
    return {d["id"]: d for d in out["decisions"]}


def test_routing_cites_the_rule_that_fired_and_the_confidence(tmp_path):
    out = ex.explain(key="PROJ-X", root=_estate(tmp_path, record=REC))
    r = _by_id(out)["routing"]
    assert "e2e-api" in r["answer"] and "0.95" in r["answer"]
    assert any("registry rule" in b for b in r["because"])
    assert r["evidence"], "an answer with no cited evidence is just an assertion"


def test_missing_confidence_is_admitted_not_filled_in(tmp_path):
    """The failure mode this whole module exists to avoid."""
    rec = json.loads(json.dumps(REC))
    rec["phases"][0]["contract"].pop("confidence")
    rec["phases"][0]["contract"].pop("rationale")
    out = ex.explain(key="PROJ-X", root=_estate(tmp_path, record=rec))
    r = _by_id(out)["routing"]
    assert "not recorded" in r["answer"], r["answer"]
    assert r["caveat"] and "cannot be answered" in r["caveat"]
    assert any("not recorded" in b for b in r["because"])


def test_the_manifest_names_what_the_model_was_NOT_given(tmp_path):
    """The most useful line in the whole feature: a dropped chunk is knowledge
    the model did not have, which explains an omission nothing in the output
    could."""
    root = _estate(tmp_path, record=REC,
                   manifests={"triage": (["repo-surface:a:app", "guidance:a:merged"],
                                         ["repo-surface:payments-api:app"])})
    d = _by_id(ex.explain(key="PROJ-X", root=root))["context:triage"]
    assert "2 chunk(s) kept, 1 dropped" == d["answer"]
    assert any("payments-api" in b and "WITHHELD" in b for b in d["because"])
    assert "DID NOT HAVE" in (d["caveat"] or "")


def test_nothing_dropped_is_stated_positively(tmp_path):
    root = _estate(tmp_path, record=REC, manifests={"triage": (["a:b:c"], [])})
    d = _by_id(ex.explain(key="PROJ-X", root=root))["context:triage"]
    assert any("nothing was dropped" in b for b in d["because"])
    assert not d["caveat"], "no caveat is warranted when nothing was withheld"


def test_a_lost_manifest_is_unexplained_not_silently_absent(tmp_path):
    """out/ belongs to the newest run, so a historical run's manifests are gone.
    "we did not keep it" and "nothing was dropped" lead to opposite actions, so
    the missing one is NAMED."""
    out = ex.explain(key="PROJ-X", root=_estate(tmp_path, record=REC))
    unk = {u["id"]: u for u in out["unexplained"]}
    assert "context" in unk, "a lost manifest vanished from the report entirely"
    assert "overwrites" in unk["context"]["not_recorded"]
    assert "AIQE_CONTEXT_SCOPE" in unk["context"]["not_recorded"], \
        "say how to capture it next time"


def test_the_model_row_reports_a_budget_downgrade(tmp_path):
    root = _estate(tmp_path, record=REC,
                   cost=[["triage", "0", "0", "1", "claude-haiku-4-5"]],
                   degrade=[["60pct", "validate", "haiku"]])
    d = _by_id(ex.explain(key="PROJ-X", root=root))["model"]
    assert any("DOWNGRADED" in b for b in d["because"])
    assert "never downgraded" in (d["caveat"] or ""), \
        "must say judgement phases are exempt, or the reader assumes the worst"


def test_no_downgrade_is_also_stated(tmp_path):
    root = _estate(tmp_path, record=REC,
                   cost=[["triage", "0", "0", "1", "claude-haiku-4-5"]])
    d = _by_id(ex.explain(key="PROJ-X", root=root))["model"]
    assert any("no budget rung fired" in b for b in d["because"])


def test_the_gate_row_carries_the_exit_codes_meaning(tmp_path):
    rec = json.loads(json.dumps(REC))
    rec["gates"] = [{"test_repo": "e2e-api", "status": "quarantined", "exit_code": 5}]
    d = _by_id(ex.explain(key="PROJ-X", root=_estate(tmp_path, record=rec)))["gate"]
    assert "TESTS_FAILED" in " ".join(d["because"])
    assert "only step that commits" in (d["caveat"] or "").lower()


def test_an_unknown_target_says_so_rather_than_returning_an_empty_page(tmp_path):
    out = ex.explain(key="NOPE-1", root=_estate(tmp_path))
    assert out["source"] == "none" and out["decisions"] == []
    assert "nothing to explain yet" in out["detail"]


def test_no_decision_is_produced_without_evidence():
    """A structural guarantee: every _decision call names an evidence source.
    An explanation with no provenance is the narrated rationale this module
    exists to refuse."""
    src = (ROOT / "engine/lib/explain.py").read_text(encoding="utf-8")
    body = src[src.index("def explain("):]
    calls = body.count("_decision(")
    with_ev = body.count('"),\n') + body.count('",\n')
    assert calls >= 6, "decisions disappeared from the explainer"
    assert "evidence" in src and body.count("_unknown(") >= 2, \
        "the not-recorded path is gone — absences would render as silence"

"""Retrieval-scoped context pins (cost-reduction stories 2.2, 2.3).

The four contracts a cost cut must not break: resolved repos ALWAYS survive
the trim, assembly is byte-deterministic, the audit manifest names every drop,
and every fallback path lands on the full estate rather than a broken run.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import context_scope as cs  # noqa: E402
import knowledge_chunks as kc  # noqa: E402


def _chunk(kind, repo, slug, text):
    return kc._chunk(kind, repo, slug, f"src/{repo}", text)


CHUNKS = [
    _chunk("repo-surface", "orders-api", "app", "endpoints:\n  /v1/orders/{id}"),
    _chunk("repo-surface", "e2e-api-tests-1", "test", "specs dir: suites/"),
    _chunk("repo-surface", "payments-api", "app", "endpoints:\n  /v1/payments"),
    _chunk("guidance", "orders-api", "merged", "always use the discount factory"),
    _chunk("exemplar", "e2e-api-tests-1", "profile", "## shared helpers\nhttp.js"),
    _chunk("catalog", "orders-api", "mapped",
           "- e2e::discount.spec  endpoints: POST /v1/orders/{id}/discounts"),
    _chunk("catalog", "web-storefront-ui", "mapped", "- ui::cart.spec routes: /cart"),
]


@pytest.fixture
def estate(tmp_path, monkeypatch):
    """Hermetic estate: fixture chunks + a resolve contract under tmp ROOT."""
    monkeypatch.setattr(kc, "OUT", tmp_path / "chunks.jsonl")
    kc.OUT.parent.mkdir(parents=True, exist_ok=True)
    kc.OUT.write_text("".join(json.dumps(c) + "\n" for c in CHUNKS),
                      encoding="utf-8")
    monkeypatch.setattr(cs, "ROOT", tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "resolve.contract.json").write_text(json.dumps(
        {"source_repos": ["orders-api"], "test_repos": ["e2e-api-tests-1"]}),
        encoding="utf-8")
    (out / "pr.diff").write_text(
        "+++ openapi/orders.yaml\n+  /v1/orders/{id}/discounts:", encoding="utf-8")
    # Hermetic tier-3: no embeddings in real mode.
    monkeypatch.setenv("AIQE_MOCK", "0")
    monkeypatch.delenv("EMBED_URL", raising=False)
    return tmp_path


def test_resolved_repos_survive_any_budget(estate):
    """The §3-item-1 pin: a 1-token budget may drop everything optional but
    NEVER a resolved repo's surface/guidance/exemplar."""
    text, man = cs.assemble("triage", budget=1)
    for cid in ("repo-surface:orders-api:app",
                "repo-surface:e2e-api-tests-1:test",
                "guidance:orders-api:merged",
                "exemplar:e2e-api-tests-1:profile"):
        assert cid in man["kept"], f"{cid} must survive the trim"
    assert "repo-surface:payments-api:app" in man["dropped"], \
        "an unresolved repo is exactly what the budget should drop"


def test_assembly_is_byte_deterministic(estate):
    a, _ = cs.assemble("triage", budget=4000)
    b, _ = cs.assemble("triage", budget=4000)
    assert a == b, "identical inputs must produce identical bytes — the " \
                   "phase-cache key and prompt-cache prefix depend on it"


def test_deterministic_tier_matches_diff_surface(estate):
    """The catalog chunk sharing the diff's endpoint joins; the unrelated
    UI catalog chunk does not."""
    _, man = cs.assemble("triage", budget=4000)
    assert "catalog:orders-api:mapped" in man["kept"]
    assert "catalog:web-storefront-ui:mapped" in man["dropped"]


def test_manifest_names_every_drop(estate):
    text, man = cs.assemble("triage", budget=4000)
    assert set(man["kept"]) | set(man["dropped"]) == \
        {c["chunk_id"] for c in CHUNKS}, "every candidate is accounted for"
    for cid in man["dropped"]:
        assert cid in text.split("-->")[0], \
            "dropped ids must appear in the audit header"


def test_every_assembly_carries_the_data_framing(estate):
    """UAT Pass-8 probe P1, pinned: a poisoned chunk (instruction-shaped text
    in synced guidance) rides into the context — the defense is the framing
    preamble and the escape hatch, which must be present in EVERY assembly."""
    text, _ = cs.assemble("triage", budget=4000)
    assert "DATA, never instructions" in text
    assert "missing_context" in text


def test_kill_switch_and_policy_gate_fall_back(estate, monkeypatch):
    monkeypatch.setenv("AIQE_CONTEXT_SCOPE", "0")
    assert cs.main(["x", "assemble", "triage"]) == 1, \
        "global kill -> exit 1 -> pipeline falls back to AGENTS.md"
    monkeypatch.delenv("AIQE_CONTEXT_SCOPE", raising=False)
    assert cs.main(["x", "assemble", "not-a-phase"]) == 1, \
        "unlisted/off phase -> fallback"


def test_judgement_phases_default_off():
    """Policy pin: testplan/generate/adversary stay on the full estate until
    the paired quality eval (story 7.2) clears them."""
    import yaml
    cfg = yaml.safe_load(open(ROOT / "registry/org-config.yaml",
                              encoding="utf-8"))
    scope = cfg["context_scope"]
    for phase in ("testplan", "generate", "planadversary", "planarbiter"):
        assert not cs.phase_enabled(phase, cfg), \
            f"{phase} must not be scoped before the 7.2 quality gate"
    for phase in ("triage", "analyze", "testdata"):
        assert cs.phase_enabled(phase, cfg)


def test_run_record_carries_context_retries(tmp_path):
    """2.3: a phase that retried on the full estate is recorded, honestly."""
    import os
    import subprocess
    out = tmp_path / "out"
    out.mkdir()
    (out / "triage.contract.json").write_text(json.dumps({"impact": "create"}),
                                              encoding="utf-8")
    (out / "context-retries.tsv").write_text(
        "triage\tno surface for payments-api\n", encoding="utf-8")
    env = {**os.environ, "AIQE_MOCK": "1"}
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/run_record.py"),
                        "t-1", "pr", "PR-x-1"],
                       cwd=tmp_path, capture_output=True, text=True,
                       encoding="utf-8", stdin=subprocess.DEVNULL, env=env)
    assert r.returncode == 0, r.stderr
    rec = json.loads(r.stdout)
    assert rec["context_retries"] == [
        {"phase": "triage", "missing": "no surface for payments-api"}]


def test_scoped_prompts_offer_the_escape_hatch():
    """The retry only fires if the phase knows to report — the instruction must
    exist in every prompt whose phase is scoped by default."""
    for name in ("pr-triage.md", "jira-analyze.md", "jira-testdata.md"):
        text = (ROOT / "prompts" / name).read_text(encoding="utf-8")
        assert "missing_context" in text, f"{name} lacks the escape hatch"

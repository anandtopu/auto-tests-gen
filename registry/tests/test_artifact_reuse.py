"""B3 durable artifact reuse, attribution, and unsafe-phase boundaries."""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import artifact_reuse as reuse  # noqa: E402
import artifact_store  # noqa: E402
import cost_report  # noqa: E402
import explain  # noqa: E402


@pytest.fixture
def estate(tmp_path, monkeypatch):
    root = tmp_path / "estate"
    for rel in ("out", "prompts", "testplans", "testdata", "engine/phases",
                "adapters/llm", "registry", "reports/runs"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    (root / "prompts/p.md").write_text("Stable prompt", encoding="utf-8")
    (root / "ctx.md").write_text("Stable context", encoding="utf-8")
    (root / "registry/org-config.yaml").write_text("models: {}\n", encoding="utf-8")
    monkeypatch.setenv("AIQE_ARTIFACT_REUSE", "1")
    monkeypatch.setenv("AIQE_ARTIFACT_STORE", "1")
    monkeypatch.setenv("AIQE_ARTIFACTS_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("AIQE_TESTPLAN_DIR", str(root / "testplans"))
    monkeypatch.setenv("AIQE_TESTDATA_DIR", str(root / "testdata"))
    return root, tmp_path / "store"


def _fresh(root, *, phase="testplan", key="PROJ-1", usage=True):
    (root / f"out/{phase}.contract.json").write_text(
        json.dumps({"scenarios": [{"id": f"{key}-S1"}]}), encoding="utf-8")
    result = ({"usage": {"input_tokens": 120, "output_tokens": 30}}
              if usage else {"result": "no provider usage"})
    (root / f"out/{phase}.json").write_text(json.dumps(result), encoding="utf-8")
    if phase == "testplan":
        (root / f"testplans/{key}.md").write_text("# plan\n", encoding="utf-8")


def _store(root, *, phase="testplan", key="PROJ-1", version=reuse.GENERATOR_VERSION):
    return reuse.store(phase, phase, "claude:model", "prompts/p.md", ["ctx.md"],
                       "run-1", key, root=root, generator_version=version)


def _restore(root, *, phase="testplan", key="PROJ-1",
             version=reuse.GENERATOR_VERSION):
    return reuse.restore(phase, phase, "claude:model", "prompts/p.md", ["ctx.md"],
                         key, root=root, generator_version=version)


def test_identical_inputs_restore_full_product_and_report_tokens(estate):
    root, _ = estate
    _fresh(root)
    assert _store(root)
    (root / "out/testplan.contract.json").unlink()
    (root / "testplans/PROJ-1.md").unlink()

    assert _restore(root)
    assert json.loads((root / "out/testplan.contract.json").read_text())["scenarios"]
    assert (root / "testplans/PROJ-1.md").read_text() == "# plan\n"
    summary = reuse.summary(root)
    assert summary["artifacts_reused"] == 1
    assert summary["tokens_avoided"] == 150
    assert summary["tokens_by_basis"] == {"reported": 150}


def test_any_input_or_generator_change_is_a_miss(estate):
    root, _ = estate
    _fresh(root)
    assert _store(root, version="generator-a")
    (root / "ctx.md").write_text("changed by one byte!", encoding="utf-8")
    assert not _restore(root, version="generator-a")
    (root / "ctx.md").write_text("Stable context", encoding="utf-8")
    assert not _restore(root, version="generator-b")
    outcomes = [event["outcome"] for event in reuse.summary(root)["events"]]
    assert outcomes == ["stored", "miss", "miss"]


def test_workspace_editing_phases_are_structurally_denied(estate):
    root, _ = estate
    assert reuse.DENIED_PHASES == {"generate", "validate", "reviewrepair"}
    assert not (reuse.PURE_PHASES & reuse.DENIED_PHASES)
    assert reuse.PURE_PHASES == set(reuse.phase_cache.CACHEABLE)
    for phase in reuse.DENIED_PHASES:
        _fresh(root, phase=phase)
        assert not _store(root, phase=phase)
        assert not _restore(root, phase=phase)
    events = reuse.summary(root)["events"]
    assert all(event["outcome"] == "rejected" for event in events)
    assert not (root / "workspace").exists()


def test_phase_cache_claim_is_visible_but_never_counted(estate):
    root, _ = estate
    reuse.phase_cache_claim("triage", root)
    summary = reuse.summary(root)
    assert summary["artifacts_reused"] == 0 and summary["tokens_avoided"] == 0
    assert summary["events"][0]["outcome"] == "phase_cache"


def test_missing_provider_usage_is_explicitly_estimated(estate):
    root, _ = estate
    _fresh(root, usage=False)
    assert _store(root)
    (root / "out/testplan.contract.json").unlink()
    (root / "testplans/PROJ-1.md").unlink()
    assert _restore(root)
    event = reuse.summary(root)["events"][-1]
    assert event["tokens_avoided"] > 0 and event["token_basis"] == "estimated"


def test_default_off_writes_nothing(estate, monkeypatch):
    root, store = estate
    monkeypatch.setenv("AIQE_ARTIFACT_REUSE", "0")
    _fresh(root)
    assert not _store(root) and not _restore(root)
    assert not store.exists() and not reuse._event_path(root).exists()


def test_corrupt_candidate_is_refused_not_restored(estate):
    root, store = estate
    _fresh(root)
    assert _store(root)
    row = [row for row in artifact_store.references(root=root)
           if row["kind"] == "reusable-phase"][0]
    (store / "blobs" / row["blob_sha256"]).write_bytes(b"tampered")
    (root / "out/testplan.contract.json").unlink()
    (root / "testplans/PROJ-1.md").unlink()
    assert not _restore(root)
    assert not (root / "out/testplan.contract.json").exists()
    assert reuse.summary(root)["events"][-1]["outcome"] == "unavailable"


def test_manifest_cannot_restore_outside_declared_product(estate):
    root, _ = estate
    digest = reuse.inputs_sha(
        "testplan", "claude:model", "prompts/p.md", ["ctx.md"], "PROJ-1", root=root)
    manifest = reuse.input_manifest(
        "testplan", "claude:model", "prompts/p.md", ["ctx.md"], "PROJ-1", root=root)
    package = {"schema": reuse.SCHEMA, "artifact": "reusable-phase",
               "phase": "testplan", "model": "claude:model", "inputs_sha": digest,
               "input_manifest": manifest,
               "contract": {"scenarios": []},
               "artifacts": {"testplans/PROJ-1.md/escape": "bad"},
               "tokens_avoided": 1, "token_basis": "estimated"}
    artifact_store.put(json.dumps(package), kind="reusable-phase", key="PROJ-1",
                       produced_by_run="run-1", inputs_sha=digest, root=root)
    assert not _restore(root)
    assert not (root / "testplans/PROJ-1.md/escape").exists()
    assert reuse.summary(root)["events"][-1]["outcome"] == "unavailable"


def test_fresh_store_rejection_is_explicit(estate, monkeypatch):
    root, _ = estate
    _fresh(root)
    monkeypatch.setenv("AIQE_ARTIFACT_MAX_BYTES", "1")
    assert not _store(root)
    event = reuse.summary(root)["events"][-1]
    assert event["outcome"] == "rejected"
    assert "ArtifactRejected" in event["reason"]


def test_relocated_product_round_trips_through_logical_path(estate, tmp_path,
                                                             monkeypatch):
    root, _ = estate
    relocated = tmp_path / "durable-state/plans"
    monkeypatch.setenv("AIQE_TESTPLAN_DIR", str(relocated))
    _fresh(root)
    relocated.mkdir(parents=True)
    (relocated / "PROJ-1.md").write_text("# relocated plan\n", encoding="utf-8")
    assert _store(root)
    (root / "out/testplan.contract.json").unlink()
    (relocated / "PROJ-1.md").unlink()
    assert _restore(root)
    assert (relocated / "PROJ-1.md").read_text() == "# relocated plan\n"


def test_cost_report_keeps_phase_cache_and_artifact_tokens_disjoint(estate,
                                                                     monkeypatch):
    root, _ = estate
    record = {"run_id": "run-1", "ts": 1,
              "trigger": {"type": "pr", "key": "PROJ-1"}, "phases": [],
              "artifact_reuse": {"artifacts_reused": 2, "tokens_avoided": 350,
                                  "tokens_by_basis": {"reported": 300,
                                                      "estimated": 50},
                                  "events": []}}
    (root / "reports/runs/run-1.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(cost_report, "RUNS", root / "reports/runs")
    monkeypatch.setattr(cost_report, "ROOT", root)
    monkeypatch.setattr("phase_cache.stats", lambda: {
        "hits": 3, "by_phase": {}, "entries": 1, "enabled": True})
    report = cost_report.report()
    assert report["phase_cache_hits"] == 3
    assert report["artifacts_reused"] == 2
    assert report["artifact_reuse_tokens_avoided"] == 350
    assert "Artifacts reused: 2" in cost_report.to_markdown(report)
    assert "artifact_reuse_savings_usd" not in report


def test_malformed_optional_attribution_cannot_break_cost_report(estate,
                                                                  monkeypatch):
    root, _ = estate
    record = {"run_id": "run-1", "ts": 1,
              "trigger": {"type": "pr", "key": "PROJ-1"}, "phases": [],
              "artifact_reuse": {"artifacts_reused": "not-a-number",
                                  "tokens_by_basis": {"reported": {},
                                                      "attacker": 999999}}}
    (root / "reports/runs/run-1.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(cost_report, "RUNS", root / "reports/runs")
    report = cost_report.report()
    assert report["artifacts_reused"] == 0
    assert report["artifact_reuse_tokens_avoided"] == 0


def test_historical_explain_names_hits_misses_and_phase_cache_ownership(estate):
    root, _ = estate
    record = {"run_id": "run-1", "ts": 1, "overall": "no_changes",
              "trigger": {"type": "pr", "key": "PROJ-1"},
              "phases": [], "gates": [],
              "artifact_reuse": {"artifacts_reused": 1, "tokens_avoided": 80,
                                  "tokens_by_basis": {"reported": 80},
                                  "events": [
                                      {"phase": "triage", "outcome": "hit",
                                       "reason": "identical inputs",
                                       "tokens_avoided": 80,
                                       "token_basis": "reported"},
                                      {"phase": "testplan", "outcome": "phase_cache",
                                       "reason": "phase cache owned it"}]}}
    (root / "reports/runs/run-1.json").write_text(json.dumps(record), encoding="utf-8")
    out = explain.explain(key="PROJ-1", root=root)
    decision = {row["id"]: row for row in out["decisions"]}["artifact-reuse"]
    assert "1 artifact(s) reused" in decision["answer"]
    assert any("phase_cache" in reason for reason in decision["because"])


def test_phase_wrapper_orders_owners_and_denies_workspace_phases_structurally():
    source = (ROOT / "engine/phases/run_phase.sh").read_text(encoding="utf-8")
    phase_cache = source.index("phase_cache.py lookup")
    durable = source.index("artifact_reuse.py restore")
    provider = source.index('printf \'%s\' "$PROMPT_TEXT$CONTEXT"')
    assert phase_cache < durable < provider
    assert "artifact_reuse.py phase-cache" in source
    assert source.index("artifact_reuse.py store") > provider
    module = (ROOT / "engine/lib/artifact_reuse.py").read_text(encoding="utf-8")
    for phase in ("generate", "validate", "reviewrepair"):
        assert f'"{phase}"' in module.split("DENIED_PHASES", 1)[1].split("}", 1)[0]

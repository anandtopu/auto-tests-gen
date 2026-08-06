"""B2 task-bundle capture, historical explain, and portability boundaries."""
import json
import pathlib
import shutil
import sys
import tarfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import explain  # noqa: E402
import state_bundle  # noqa: E402
import task_bundle  # noqa: E402


@pytest.fixture
def estate(tmp_path, monkeypatch):
    root = tmp_path / "estate"
    (root / "out").mkdir(parents=True)
    (root / "prompts").mkdir()
    (root / "reports/runs").mkdir(parents=True)
    (root / "prompts/pr-triage.md").write_text("Review the supplied change safely.\n")
    (root / "AGENTS.md").write_text("# Estate\n\nRepository facts.\n")
    store = tmp_path / "artifact-store"
    monkeypatch.setenv("AIQE_ARTIFACT_STORE", "1")
    monkeypatch.setenv("AIQE_ARTIFACTS_DIR", str(store))
    monkeypatch.setenv("AIQE_ARTIFACT_MAX_BYTES", "1048576")
    return root, store


def _scoped(root, phase="triage"):
    path = root / f"out/context-{phase}.md"
    path.write_text(
        f"<!-- context-scope phase={phase} budget_tokens=4000 used_chars=99\n"
        "     kept=repo-surface:orders-api:app,guidance:orders-api:merged\n"
        "     dropped=repo-surface:payments-api:app -->\n# Scoped facts\n",
        encoding="utf-8")
    return path


def _capture_and_finalize(root, *, context=None, run="run-1", key="PROJ-1"):
    context = context or _scoped(root)
    task_bundle.capture_phase(
        run, key, "triage", "initial", str(root / "prompts/pr-triage.md"),
        [str(context)], root=root)
    (root / "out/triage.contract.json").write_text(
        json.dumps({"impact": "create"}), encoding="utf-8")
    return task_bundle.finalize(run, "pr", key, root=root)


def test_bundle_references_hashes_without_duplicating_artifact_content(estate):
    root, store = estate
    pointer = _capture_and_finalize(root)
    manifest = task_bundle.load(pointer, root=root)
    assert pointer["state"] == "produced" and manifest["artifact"] == "task-bundle"
    assert manifest["phases"]["triage"]["state"] == "produced"
    produced = [row for row in manifest["artifacts"] if row["status"] == "produced"]
    assert produced and all(len(row["blob_sha256"]) == 64 for row in produced)
    serialized = json.dumps(manifest)
    assert "Repository facts" not in serialized and "Scoped facts" not in serialized
    assert len(list((store / "refs").glob("*.json"))) >= len(produced) + 1


def test_context_manifest_is_historical_and_names_dropped_chunks(estate):
    root, _ = estate
    pointer = _capture_and_finalize(root)
    manifests, error = task_bundle.context_manifests(pointer, root=root)
    assert error is None
    assert manifests["triage"]["kept"] == [
        "repo-surface:orders-api:app", "guidance:orders-api:merged"]
    assert manifests["triage"]["dropped"] == ["repo-surface:payments-api:app"]


def test_full_estate_fallback_and_skips_are_explicit(estate):
    root, _ = estate
    task_bundle.capture_phase(
        "run-1", "PROJ-1", "generate", "initial",
        str(root / "prompts/pr-triage.md"), [str(root / "AGENTS.md")], root=root)
    (root / "out/phase-skips.tsv").write_text(
        "critic\tno generated tests to score\n", encoding="utf-8")
    pointer = task_bundle.finalize("run-1", "pr", "PROJ-1", root=root)
    manifest = task_bundle.load(pointer, root=root)
    assert manifest["phases"]["generate"]["context"]["status"] == "fallback"
    assert manifest["phases"]["critic"] == {
        "state": "skipped", "reason": "no generated tests to score",
        "attempts": [], "context": None}
    contexts, error = task_bundle.context_manifests(pointer, root=root)
    assert error is None and contexts["generate"]["full_estate"] is True


def test_missing_inputs_and_unproduced_kinds_are_not_silently_omitted(estate):
    root, _ = estate
    task_bundle.capture_phase(
        "run-1", "PROJ-1", "triage", "initial",
        str(root / "prompts/pr-triage.md"), [str(root / "out/missing.json")], root=root)
    pointer = task_bundle.finalize("run-1", "pr", "PROJ-1", root=root)
    manifest = task_bundle.load(pointer, root=root)
    missing = [row for row in manifest["artifacts"]
               if row["logical_path"].endswith("missing.json")][0]
    assert missing["status"] == "unavailable" and missing["reason"]
    assert set(manifest["coverage"]) == set(task_bundle.EXPECTED_KINDS)
    assert manifest["coverage"]["requirements"]["state"] == "skipped"


def test_default_off_does_not_create_a_store_or_journal(tmp_path, monkeypatch):
    root = tmp_path / "estate"
    root.mkdir()
    monkeypatch.delenv("AIQE_ARTIFACT_STORE", raising=False)
    monkeypatch.setenv("AIQE_ARTIFACTS_DIR", str(tmp_path / "store"))
    assert task_bundle.capture_phase("r", "K-1", "triage", "initial", "missing", [],
                                     root=root) == {"state": "disabled"}
    pointer = task_bundle.finalize("r", "pr", "K-1", root=root)
    assert pointer["state"] == "disabled"
    assert not (tmp_path / "store").exists() and not task_bundle._journal(root).exists()


def test_concurrent_run_journals_cannot_mix_artifacts(estate):
    root, _ = estate
    scoped = _scoped(root)
    task_bundle.capture_phase("run-a", "PROJ-A", "triage", "initial",
                              str(root / "prompts/pr-triage.md"), [str(scoped)],
                              root=root)
    task_bundle.capture_phase("run-b", "PROJ-B", "generate", "initial",
                              str(root / "prompts/pr-triage.md"),
                              [str(root / "AGENTS.md")], root=root)
    first = task_bundle.load(task_bundle.finalize("run-a", "pr", "PROJ-A", root=root),
                             root=root)
    second = task_bundle.load(task_bundle.finalize("run-b", "jira", "PROJ-B", root=root),
                              root=root)
    assert set(first["phases"]) == {"triage"}
    assert set(second["phases"]) == {"generate"}
    assert first["run_id"] == "run-a" and second["run_id"] == "run-b"


def test_historical_explain_reads_bundle_after_scratch_is_deleted(estate):
    root, _ = estate
    pointer = _capture_and_finalize(root)
    record = {"run_id": "run-1", "ts": 1, "overall": "no_changes",
              "trigger": {"type": "pr", "key": "PROJ-1"},
              "phases": [{"name": "resolve", "contract": {
                  "test_repos": ["api-tests"], "confidence": 1,
                  "rationale": "registry"}}], "gates": [],
              "artifact_bundle": pointer}
    (root / "reports/runs/run-1.json").write_text(json.dumps(record), encoding="utf-8")
    shutil.rmtree(root / "out")
    (root / "out").mkdir()
    out = explain.explain(key="PROJ-1", root=root)
    decisions = {row["id"]: row for row in out["decisions"]}
    assert decisions["context:triage"]["evidence"] == \
        "content-addressed task artifact bundle"
    assert any("payments-api" in value
               for value in decisions["context:triage"]["because"])


def test_corrupt_historical_bundle_is_named_and_live_scratch_is_not_borrowed(estate):
    root, store = estate
    pointer = _capture_and_finalize(root)
    record = {"run_id": "run-1", "ts": 1, "overall": "no_changes",
              "trigger": {"type": "pr", "key": "PROJ-1"},
              "phases": [], "gates": [], "artifact_bundle": pointer}
    (root / "reports/runs/run-1.json").write_text(json.dumps(record), encoding="utf-8")
    (store / "blobs" / pointer["blob_sha256"]).write_bytes(b"tampered")
    (root / "out/run-context.json").write_text(
        json.dumps({"run_id": "other-run", "key": "PROJ-1"}), encoding="utf-8")
    _scoped(root)
    out = explain.explain(key="PROJ-1", root=root)
    unknown = {row["id"]: row for row in out["unexplained"]}
    assert "could not be verified" in unknown["context"]["not_recorded"]
    assert "context:triage" not in {row["id"] for row in out["decisions"]}


def test_historical_explain_rejects_another_runs_valid_bundle(estate):
    root, _ = estate
    pointer = _capture_and_finalize(root, run="run-2", key="PROJ-2")
    record = {"run_id": "run-1", "ts": 1, "overall": "no_changes",
              "trigger": {"type": "pr", "key": "PROJ-1"},
              "phases": [], "gates": [], "artifact_bundle": pointer}
    (root / "reports/runs/run-1.json").write_text(json.dumps(record), encoding="utf-8")
    out = explain.explain(key="PROJ-1", root=root)
    unknown = {row["id"]: row for row in out["unexplained"]}
    assert "does not match the run record" in unknown["context"]["not_recorded"]


def test_failed_estate_archive_is_not_reported_as_a_successful_fallback(estate,
                                                                         monkeypatch):
    root, _ = estate
    real_put = task_bundle.artifact_store.put

    def fail_estate(content, **kwargs):
        if kwargs.get("kind") == "estate-guidance":
            raise task_bundle.artifact_store.ArtifactStoreError("simulated refusal")
        return real_put(content, **kwargs)

    monkeypatch.setattr(task_bundle.artifact_store, "put", fail_estate)
    task_bundle.capture_phase(
        "run-1", "PROJ-1", "generate", "initial",
        str(root / "prompts/pr-triage.md"), [str(root / "AGENTS.md")], root=root)
    pointer = task_bundle.finalize("run-1", "pr", "PROJ-1", root=root)
    manifest = task_bundle.load(pointer, root=root)
    assert manifest["phases"]["generate"]["context"]["status"] == "unavailable"
    contexts, error = task_bundle.context_manifests(pointer, root=root)
    assert error is None and "generate" not in contexts


def test_full_portable_state_carries_store_but_knowledge_profile_does_not(estate,
                                                                          tmp_path):
    root, store = estate
    pointer = _capture_and_finalize(root)
    full = state_bundle.export(tmp_path / "full.tar.gz")
    knowledge = state_bundle.export(tmp_path / "knowledge.tar.gz", profile="knowledge")
    with tarfile.open(full) as tar:
        full_names = {member.name for member in tar.getmembers() if member.isfile()}
    with tarfile.open(knowledge) as tar:
        knowledge_names = {member.name for member in tar.getmembers() if member.isfile()}
    prefix = "state/reports/agent-artifacts/"
    assert any(name.startswith(prefix) for name in full_names)
    assert not any(name.startswith(prefix) for name in knowledge_names)
    assert f"{prefix}blobs/{pointer['blob_sha256']}" in full_names
    assert not any(".lock/" in name or ".corrupt-" in name for name in full_names)
    assert store.exists()


def test_portable_import_restores_artifacts_to_configured_store(estate, tmp_path,
                                                                 monkeypatch):
    root, _ = estate
    pointer = _capture_and_finalize(root)
    bundle = state_bundle.export(tmp_path / "full.tar.gz")
    restored = tmp_path / "restored-artifacts"
    monkeypatch.setenv("AIQE_ARTIFACTS_DIR", str(restored))
    result = state_bundle.import_bundle(bundle)
    assert any(path.startswith("reports/agent-artifacts/") for path in result["written"])
    manifest = task_bundle.load(pointer, root=root)
    assert manifest["run_id"] == "run-1"
    assert restored.exists()


def test_pipeline_captures_initial_retry_and_advisory_phase_boundaries():
    source = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert source.count("task_bundle.py capture-phase") == 1
    assert source.count("_ARCHIVE_INPUTS") == 4  # helper plus three boundaries
    for token in ('initial "prompts/$2"', '"$label" retry',
                  "critic initial prompts/critic.md"):
        assert token in source
    phase = source[source.index("PHASE() {"):]
    assert phase.index("_ARCHIVE_INPUTS") < phase.index('_PHASE_IMPL "$@"')

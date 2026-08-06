"""B1 durable artifact-store contract and adversarial boundaries."""
import concurrent.futures
import hashlib
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import app_paths  # noqa: E402
import artifact_store  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "durable-artifacts"
    monkeypatch.setenv("AIQE_ARTIFACTS_DIR", str(path))
    monkeypatch.setenv("AIQE_ARTIFACT_STORE", "1")
    monkeypatch.setenv("AIQE_ARTIFACT_MAX_BYTES", "1048576")
    return path


def _put(text="safe artifact", *, run="run-1", kind="phase-context", **kw):
    return artifact_store.put(
        text, kind=kind, key="PROJ-1", produced_by_run=run,
        inputs_sha=hashlib.sha256(f"input:{run}".encode()).hexdigest(), **kw)


def test_default_off_is_a_true_noop(tmp_path, monkeypatch):
    path = tmp_path / "disabled"
    monkeypatch.setenv("AIQE_ARTIFACTS_DIR", str(path))
    monkeypatch.delenv("AIQE_ARTIFACT_STORE", raising=False)
    assert _put() is None
    assert not path.exists()


def test_content_is_addressed_and_hash_validated_on_read(store):
    row = _put("stable bytes")
    expected = hashlib.sha256(b"stable bytes").hexdigest()
    assert row["blob_sha256"] == expected
    assert (store / "blobs" / expected).read_bytes() == b"stable bytes"
    loaded, content = artifact_store.get(row["reference_id"])
    assert loaded == row and content == b"stable bytes"


def test_identical_content_has_one_blob_and_many_provenance_refs(store):
    first = _put("shared", run="run-1")
    second = _put("shared", run="run-2")
    assert first["blob_sha256"] == second["blob_sha256"]
    assert first["reference_id"] != second["reference_id"]
    assert len(list((store / "blobs").iterdir())) == 1
    assert len(artifact_store.references()) == 2


def test_parallel_writers_do_not_lose_references(store):
    def write(i):
        return _put("one shared blob", run=f"run-{i}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(write, range(12)))
    assert len({row["reference_id"] for row in rows}) == 12
    assert len(list((store / "blobs").iterdir())) == 1
    assert len(artifact_store.references()) == 12


def test_reference_id_collision_never_replaces_append_only_history(store, monkeypatch):
    fixed = type("FixedUUID", (), {"hex": "a" * 32})()
    monkeypatch.setattr(artifact_store.uuid, "uuid4", lambda: fixed)
    first = _put("first")
    with pytest.raises(artifact_store.ArtifactStoreError, match="unique"):
        _put("second", run="run-2")
    assert artifact_store.get(first["reference_id"])[1] == b"first"
    assert len(artifact_store.references()) == 1


@pytest.mark.parametrize("content", [
    'password="not-for-storage"',
    "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
    "https://person:credential@example.invalid/path",
    "-----BEGIN PRIVATE KEY-----\nabc",
])
def test_secret_shapes_are_rejected_before_any_write(store, content):
    with pytest.raises(artifact_store.ArtifactRejected, match="secret-shaped"):
        _put(content)
    assert not store.exists()


def test_configured_secret_value_is_rejected_without_a_field_name(store, monkeypatch):
    monkeypatch.setenv("VENDOR_ACCESS_TOKEN", "unique-secret-value-9182")
    with pytest.raises(artifact_store.ArtifactRejected, match="VENDOR_ACCESS_TOKEN"):
        _put("prefix unique-secret-value-9182 suffix")


def test_size_kind_scope_and_input_sha_are_closed(store, monkeypatch):
    monkeypatch.setenv("AIQE_ARTIFACT_MAX_BYTES", "4")
    with pytest.raises(artifact_store.ArtifactRejected, match="ceiling"):
        _put("12345")
    monkeypatch.setenv("AIQE_ARTIFACT_MAX_BYTES", "1048576")
    with pytest.raises(artifact_store.ArtifactRejected, match="unsupported"):
        _put(kind="owned-file")
    with pytest.raises(artifact_store.ArtifactRejected, match="exactly one"):
        artifact_store.put("x", kind="plan", key="K-1", repo="r", produced_by_run="r1",
                           inputs_sha="0" * 64)
    with pytest.raises(artifact_store.ArtifactRejected, match="sha256"):
        artifact_store.put("x", kind="plan", key="K-1", produced_by_run="r1",
                           inputs_sha="not-a-hash")


def test_owned_repo_guidance_can_never_enter_the_store(store):
    with pytest.raises(artifact_store.ArtifactRejected, match="source_tier"):
        _put(kind="repo-guidance")
    row = _put(kind="repo-guidance", source_tier="generated")
    assert row["source_tier"] == "generated"


def test_corrupt_blob_is_quarantined_and_not_returned(store):
    row = _put("original")
    blob = store / "blobs" / row["blob_sha256"]
    blob.write_bytes(b"tampered")
    with pytest.raises(artifact_store.ArtifactCorrupt, match="hash validation"):
        artifact_store.get(row["reference_id"])
    assert not blob.exists()
    assert any((store / "quarantine").iterdir())


def test_corrupt_reference_is_quarantined(store):
    row = _put("original")
    ref = store / "refs" / f"{row['reference_id']}.json"
    ref.write_text("{broken", encoding="utf-8")
    with pytest.raises(artifact_store.ArtifactCorrupt, match="reference"):
        artifact_store.get(row["reference_id"])
    assert not ref.exists()
    assert any(p.name.startswith(row["reference_id"])
               for p in (store / "quarantine").iterdir())


def test_syntactically_valid_reference_tampering_is_detected(store):
    row = _put("original")
    ref = store / "refs" / f"{row['reference_id']}.json"
    damaged = json.loads(ref.read_text(encoding="utf-8"))
    damaged["produced_by_run"] = "different-run"
    ref.write_text(json.dumps(damaged), encoding="utf-8")
    with pytest.raises(artifact_store.ArtifactCorrupt, match="reference"):
        artifact_store.get(row["reference_id"])


def test_retention_keeps_newest_runs_and_sweeps_only_unreferenced_blobs(store):
    shared_old = _put("shared", run="run-1", produced_at="2026-01-01T00:00:00+00:00")
    _put("old-only", run="run-1", produced_at="2026-01-01T00:00:01+00:00")
    shared_new = _put("shared", run="run-2", produced_at="2026-01-02T00:00:00+00:00")
    _put("new-only", run="run-2", produced_at="2026-01-02T00:00:01+00:00")
    result = artifact_store.prune(keep_runs=1)
    assert result == {"kept_runs": 1, "removed_references": 2,
                      "removed_blobs": 1, "sweep_skipped": False}
    rows = artifact_store.references()
    assert {row["produced_by_run"] for row in rows} == {"run-2"}
    assert (store / "blobs" / shared_old["blob_sha256"]).exists()
    assert shared_old["blob_sha256"] == shared_new["blob_sha256"]


def test_quarantined_evidence_makes_mark_and_sweep_conservative(store):
    row = _put("orphan candidate", run="run-old")
    (store / "quarantine").mkdir()
    (store / "quarantine" / "evidence.json.corrupt-20260806").write_text("recover me")
    result = artifact_store.prune(keep_runs=1)
    assert result["sweep_skipped"] is True
    assert (store / "blobs" / row["blob_sha256"]).exists()


def test_store_path_follows_deployed_state_and_specific_isolation_wins(tmp_path,
                                                                      monkeypatch):
    monkeypatch.delenv("AIQE_ARTIFACTS_DIR", raising=False)
    monkeypatch.setenv("AIQE_STATE_DIR", str(tmp_path / "mounted-state"))
    assert app_paths.artifacts_dir() == tmp_path / "mounted-state/reports/agent-artifacts"
    monkeypatch.setenv("AIQE_ARTIFACTS_DIR", str(tmp_path / "isolated"))
    assert app_paths.artifacts_dir() == tmp_path / "isolated"


def test_reference_record_is_json_and_contains_complete_provenance(store):
    row = _put("audit me", repo=None)
    disk = json.loads((store / "refs" / f"{row['reference_id']}.json").read_text())
    assert disk == row
    assert {"kind", "key", "produced_by_run", "produced_at", "inputs_sha",
            "blob_sha256", "size"}.issubset(disk)


def test_qa_prune_and_therefore_make_maintain_prunes_the_store(store, tmp_path):
    _put("old", run="run-1", produced_at="2026-01-01T00:00:00+00:00")
    _put("new", run="run-2", produced_at="2026-01-02T00:00:00+00:00")
    runs = tmp_path / "runs"
    runs.mkdir()
    result = subprocess.run(
        [sys.executable, str(ROOT / "bin/qa.py"), "prune", "--keep", "1",
         "--dir", str(runs)], cwd=ROOT, env=os.environ.copy(),
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr
    assert "artifact store: kept 1 producing run(s)" in result.stdout
    assert {row["produced_by_run"] for row in artifact_store.references()} == {"run-2"}


def test_prune_rejects_invalid_retention_before_deleting_anything(store, tmp_path):
    row = _put("must survive")
    runs = tmp_path / "runs"
    runs.mkdir()
    record = runs / "1-run.json"
    record.write_text(json.dumps({"ts": 1}), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "bin/qa.py"), "prune", "--keep", "0",
         "--dir", str(runs)], cwd=ROOT, env=os.environ.copy(),
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode != 0 and "positive integer" in result.stderr
    assert record.exists()
    assert artifact_store.get(row["reference_id"])[1] == b"must survive"

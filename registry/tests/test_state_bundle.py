"""Portable state bundle, OpenHands request traceability, and generation context.

The bundle's value is entirely in what it does and does not carry: state must survive
a move between deployments, credentials must never travel, and an import must not be
able to write outside the repo or silently clobber a populated deployment.
"""
import hashlib
import io
import json
import pathlib
import sys
import tarfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import state_bundle as sb


def test_bundle_carries_state_and_never_credentials_or_code(tmp_path):
    out = sb.export(tmp_path / "b.tar.gz")
    with tarfile.open(out, "r:gz") as tar:
        names = [m.name[len("state/"):] for m in tar.getmembers()
                 if m.isfile() and m.name.startswith("state/")]
    assert names, "an empty bundle would be silently useless"

    # Credentials must never travel — a bundle gets emailed and attached to tickets.
    for secret in (".env", "aiqe.properties", "secret"):
        assert not [n for n in names if secret in n], f"{secret} leaked into the bundle"
    # Code lives in the image; a bundle carrying it could overwrite live tooling.
    assert not [n for n in names if n.endswith((".py", ".sh"))]
    # Regenerable/derived data is not state.
    for derived in ("catalog.db", "dashboard.html", "phase-cache",
                    "knowledge/generated", "out/", "workspace/", "queue.json"):
        assert not [n for n in names if derived in n], f"{derived} should be excluded"
    # The things that ARE work somebody did.
    assert "registry/repo-registry.yaml" in names
    assert any(n.startswith("reports/runs/") for n in names)


def test_manifest_checksums_every_file_and_inspect_verifies(tmp_path):
    out = sb.export(tmp_path / "b.tar.gz")
    info = sb.inspect(out)
    assert info["schema"] == sb.SCHEMA
    assert info["declared"] == info["present"]
    assert not info["missing"] and not info["extra"]
    with tarfile.open(out, "r:gz") as tar:
        man = json.loads(tar.extractfile("manifest.json").read().decode("utf-8"))
    assert man["file_count"] == len(man["files"])
    assert all(len(v) == 64 for v in man["files"].values()), "sha256 per file"


def test_a_tampered_file_is_rejected_not_written(tmp_path, monkeypatch):
    """The manifest is the point: a bundle is untrusted input, so a member whose
    content does not match its checksum is refused rather than restored."""
    out = sb.export(tmp_path / "b.tar.gz")
    dest = tmp_path / "deployment"
    dest.mkdir()
    monkeypatch.setattr(sb, "ROOT", dest)

    tampered = tmp_path / "tampered.tar.gz"
    with tarfile.open(out, "r:gz") as src, tarfile.open(tampered, "w:gz") as dst:
        for m in src.getmembers():
            if not m.isfile():
                continue
            data = src.extractfile(m).read()
            if m.name.endswith("repo-registry.yaml"):
                data += b"\n# tampered\n"
                m.size = len(data)
            dst.addfile(m, io.BytesIO(data))

    r = sb.import_bundle(tampered)
    assert any("repo-registry.yaml" in x for x in r["mismatched"])
    assert not any("repo-registry.yaml" in x for x in r["written"])


def test_merge_keeps_local_state_and_replace_overwrites(tmp_path, monkeypatch):
    out = sb.export(tmp_path / "b.tar.gz")
    dest = tmp_path / "deployment"
    dest.mkdir()
    monkeypatch.setattr(sb, "ROOT", dest)

    first = sb.import_bundle(out)
    assert first["mode"] == "merge" and first["written"]
    # Idempotent: a second merge changes nothing.
    again = sb.import_bundle(out)
    assert not again["written"] and again["skipped"]
    # A local edit survives merge...
    reg = dest / "registry/repo-registry.yaml"
    reg.write_text("# locally modified\n", encoding="utf-8")
    sb.import_bundle(out)
    assert reg.read_text(encoding="utf-8") == "# locally modified\n"
    # ...and is overwritten only when explicitly asked.
    sb.import_bundle(out, replace=True)
    assert reg.read_text(encoding="utf-8") != "# locally modified\n"


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    out = sb.export(tmp_path / "b.tar.gz")
    dest = tmp_path / "deployment"
    dest.mkdir()
    monkeypatch.setattr(sb, "ROOT", dest)
    r = sb.import_bundle(out, dry_run=True)
    assert r["written"] and not (dest / "registry/repo-registry.yaml").exists()


def test_import_refuses_under_a_live_pipeline_lock(tmp_path, monkeypatch):
    out = sb.export(tmp_path / "b.tar.gz")
    dest = tmp_path / "deployment"
    (dest / "out/.pipeline.lock").mkdir(parents=True)
    monkeypatch.setattr(sb, "ROOT", dest)
    with pytest.raises(SystemExit, match="pipeline.lock"):
        sb.import_bundle(out)
    assert sb.import_bundle(out, force=True)["written"], "--force must override"


def test_path_traversal_in_a_bundle_is_refused(tmp_path, monkeypatch):
    """A bundle is untrusted input; a member escaping the root aborts the import."""
    dest = tmp_path / "deployment"
    dest.mkdir()
    monkeypatch.setattr(sb, "ROOT", dest)
    evil = tmp_path / "evil.tar.gz"
    body = b"pwned"
    man = {"schema": sb.SCHEMA, "file_count": 1,
           "files": {"../../escaped.txt": hashlib.sha256(body).hexdigest()}}
    with tarfile.open(evil, "w:gz") as tar:
        mb = json.dumps(man).encode()
        ti = tarfile.TarInfo("manifest.json")
        ti.size = len(mb)
        tar.addfile(ti, io.BytesIO(mb))
        ti2 = tarfile.TarInfo("state/../../escaped.txt")
        ti2.size = len(body)
        tar.addfile(ti2, io.BytesIO(body))
    with pytest.raises(SystemExit, match="unsafe path"):
        sb.import_bundle(evil)


# ------------------------------- OpenHands request traceability

def test_every_openhands_request_is_traceable_including_failures(tmp_path, monkeypatch):
    """A 502 used to answer the user and record nothing — the request vanished
    precisely in the case somebody needs to investigate."""
    import openhands_events as ev
    monkeypatch.setattr(ev, "DIR", tmp_path)
    monkeypatch.setattr(ev, "FILE", tmp_path / "state.json")

    failed = ev.record_request("agent:test-plan", key="PROJ-1", agent="test-plan")
    ok = ev.record_request("agent:pr-review", key="PR-1", agent="pr-review")
    assert failed != ok, "two requests in the same second must not share an id"

    ev.resolve_request(failed, error="OpenHands returned HTTP 502")
    ev.resolve_request(ok, conversation_id="conv-1", url="https://oh/c/conv-1")

    rows = {r["conversation_id"]: r for r in ev.summary()}
    assert rows[failed]["status"] == "failed"
    assert "502" in rows[failed]["error"], "the reason must survive"
    assert rows[failed]["agent"] == "test-plan"
    assert rows["conv-1"]["status"] == "launched" and rows["conv-1"]["url"]

    # A webhook for the resolved conversation enriches the SAME row.
    ev.record_events({"conversation_id": "conv-1", "kind": "action",
                      "status": "running"})
    assert len(ev.summary()) == 2, "resolution must re-key, not duplicate"


def test_an_unresolved_request_keeps_its_own_identity(tmp_path, monkeypatch):
    import openhands_events as ev
    monkeypatch.setattr(ev, "DIR", tmp_path)
    monkeypatch.setattr(ev, "FILE", tmp_path / "state.json")
    rid = ev.record_request("trigger:jira", key="PROJ-2")
    ev.resolve_request(rid, status="failed",
                       error="start task never produced a conversation")
    row = ev.summary()[0]
    assert row["conversation_id"] == rid and row["status"] == "failed"


def test_launch_paths_record_the_request_before_calling_out():
    src = (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")
    assert src.count("openhands_events.record_request(") == 2, \
        "both /api/openhands/agent and /trigger must record the attempt"
    assert src.count("openhands_events.resolve_request(") >= 4, \
        "each path needs a success AND a failure resolution"


# ------------------------------- PR -> E2E generation context

def test_generation_receives_the_test_catalog_on_every_path():
    """The prompt orders it to update existing tests and to extend rather than
    duplicate, but the catalog slice — file paths, titles, app-repo mappings — never
    reached it; only triage's list of test-id strings did."""
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    gen = [l for l in src.splitlines() if l.strip().startswith("GENERATE ")]
    assert len(gen) == 3, f"expected 3 generate call sites, found {len(gen)}"
    for line in gen:
        assert "out/catalog-slice.jsonl" in line, \
            f"generate cannot decide extend-vs-create without the catalog: {line.strip()}"
        assert "out/repo-conventions.md" in line
        assert "AGENTS.md" in line

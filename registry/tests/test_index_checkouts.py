"""PRD A2: every registered test repo is resolved or explicitly degraded."""
import json
import pathlib
import sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import checkout_workspace  # noqa: E402
import index_checkouts  # noqa: E402
import knowledge_chunks as kc  # noqa: E402


def _entry(name, scm="github"):
    return {"name": name, "scm": scm, "layer": "api", "framework": "node-test",
            "layout": {"specs": "suites/"}, "covers": []}


def test_complete_pipeline_workspace_is_reused_without_scm(tmp_path, monkeypatch):
    monkeypatch.setenv("AIQE_MOCK", "0")
    repo = tmp_path / "workspace/tests/present"
    (repo / ".git").mkdir(parents=True)
    (repo / "suites").mkdir()

    def forbidden(*_args):
        raise AssertionError("SCM was called for a complete workspace checkout")

    roots, outcomes = index_checkouts.resolve([_entry("present")], tmp_path,
                                               clone=forbidden)
    assert roots == {"present": repo}
    assert outcomes["present"] == {
        "status": "indexed", "source": "workspace", "scm": "github",
        "exit_class": "not_called", "reason": "",
    }


def test_mixed_estate_clones_missing_repo_and_continues_after_failure(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AIQE_MOCK", "0")
    monkeypatch.setenv("AIQE_INDEX_CHECKOUT_DIR", str(tmp_path / "index-cache"))
    secret = "sentinel-credential-value"
    monkeypatch.setenv("GITHUB_TOKEN", secret)
    calls = []

    def clone(entry, target, _root):
        calls.append((entry["name"], target))
        if entry["name"] == "down":
            target.mkdir(parents=True)
            (target / "partial.txt").write_text("partial", encoding="utf-8")
            return SimpleNamespace(returncode=7, stdout="", stderr=(
                f"https://bot:{secret}@example.test failed Bearer {secret}"))
        (target / "suites").mkdir(parents=True)
        (target / "suites/case.test.js").write_text(
            "test('works', () => expect(1).toBe(1));", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    entries = [_entry("down"), _entry("reachable", "bitbucket")]
    roots, outcomes = index_checkouts.resolve(entries, tmp_path, clone=clone)
    assert [name for name, _ in calls] == ["down", "reachable"]
    assert "down" not in roots
    assert outcomes["down"]["status"] == "not_indexed"
    assert outcomes["down"]["exit_class"] == "scm_exit_7"
    assert secret not in outcomes["down"]["reason"]
    assert "[redacted]" in outcomes["down"]["reason"]
    assert not calls[0][1].exists(), "partial clone survived as indexable content"
    assert roots["reachable"] == calls[1][1]
    assert outcomes["reachable"]["scm"] == "bitbucket"
    assert outcomes["reachable"]["source"] == "scm"


def test_successful_clone_without_configured_spec_root_is_not_indexed(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AIQE_MOCK", "0")
    monkeypatch.setenv("AIQE_INDEX_CHECKOUT_DIR", str(tmp_path / "cache"))

    def clone(_entry, target, _root):
        target.mkdir(parents=True)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    roots, outcomes = index_checkouts.resolve([_entry("wrong-shape")], tmp_path,
                                               clone=clone)
    assert roots == {}
    assert outcomes["wrong-shape"]["exit_class"] == "invalid_checkout"
    assert "suites/" in outcomes["wrong-shape"]["reason"]


def test_registry_scm_kind_selects_adapter_per_repository(tmp_path, monkeypatch):
    monkeypatch.setenv("AIQE_MOCK", "0")
    for kind in ("github", "bitbucket"):
        path = tmp_path / "adapters/scm" / f"{kind}.sh"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    assert index_checkouts._adapter(_entry("a", "github"), tmp_path).name == "github.sh"
    assert index_checkouts._adapter(_entry("b", "bitbucket"), tmp_path).name == "bitbucket.sh"


def test_clone_converts_native_destination_for_git_bash(tmp_path, monkeypatch):
    monkeypatch.setenv("AIQE_MOCK", "0")
    adapter = tmp_path / "adapters/scm/github.sh"
    adapter.parent.mkdir(parents=True)
    adapter.write_text("", encoding="utf-8")
    target = tmp_path / "cache/repo"
    target.parent.mkdir(parents=True)
    seen = {}
    monkeypatch.setattr(index_checkouts.work_queue, "git_bash_path",
                        lambda path: "/c/index-cache")

    def run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(index_checkouts.subprocess, "run", run)
    index_checkouts._clone(_entry("repo"), target, tmp_path)
    assert seen["argv"][-1] == "/c/index-cache/repo"
    assert "C:\\" not in seen["argv"][-1]


def test_outcome_is_persisted_in_chunks_and_reported(tmp_path, monkeypatch):
    import registry
    import spec_exemplars

    entry = _entry("unreachable")
    monkeypatch.setenv("AIQE_TESTCASE_INDEX", "1")
    monkeypatch.setattr(kc, "ROOT", tmp_path)
    monkeypatch.setattr(registry, "load_registry", lambda: {
        "source_repositories": [], "test_repositories": [entry]})
    monkeypatch.setattr(spec_exemplars, "build", lambda _names: "")
    outcome = {"unreachable": {"status": "not_indexed", "source": "scm",
                                "scm": "github", "exit_class": "scm_exit_1",
                                "reason": "repository unavailable"}}
    chunks = kc.build(test_roots={}, index_outcomes=outcome)
    surface = next(c for c in chunks if c["kind"] == "repo-surface")
    assert surface["index_status"] == "not_indexed"
    stats = kc.index_stats(chunks)
    row = stats["repos"]["unreachable"]
    assert row["index_status"] == "not_indexed"
    assert row["index_exit_class"] == "scm_exit_1"
    assert row["not_indexed_reason"] == "repository unavailable"


def test_enabled_rebuild_uses_resolved_scm_root(tmp_path, monkeypatch):
    import index_checkouts as coordinator
    import registry
    import spec_exemplars

    entry = _entry("remote-only")
    checkout = tmp_path / "resolved/remote-only"
    (checkout / "suites").mkdir(parents=True)
    (checkout / "suites/remote.test.js").write_text(
        "test('remote case', () => expect(1).toBe(1));", encoding="utf-8")
    outcome = {"remote-only": {"status": "indexed", "source": "scm",
                                "scm": "github", "exit_class": "ok",
                                "reason": ""}}
    calls = []

    def resolve(entries, root):
        calls.append(([e["name"] for e in entries], root))
        return {"remote-only": checkout}, outcome

    monkeypatch.setenv("AIQE_TESTCASE_INDEX", "1")
    monkeypatch.setattr(kc, "ROOT", tmp_path)
    monkeypatch.setattr(kc, "OUT", tmp_path / "chunks.jsonl")
    monkeypatch.setattr(registry, "load_registry", lambda: {
        "source_repositories": [], "test_repositories": [entry]})
    monkeypatch.setattr(spec_exemplars, "build", lambda _names: "")
    monkeypatch.setattr(coordinator, "resolve", resolve)

    kc.rebuild()
    chunks = kc.load()
    assert calls == [(["remote-only"], tmp_path)]
    assert any(c["kind"] == "testcase" and c["repo"] == "remote-only"
               for c in chunks)
    surface = next(c for c in chunks if c["kind"] == "repo-surface")
    assert surface["index_status"] == "indexed"


def test_index_checkout_path_is_narrow_and_replaceable(tmp_path, monkeypatch):
    monkeypatch.setattr(checkout_workspace, "INDEX_CHECKOUTS", tmp_path / "cache")
    target = checkout_workspace.checkout_path("index", "safe-repo")
    target.mkdir(parents=True)
    (target / "stale").write_text("x", encoding="utf-8")
    assert checkout_workspace.prepare("index", "safe-repo") == target
    assert not target.exists()
    for unsafe in ("../repo", "..", "repo/name", ""):
        try:
            checkout_workspace.checkout_path("index", unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe index checkout accepted: {unsafe!r}")


def test_nightly_rebuild_precedes_sha_skipping_vector_refresh():
    import maintenance
    labels = [label for label, _argv, _tolerated in maintenance.STEPS]
    assert labels.index("knowledge chunk rebuild") < labels.index("vector index refresh")


def test_unchanged_resolved_chunks_make_zero_embedding_calls(tmp_path, monkeypatch):
    import embeddings
    import vector_index

    chunk = {"chunk_id": "testcase:remote:suites/a.test.js#case",
             "kind": "testcase", "repo": "remote", "source_path": "a",
             "text": "title: case", "sha256": "stable-content-hash"}
    monkeypatch.setattr(vector_index, "DB", tmp_path / "vectors.db")
    monkeypatch.setattr(vector_index, "SPEND", tmp_path / "embed-spend.json")
    monkeypatch.setattr(kc, "OUT", tmp_path / "chunks.jsonl")
    monkeypatch.setattr(embeddings, "configured", lambda: True)
    calls = []

    def embed(texts):
        calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(embeddings, "embed", embed)
    kc.OUT.write_text(json.dumps(chunk) + "\n", encoding="utf-8")
    first = vector_index.refresh()
    calls_after_first = len(calls)
    second = vector_index.refresh()
    assert first["embedded"] == 1 and calls_after_first == 1
    assert second["embedded"] == 0 and second["skipped"] == 1
    assert len(calls) == calls_after_first

"""Knowledge chunk store pins (cost-reduction story 2.1).

The contracts that retrieval (2.2) and the vector index (3.2) will build on:
determinism (same estate -> byte-identical file), stable content-independent
ids, real provenance, and bounded chunk size.
"""
import hashlib
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import knowledge_chunks as kc  # noqa: E402


@pytest.fixture(scope="module")
def chunks():
    return kc.build()


def test_rebuild_is_deterministic(tmp_path, monkeypatch):
    monkeypatch.setattr(kc, "OUT", tmp_path / "chunks.jsonl")
    kc.rebuild()
    h1 = hashlib.sha256(kc.OUT.read_bytes()).hexdigest()
    kc.rebuild()
    h2 = hashlib.sha256(kc.OUT.read_bytes()).hexdigest()
    assert h1 == h2, "same estate must produce a byte-identical chunk file"


def test_ids_unique_and_shape_complete(chunks):
    ids = [c["chunk_id"] for c in chunks]
    assert len(ids) == len(set(ids)), "chunk ids must be unique"
    for c in chunks:
        for field in ("chunk_id", "kind", "repo", "source_path", "text", "sha256"):
            assert c.get(field), f"chunk {c.get('chunk_id')} missing {field}"
        assert c["sha256"] == hashlib.sha256(
            c["text"].encode("utf-8")).hexdigest()


def test_id_is_content_independent():
    """An edited source keeps its chunk id (the index sees changed-in-place via
    sha256) — ids derive from kind+repo+slug, never from content."""
    a = kc._chunk("guidance", "repo-x", "merged", "src", "old text")
    b = kc._chunk("guidance", "repo-x", "merged", "src", "completely new text")
    assert a["chunk_id"] == b["chunk_id"]
    assert a["sha256"] != b["sha256"]


def test_every_registry_repo_has_a_surface_chunk(chunks):
    from registry import load_registry
    reg = load_registry()
    surface_repos = {c["repo"] for c in chunks if c["kind"] == "repo-surface"}
    for r in reg["source_repositories"] + reg["test_repositories"]:
        assert r["name"] in surface_repos, \
            f"{r['name']} lost its surface chunk — retrieval would hide the repo"


def test_chunks_are_bounded(chunks):
    for c in chunks:
        assert len(c["text"]) <= kc.MAX_CHUNK_CHARS, \
            "a chunk is a retrieval unit, not a whole-file smuggling route"


def test_provenance_paths_are_real(chunks):
    """Every chunk maps back to something that exists — the audit property that
    makes a trimmed context debuggable."""
    for c in chunks:
        sp = c["source_path"]
        if sp.startswith(("spec_exemplars(", "knowledge (")):
            continue                      # synthesized provenance labels
        p = ROOT / sp.rstrip("/")
        if "*" in sp:
            assert list(ROOT.glob(sp)), f"glob provenance {sp} matches nothing"
        else:
            assert p.exists(), f"provenance {sp} does not exist"


def test_load_skips_bad_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(kc, "OUT", tmp_path / "chunks.jsonl")
    kc.OUT.parent.mkdir(parents=True, exist_ok=True)
    good = json.dumps({"chunk_id": "k:r:s", "kind": "k", "repo": "r",
                       "source_path": "x", "text": "t", "sha256": "h"})
    kc.OUT.write_text("{torn\n" + good + "\n", encoding="utf-8")
    loaded = kc.load()
    assert len(loaded) == 1 and loaded[0]["chunk_id"] == "k:r:s"

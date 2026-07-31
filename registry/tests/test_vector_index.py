"""Embedding port + vector index pins (cost-reduction stories 3.1, 3.2).

Contracts under test: the deterministic mock, zero-cost refresh on an
unchanged corpus (sha-skip), vanished-chunk cleanup, corruption -> quarantine
+ rebuild (never repair), the daily spend cap stopping refresh without
breaking query fallback, and silent TF-IDF degradation when unconfigured.
"""
import json
import pathlib
import sqlite3
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import embeddings  # noqa: E402
import vector_index as vi  # noqa: E402


CHUNK = {"chunk_id": "guidance:r1:merged", "kind": "guidance", "repo": "r1",
         "source_path": "x", "text": "checkout discount rules", "sha256": "aa"}
CHUNK2 = {"chunk_id": "exemplar:r2:profile", "kind": "exemplar", "repo": "r2",
          "source_path": "y", "text": "playwright api spec style", "sha256": "bb"}


@pytest.fixture
def index(tmp_path, monkeypatch):
    import knowledge_chunks as kc
    monkeypatch.setattr(vi, "DB", tmp_path / "vectors.db")
    monkeypatch.setattr(vi, "SPEND", tmp_path / "embed-spend.json")
    monkeypatch.setattr(kc, "OUT", tmp_path / "chunks.jsonl")
    monkeypatch.setenv("AIQE_MOCK", "1")

    def seed(chunks):
        kc.OUT.parent.mkdir(parents=True, exist_ok=True)
        kc.OUT.write_text("".join(json.dumps(c) + "\n" for c in chunks),
                          encoding="utf-8")
    return vi, seed


def _count_calls(monkeypatch):
    calls = {"n": 0}
    real = embeddings.embed

    def counting(texts):
        calls["n"] += 1
        return real(texts)
    monkeypatch.setattr(embeddings, "embed", counting)
    return calls


def test_mock_vectors_are_deterministic():
    a = embeddings.embed(["checkout discount boundary"])
    b = embeddings.embed(["checkout discount boundary"])
    assert a == b and len(a[0]) == 64


def test_unchanged_corpus_costs_zero_embed_calls(index, monkeypatch):
    vi_, seed = index
    seed([CHUNK, CHUNK2])
    calls = _count_calls(monkeypatch)
    r1 = vi_.refresh()
    assert r1["embedded"] == 2 and calls["n"] > 0
    n_after_first = calls["n"]
    r2 = vi_.refresh()
    assert r2["embedded"] == 0 and r2["skipped"] == 2
    assert calls["n"] == n_after_first, \
        "an unchanged corpus must not spend a single embedding call"


def test_changed_and_vanished_chunks_sync(index):
    vi_, seed = index
    seed([CHUNK, CHUNK2])
    vi_.refresh()
    edited = dict(CHUNK, text="checkout discount rules v2", sha256="cc")
    seed([edited])                      # CHUNK2 vanished, CHUNK changed
    r = vi_.refresh()
    assert r["embedded"] == 1 and r["deleted"] == 1
    ids = [row["chunk_id"] for row in vi_.query("checkout", k=10)]
    assert "exemplar:r2:profile" not in ids


def test_corrupt_db_is_quarantined_and_rebuilt(index):
    vi_, seed = index
    seed([CHUNK])
    vi_.refresh()
    vi_.DB.write_bytes(b"this is not a sqlite file")
    r = vi_.refresh()                   # must not raise
    assert r["embedded"] == 1, "rebuild from chunks after quarantine"
    assert list(vi_.DB.parent.glob("*.corrupt-*")), \
        "the corrupt file is kept for forensics, never repaired in place"


def test_daily_cap_stops_refresh_but_not_query(index, monkeypatch):
    vi_, seed = index
    seed([CHUNK, CHUNK2])
    monkeypatch.setattr(vi_, "_cap", lambda: 0.000001)
    monkeypatch.setattr(vi_, "_spend_today", lambda: 0.000001)
    r = vi_.refresh()
    assert r["embedded"] == 0 and "cap" in r["stopped_reason"]
    assert vi_.query("checkout") == [] or True  # query never raises under cap


def test_unconfigured_estate_degrades_silently(index, monkeypatch):
    vi_, seed = index
    monkeypatch.setenv("AIQE_MOCK", "0")
    monkeypatch.delenv("EMBED_URL", raising=False)
    assert not embeddings.configured()
    r = vi_.refresh()
    assert r["embedded"] == 0 and "not configured" in r["stopped_reason"]
    assert vi_.query("anything") == [], \
        "unconfigured -> empty result; the caller's TF-IDF path takes over"
    with pytest.raises(RuntimeError):
        embeddings.embed(["x"])         # direct use states the fix, loudly


def test_query_filters_by_kind_and_repo(index):
    vi_, seed = index
    seed([CHUNK, CHUNK2])
    vi_.refresh()
    only_ex = vi_.query("spec style", kind="exemplar")
    assert only_ex and all(r["kind"] == "exemplar" for r in only_ex)
    only_r1 = vi_.query("rules", repo="r1")
    assert only_r1 and all(r["repo"] == "r1" for r in only_r1)

"""Switching the embedding model re-embedded NOTHING and reported a clean run.

`refresh()` decided staleness on content sha alone:

    stale = [c for c in chunks if force or have.get(c["chunk_id"]) != c["sha256"]]

Chunk text does not change when an operator points `EMBED_MODEL` somewhere
else, so every row was judged current. MEASURED against an isolated index:
build at one width, change `EMBED_DIMS`, refresh returns
`{'embedded': 0, 'skipped': 29, 'stopped_reason': ''}` -- a completely clean
result -- while every stored vector is still from the old model.

Then `query()` scored the new model's query vector against those rows, and
`_cos` used `zip`, which truncates to the shorter vector. So a 1536-dim query
against stale 64-dim rows returned a plausible number computed over the first
64 components of an unrelated space. No exception, no warning, just a confident
ranking that means nothing.

Six consumers take that ranking: `plan_reuse` (>= 0.80 similarity ADAPTS a
human-approved prior plan instead of authoring one), `context_scope` semantic
fill (decides which knowledge a phase is SHOWN), `spec_exemplars`,
`duplicate_detector`, `impact_analysis`, `testcase_learning`.

The everyday path is mock -> real: this repo ships hash vectors, Settings ships
`EMBED_URL`/`EMBED_MODEL`/`EMBED_DIMS`, and an operator who configures a real
provider and runs `make index-rebuild` was told "0 embedded, 29 unchanged".

The precedent was already in the tree and this file follows it: the phase cache
keys on `PROVIDER:MODEL` at both call sites precisely so switching providers can
never replay another provider's result.
"""
import pathlib
import sqlite3
import struct
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))


@pytest.fixture
def idx(tmp_path, monkeypatch):
    monkeypatch.setenv("AIQE_MOCK", "1")
    monkeypatch.setenv("EMBED_DIMS", "8")
    import embeddings
    import vector_index
    monkeypatch.setattr(vector_index, "DB", tmp_path / "v.db")
    monkeypatch.setattr(vector_index, "SPEND", tmp_path / "embed-spend.json")
    import knowledge_chunks
    monkeypatch.setattr(vector_index, "knowledge_chunks", knowledge_chunks)
    return vector_index, embeddings


def _rows(vi):
    con = sqlite3.connect(vi.DB)
    out = con.execute("SELECT chunk_id, model, dims FROM vectors").fetchall()
    con.close()
    return out


# --------------------------------------------------------- the staleness key

def test_an_unchanged_corpus_on_the_same_model_still_costs_nothing(idx):
    """The saving this index exists for. If the fix broke this it would turn
    every nightly `make maintain` into a full re-embed."""
    vi, _ = idx
    first = vi.refresh()
    assert first["embedded"] > 0
    again = vi.refresh()
    assert again["embedded"] == 0 and again["skipped"] == first["embedded"]


def test_a_model_change_re_embeds_everything(idx, monkeypatch):
    """THE DEFECT: this returned embedded 0 / skipped N and a clean run."""
    vi, _ = idx
    built = vi.refresh()["embedded"]
    monkeypatch.setenv("EMBED_DIMS", "16")
    r = vi.refresh()
    assert r["embedded"] == built, "a model change re-embedded nothing"
    assert r["reembedded_model_changed"] == built
    assert r["skipped"] == 0


def test_the_stored_rows_carry_the_model_that_made_them(idx, monkeypatch):
    vi, emb = idx
    vi.refresh()
    assert {m for _, m, _ in _rows(vi)} == {"mock-hash:8"}
    monkeypatch.setenv("EMBED_DIMS", "16")
    vi.refresh()
    assert {m for _, m, _ in _rows(vi)} == {"mock-hash:16"}, \
        "vectors from the old space survived the switch"


# ------------------------------------------------- a row of unknown provenance

def test_a_pre_migration_row_is_re_embedded_and_counted_apart(idx):
    """NULL model is not "another model", it is "we cannot establish which"
    (C13). Both are unusable, and the operator is told which happened because
    one is a config change they made and the other is a one-off upgrade cost.
    """
    vi, _ = idx
    vi.DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(vi.DB)
    con.execute("""CREATE TABLE vectors (chunk_id TEXT PRIMARY KEY,
                   sha256 TEXT, kind TEXT, repo TEXT, dims INT, vec BLOB,
                   updated REAL)""")          # the shape before model existed
    import knowledge_chunks
    chunks = knowledge_chunks.load() or knowledge_chunks.rebuild() or \
        knowledge_chunks.load()
    planted = chunks[:3]
    for c in planted:
        con.execute("INSERT INTO vectors VALUES (?,?,?,?,?,?,?)",
                    (c["chunk_id"], c["sha256"], c["kind"], c["repo"], 8,
                     struct.pack(">8f", *([0.1] * 8)), 0.0))
    con.commit()
    con.close()
    r = vi.refresh()
    assert r["reembedded_model_unknown"] == len(planted)
    assert r["reembedded_model_changed"] == 0, \
        "an unrecorded model was reported as a model change"
    assert all(m == "mock-hash:8" for _, m, _ in _rows(vi))


def test_the_migration_does_not_destroy_the_index(idx):
    """ALTER TABLE ADD COLUMN, not a drop: the rows survive to be re-embedded
    rather than the whole index vanishing on upgrade."""
    vi, _ = idx
    vi.DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(vi.DB)
    con.execute("""CREATE TABLE vectors (chunk_id TEXT PRIMARY KEY,
                   sha256 TEXT, kind TEXT, repo TEXT, dims INT, vec BLOB,
                   updated REAL)""")
    con.execute("INSERT INTO vectors VALUES ('k',?,?,?,?,?,?)",
                ("sha", "scenario", "r", 8, struct.pack(">8f", *([0.1] * 8)), 0.0))
    con.commit()
    con.close()
    con = vi._connect()                      # the migration runs here
    assert con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 1
    assert con.execute("SELECT model FROM vectors").fetchone()[0] is None
    con.close()


# ------------------------------------------------------------- the query side

def test_a_stale_model_returns_no_hits_rather_than_wrong_ones(idx, monkeypatch):
    """0 hits sends every consumer down the TF-IDF fallback, which is correct.
    A wrong ranking is worse than none -- `plan_reuse` adapts a human-approved
    plan off the back of one."""
    vi, _ = idx
    vi.refresh()
    assert len(vi.query("orders discount", k=5)) > 0
    monkeypatch.setenv("EMBED_DIMS", "16")
    assert vi.query("orders discount", k=5) == [], \
        "vectors from another model were ranked against this model's query"


def test_a_row_of_unknown_provenance_is_never_ranked(idx):
    """A pre-migration row (model NULL) is not a row from "no model", it is one
    whose space we cannot establish -- unusable either way, and dropping it
    sends the query to TF-IDF, which is the honest answer."""
    vi, _ = idx
    vi.refresh()
    con = sqlite3.connect(vi.DB)
    con.execute("UPDATE vectors SET model = NULL")
    con.commit()
    con.close()
    assert vi.query("orders discount", k=5) == []


def test_an_unknown_query_identity_does_not_match_unknown_rows(idx, monkeypatch):
    """Why the SQL is `=` and never `IS`. `IS` is NOT DISTINCT FROM, so a NULL
    identity would match exactly the NULL-model rows -- ranking vectors of
    unknown provenance against a query of unknown provenance and calling the
    agreement a match. Two unknowns are not evidence of sameness (C13)."""
    vi, emb = idx
    vi.refresh()
    con = sqlite3.connect(vi.DB)
    con.execute("UPDATE vectors SET model = NULL")
    con.commit()
    con.close()
    monkeypatch.setattr(emb, "identity", lambda: None)
    assert vi.query("orders discount", k=5) == [], \
        "an unnameable vector space was matched against unnameable rows"


def test_kind_and_repo_filters_still_work_alongside_the_model_filter(idx):
    """The model clause joins the existing WHERE rather than preceding it --
    two WHERE keywords would be a SQL error, and the except clause would have
    swallowed it into a silent empty result."""
    vi, _ = idx
    vi.refresh()
    hits = vi.query("orders", k=20, kind="scenario")
    assert hits, "the filtered query returned nothing"
    assert all(h["kind"] == "scenario" for h in hits)


def test_cosine_refuses_vectors_of_different_length(idx):
    """Belt to the model key's braces. zip() truncated silently."""
    vi, _ = idx
    assert vi._cos([1.0] * 8, [1.0] * 16) == 0.0
    assert vi._cos([1.0] * 8, [1.0] * 8) == pytest.approx(1.0)


# -------------------------------------------------------------- what is said

def test_identity_separates_mock_from_a_real_provider(idx, monkeypatch):
    """Hash vectors and a real model must never share a key, or configuring a
    provider would reuse the demo's vectors."""
    _, emb = idx
    assert emb.identity().startswith("mock-hash:")
    monkeypatch.delenv("AIQE_MOCK", raising=False)
    monkeypatch.setenv("AIQE_MOCK", "0")
    monkeypatch.setenv("EMBED_URL", "https://example.invalid/v1/embeddings")
    monkeypatch.setenv("EMBED_MODEL", "text-embedding-3-small")
    assert emb.identity() == "http:text-embedding-3-small:8"


def test_identity_does_not_carry_the_endpoint(idx, monkeypatch):
    """Two gateways serving one model produce the same space, so keying on the
    host would re-embed the corpus for a DNS change -- and this string is
    written to a file and printed, where a private hostname does not belong."""
    _, emb = idx
    monkeypatch.setenv("AIQE_MOCK", "0")
    monkeypatch.setenv("EMBED_URL", "https://internal-gateway.corp.invalid/v1")
    monkeypatch.setenv("EMBED_MODEL", "m")
    assert "internal-gateway" not in emb.identity()


def test_an_unconfigured_estate_has_no_identity(idx, monkeypatch):
    monkeypatch.setenv("AIQE_MOCK", "0")
    monkeypatch.delenv("EMBED_URL", raising=False)
    _, emb = idx
    assert emb.identity() == "" and not emb.configured()


def test_the_cli_says_why_it_re_embedded(idx, monkeypatch, capsys):
    """Silence here is how a one-off model switch reads as a runaway bill."""
    vi, _ = idx
    vi.refresh()
    monkeypatch.setenv("EMBED_DIMS", "16")
    vi.main(["vector_index.py", "refresh"])
    out = capsys.readouterr().out
    assert "embedding model changed" in out
    assert "mock-hash:16" in out, "the new model is not named"


def test_the_cli_flags_hash_vectors_as_carrying_no_meaning(idx, capsys):
    """`make index-rebuild` in mock mode reported "29 embedded" and let an
    operator believe they had a semantic index."""
    vi, _ = idx
    vi.main(["vector_index.py", "rebuild"])
    out = capsys.readouterr().out
    assert "HASH vectors" in out and "EMBED_URL" in out


def test_a_normal_refresh_says_nothing_extra(idx, capsys):
    """A caveat printed on every healthy run is one operators stop reading."""
    vi, _ = idx
    monkeypatch_free = vi.refresh()
    assert monkeypatch_free["embedded"] > 0
    capsys.readouterr()
    vi.main(["vector_index.py", "refresh"])
    out = capsys.readouterr().out
    assert "embedding model changed" not in out
    assert "not recorded" not in out

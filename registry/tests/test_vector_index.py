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


# ---- the numeric core: wrong here degrades every ranking and breaks nothing --
def test_a_vector_survives_the_round_trip():
    """float32 packing is lossy against Python floats, so the pin is on the
    ROUND TRIP rather than on equality with the input — and on re-packing being
    byte-identical, or the store drifts a little on every refresh."""
    vec = [0.1, -0.5, 0.0, 1.0, 1e-7]
    blob = vi._pack(vec)
    got = list(vi._unpack(blob, len(vec)))
    assert len(got) == len(vec)
    for a, b in zip(vec, got):
        assert abs(a - b) < 1e-6, (a, b)
    assert vi._pack(got) == blob


def test_cosine_similarity_is_actually_cosine():
    assert vi._cos([1, 0], [1, 0]) == pytest.approx(1.0)
    assert vi._cos([1, 0], [0, 1]) == pytest.approx(0.0)
    assert vi._cos([1, 0], [-1, 0]) == pytest.approx(-1.0)
    # Magnitude must not matter — only direction.
    assert vi._cos([3, 4], [30, 40]) == pytest.approx(1.0)


def test_a_zero_vector_scores_zero_instead_of_dividing_by_zero():
    """An all-zero embedding is what a misconfigured or truncating provider
    returns. Dividing by its norm raises, and a raise here turns "retrieval
    degrades to lexical" into "the query crashes" — the fallback this whole
    subsystem is built around stops working exactly when it is needed."""
    assert vi._cos([0, 0, 0], [1, 2, 3]) == 0.0
    assert vi._cos([1, 2, 3], [0, 0, 0]) == 0.0
    assert vi._cos([0, 0], [0, 0]) == 0.0


def test_the_once_a_day_marker_is_only_set_after_delivery(tmp_path, monkeypatch):
    """`_notify_once` wrote the `notified-<day>` marker BEFORE sending.

    A failed send still counted as "notified today", so the message — that the
    embed budget stopped the index refreshing — was skipped for the rest of the
    day, and retrieval quietly degraded to lexical with nobody told. Fourth
    module with this exact shape (coverage_drift, spec_drift, and here).
    """
    spend = tmp_path / "embed-spend.json"
    monkeypatch.setattr(vi, "SPEND", spend)
    monkeypatch.setattr(vi, "ROOT", tmp_path)

    # No adapter on disk -> nothing delivered -> the day must NOT be marked.
    vi._notify_once("budget stopped the refresh")
    assert not spend.exists() or f"notified-{vi._day()}" not in json.loads(
        spend.read_text(encoding="utf-8")), "a day was marked without a delivery"

    # A MOCK adapter exiting 0 is NOT a delivery. This assertion used to expect
    # the marker and said "a working adapter marks the day" -- it encoded the
    # belief this module was later fixed for, the same way test_spec_drift's
    # did. Under AIQE_MOCK=1 (the deployed default) nothing left the machine,
    # so suppressing the rest of the day's warnings would tell nobody while
    # retrieval degraded to lexical.
    mock_ad = tmp_path / "adapters" / "mock"
    mock_ad.mkdir(parents=True)
    mock_calls = tmp_path / "mock-calls.txt"
    (mock_ad / "notify.sh").write_text(
        f'#!/usr/bin/env bash\necho x >> "{mock_calls.as_posix()}"\nexit 0\n',
        encoding="utf-8", newline="\n")
    monkeypatch.setenv("AIQE_MOCK", "1")
    vi._notify_once("budget stopped the refresh")
    assert mock_calls.exists(), "the mock adapter was not even invoked"
    assert not spend.exists() or f"notified-{vi._day()}" not in json.loads(
        spend.read_text(encoding="utf-8")), \
        "a simulated send suppressed the rest of the day's warnings"

    # A REAL adapter marks the day, and the second call stays silent. That
    # idempotence is what the marker is for, and losing it would turn one alarm
    # into one per batch.
    real_ad = tmp_path / "adapters" / "notify"
    real_ad.mkdir(parents=True)
    calls = tmp_path / "calls.txt"
    (real_ad / "slack.sh").write_text(
        f'#!/usr/bin/env bash\necho x >> "{calls.as_posix()}"\nexit 0\n',
        encoding="utf-8", newline="\n")
    monkeypatch.setenv("AIQE_MOCK", "0")
    vi._notify_once("budget stopped the refresh")
    assert calls.exists() and len(calls.read_text().split()) == 1
    assert json.loads(spend.read_text(encoding="utf-8"))[f"notified-{vi._day()}"]
    vi._notify_once("budget stopped the refresh")
    assert len(calls.read_text().split()) == 1, "the marker stopped suppressing"

    vi._notify_once("budget stopped the refresh")
    assert len(calls.read_text().split()) == 1, "notified twice in one day"

"""A corrupt vector index is never reported as an empty one, and self-heals.

Two silent-degradation paths, both C13:

`stats()` returned `{"rows": 0, "by_kind": {}}` on any sqlite3.Error — an index
we could not READ rendered exactly like an index with nothing in it, which is
the reading that stops anyone investigating.

`query()` returned `[]` without quarantining. Five consumers (context_scope,
plan_reuse, spec_exemplars, duplicate_detector, impact_analysis) treat `[]` as
the designed TF-IDF fallback, so a damaged index degraded every semantic path
to lexical silently and INDEFINITELY — and refresh()'s quarantine could not
fire either, because it ran with the sqlite handle still open (fixed
separately). Nothing anywhere would have recovered it.

`[]` stays the contract — raising would break every consumer instead of
degrading it — but corruption now quarantines so the next refresh rebuilds. A
LOCKED or busy database is transient and must survive: quarantining that would
delete a working index because a query arrived at a busy moment.
"""
import pathlib
import sqlite3
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import vector_index as vi  # noqa: E402


def _build(db, rows=120):
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE vectors (chunk_id TEXT PRIMARY KEY, kind TEXT, repo TEXT,"
        " sha256 TEXT, dims INT, vec BLOB);")
    for i in range(rows):
        con.execute("INSERT INTO vectors VALUES (?,?,?,?,?,?)",
                    (f"c{i}", "spec", "r", "s", 2, b"\x00" * 8))
    con.commit()
    con.close()


def _corrupt(db):
    """Destroy everything past the header so connect() still succeeds and the
    failure surfaces on the SELECT — the shape that actually occurs."""
    raw = bytearray(db.read_bytes())
    raw[100:] = b"\xa5" * (len(raw) - 100)
    db.write_bytes(bytes(raw))


@pytest.mark.parametrize("msg,corrupt", [
    ("database disk image is malformed", True),
    ("file is not a database", True),
    ("database is locked", False),
    ("database table is locked", False),
    ("attempt to write a readonly database", False),
])
def test_only_real_corruption_is_treated_as_corruption(msg, corrupt):
    """The discriminator decides whether deleting the file is repair or damage."""
    assert vi._is_corruption(sqlite3.DatabaseError(msg)) is corrupt


def test_stats_never_reports_a_corrupt_index_as_empty(tmp_path, monkeypatch):
    db = tmp_path / "vectors.db"
    _build(db)
    _corrupt(db)
    monkeypatch.setattr(vi, "DB", db)
    s = vi.stats()
    assert s["rows"] is None, (
        "an unreadable index reported a row COUNT — 0 is indistinguishable "
        "from a freshly created index, which is the reading that stops anyone "
        "investigating (C13)")
    assert s["by_kind"] is None, "by_kind must not read as 'no kinds present'"
    assert s.get("unavailable"), "the reason for the failure is not reported"


def test_stats_still_counts_a_healthy_index(tmp_path, monkeypatch):
    """The other direction — a 'safe' stats() that always says unavailable
    would satisfy the test above and make the Cost/knowledge views useless."""
    db = tmp_path / "vectors.db"
    _build(db, rows=7)
    monkeypatch.setattr(vi, "DB", db)
    s = vi.stats()
    assert s["rows"] == 7 and s["by_kind"] == {"spec": 7}
    assert not s.get("unavailable")


def test_query_falls_back_and_quarantines_on_corruption(tmp_path, monkeypatch):
    db = tmp_path / "vectors.db"
    _build(db)
    _corrupt(db)
    monkeypatch.setattr(vi, "DB", db)
    monkeypatch.setattr(vi.embeddings, "configured", lambda: True)
    monkeypatch.setattr(vi.embeddings, "embed", lambda texts: [[0.1, 0.2]])

    assert vi.query("anything", k=3) == [], (
        "query must keep returning [] — five consumers read it as the TF-IDF "
        "fallback, and raising would break every semantic path")
    assert list(db.parent.glob("*.corrupt-*")), (
        "the corrupt index was not quarantined, so every future query falls "
        "back to lexical forever with nothing to recover it")


def test_a_busy_index_is_never_quarantined(tmp_path, monkeypatch):
    """Transient errors must leave the file alone. Deleting a healthy index
    because a query arrived while it was locked is damage, not recovery."""
    db = tmp_path / "vectors.db"
    _build(db)
    monkeypatch.setattr(vi, "DB", db)
    monkeypatch.setattr(vi.embeddings, "configured", lambda: True)
    monkeypatch.setattr(vi.embeddings, "embed", lambda texts: [[0.1, 0.2]])

    def busy(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(vi, "_connect", busy)

    assert vi.query("anything") == []
    assert not list(db.parent.glob("*.corrupt-*")), \
        "a locked database was quarantined — that destroys a working index"
    assert db.exists(), "the index file was removed on a transient error"

#!/usr/bin/env python3
"""Artifact vector index (cost-reduction story 3.2).

SQLite + float32 BLOBs + pure-Python cosine, per the ADR
(docs/adr/embeddings.md): the corpus is hundreds-to-low-thousands of chunks, so
brute force is ~10 ms and a native vector extension or a server would be pure
operational weight. Documented revisit trigger: corpus > 50k chunks or p95
query > 200 ms.

Contracts:
- refresh() embeds ONLY chunks whose sha256 changed and deletes rows whose
  chunk_id vanished — an unchanged corpus costs zero embedding calls (pinned).
- Embedding spend is estimated (chars/4 tokens x org-config
  `budgets.embed_price_per_mtok`) into a day-ledger; over
  `budgets.max_embed_usd_per_day` refresh STOPS (partial, reported) — the
  cost-saving layer must never become its own runaway bill. query() never
  spends more than one embedding call.
- Corruption recovery is REGENERATION: any sqlite error moves the db aside
  (.corrupt-<ts>, like fs_lock) and rebuilds from chunks.jsonl. The index is
  derived data; there is nothing to repair.
- The store is gitignored and excluded from state bundles; `make maintain`
  refreshes it, `make index-rebuild` forces from scratch.

CLI: vector_index.py refresh|rebuild|stats|query <text>
"""
import datetime
import math
import os
import pathlib
import sqlite3
import struct
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import embeddings
import env_flag  # AIQE_MOCK means what it says
import fs_lock
import knowledge_chunks

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB = pathlib.Path(os.environ.get("AIQE_VECTOR_DB",
                                 ROOT / "reports/knowledge-index/vectors.db"))
SPEND = DB.parent / "embed-spend.json"

DDL = """CREATE TABLE IF NOT EXISTS vectors (
  chunk_id TEXT PRIMARY KEY, sha256 TEXT, kind TEXT, repo TEXT,
  dims INT, vec BLOB, updated REAL, model TEXT)"""

# Rows written before `model` existed carry NULL. That is NOT "a row from
# another model" — it is a row whose model we cannot establish (C13), and the
# only safe reading is that it is unusable. Both get re-embedded; they are
# COUNTED separately so the refresh can say which happened.
UNKNOWN_MODEL = None


def _cfg():
    try:
        import yaml
        return (yaml.safe_load(open(ROOT / "registry/org-config.yaml",
                                    encoding="utf-8")) or {}).get("budgets") or {}
    except Exception:
        return {}


def _pack(vec):
    return struct.pack(f">{len(vec)}f", *vec)


def _unpack(blob, n):
    return struct.unpack(f">{n}f", blob)


def _cos(a, b):
    # zip() silently truncates to the shorter vector, so a 1536-dim query
    # scored against a stale 64-dim row returned a perfectly plausible number
    # over the first 64 components of an unrelated space. Belt to the model
    # key's braces: incomparable vectors score nothing, they do not score badly.
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    try:
        con.execute(DDL)
        # Migrate an index created before the model column. ADD COLUMN leaves
        # existing rows NULL, which is exactly right: their model is unknown,
        # and refresh() re-embeds them once rather than trusting them.
        cols = {r[1] for r in con.execute("PRAGMA table_info(vectors)")}
        if "model" not in cols:
            con.execute("ALTER TABLE vectors ADD COLUMN model TEXT")
    except sqlite3.Error:
        # Close BEFORE the caller quarantines: Windows refuses to rename a
        # file with an open handle, which would leave the corrupt db in place.
        con.close()
        raise
    return con


# sqlite3.Error covers two very different situations, and the difference decides
# whether destroying the file is repair or damage: a MALFORMED image is derived
# data we rebuild, while "database is locked"/"busy" is a healthy index somebody
# else is reading this instant. Quarantining the second would delete a working
# index because a query arrived at a busy moment.
_CORRUPTION_SIGNS = ("malformed", "not a database", "disk image",
                     "file is encrypted", "corrupt")


def _is_corruption(exc):
    return any(s in str(exc).lower() for s in _CORRUPTION_SIGNS)


def _quarantine():
    """A corrupt index is derived data — move it aside for forensics and
    rebuild. Never attempt repair."""
    try:
        DB.rename(DB.with_name(f"{DB.name}.corrupt-{int(time.time())}"))
    except OSError:
        try:
            DB.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- spend cap
def _day():
    return time.strftime("%Y-%m-%d")


def _spend_today():
    d = fs_lock.read_json_guarded(SPEND, {})
    row = d.get(_day(), 0.0) if isinstance(d, dict) else 0.0
    if isinstance(row, dict):
        row = row.get("cost_usd")
    try:
        value = float(row or 0.0)
        return value if math.isfinite(value) and value >= 0 else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _record_spend(usd, tokens=0):
    """Record one embedding adapter call without changing cap ownership.

    Older ledgers stored a scalar dollar total per day.  They remain readable;
    the first new write upgrades today's value to a structured daily row so the
    cost report can state call/token evidence instead of guessing it.
    """
    def count(value):
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError, OverflowError):
            return 0

    with fs_lock.lock(SPEND):
        d = fs_lock.read_json_guarded(SPEND, {})
        d = d if isinstance(d, dict) else {}
        d = {k: v for k, v in d.items()
             if k.startswith("notified-") or k >= _day()}  # keep today + markers
        old = d.get(_day(), {})
        if isinstance(old, dict):
            old_cost = old.get("cost_usd", 0.0)
            old_calls = old.get("calls", 0)
            old_tokens = old.get("tokens", 0)
        else:                              # legacy scalar daily total
            old_cost, old_calls, old_tokens = old, 0, 0
        try:
            old_cost = float(old_cost or 0.0)
        except (TypeError, ValueError, OverflowError):
            old_cost = 0.0
        d[_day()] = {
            "cost_usd": round(old_cost + float(usd), 6),
            "basis": "estimated", "provider": "embeddings",
            "calls": count(old_calls) + 1,
            "tokens": count(old_tokens) + count(tokens),
        }
        fs_lock.write_json_atomic(SPEND, d, sort_keys=True)


def spend_rows(days=None):
    """Return honest daily embedding rows, including legacy scalar entries."""
    doc = fs_lock.read_json_guarded(SPEND, {})
    if not isinstance(doc, dict):
        return []
    cutoff = None
    if days is not None:
        cutoff = datetime.date.today() - datetime.timedelta(days=max(0, int(days) - 1))
    rows = []

    def count(value):
        try:
            value = int(value or 0)
            return value if value >= 0 else 0
        except (TypeError, ValueError, OverflowError):
            return 0

    for day, raw in sorted(doc.items()):
        try:
            stamp = datetime.date.fromisoformat(day)
        except (TypeError, ValueError):
            continue                       # notification markers / malformed keys
        if cutoff is not None and stamp < cutoff:
            continue
        legacy = not isinstance(raw, dict)
        value = raw if legacy else raw.get("cost_usd")
        try:
            cost = float(value)
            cost = cost if math.isfinite(cost) and cost >= 0 else None
        except (TypeError, ValueError, OverflowError):
            cost = None
        basis = "estimated" if legacy else str(raw.get("basis") or "unknown")
        if basis not in ("reported", "estimated", "local", "simulated",
                         "unknown", "unrecorded", "not-reconciled"):
            basis = "unknown"
        if basis in ("unknown", "unrecorded", "not-reconciled"):
            cost = None
        rows.append({
            "day": day, "provider": "embeddings" if legacy else
                   str(raw.get("provider") or "embeddings"),
            "basis": basis, "cost_usd": cost,
            "calls": None if legacy else count(raw.get("calls")),
            "tokens": None if legacy else count(raw.get("tokens")),
            "legacy": legacy,
        })
    return rows


def _est_usd(texts):
    price = float(_cfg().get("embed_price_per_mtok") or 0.02)  # $/M tokens
    toks = sum(len(t) for t in texts) / 4
    return toks / 1_000_000 * price


def _cap():
    v = _cfg().get("max_embed_usd_per_day")
    return float(v) if isinstance(v, (int, float)) and v > 0 else 0.0


# ---------------------------------------------------------------- refresh
def refresh(force=False):
    """Sync the index to chunks.jsonl. Returns {embedded, skipped, deleted,
    stopped_reason}. sha-skip: unchanged chunks cost nothing."""
    if not embeddings.configured():
        return {"embedded": 0, "skipped": 0, "deleted": 0,
                "stopped_reason": "embeddings not configured (TF-IDF fallback covers queries)"}
    chunks = knowledge_chunks.load()
    if not chunks:
        knowledge_chunks.rebuild()
        chunks = knowledge_chunks.load()
    # The handle must be CLOSED before quarantining. A damaged data page lets
    # `_connect()`'s CREATE TABLE IF NOT EXISTS succeed (it only touches page 1)
    # and surfaces only on this SELECT — so the failure arrives with `con` open,
    # and on Windows both the rename and the unlink fallback inside _quarantine()
    # then fail with WinError 32 (file in use). Measured against a real corrupted
    # db: the corrupt file was left in place, unquarantined, and the next
    # _connect() handed back the same broken file with have={} — every chunk
    # judged stale and the first REPLACE INTO raising out of refresh(). The
    # nightly `make maintain` would fail that way forever, because the one
    # mechanism meant to recover it could never get the file handle-free.
    con = None
    try:
        con = _connect()
        have = {r[0]: (r[1], r[2]) for r in con.execute(
            "SELECT chunk_id, sha256, model FROM vectors")}
    except sqlite3.Error:
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass
        _quarantine()
        con = _connect()
        have = {}

    wanted = {c["chunk_id"]: c for c in chunks}
    # A stored vector is usable only if its CONTENT is unchanged AND it came
    # from the SAME model. Keying on content alone meant a model switch
    # re-embedded nothing and reported a clean run — measured: rebuild at one
    # width, change EMBED_DIMS, refresh returns embedded 0 / skipped 29 /
    # stopped_reason "" while every row is still from the old space.
    now_model = embeddings.identity()
    changed_model = unknown_model = 0
    stale = []
    for c in chunks:
        prior = have.get(c["chunk_id"])
        if force or prior is None or prior[0] != c["sha256"]:
            stale.append(c)
            continue
        if prior[1] != now_model:
            stale.append(c)
            if prior[1] is UNKNOWN_MODEL:
                unknown_model += 1
            else:
                changed_model += 1
    gone = [cid for cid in have if cid not in wanted]
    embedded, stopped = 0, ""

    cap = _cap()
    BATCH = 32
    for i in range(0, len(stale), BATCH):
        batch = stale[i:i + BATCH]
        est = _est_usd([c["text"] for c in batch])
        if cap and _spend_today() + est > cap:
            stopped = (f"daily embed cap ${cap:.2f} reached — refresh is partial; "
                       f"queries fall back where vectors are missing")
            _notify_once(stopped)
            break
        try:
            vecs = embeddings.embed([c["text"] for c in batch])
        except RuntimeError as e:
            stopped = f"embed adapter failed: {e}"
            break
        _record_spend(est, sum(len(c["text"]) for c in batch) // 4)
        with con:
            for c, v in zip(batch, vecs):
                con.execute(
                    "REPLACE INTO vectors VALUES (?,?,?,?,?,?,?,?)",
                    (c["chunk_id"], c["sha256"], c["kind"], c["repo"],
                     len(v), _pack(v), time.time(), now_model))
        embedded += len(batch)
    with con:
        for cid in gone:
            con.execute("DELETE FROM vectors WHERE chunk_id=?", (cid,))
    con.close()
    return {"embedded": embedded, "skipped": len(chunks) - len(stale),
            "deleted": len(gone), "stopped_reason": stopped,
            "model": now_model, "reembedded_model_changed": changed_model,
            "reembedded_model_unknown": unknown_model}


def _notify_once(msg):
    """Best-effort Notify-port ping, at most once per day (the marker rides the
    spend ledger's keyspace)."""
    d = fs_lock.read_json_guarded(SPEND, {})
    d = d if isinstance(d, dict) else {}
    marker = f"notified-{_day()}"
    if d.get(marker):
        return
    # Deliver FIRST, then claim it was delivered. Writing the marker up front
    # meant a failed send still counted as "notified today", so the message —
    # that the embed budget stopped the index refreshing — was skipped for the
    # rest of the day and retrieval quietly degraded to lexical with nobody
    # told. Same shape as the coverage-drift and spec-drift alarms.
    delivered = False
    try:
        import subprocess

        import work_queue
        adapter = ROOT / ("adapters/mock/notify.sh"
                          if env_flag.mock()
                          else "adapters/notify/slack.sh")
        if adapter.exists():
            r = subprocess.run([work_queue.bash_exe(), str(adapter), "post",
                                f"[ai-qe] {msg}"], cwd=ROOT,
                               capture_output=True,
                               stdin=subprocess.DEVNULL, timeout=30)
            delivered = r.returncode == 0
    except Exception:                      # noqa: BLE001
        delivered = False                  # best-effort, but not a silent one
    if not delivered:
        return                             # no marker: the next run retries
    with fs_lock.lock(SPEND):
        current = fs_lock.read_json_guarded(SPEND, {})
        current = current if isinstance(current, dict) else {}
        current[marker] = True
        fs_lock.write_json_atomic(SPEND, current, sort_keys=True)


# ---------------------------------------------------------------- query
def query(text, k=5, kind=None, repo=None):
    """Top-k chunks by cosine similarity: [{chunk_id, kind, repo, score}].
    One embedding call; [] when unconfigured or on any failure — callers'
    TF-IDF fallback is the degradation path, never an exception."""
    if not embeddings.configured():
        return []
    try:
        qv = embeddings.embed([text])[0]
        con = _connect()
        # Only rows from the CURRENT vector space are comparable with `qv`.
        # A row from another model is not a weaker match, it is not a match at
        # all -- and refresh() is already going to replace it. Filtering here
        # means a partially-refreshed index (the daily embed cap stopped it)
        # ranks the rows it can and leaves the rest to the TF-IDF fallback,
        # which is what "queries fall back where vectors are missing" promises.
        sql = "SELECT chunk_id, kind, repo, dims, vec FROM vectors"
        # `=`, deliberately, NOT `IS`. A pre-migration row carries model NULL,
        # which means we cannot establish which space its vector came from --
        # and an unknown space is not this one (C13). Equality can never match
        # NULL, so those rows drop out and the query falls back to TF-IDF.
        # `IS` is the trap: it is NOT DISTINCT FROM, so the day identity() can
        # return None (unconfigured, a future provider that cannot name itself)
        # it would match exactly the NULL rows and rank vectors of unknown
        # provenance -- the defect this whole model key exists to prevent. Two
        # unknowns are not evidence of sameness. Pinned both ways.
        conds, args = ["model = ?"], [embeddings.identity()]
        if kind:
            conds.append("kind=?")
            args.append(kind)
        if repo:
            conds.append("repo=?")
            args.append(repo)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        rows = list(con.execute(sql, args))
        con.close()
    except (sqlite3.Error, RuntimeError) as exc:
        # [] stays the contract — five consumers (context_scope, plan_reuse,
        # spec_exemplars, duplicate_detector, impact_analysis) read it as the
        # designed TF-IDF fallback, and raising here would break every semantic
        # path instead of degrading it.
        #
        # But CORRUPTION must not degrade silently and forever. Nothing on this
        # read path ever quarantined, and refresh()'s quarantine could not fire
        # (it ran with the handle open), so a damaged index meant every semantic
        # query fell back to lexical indefinitely with no signal anywhere.
        # Quarantine here so the next refresh rebuilds; a LOCKED or busy
        # database is transient and must NOT be destroyed, so only genuine
        # corruption qualifies.
        if _is_corruption(exc):
            _quarantine()
            print(f"[vector_index] index was corrupt ({exc}); quarantined — "
                  f"queries use the TF-IDF fallback until the next refresh "
                  f"rebuilds it", file=sys.stderr)
        return []
    scored = []
    for cid, ckind, crepo, dims_, blob in rows:
        try:
            score = _cos(qv, _unpack(blob, dims_))
        except struct.error:
            continue
        scored.append({"chunk_id": cid, "kind": ckind, "repo": crepo,
                       "score": round(score, 4)})
    scored.sort(key=lambda s: -s["score"])
    return scored[:k]


def stats():
    try:
        con = _connect()
        rows = list(con.execute(
            "SELECT kind, COUNT(*) FROM vectors GROUP BY kind"))
        total = sum(n for _, n in rows)
        con.close()
    except sqlite3.Error as exc:
        # An index we could not READ is not an index with nothing in it. This
        # returned rows: 0 with an empty by_kind — indistinguishable from a
        # freshly created index, which is exactly the reading that stops anyone
        # investigating (C13). `rows` is None so no caller can sum it into a
        # total, and the reason travels with it.
        return {"rows": None, "by_kind": None, "unavailable": str(exc)[:200],
                "configured": embeddings.configured(),
                "spend_today_usd": _spend_today(), "db": str(DB)}
    return {"rows": total, "by_kind": dict(rows),
            "configured": embeddings.configured(),
            "spend_today_usd": _spend_today(), "db": str(DB)}


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "stats"
    if cmd in ("refresh", "rebuild"):
        r = refresh(force=(cmd == "rebuild"))
        print(f"vector index: {r['embedded']} embedded, {r['skipped']} unchanged, "
              f"{r['deleted']} removed"
              + (f" — {r['stopped_reason']}" if r["stopped_reason"] else ""))
        # Say WHY money was spent re-embedding chunks nobody edited. Silence
        # here is how a one-off model switch reads as a runaway bill.
        if r.get("reembedded_model_changed"):
            print(f"  {r['reembedded_model_changed']} chunk(s) re-embedded "
                  f"because the embedding model changed (now {r['model']}): "
                  f"vectors from two models cannot be compared, so the old "
                  f"ones were not reusable")
        if r.get("reembedded_model_unknown"):
            print(f"  {r['reembedded_model_unknown']} chunk(s) re-embedded "
                  f"because the model that produced them was not recorded "
                  f"(index predates model tracking) -- a one-off cost")
        if r.get("model", "").startswith("mock-hash"):
            print("  NOTE: these are deterministic HASH vectors (mock mode). "
                  "They exercise the plumbing and carry no semantic meaning; "
                  "set EMBED_URL and re-run for a real index.")
        return 0
    if cmd == "stats":
        s = stats()
        print(f"vector index: {s['rows']} row(s) {s.get('by_kind', {})} — "
              f"configured={s['configured']}, spend today ${s['spend_today_usd']:.4f}")
        return 0
    if cmd == "query":
        for r in query(" ".join(argv[2:]) or "checkout"):
            print(f"{r['score']:.4f}  {r['chunk_id']}")
        return 0
    print("usage: vector_index.py refresh|rebuild|stats|query <text>",
          file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv))

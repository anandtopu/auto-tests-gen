#!/usr/bin/env python3
"""Build the SQLite query index (reports/catalog.db) from the catalog JSONL +
health data. The JSONL files remain the committed source of truth; the DB is a
deterministic, regenerable index for fast ad-hoc queries at estate scale
(hundreds of repos — the post-PoC path the architecture flags in §5.9.1).

Rebuilt by: catalog bootstrap, bin/qa.py mapping edits, make catalog-db.
Query it: bin/qa.py sql "SELECT ..." (read-only).
"""
import glob, json, pathlib, sqlite3, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB = ROOT / "reports/catalog.db"


def rebuild():
    health = {}
    hf = ROOT / "catalog/health.json"
    if hf.exists():
        health = json.load(open(hf, encoding="utf-8"))
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript("""
        DROP TABLE IF EXISTS tests;
        CREATE TABLE tests (
            test_id    TEXT PRIMARY KEY,
            test_repo  TEXT, file TEXT, title TEXT, layer TEXT,
            status     TEXT, confidence REAL,
            app_repos  TEXT,   -- comma-joined
            endpoints  TEXT, routes TEXT, methods TEXT,
            pass_rate  REAL, last_status TEXT, flaky INTEGER
        );
        CREATE INDEX idx_tests_app ON tests(app_repos);
        CREATE INDEX idx_tests_status ON tests(status);
    """)
    n = 0
    for f in sorted(glob.glob(str(ROOT / "catalog/*.jsonl"))):
        if pathlib.Path(f).name == "catalog.sample.jsonl":
            continue
        for line in open(f, encoding="utf-8"):
            if not line.strip():
                continue
            e = json.loads(line)
            m, ev = e["mapping"], e["evidence"]
            h = health.get(e["test_id"], {})
            con.execute(
                "INSERT OR REPLACE INTO tests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (e["test_id"], e["test_repo"], e["file"], e["title"], e.get("layer", ""),
                 m["status"], m.get("confidence", 0), ",".join(m.get("app_repos", [])),
                 ",".join(ev.get("endpoints", [])), ",".join(ev.get("ui_routes", [])),
                 ",".join(m.get("method", [])), h.get("pass_rate"),
                 h.get("last_status"), int(bool(h.get("flaky")))))
            n += 1
    con.commit()
    con.close()
    return n


if __name__ == "__main__":
    print(f"catalog.db rebuilt: {rebuild()} tests indexed ({DB})")

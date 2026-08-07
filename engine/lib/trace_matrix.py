#!/usr/bin/env python3
"""Requirement traceability matrix (roadmap 3.1) — one row per scenario, end to end.

`trace.py` answers "what happened to this key, chronologically" — an event stream
for one story. This answers the auditor's different question: **for every
requirement, show me the chain** — ticket → plan scenario → generated spec → gate
commit → how it is doing in CI. Regulated teams ask for exactly this table, and
every link already exists in state the platform writes; this is a read-only join,
no new stores.

Row shape (one per scenario, plus rows for tests generated without a plan — the PR
path has no scenarios, and hiding those tests would make the matrix lie by
omission):

    key · scenario_id · scenario_title · behavior_ref · spec file · test_repo ·
    action · gate_status · commit · run_id · ci_runs · ci_failures · ci_last

Identity: scenario→test joins on the `scenario_id` the generate contract stamps on
every test. CI health joins on the catalog test_id convention
(`<repo>::<file>::<title>`), best-effort by file when titles drift.

CLI:  trace_matrix.py [KEY] [--csv]
API:  GET /api/trace-matrix?key=K  (dashboard)
"""
import csv
import glob
import io
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

STATE_FILES = ("reviews.json", "queue.json", "hooks-seen.json")


def _run_records():
    out = []
    for f in glob.glob(str(ROOT / "reports/runs/*.json")):
        if pathlib.Path(f).name in STATE_FILES:
            continue
        try:
            r = json.load(open(f, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(r, dict) and isinstance(r.get("trigger"), dict):
            out.append(r)
    out.sort(key=lambda r: r.get("ts", 0))
    return out


def _latest_per_key(records, key=None):
    latest = {}
    for r in records:
        k = r.get("trigger", {}).get("key", "")
        if not k or (key and k != key):
            continue
        latest[k] = r          # records are ts-sorted, so last write wins
    return latest


def _contracts(record):
    return {p.get("name"): p.get("contract") or {}
            for p in record.get("phases", []) if isinstance(p, dict)}


def _scenarios_for(key):
    """Scenario rows from the plan contract snapshot, when the key has one.
    PR keys legitimately have none."""
    import plan_state
    try:
        p = plan_state.contract_path(key)
    except SystemExit:
        return []
    if not p.exists():
        return []
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [s for s in doc.get("scenarios") or [] if isinstance(s, dict)]


def _health_index():
    """CI health keyed two ways: exact test_id and by spec file (drift fallback)."""
    import test_health
    by_id, by_file = {}, {}
    for tid, h in (test_health.load() or {}).items():
        by_id[tid] = h
        parts = tid.split("::")
        if len(parts) >= 2:
            by_file.setdefault(parts[1], h)
    return by_id, by_file


def _gate_for(record, repo):
    for g in record.get("gates", []):
        if isinstance(g, dict) and g.get("test_repo") == repo:
            return g
    return {}


def build(key=None):
    """The matrix rows. Total: missing links render as empty cells, never errors —
    an audit artifact that crashes on partial data is worthless."""
    rows = []
    latest = _latest_per_key(_run_records(), key)
    by_id, by_file = _health_index()

    for k, record in sorted(latest.items()):
        # Reuse provenance (cost-reduction 3.3/6.2): an audit row must say when
        # the plan behind it was adapted from another key's approved plan.
        try:
            import plan_state
            reused = plan_state.get(k).get("reused_from", "") or ""
        except Exception:
            reused = ""
        # SDD 3.3: close the requirement end of the chain and render waivers —
        # an audit distinguishes "waived, with a signed reason" from "missing".
        req_by_sid, waivers = {}, {}
        try:
            import spec_store
            spec = spec_store.load(k)
            if spec:
                req_by_sid = {sc.get("id"): ", ".join(sc.get("requirement_refs") or [])
                              for sc in spec.get("scenarios", [])}
            waivers = spec_store.load_waivers(k)
        except Exception:
            pass
        contracts = _contracts(record)
        tests = (contracts.get("generate") or {}).get("tests") or []
        # Old/single-agent generate contracts did not stamp `repo` on each
        # test. One gate makes the owner unambiguous; with multiple gates we
        # deliberately leave it unknown rather than invent a cross-repo link.
        gate_repos = {g.get("test_repo") for g in record.get("gates", [])
                      if isinstance(g, dict) and g.get("test_repo")}
        inferred_repo = next(iter(gate_repos)) if len(gate_repos) == 1 else ""
        tests_by_scenario = {}
        for t in tests:
            if isinstance(t, dict):
                tests_by_scenario.setdefault(str(t.get("scenario_id") or ""), []).append(t)

        def _row(scenario, test):
            # Inference applies only to a generated test. A scenario with no
            # test is deliberately an uncovered row and must not inherit the
            # run's successful gate as if it had been committed.
            repo = (test or {}).get("repo") or (inferred_repo if test else "")
            gate = _gate_for(record, repo) if repo else {}
            file = (test or {}).get("file") or ""
            health = by_id.get(f"{repo}::{file}::{(test or {}).get('name', '')}") \
                or by_file.get(file) or {}
            return {
                "key": k,
                "scenario_id": (scenario or {}).get("id", ""),
                "scenario_title": (scenario or {}).get("title", ""),
                "behavior_ref": (scenario or {}).get("behavior_ref", ""),
                "file": file,
                "test_repo": repo,
                "action": (test or {}).get("action", ""),
                "gate_status": gate.get("status", ""),
                "commit": (gate.get("commit") or "")[:9],
                "run_id": record.get("run_id", ""),
                "ci_runs": health.get("runs", ""),
                "ci_failures": health.get("failures", ""),
                "ci_last": health.get("last_status", ""),
                "reused_from": reused,
                "requirements": req_by_sid.get((scenario or {}).get("id"), ""),
                "waiver": _waiver_cell(waivers.get((scenario or {}).get("id"))),
            }

        claimed = set()
        for s in _scenarios_for(k):
            sid = str(s.get("id") or "")
            matched = tests_by_scenario.get(sid, [])
            if matched:
                for t in matched:
                    rows.append(_row(s, t))
                    claimed.add(id(t))
            else:
                # A scenario with NO test is the most important row on an audit —
                # it is a requirement someone approved that nothing exercises yet.
                rows.append(_row(s, None))
        for t in tests:
            if id(t) not in claimed:
                rows.append(_row(None, t))    # PR-path / unplanned tests
    return rows


FIELDS = ["key", "scenario_id", "scenario_title", "behavior_ref", "file",
          "test_repo", "action", "gate_status", "commit", "run_id",
          "ci_runs", "ci_failures", "ci_last", "reused_from",
          "requirements", "waiver"]


def _waiver_cell(w):
    if not w:
        return ""
    tag = "waived (EXPIRED)" if w.get("expired") else "waived"
    return f"{tag}: {w.get('reason', '')} ({w.get('by', '?')})"


def to_csv(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    rows = build(args[0] if args else None)
    if "--csv" in sys.argv:
        print(to_csv(rows), end="")
    else:
        if not rows:
            print("no traceable runs yet")
        for r in rows:
            gap = "" if r["file"] else "   <- APPROVED SCENARIO WITH NO TEST"
            print(f"{r['key']:<20} {r['scenario_id']:<16} "
                  f"{(r['file'] or '-'):<52} {r['gate_status'] or '-':<10} "
                  f"{str(r['ci_last'] or '-'):<7}{gap}")

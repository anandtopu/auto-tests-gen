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
            _st = plan_state.get(k)
            reused = _st.get("reused_from", "") or ""
            # The row label depends on this. The contract snapshot is written
            # when the plan is DRAFTED, not when it is approved, so a row from
            # it says nothing about approval on its own.
            plan_status = _st.get("status", "") or ""
        except Exception:
            reused = ""
            plan_status = ""
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
                "plan_status": plan_status,
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
          "requirements", "waiver", "plan_status"]


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


def _approved(row):
    """Only a signed-off plan makes its scenarios approved requirements."""
    return (row.get("plan_status") or "") == "approved"


def _waived(row):
    """Is this scenario's absence deliberate and still valid?

    The gate treats an approved scenario as satisfied if it is covered OR
    carries a non-expired waiver (engine/gate/spec_check.py). The text report
    computed the waiver per row and never mentioned it, so a scenario somebody
    had explicitly and validly waived was still counted in the loudest line an
    audit reads -- APPROVED SCENARIO WITH NO TEST. That is a report
    contradicting the gate, and a false alarm in an audit trains people to skim
    the real ones.

    EXPIRED waivers deliberately do NOT count. An expired waiver is a decision
    that has run out, which is the case most worth surfacing, not least.
    """
    cell = row.get("waiver") or ""
    return cell.startswith("waived") and "EXPIRED" not in cell


def _uncovered_label(row):
    """What to call a scenario with no test.

    The contract snapshot this matrix reads is written when a plan is DRAFTED
    (pipeline.sh plan stops after testplan and marks it draft), so labelling
    every uncovered row "APPROVED SCENARIO" asserted a sign-off that may never
    have happened -- on the one artifact regulated teams read. Measured on this
    estate: PROJ-301 is status=draft with no approval in history, and the table
    called all three of its scenarios approved.
    """
    if _approved(row):
        # Same correction one level down: the gate accepts a validly waived
        # scenario, so shouting APPROVED SCENARIO WITH NO TEST at it makes the
        # row disagree with the component that decides whether code ships. An
        # EXPIRED waiver is still a gap -- more urgent than most, since someone
        # decided this was temporary and the clock ran out.
        if _waived(row):
            return "approved, no test, WAIVED (the gate accepts this)"
        return "APPROVED SCENARIO WITH NO TEST"
    status = (row.get("plan_status") or "unknown").upper()
    return f"{status} SCENARIO WITH NO TEST (plan not approved)"


def render_text(rows):
    """The human table. Returns lines; the CLI prints them.

    Extracted from __main__ so the empty case, the all-covered case and the
    header can be tested -- the audit summary is the part most worth pinning
    and it was unreachable from a test while it lived inside the entry block.
    """
    out = []
    if not rows:
        out.append("no traceable runs yet")
        return out
    # The CSV has carried a header all along; the text form did not, so an
    # audit-facing report showed five unlabelled columns and the reader had to
    # guess whether "committed" was the gate or the CI. Its siblings
    # (`qa.py reviews`, `qa.py coverage`) both label their columns.
    out.append(f"{'key':<20} {'scenario':<16} {'test file':<52} "
               f"{'gate':<10} {'ci':<7}")
    for r in rows:
        gap = "" if r["file"] else f"   <- {_uncovered_label(r)}"
        out.append(f"{r['key']:<20} {r['scenario_id']:<16} "
                   f"{(r['file'] or '-'):<52} {r['gate_status'] or '-':<10} "
                   f"{str(r['ci_last'] or '-'):<7}{gap}")
    # The count an audit actually opens with. Per-row markers make it findable;
    # they do not make it countable, and "how many approved scenarios have no
    # test?" is the question being asked.
    uncovered = [r for r in rows if not r["file"]]
    out.append("")
    if uncovered:
        # A validly waived scenario is not an unexplained gap: the gate accepts
        # it, so counting it here would make this report disagree with the
        # component that decides whether code ships. Counted and named on its
        # own line instead of being dropped -- a waiver is a decision somebody
        # signed, and an audit should see how many are in force.
        waived = [r for r in uncovered if _approved(r) and _waived(r)]
        approved = [r for r in uncovered if _approved(r) and not _waived(r)]
        other = [r for r in uncovered if not _approved(r)]
        if waived:
            out.append(f"{len(waived)} of {len(rows)} row(s): approved scenario "
                       f"with no test, WAIVED (not a gap; the gate accepts "
                       f"these) -- " +
                       ", ".join(sorted(r["scenario_id"] or r["key"]
                                        for r in waived)))
        if approved:
            out.append(f"{len(approved)} of {len(rows)} row(s): APPROVED SCENARIO "
                       f"WITH NO TEST -- " +
                       ", ".join(sorted(r["scenario_id"] or r["key"] for r in approved)))
        if other:
            # Said separately and NOT called approved: these scenarios exist in
            # a plan nobody has signed off, so an audit must not read them as
            # approved requirements going unexercised.
            out.append(f"{len(other)} of {len(rows)} row(s): scenario with no test "
                       f"in a plan that is NOT approved -- " +
                       ", ".join(sorted(f"{r['scenario_id'] or r['key']}"
                                        f"({r.get('plan_status') or 'unknown'})"
                                        for r in other)))
    else:
        out.append(f"{len(rows)} row(s), every approved scenario has a test")
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    rows = build(args[0] if args else None)
    if "--csv" in sys.argv:
        print(to_csv(rows), end="")
    else:
        for line in render_text(rows):
            print(line)

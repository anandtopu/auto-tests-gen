#!/usr/bin/env python3
"""Team status report — what was completed, what's queued, and delivery metrics.

Aggregates the platform's existing state (run records, review board, work
queue, catalog, CI health, coverage gaps) into one shareable report for
standups / release readouts. Markdown is the source format; HTML/DOCX/PDF
reuse export_plan's generic renderers. Filters: --days N (rolling window),
--release X (only keys tracked against that fixVersion).

CLI: bin/qa.py report / make report. Served: GET /api/report on the dashboard.
"""
import glob, json, pathlib, sys, time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import coverage_gaps, export_plan, review_state, test_health, work_queue

STATE_FILES = ("reviews.json", "queue.json", "hooks-seen.json")
FORMATS = export_plan.FORMATS
CONTENT_TYPES = export_plan.CONTENT_TYPES
PENDING = ("pending_review", "in_review")


def _runs():
    out = []
    for f in glob.glob(str(ROOT / "reports/runs/*.json")):
        if pathlib.Path(f).name in STATE_FILES:
            continue
        try:
            out.append(json.load(open(f, encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    out.sort(key=lambda r: r.get("ts", 0), reverse=True)
    return out


def _catalog():
    out = []
    for f in sorted(glob.glob(str(ROOT / "catalog/*.jsonl"))):
        if pathlib.Path(f).name == "catalog.sample.jsonl":
            continue
        for line in open(f, encoding="utf-8"):
            if line.strip():
                out.append(json.loads(line))
    return out


def build(days=None, release=None):
    """Structured report data. days=None means all time; release filters runs
    and review entries to keys tracked against that version."""
    now = time.time()
    cutoff = now - days * 86400 if days else 0
    reviews = review_state.load()
    rel_of = lambda key: reviews.get(key, {}).get("release", "")
    runs = [r for r in _runs() if r.get("ts", 0) >= cutoff
            and (not release or rel_of(r["trigger"]["key"]) == release)]

    completed, quarantined = [], []
    n_tests, n_created, n_updated, repair_loops = 0, 0, 0, []
    for r in runs:
        key = r["trigger"]["key"]
        contracts = {p["name"]: p["contract"] for p in r.get("phases", [])}
        for t in contracts.get("generate", {}).get("tests", []):
            n_tests += 1
            if t.get("action") == "updated":
                n_updated += 1
            else:
                n_created += 1
        v = contracts.get("validate", {})
        if v.get("repair_loops") is not None:
            repair_loops.append(v["repair_loops"])
        row = {"key": key, "type": r["trigger"]["type"], "ts": r.get("ts", 0),
               "release": rel_of(key),
               "review": reviews.get(key, {}).get("status", ""),
               "gates": [{"repo": g["test_repo"], "status": g["status"],
                          "commit": (g.get("commit") or "")[:7]}
                         for g in r.get("gates", [])]}
        if r.get("overall") == "committed":
            completed.append(row)
        elif r.get("overall") == "quarantined":
            quarantined.append(row)

    pending = sorted(
        ({"key": k, "status": e["status"], "release": e.get("release", ""),
          "age_days": (now - e.get("updated", now)) / 86400}
         for k, e in reviews.items() if e.get("status") in PENDING
         and (not release or e.get("release", "") == release)),
        key=lambda p: -p["age_days"])
    approved = [k for k, e in reviews.items() if e.get("status") == "approved"
                and e.get("updated", 0) >= cutoff
                and (not release or e.get("release", "") == release)]

    by_release = {}
    for row in completed + quarantined:
        b = by_release.setdefault(row["release"] or "(none)",
                                  {"committed": 0, "quarantined": 0, "pending": 0})
        b["committed" if row in completed else "quarantined"] += 1
    for p in pending:
        by_release.setdefault(p["release"] or "(none)",
                              {"committed": 0, "quarantined": 0, "pending": 0})["pending"] += 1

    per_day = {}
    for r in runs:
        d = time.strftime("%Y-%m-%d", time.localtime(r.get("ts", 0)))
        per_day[d] = per_day.get(d, 0) + 1

    catalog = _catalog()
    by_status = {}
    for e in catalog:
        s = e["mapping"]["status"]
        by_status[s] = by_status.get(s, 0) + 1
    health = test_health.load()
    flaky = sorted(t for t, h in health.items() if h.get("flaky"))
    gaps = sum(len(v["uncovered"]) for v in coverage_gaps.compute().values())

    return {"generated": now, "days": days, "release": release,
            "totals": {"runs": len(runs), "committed": len(completed),
                       "quarantined": len(quarantined),
                       "no_changes": len(runs) - len(completed) - len(quarantined),
                       "tests_generated": n_tests, "tests_created": n_created,
                       "tests_updated": n_updated,
                       "avg_repair_loops": (round(sum(repair_loops) / len(repair_loops), 2)
                                            if repair_loops else 0)},
            "completed": completed, "quarantined": quarantined,
            "pending_review": pending, "approved_in_period": sorted(approved),
            "queue": work_queue.load(), "by_release": by_release,
            "per_day": dict(sorted(per_day.items(), reverse=True)),
            "catalog": {"total": len(catalog), "by_status": by_status,
                        "coverage_gaps": gaps, "flaky": flaky}}


def to_markdown(days=None, release=None):
    d = build(days, release)
    t = d["totals"]
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(d["generated"]))
    period = (f"last {days} day(s)" if days else "all time") + \
             (f" · release {release}" if release else "")
    q_by = {}
    for i in d["queue"]:
        q_by[i["status"]] = q_by.get(i["status"], 0) + 1
    rate = f"{t['committed'] / t['runs']:.0%}" if t["runs"] else "n/a"
    L = [f"# QA Team Report — {when}", "", f"Period: **{period}**", "",
         "## Summary", "",
         "| metric | value |", "| --- | --- |",
         f"| Pipeline runs | {t['runs']} |",
         f"| Committed (tests pushed) | {t['committed']} ({rate}) |",
         f"| Quarantined by the gate | {t['quarantined']} |",
         f"| No changes needed | {t['no_changes']} |",
         f"| Tests generated | {t['tests_generated']} "
         f"({t['tests_created']} new, {t['tests_updated']} extended existing) |",
         f"| Avg repair loops per run | {t['avg_repair_loops']} |",
         f"| Awaiting team review | {len(d['pending_review'])} |",
         f"| Approved in period | {len(d['approved_in_period'])} |",
         f"| Queue backlog | {q_by.get('queued', 0)} queued, "
         f"{q_by.get('running', 0)} running, {q_by.get('failed', 0)} failed |"]

    L += ["", "## Completed work", ""]
    if d["completed"]:
        L += ["| key | type | when | release | committed to | review |",
              "| --- | --- | --- | --- | --- | --- |"]
        for r in d["completed"]:
            when_r = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"]))
            repos = ", ".join(f"`{g['repo']}@{g['commit']}`"
                              for g in r["gates"] if g["status"] == "committed")
            L.append(f"| {r['key']} | {r['type']} | {when_r} | {r['release'] or '—'} "
                     f"| {repos} | {r['review'] or '—'} |")
    else:
        L.append("Nothing committed in this period.")

    if d["quarantined"]:
        L += ["", "## Quarantined runs (needs engineer attention)", "",
              "| key | type | when | release |", "| --- | --- | --- | --- |"]
        for r in d["quarantined"]:
            when_r = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"]))
            L.append(f"| {r['key']} | {r['type']} | {when_r} | {r['release'] or '—'} |")

    L += ["", "## Awaiting team review", ""]
    if d["pending_review"]:
        L += ["| key | status | release | waiting |", "| --- | --- | --- | --- |"]
        for p in d["pending_review"]:
            L.append(f"| {p['key']} | {p['status']} | {p['release'] or '—'} "
                     f"| {p['age_days']:.1f} day(s) |")
    else:
        L.append("Review board is clear.")

    L += ["", "## Work queue", ""]
    if d["queue"]:
        L += ["| id | status | type | key | release | requested by |",
              "| --- | --- | --- | --- | --- | --- |"]
        for i in d["queue"]:
            L.append(f"| {i['id']} | {i['status']} | {i['mode']} "
                     f"| {work_queue.key_of(i)} | {i.get('release') or '—'} "
                     f"| {i.get('requested_by') or '—'} |")
    else:
        L.append("Queue is empty.")

    if d["by_release"]:
        L += ["", "## By release", "",
              "| release | committed | quarantined | awaiting review |",
              "| --- | --- | --- | --- |"]
        for rel in sorted(d["by_release"]):
            b = d["by_release"][rel]
            L.append(f"| {rel} | {b['committed']} | {b['quarantined']} | {b['pending']} |")

    if d["per_day"]:
        L += ["", "## Throughput (runs per day)", "", "| day | runs |", "| --- | --- |"]
        L += [f"| {day} | {n} |" for day, n in list(d["per_day"].items())[:14]]

    c = d["catalog"]
    st = c["by_status"]
    L += ["", "## Estate health", "",
          f"- **{c['total']}** tests cataloged: {st.get('auto', 0)} auto-mapped, "
          f"{st.get('confirmed', 0)} confirmed, {st.get('needs_review', 0)} need review, "
          f"{st.get('orphan', 0)} orphan",
          f"- **{c['coverage_gaps']}** uncovered surface(s) (routes/endpoints with no "
          f"mapped test — see `make gaps`)",
          f"- Flaky tests from CI ingest: "
          + (", ".join(f"`{f}`" for f in c["flaky"]) if c["flaky"] else "none"), ""]
    return "\n".join(L)


def render(fmt="md", days=None, release=None):
    """Return (bytes, content_type) for any supported format."""
    if fmt not in FORMATS:
        sys.exit(f"format must be one of: {', '.join(FORMATS)}")
    md = to_markdown(days, release)
    if fmt == "md":
        data = md.encode("utf-8")
    elif fmt == "html":
        data = export_plan.md_to_html_doc(md, "QA Team Report").encode("utf-8")
    elif fmt == "docx":
        data = export_plan.md_to_docx(md)
    else:
        data = export_plan.md_to_pdf(md)
    return data, CONTENT_TYPES[fmt]


def export(fmt="md", days=None, release=None, out=None):
    data, _ = render(fmt, days, release)
    if out is None:
        stamp = time.strftime("%Y-%m-%d")
        suffix = f"-{release}" if release else ""
        out = ROOT / f"reports/exports/team-report-{stamp}{suffix}.{fmt}"
    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out

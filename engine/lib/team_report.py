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
import run_progress
import app_paths

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
    for f in app_paths.catalog_files(ROOT):
        for line in open(f, encoding="utf-8"):
            if line.strip():
                out.append(json.loads(line))
    return out


def build(days=None, release=None):
    """Structured report data. days=None means all time; release filters runs
    and review entries to keys tracked against that version."""
    now = time.time()
    cutoff = now - days * 86400 if days else 0
    # Three reads below (`rel_of`, `pending`, `approved`) called .get() straight
    # on a review entry, so one wrong-shaped value in reviews.json raised
    # AttributeError out of build() and took down `make report`, GET
    # /api/report AND the emailed report together — found by a defensive test
    # written for the release filter, not by the suite. The SHAPE guarantee now
    # lives in review_state (nine call sites had the same hole; fixing this one
    # would have left the others), and this report names what was skipped
    # because a silently smaller board reads as a smaller backlog.
    reviews, malformed_reviews = review_state.load_with_issues()
    rel_of = lambda key: reviews.get(key, {}).get("release", "")
    runs = [r for r in _runs() if r.get("ts", 0) >= cutoff
            and (not release or rel_of(r["trigger"]["key"]) == release)]

    # Everything below this line has to respect `release` too. Cost and the
    # work queue did not, so a release readout mixed scoped and estate-wide
    # rows in one Summary table. A non-dict review entry is skipped rather than
    # crashing the report — the same defensive read the reviewer rota needs.
    release_keys = ({k for k, e in reviews.items()
                     if e.get("release", "") == release} if release else None)
    queue_all = work_queue.load()
    queue = ([i for i in queue_all if i.get("release", "") == release]
             if release else queue_all)
    # Is this release known to the estate AT ALL? work_queue only writes a
    # release into review_state once a run SUCCEEDS, so a release whose tickets
    # are merely queued has no board entry — asking only the board would flag a
    # perfectly real release as a typo.
    release_known = bool(release_keys) or any(
        i.get("release", "") == release for i in queue_all) if release else True

    completed, quarantined, review_refused = [], [], []
    n_tests, n_created, n_updated, repair_loops = 0, 0, 0, []
    # Counted, not silently dropped: a denominator that shrinks with no
    # explanation is its own lie (the scorecard's commit-rate precedent).
    unmeasured_loops = [0]
    for r in runs:
        key = r["trigger"]["key"]
        contracts = {p["name"]: p["contract"] for p in r.get("phases", [])}
        for t in run_progress.dict_rows(contracts.get("generate", {}).get("tests")):
            n_tests += 1
            if t.get("action") == "updated":
                n_updated += 1
            else:
                n_created += 1
        v = contracts.get("validate", {})
        if v.get("repair_loops") is not None:
            import phase_provenance
            if phase_provenance.of("validate", record=r) == phase_provenance.MEASURED:
                repair_loops.append(v["repair_loops"])
            else:
                unmeasured_loops[0] += 1
        row = {"key": key, "type": r["trigger"]["type"], "ts": r.get("ts", 0),
               "release": rel_of(key),
               "review": reviews.get(key, {}).get("status", ""),
               "review_fixes": ((r.get("review_delivery") or {}).get("fixes") or [])[:4],
               "gates": [{"repo": g["test_repo"], "status": g["status"],
                          "commit": (g.get("commit") or "")[:7]}
                         for g in r.get("gates", [])]}
        if r.get("overall") == "committed":
            completed.append(row)
        elif r.get("overall") == "quarantined":
            quarantined.append(row)
        elif r.get("overall") == "review_refused":
            review_refused.append(row)

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
    for row in completed + quarantined + review_refused:
        b = by_release.setdefault(row["release"] or "(none)",
                                  {"committed": 0, "quarantined": 0,
                                   "review_refused": 0, "pending": 0})
        bucket = ("committed" if row in completed else
                  "quarantined" if row in quarantined else "review_refused")
        b[bucket] += 1
    for p in pending:
        by_release.setdefault(p["release"] or "(none)",
                              {"committed": 0, "quarantined": 0,
                               "review_refused": 0, "pending": 0})["pending"] += 1

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
    # Observed repos only. An unharvestable repo contributes no `uncovered`
    # entries, so summing it in would quietly fold "we could not look" into a
    # total a lead reads as "this is how much is uncovered".
    _surface = coverage_gaps.compute()
    gaps = sum(len(v["uncovered"]) for v in _surface.values()
               if coverage_gaps.observed(v))
    gaps_unchecked = sorted(n for n, v in _surface.items()
                            if not coverage_gaps.observed(v))

    return {"generated": now, "days": days, "release": release,
            "totals": {"runs": len(runs), "committed": len(completed),
                       "quarantined": len(quarantined),
                       "review_refused": len(review_refused),
                       "no_changes": (len(runs) - len(completed) - len(quarantined)
                                      - len(review_refused)),
                       "tests_generated": n_tests, "tests_created": n_created,
                       "tests_updated": n_updated,
                       "avg_repair_loops": (round(sum(repair_loops) / len(repair_loops), 2)
                                            if repair_loops else None),
                       "unmeasured_repair_loop_runs": unmeasured_loops[0]},
            "completed": completed, "quarantined": quarantined,
            "review_refused": review_refused,
            "pending_review": pending, "approved_in_period": sorted(approved),
            "queue": queue, "release_known": release_known,
            "malformed_reviews": malformed_reviews,
            "by_release": by_release,
            "per_day": dict(sorted(per_day.items(), reverse=True)),
            "catalog": {"total": len(catalog), "by_status": by_status,
                        "coverage_gaps": gaps,
                        "coverage_unchecked": gaps_unchecked, "flaky": flaky},
            "cost": _cost_line(days, release_keys)}


def _cost_line(days, keys=None):
    """One honest cost summary line (cost-reduction 1.2). A simulated figure is
    labelled so it can never be quoted as a measured dollar.

    `keys` scopes the figure to a release's keys. Without it this line ignored
    the report's release filter entirely, so a per-release readout printed the
    WHOLE estate's spend in the same Summary table as correctly-filtered zeros:
    measured, "Pipeline runs | 0" directly above "LLM spend | ~$13.0000 across
    617 run(s)". Labelling the number was not enough — it sat beside scoped
    rows, and this report is emailed and pasted into status updates, where the
    figure travels and any caveat does not.
    """
    try:
        import cost_report
        rep = cost_report.report(days, keys=keys)
        if not rep["runs"] or rep["simulated_share"] is None:
            return ""
        label = ("simulated" if rep["simulated_share"] == 1.0
                 else "measured" if rep["simulated_share"] == 0.0
                 else f"{int(rep['simulated_share'] * 100)}% simulated")
        # The `~` on the NUMBER, not only a parenthetical after it. This line
        # printed `$12.0000 ... (99% simulated)` in a report that gets pasted
        # into a status update, where the figure travels and the parenthetical
        # does not. Matches cost_report's headline and the Overview tile; the
        # docstring above has always promised it.
        tilde = "~" if rep["simulated_share"] else ""
        return (f"{tilde}${rep['total_cost_usd']:.4f} across {rep['runs']} "
                f"run(s) ({label})")
    except Exception:
        return ""


def _repair_loop_cell(totals):
    """Averaged over MEASURED runs only, saying so when there were none.

    A mock validate phase emits a constant, so averaging it reports the stub.
    `n/a` names how many runs were excluded -- a denominator that shrinks in
    silence is the failure this rule exists to prevent.
    """
    avg = totals.get("avg_repair_loops")
    if avg is not None:
        return str(avg)
    skipped = totals.get("unmeasured_repair_loop_runs") or 0
    extra = f" ({skipped} simulated run(s) excluded)" if skipped else ""
    return f"n/a - no run with a MEASURED validate phase{extra}"


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
    L = [f"# QA Team Report — {when}", "", f"Period: **{period}**", ""]
    if d.get("malformed_reviews"):
        L += [f"> **{len(d['malformed_reviews'])} review-board entr"
              f"{'y is' if len(d['malformed_reviews']) == 1 else 'ies are'} "
              f"unreadable** and excluded from every count below "
              f"({', '.join('`%s`' % k for k in d['malformed_reviews'][:5])}"
              f"{', …' if len(d['malformed_reviews']) > 5 else ''}). The "
              f"review numbers are a floor, not a total.", ""]
    if not d.get("release_known", True):
        # C13 at the top of the document, because every zero below it is the
        # filter matching nothing rather than a finding about the release.
        L += [f"> **No work in this estate is tracked against release "
              f"`{release}`.** The zeros below are that filter matching "
              f"nothing — not a statement that the release is clear. Check the "
              f"value (`make reviews` lists the releases in use).", ""]
    L += ["## Summary", "",
         "| metric | value |", "| --- | --- |",
         f"| Pipeline runs | {t['runs']} |",
         f"| Committed (tests pushed) | {t['committed']} ({rate}) |",
         f"| Quarantined by the gate | {t['quarantined']} |",
         f"| Refused before the gate by required agent review | {t['review_refused']} |",
         f"| No changes needed | {t['no_changes']} |",
         f"| Tests generated | {t['tests_generated']} "
         f"({t['tests_created']} new, {t['tests_updated']} extended existing) |",
         *([f"| LLM spend | {d['cost']} |"] if d.get("cost") else []),
         f"| Avg repair loops per run | {_repair_loop_cell(t)} |",
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

    if d["review_refused"]:
        L += ["", "## Agent-review refusals (fix and re-run)", "",
              "| key | type | when | release | fixes |",
              "| --- | --- | --- | --- | --- |"]
        for r in d["review_refused"]:
            when_r = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"]))
            fixes = "; ".join(str(f).replace("|", "\\|") for f in r["review_fixes"])
            L.append(f"| {r['key']} | {r['type']} | {when_r} | {r['release'] or '—'} "
                     f"| {fixes or 'see run record'} |")

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
              "| release | committed | quarantined | review refused | awaiting review |",
              "| --- | --- | --- | --- | --- |"]
        for rel in sorted(d["by_release"]):
            b = d["by_release"][rel]
            L.append(f"| {rel} | {b['committed']} | {b['quarantined']} "
                     f"| {b['review_refused']} | {b['pending']} |")

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
          f"mapped test — see `make gaps`)"
          + (f"; **{len(c['coverage_unchecked'])} repo(s) NOT checked** "
             f"({', '.join(c['coverage_unchecked'])}) — that count excludes them"
             if c.get("coverage_unchecked") else ""),
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

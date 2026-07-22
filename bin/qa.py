#!/usr/bin/env python3
"""QA operations CLI — monitor runs, query the test-knowledge catalog, and manage
app-repo <-> test-repo mappings. All data comes from reports/runs/, catalog/*.jsonl,
and registry/repo-registry.yaml; mapping edits always regenerate the coverage map.

  bin/qa.py status   [-n 10]                    recent pipeline runs + gate outcomes
  bin/qa.py artifacts <KEY> [--full] [--all]    view generated plan/data/tests for a PR or story
  bin/qa.py coverage                            app-repo x test-repo coverage matrix
  bin/qa.py tests    [--app R] [--repo T] [--status S] [--layer L]
  bin/qa.py review                              pending mapping-review queue (all repos)
  bin/qa.py reviews                             team-review board for PRs / JIRA tickets
  bin/qa.py mark <KEY> <status> [--by] [--note] set team-review status
      statuses: pending_review | in_review | approved | changes_requested
  bin/qa.py release <KEY> <version>             set the target release version for a PR/ticket
      (JIRA keys get this automatically from the ticket's fixVersions)
  bin/qa.py export-plan <KEY> [--format md|html|docx|pdf] [--out FILE]
      export the ticket's generated test plan (+ scenarios, data, tests,
      validation, review/release status) for sharing outside Git
  bin/qa.py publish-plan <KEY> [--space QA] [--title T]
      one-way mirror the plan to a Confluence page (Knowledge port;
      mock adapter unless AIQE_MOCK=0 with CONFLUENCE_URL credentials)
  bin/qa.py attach-plan <KEY> [--format pdf|docx|md|html]
      export the plan and attach it to the JIRA ticket (Tracker port;
      mock adapter unless AIQE_MOCK=0)
  bin/qa.py gaps [--repo R]                     surface with NO test evidence (coverage gaps)
  bin/qa.py report [--days N] [--release X] [--format md|html|docx|pdf] [--out F]
                                                team status report: completed work, review
                                                backlog, queue, throughput, estate health
  bin/qa.py openhands                           live OpenHands agent conversations
                                                (fed by the receiver's webhook routes)
  bin/qa.py critic [-n 10] [--findings]         advisory test-quality scores per run
                                                (vacuous/duplicate/weak specs the gate
                                                cannot catch; never gates a commit)
  bin/qa.py plan show|list|edit|review|approve|request-changes|link <KEY>
                                                JIRA test-plan workflow: review, edit
                                                (--file), approve (--by), link to the
                                                ticket; then `make plan-tests KEY=...`
  bin/qa.py email report|run <RUN_ID>|digest [--days N] [--release X] [--to a@b,c@d]
                                                generate + send an email (team report,
                                                run summary, or review digest) via SMTP
  bin/qa.py ingest-results <junit.xml|jenkins.json>   CI results -> per-test health
      (pass rate / flakiness in catalog/health.json; Jenkins role 3)
  bin/qa.py sql "SELECT ..."                    query the SQLite catalog index (read-only)
  bin/qa.py prune [--keep 200]                  retention: delete the oldest run
      records + their diffs beyond --keep (never touches reviews/queue state)
  bin/qa.py run-inline "<pasted JIRA context>" [--key K] [--components a,b]
      [--labels x,y] [--repos r1,r2] [--type Story|Bug|Security] [--queue]
      run Workflow B from pasted text (no ticket needed); --queue enqueues
      instead of running immediately
  bin/qa.py apply-review <queue.csv>            apply QE decisions back into the catalog
  bin/qa.py map <test_id> --repos a,b|ORPHAN    set one mapping directly (confirmed)
"""
import argparse, csv, glob, json, pathlib, subprocess, sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine/lib"))
from registry import load_registry
import review_state


def load_catalog():
    entries = []
    for f in sorted(glob.glob(str(ROOT / "catalog/*.jsonl"))):
        if pathlib.Path(f).name == "catalog.sample.jsonl":
            continue
        for line in open(f, encoding="utf-8"):
            if line.strip():
                entries.append((f, json.loads(line)))
    return entries


def save_catalog(path, entries):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def regen_coverage():
    subprocess.run([sys.executable, str(ROOT / "catalog/bootstrap/regen_coverage.py")],
                   cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(ROOT / "bin/gen_agents_md.py")],
                   cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    subprocess.run([sys.executable, str(ROOT / "catalog/bootstrap/index_db.py")],
                   cwd=ROOT, check=True, stdout=subprocess.DEVNULL)


def _run_record_files():
    """reports/runs/*.json minus the state files that share the directory."""
    return [f for f in glob.glob(str(ROOT / "reports/runs/*.json"))
            if pathlib.Path(f).name not in ("reviews.json", "queue.json", "hooks-seen.json")]


def cmd_status(args):
    runs = []
    for f in _run_record_files():
        try:
            runs.append(json.load(open(f, encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            print(f"warning: skipping unreadable run record {f}", file=sys.stderr)
    runs.sort(key=lambda r: r.get("ts", 0), reverse=True)
    if not runs:
        print("no run records yet - run a pipeline (make demo-pr / demo-jira) first")
        return
    ICON = {"committed": "OK ", "no_changes": "-- ", "quarantined": "!! "}
    reviews = review_state.load()
    print(f"{'run_id':<18} {'trigger':<22} {'overall':<12} {'team review':<18} {'release':<10} gates")
    for r in runs[: args.n]:
        gates = ", ".join(
            f"{g['test_repo']}={g['status']}"
            + (f"@{g['commit'][:7]}" if g.get("commit") else "")
            + ("" if g["exit_code"] == 0 else f"(exit {g['exit_code']})")
            for g in r.get("gates", [])) or "-"
        key = r["trigger"]["key"]
        trig = f"{r['trigger']['type']}:{key}"
        e = reviews.get(key, {})
        rev = e.get("status") or "-"
        rel = e.get("release") or "-"
        print(f"{r['run_id']:<18} {trig:<22} {ICON.get(r['overall'], '') + r['overall']:<12} "
              f"{rev:<18} {rel:<10} {gates}")
    quarantined = [r for r in runs[: args.n] if r["overall"] == "quarantined"]
    if quarantined:
        print(f"\n{len(quarantined)} quarantined run(s) need attention - logs under reports/")
    pending = [k for k, v in reviews.items() if v.get("status") in ("pending_review", "in_review")]
    if pending:
        print(f"awaiting team review: {', '.join(sorted(pending))}   "
              f"(bin/qa.py mark <KEY> approved --by <name>)")


def cmd_coverage(args):
    reg = load_registry()
    sources = [s["name"] for s in reg["source_repositories"]]
    trepos = reg["test_repositories"]
    counts = {}          # (app_repo, test_repo) -> mapped test count
    for _, e in load_catalog():
        if e["mapping"]["status"] in ("confirmed", "auto"):
            for app in e["mapping"]["app_repos"]:
                counts[(app, e["test_repo"])] = counts.get((app, e["test_repo"]), 0) + 1
    w = max(len(s) for s in sources) + 2
    print(" " * w + "".join(f"{t['name']:<20}" for t in trepos))
    uncovered = []
    for s in sources:
        row = ""
        covered = False
        for t in trepos:
            n = counts.get((s, t["name"]), 0)
            in_covers = s in t.get("covers", [])
            cell = f"{n} tests" if n else ("covers" if in_covers else ".")
            covered = covered or n > 0 or in_covers
            row += f"{cell:<20}"
        print(f"{s:<{w}}{row}")
        if not covered:
            uncovered.append(s)
    if uncovered:
        print(f"\nWARNING - no E2E coverage mapped for: {', '.join(uncovered)}")
    empty = [t["name"] for t in trepos if not t.get("covers")]
    if empty:
        print(f"NOTE - test repos with empty coverage (run bootstrap?): {', '.join(empty)}")


def cmd_tests(args):
    import test_health
    health = test_health.load()
    shown = 0
    for _, e in load_catalog():
        m = e["mapping"]
        if args.app and args.app not in m["app_repos"]:
            continue
        if args.repo and e["test_repo"] != args.repo:
            continue
        if args.status and m["status"] != args.status:
            continue
        if args.layer and e["layer"] != args.layer:
            continue
        ev = e["evidence"]["endpoints"] or e["evidence"]["ui_routes"]
        h = health.get(e["test_id"], {})
        hcol = (f"pass={h['pass_rate']:.0%}" + ("(FLAKY)" if h.get("flaky") else "")
                if h else "-")
        print(f"{m['status']:<13} conf={m['confidence']:<5} {e['test_repo']:<18} "
              f"{e['title'][:40]:<42} -> {','.join(m['app_repos']) or '-':<18} "
              f"{hcol:<16} {(ev[0] if ev else '')}")
        shown += 1
    print(f"\n{shown} test(s)")


def _runs_for_key(key):
    runs = []
    for f in _run_record_files():
        try:
            r = json.load(open(f, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        k = r.get("trigger", {}).get("key", "")
        if key.lower() in (k.lower(), k.lower().replace("pr-", "")):
            runs.append(r)
    return sorted(runs, key=lambda r: r.get("ts", 0), reverse=True)


def cmd_artifacts(args):
    """Everything a run generated for one PR key or JIRA story, newest run first."""
    runs = _runs_for_key(args.key)
    if not runs:
        keys = sorted({json.load(open(f, encoding="utf-8"))["trigger"]["key"]
                       for f in _run_record_files()})
        sys.exit(f"no runs recorded for '{args.key}'. Known keys: {', '.join(keys) or 'none'}")
    for r in runs if args.all else runs[:1]:
        key = r["trigger"]["key"]
        rev = review_state.load().get(key, {})
        rev_note = ""
        if rev:
            rev_note = (f"  team-review={rev.get('status') or '-'}"
                        + (f" by {rev['reviewer']}" if rev.get("reviewer") else "")
                        + (f"  release={rev['release']}" if rev.get("release") else ""))
        print(f"=== run {r['run_id']}  ({r['trigger']['type']}:{key})  "
              f"overall={r['overall']}{rev_note} ===")
        contracts = {p["name"]: p["contract"] for p in r.get("phases", [])}

        plan = ROOT / f"testplans/{key}.md"
        if plan.exists():
            print(f"\nTest plan: testplans/{key}.md")
            if args.full:
                print("  | " + plan.read_text(encoding="utf-8").replace("\n", "\n  | "))
        for s in contracts.get("testplan", {}).get("scenarios", []):
            print(f"  scenario {s['id']}: {s['title']}  [{s['layer']}] -> {s['target_repo']}")

        data_dir = ROOT / f"testdata/{key}"
        if data_dir.exists():
            print("\nTest data:")
            for p in sorted(data_dir.rglob("*")):
                if p.is_file():
                    print(f"  testdata/{key}/{p.relative_to(data_dir).as_posix()}")

        gen = contracts.get("generate", {})
        if gen.get("tests"):
            print("\nGenerated tests:")
            for t in gen["tests"]:
                print(f"  {t.get('action', '?'):<8} {t['file']}   ({t.get('name', '')})")
        for q in gen.get("open_questions", []) or contracts.get("testplan", {}).get("open_questions", []):
            print(f"  open question: {q}")

        v = contracts.get("validate", {})
        if v:
            print(f"\nValidation: {v.get('passed', '?')} passed, {v.get('failed', '?')} failed, "
                  f"{v.get('repair_loops', '?')} repair loop(s)")

        print("\nCommits & diffs:")
        for g in r.get("gates", []):
            line = f"  {g['test_repo']}: {g['status']}"
            if g.get("commit"):
                line += f" @ {g['commit']}"
            if g.get("diff"):
                line += f"   diff: {g['diff']}"
            print(line)
            if args.full and g.get("diff") and (ROOT / g["diff"]).exists():
                print("  | " + (ROOT / g["diff"]).read_text(encoding="utf-8", errors="replace")
                      .replace("\n", "\n  | "))
        print()
    if not args.full:
        print("(--full prints the plan and the generated test code; --all shows every run)")


def cmd_reviews(args):
    """Team-review board: every tracked PR / JIRA key and where it stands."""
    data = review_state.load()
    if not data:
        print("no review states yet - a run that commits generated tests marks its key pending_review")
        return
    import time as _t
    order = {"pending_review": 0, "in_review": 1, "changes_requested": 2, "approved": 3}
    print(f"{'key':<22} {'status':<18} {'release':<10} {'reviewer':<14} {'updated':<17} note")
    for key, e in sorted(data.items(), key=lambda kv: (order.get(kv[1].get("status"), 9), kv[0])):
        ts = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(e.get("updated", 0)))
        print(f"{key:<22} {e.get('status') or '-':<18} {e.get('release') or '-':<10} "
              f"{e.get('reviewer') or '-':<14} {ts:<17} {e.get('note', '')[:50]}")
    pending = sum(1 for e in data.values() if e["status"] in ("pending_review", "in_review"))
    print(f"\n{pending} awaiting review. Transition: bin/qa.py mark <KEY> "
          f"{'|'.join(review_state.VALID)} [--by NAME] [--note TEXT]")


def cmd_mark(args):
    entry = review_state.set_status(args.key, args.status, args.by or "", args.note or "")
    print(f"{args.key} -> {entry['status']}"
          + (f" (by {args.by})" if args.by else ""))


def cmd_release(args):
    entry = review_state.set_release(args.key, args.version)
    print(f"{args.key} -> release {entry['release']}")


def cmd_export_plan(args):
    import export_plan
    path = export_plan.export(args.key, args.format, args.out)
    print(f"exported: {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")


def cmd_publish_plan(args):
    import export_plan
    print(export_plan.publish_to_confluence(args.key, args.space, args.title))


def cmd_attach_plan(args):
    import export_plan
    print(export_plan.attach_to_jira(args.key, args.format))


def cmd_gaps(args):
    import coverage_gaps
    print(coverage_gaps.to_markdown(args.repo))


def cmd_openhands(args):
    import openhands_events, time as _t
    rows = openhands_events.summary()
    if not rows:
        print("no OpenHands conversations recorded yet.\n"
              "Point the Agent Server's WebhookSpec.base_url at "
              "<receiver>/hooks/openhands — see docs/integrations/openhands.md")
        return
    print(f"{'conversation':<34} {'status':<12} {'events':>6} {'age':>7}  repo / key")
    for r in rows:
        age = f"{(_t.time() - r['updated']) / 60:.0f}m" if r["updated"] else "-"
        print(f"{r['conversation_id'][:34]:<34} {r['status']:<12} "
              f"{r['event_count']:>6} {age:>7}  {r['repo'] or r['key'] or '-'}"
              + (f"   ERROR: {r['error'][:50]}" if r["error"] else ""))


def cmd_critic(args):
    """Advisory critic scores per run. Nothing here gated anything — it is the
    quality signal the deterministic gate structurally cannot produce (§5.8.7)."""
    runs = []
    for f in _run_record_files():
        try:
            r = json.load(open(f, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if r.get("critic"):
            runs.append(r)
    runs.sort(key=lambda r: r.get("ts", 0), reverse=True)
    if not runs:
        print("no critic signal recorded yet.\n"
              "The critic runs after validate when critic.enabled is set in "
              "registry/org-config.yaml (AIQE_CRITIC=0 skips it for one run).")
        return
    print(f"{'run_id':<18} {'trigger':<22} {'score':>6} {'verdict':<9} {'noise':>9} findings")
    for r in runs[: args.n]:
        c = r["critic"]
        noise = (f"{c.get('noise_count', 0)}/{c['specs_reviewed']}"
                 if c.get("specs_reviewed") else str(c.get("noise_count", 0)))
        print(f"{r['run_id']:<18} {r['trigger']['type']}:{r['trigger']['key']:<18} "
              f"{c['score']:>6.2f} {c['verdict']:<9} {noise:>9} "
              f"{len(c.get('findings', []))}")
    if args.findings:
        print()
        for r in runs[: args.n]:
            c = r["critic"]
            if not c.get("findings"):
                continue
            print(f"--- {r['run_id']} ({r['trigger']['key']}) — {c.get('rationale', '')}")
            for f in c["findings"]:
                print(f"    [{f.get('severity', '?'):<4} {f.get('kind', '?'):<9}] "
                      f"{f.get('file', '?')}: {f.get('note', '')}")
    avg = sum(r["critic"]["score"] for r in runs) / len(runs)
    print(f"\naverage score {avg:.2f} over {len(runs)} scored run(s) — "
          "advisory only, never gates a commit")


def cmd_plan(args):
    """JIRA test-plan workflow: author -> review/edit -> approve -> link -> generate."""
    import plan_state
    key, act = args.key, args.action
    if act == "show":
        e = plan_state.get(key)
        p = plan_state.plan_path(key)
        if not p.exists():
            sys.exit(f"no test plan for {key} (create one: make plan KEY={key})")
        print(f"# status: {e.get('status', 'unknown')}"
              + (f" (by {e['by']})" if e.get("by") else "")
              + (f"  [linked: {e['linked']['ref']}]" if e.get("linked") else "")
              + (f"  [tests: run {e['generated_run']}]" if e.get("generated_run") else ""))
        print(p.read_text(encoding="utf-8"))
    elif act == "list":
        rows = plan_state.summary()
        if not rows:
            print("no test plans yet — create one with: make plan KEY=PROJ-123")
            return
        print(f"{'key':<16} {'status':<18} {'linked':<7} {'tests run':<18} note")
        for r in rows:
            print(f"{r['key']:<16} {r['status']:<18} "
                  f"{'yes' if r['linked'] else '-':<7} "
                  f"{str(r['generated_run'] or '-'):<18} {r['note']}")
    elif act == "edit":
        if not args.file:
            sys.exit("edit needs --file <path> with the new plan markdown")
        text = pathlib.Path(args.file).read_text(encoding="utf-8")
        e = plan_state.save_plan(key, text, args.by or "cli")
        print(f"{key}: plan updated -> status {e['status']} ({e['note']})")
    elif act in ("approve", "request-changes", "review"):
        status = {"approve": "approved", "request-changes": "changes_requested",
                  "review": "in_review"}[act]
        e = plan_state.set_status(key, status, args.by or "cli", args.note or "")
        print(f"{key}: test plan -> {e['status']}"
              + (f" (by {e['by']})" if e.get("by") else ""))
        if status == "approved":
            print(f"  next: link it to the ticket (make plan-link KEY={key}) "
                  f"and generate tests (make plan-tests KEY={key})")
    elif act == "link":
        plan_state.require_approved(key)       # only approved plans go to the ticket
        import export_plan
        ref = export_plan.attach_to_jira(key, args.format or "pdf")
        plan_state.mark_linked(key, ref, args.by or "cli")
        print(f"{key}: {ref}")
    else:
        sys.exit(f"unknown plan action: {act}")


def cmd_email(args):
    import email_notify
    if args.kind == "report":
        parts = email_notify.team_report_email(args.days, args.release)
    elif args.kind == "run":
        if not args.target:
            sys.exit("email run needs a RUN_ID: qa.py email run <RUN_ID>")
        parts = email_notify.run_summary(args.target)
    else:                                             # digest
        parts = email_notify.review_digest()
    print(email_notify.send(*parts, to=args.to))


def cmd_report(args):
    import team_report
    if args.out or args.format != "md":
        path = team_report.export(args.format, args.days, args.release,
                                  args.out and pathlib.Path(args.out))
        print(f"report written: "
              f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
    else:
        print(team_report.to_markdown(args.days, args.release))


def cmd_ingest_results(args):
    import test_health
    matched, unmatched = test_health.ingest(args.file)
    print(f"ingested: {matched} case(s) matched to catalog tests, {unmatched} unmatched")
    regen_coverage()                              # health flows into catalog.db + AGENTS.md


def cmd_sql(args):
    import sqlite3
    db = ROOT / "reports/catalog.db"
    if not db.exists():
        subprocess.run([sys.executable, str(ROOT / "catalog/bootstrap/index_db.py")],
                       cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        cur = con.execute(args.query)
        cols = [d[0] for d in cur.description or []]
        if cols:
            print(" | ".join(cols))
        for row in cur.fetchall():
            print(" | ".join("" if v is None else str(v) for v in row))
    finally:
        con.close()


def cmd_prune(args):
    runs_dir = pathlib.Path(args.dir) if args.dir else ROOT / "reports/runs"
    records = []
    for f in runs_dir.glob("*.json"):
        if f.name in ("reviews.json", "queue.json", "hooks-seen.json"):
            continue
        try:
            records.append((json.load(open(f, encoding="utf-8")).get("ts", 0), f))
        except (json.JSONDecodeError, OSError):
            continue
    records.sort(reverse=True)                      # newest first
    doomed = records[args.keep:]
    removed = 0
    for _, f in doomed:
        stem = f.stem                               # <RUN_ID>
        for d in runs_dir.glob(f"{stem}-*.diff"):
            d.unlink(missing_ok=True)
            removed += 1
        f.unlink(missing_ok=True)
        removed += 1
    print(f"kept {min(len(records), args.keep)} run record(s); "
          f"removed {len(doomed)} old record(s) ({removed} files)")


def cmd_run_inline(args):
    import os, subprocess
    import inline_ticket, work_queue
    csv_ = lambda s: [v.strip() for v in (s or "").split(",") if v.strip()]
    ticket = inline_ticket.build(args.text, args.key, csv_(args.components),
                                 csv_(args.labels), csv_(args.repos), args.type)
    path = inline_ticket.write(ticket)
    print(f"inline ticket: {ticket['key']} ({path.relative_to(ROOT)})")
    if args.queue:
        item, fresh = work_queue.add("jira", ticket["key"], release="",
                                     requested_by="inline", inline_file=path)
        print(f"{'queued' if fresh else 'already queued'}: {item['id']}  "
              f"(drain with: make queue-run)")
        return
    env = {**os.environ, "AIQE_INLINE_FILE": str(path)}
    env.setdefault("AIQE_MOCK", "1")
    r = subprocess.run([work_queue.bash_exe(), "engine/pipeline.sh", "jira", ticket["key"]],
                       cwd=ROOT, env=env, stdin=subprocess.DEVNULL)
    sys.exit(r.returncode)


def cmd_review(args):
    pending = [(f, e) for f, e in load_catalog()
               if e["mapping"]["status"] in ("needs_review", "orphan")]
    if not pending:
        print("review queue is empty")
        return
    for _, e in pending:
        m = e["mapping"]
        print(f"{m['status']:<13} conf={m['confidence']:<5} {e['test_id']}")
        print(f"              proposed={m['app_repos']} evidence={e['evidence']['endpoints'][:2]}")
    print(f"\n{len(pending)} pending. Export/edit CSVs in catalog/review/, "
          f"then: bin/qa.py apply-review <csv>")


def _set_mapping(entry, decision):
    """decision: 'ORPHAN' or ';'/','-separated app repo names."""
    if decision.strip().upper() == "ORPHAN":
        entry["mapping"].update(app_repos=[], services=[], status="orphan", confidence=0.0)
    else:
        repos = sorted(r.strip() for r in decision.replace(";", ",").split(",") if r.strip())
        reg_names = {s["name"] for s in load_registry()["source_repositories"]}
        unknown = [r for r in repos if r not in reg_names]
        if unknown:
            sys.exit(f"unknown source repo(s) {unknown} - register first (bin/onboard.sh)")
        entry["mapping"].update(app_repos=repos, services=repos,
                                status="confirmed", confidence=1.0)
        entry["mapping"]["method"] = sorted(set(entry["mapping"]["method"]) | {"human_review"})


def cmd_apply_review(args):
    decisions = {}
    with open(args.csv, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            decision = (row.get("decision(app_repos or ORPHAN)") or row.get("decision") or "").strip()
            if decision:
                decisions[row["test_id"]] = decision
    if not decisions:
        sys.exit("no filled-in decisions found in the CSV's decision column")
    applied = 0
    by_file = {}
    for f, e in load_catalog():
        by_file.setdefault(f, []).append(e)
    for f, entries in by_file.items():
        touched = False
        for e in entries:
            if e["test_id"] in decisions:
                _set_mapping(e, decisions.pop(e["test_id"]))
                applied += 1
                touched = True
        if touched:
            save_catalog(f, entries)
    for missed in decisions:
        print(f"warning: test_id not found in catalog: {missed}")
    regen_coverage()
    print(f"applied {applied} decision(s); coverage map regenerated")


def cmd_map(args):
    by_file = {}
    for f, e in load_catalog():
        by_file.setdefault(f, []).append(e)
    for f, entries in by_file.items():
        for e in entries:
            if e["test_id"] == args.test_id:
                _set_mapping(e, args.repos)
                save_catalog(f, entries)
                regen_coverage()
                print(f"mapped: {args.test_id} -> {e['mapping']['app_repos'] or 'ORPHAN'} "
                      f"(status={e['mapping']['status']})")
                return
    sys.exit(f"test_id not found: {args.test_id}  (list ids with: bin/qa.py tests)")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status"); s.add_argument("-n", type=int, default=10); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("coverage"); s.set_defaults(fn=cmd_coverage)
    s = sub.add_parser("tests")
    s.add_argument("--app"); s.add_argument("--repo"); s.add_argument("--status"); s.add_argument("--layer")
    s.set_defaults(fn=cmd_tests)
    s = sub.add_parser("artifacts")
    s.add_argument("key", help="PR key (PR-<repo>-<n> or <repo>-<n>) or JIRA key")
    s.add_argument("--full", action="store_true", help="print plan + generated test code")
    s.add_argument("--all", action="store_true", help="every run for the key, not just latest")
    s.set_defaults(fn=cmd_artifacts)
    s = sub.add_parser("reviews"); s.set_defaults(fn=cmd_reviews)
    s = sub.add_parser("mark")
    s.add_argument("key"); s.add_argument("status", choices=review_state.VALID)
    s.add_argument("--by"); s.add_argument("--note")
    s.set_defaults(fn=cmd_mark)
    s = sub.add_parser("release")
    s.add_argument("key"); s.add_argument("version")
    s.set_defaults(fn=cmd_release)
    s = sub.add_parser("export-plan")
    s.add_argument("key")
    s.add_argument("--format", choices=["md", "html", "docx", "pdf"], default="md")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_export_plan)
    s = sub.add_parser("publish-plan")
    s.add_argument("key"); s.add_argument("--space"); s.add_argument("--title")
    s.set_defaults(fn=cmd_publish_plan)
    s = sub.add_parser("attach-plan")
    s.add_argument("key")
    s.add_argument("--format", choices=["md", "html", "docx", "pdf"], default="pdf")
    s.set_defaults(fn=cmd_attach_plan)
    s = sub.add_parser("gaps"); s.add_argument("--repo"); s.set_defaults(fn=cmd_gaps)
    s = sub.add_parser("report")
    s.add_argument("--days", type=int); s.add_argument("--release")
    s.add_argument("--format", default="md", choices=["md", "html", "docx", "pdf"])
    s.add_argument("--out")
    s.set_defaults(fn=cmd_report)
    s = sub.add_parser("openhands"); s.set_defaults(fn=cmd_openhands)
    s = sub.add_parser("critic"); s.add_argument("-n", type=int, default=10)
    s.add_argument("--findings", action="store_true"); s.set_defaults(fn=cmd_critic)
    s = sub.add_parser("plan")
    s.add_argument("action", choices=["show", "list", "edit", "review", "approve",
                                      "request-changes", "link"])
    s.add_argument("key", nargs="?", default="")
    s.add_argument("--file"); s.add_argument("--by"); s.add_argument("--note")
    s.add_argument("--format", default="pdf")
    s.set_defaults(fn=cmd_plan)
    s = sub.add_parser("email")
    s.add_argument("kind", choices=["report", "run", "digest"])
    s.add_argument("target", nargs="?")               # RUN_ID for `email run`
    s.add_argument("--days", type=int); s.add_argument("--release"); s.add_argument("--to")
    s.set_defaults(fn=cmd_email)
    s = sub.add_parser("ingest-results"); s.add_argument("file")
    s.set_defaults(fn=cmd_ingest_results)
    s = sub.add_parser("sql"); s.add_argument("query"); s.set_defaults(fn=cmd_sql)
    s = sub.add_parser("prune")
    s.add_argument("--keep", type=int, default=200)
    s.add_argument("--dir", help=argparse.SUPPRESS)   # test override
    s.set_defaults(fn=cmd_prune)
    s = sub.add_parser("run-inline")
    s.add_argument("text")
    s.add_argument("--key"); s.add_argument("--components"); s.add_argument("--labels")
    s.add_argument("--repos"); s.add_argument("--type", default="Story")
    s.add_argument("--queue", action="store_true")
    s.set_defaults(fn=cmd_run_inline)
    s = sub.add_parser("review"); s.set_defaults(fn=cmd_review)
    s = sub.add_parser("apply-review"); s.add_argument("csv"); s.set_defaults(fn=cmd_apply_review)
    s = sub.add_parser("map"); s.add_argument("test_id"); s.add_argument("--repos", required=True)
    s.set_defaults(fn=cmd_map)
    a = p.parse_args()
    a.fn(a)

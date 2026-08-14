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
  bin/qa.py trace [<KEY>]                       the full chain for a story/PR:
                                                plan -> tests -> gate -> review ->
                                                release, chronological
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
      records/diffs and artifact references beyond --keep producing runs
  bin/qa.py run-inline "<pasted JIRA context>"|--file F [--key K] [--components a,b]
      [--labels x,y] [--repos r1,r2] [--type Story|Bug|Security] [--queue]
      run Workflow B from pasted text (no ticket needed); --queue enqueues
      instead of running immediately
  bin/qa.py apply-review <queue.csv>            apply QE decisions back into the catalog
  bin/qa.py map <test_id> --repos a,b|ORPHAN    set one mapping directly (confirmed)
"""
import argparse, csv, glob, json, os, pathlib, subprocess, sys

sys.stdout.reconfigure(encoding="utf-8")
# stderr too, for the reason bin/repos.py already records: every refusal here
# goes through sys.exit(msg), which writes to stderr, and those messages carry
# em-dashes. Without this the CLI's REFUSALS -- the output an operator most
# needs to read -- are encoded with the locale codec, so `qa.py quarantine
# <unknown>` rendered `... 'no-such' — bin/qa.py sql ...` in a CI log.
sys.stderr.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine/lib"))
import app_paths                      # R12: mutable paths resolve here
from registry import load_registry
import review_state
import test_reviewer
import spend_history
import cost_statement


def _load_catalog_file(path):
    entries = []
    for line in open(path, encoding="utf-8"):
        if line.strip():
            entries.append(json.loads(line))
    return entries


def load_catalog():
    entries = []
    for f in app_paths.catalog_files(ROOT):
        entries.extend((f, e) for e in _load_catalog_file(f))
    return entries


def _write_catalog_atomic(path, entries):
    """Replace one JSONL shard without ever exposing a partial catalog.

    Build the complete payload before touching disk, then use the same-volume
    atomic replace used by the other durable state stores. Callers that perform
    a read-modify-write hold the shard lock around both halves of the operation.
    """
    import fs_lock
    path = pathlib.Path(path)
    payload = "".join(json.dumps(e) + "\n" for e in entries)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
        fs_lock.replace_atomic(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def save_catalog(path, entries):
    import fs_lock
    with fs_lock.lock(path):
        _write_catalog_atomic(path, entries)


def _mutate_catalog_entry(test_id, mutate):
    """Atomically mutate one catalog row, returning its post-mutation value.

    Mapping and quarantine are human decisions. Loading before taking the lock
    lets simultaneous jobs overwrite one another even if the eventual file
    replace is atomic, so the lock covers the full transaction.
    """
    import fs_lock
    for f in app_paths.catalog_files(ROOT):
        with fs_lock.lock(f):
            entries = _load_catalog_file(f)
            for e in entries:
                if e["test_id"] == test_id:
                    mutate(e)
                    _write_catalog_atomic(f, entries)
                    return e
    return None


def regen_coverage():
    # Hold the registry lock while the child rewrites covers[] — otherwise this
    # races a dashboard/CLI repo mutation's own read-modify-write (repo_admin runs
    # the same script while holding this lock).
    import fs_lock
    with fs_lock.lock(app_paths.registry_file(ROOT)):
        subprocess.run([sys.executable, str(ROOT / "catalog/bootstrap/regen_coverage.py")],
                       cwd=ROOT, check=True, stdin=subprocess.DEVNULL)
    subprocess.run([sys.executable, str(ROOT / "bin/gen_agents_md.py")],
                   cwd=ROOT, check=True, stdout=subprocess.DEVNULL,
                   stdin=subprocess.DEVNULL)
    subprocess.run([sys.executable, str(ROOT / "catalog/bootstrap/index_db.py")],
                   cwd=ROOT, check=True, stdout=subprocess.DEVNULL,
                   stdin=subprocess.DEVNULL)


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
    ICON = {"committed": "OK ", "no_changes": "-- ", "quarantined": "!! ",
            "review_refused": "RV "}
    reviews = review_state.load()
    with_cost = getattr(args, "cost", False)
    spend_by_run = {}
    if with_cost:
        for row in spend_history.spend_rows():
            spend_by_run.setdefault(row["run_id"], []).append(row)
    cost_col = f" {'cost':<9}" if with_cost else ""
    print(f"{'run_id':<18} {'trigger':<22} {'overall':<18} {'team review':<18} {'release':<10}{cost_col} gates")
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
        cost_cell = ""
        if with_cost:
            spends = spend_by_run.get(str(r.get("run_id") or ""), [])
            tot = sum(s.get("cost_usd") or 0 for s in spends)
            sim = any(s.get("simulated") for s in spends)
            # `~` marks a figure containing simulated components — a simulated
            # number must never read as a measured dollar.
            cost_cell = f" {('~' if sim else '') + f'${tot:.4f}' if spends else '-':<9}"
        print(f"{r['run_id']:<18} {trig:<22} {ICON.get(r['overall'], '') + r['overall']:<18} "
              f"{rev:<18} {rel:<10}{cost_cell} {gates}")
    quarantined = [r for r in runs[: args.n] if r["overall"] == "quarantined"]
    if quarantined:
        print(f"\n{len(quarantined)} quarantined run(s) need attention - logs under reports/")
    refused = [r for r in runs[: args.n] if r["overall"] == "review_refused"]
    if refused:
        print(f"\n{len(refused)} run(s) refused before the gate - fix the agent-review findings")
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
    all_spend = spend_history.spend_rows()
    spend_by_run = {}
    for row in all_spend:
        spend_by_run.setdefault(row["run_id"], []).append(row)
    runs = _runs_for_key(args.key)
    known_ids = {str(run.get("run_id") or "") for run in runs}
    for run_id, rows in spend_by_run.items():
        row = rows[0]
        key = row["key"]
        if (run_id not in known_ids
                and args.key.lower() in (key.lower(), key.lower().replace("pr-", ""))):
            runs.append({"run_id": run_id, "ts": row["ts"],
                         "trigger": {"type": row["mode"], "key": key},
                         "overall": "aborted", "phases": [], "gates": [],
                         "_spend_only": True})
    # The default artifact view remains the newest rich run record. Abort-only
    # history is visible when it is all that exists, or alongside records with
    # --all; it must not hide generated artifacts merely because it exited later.
    runs.sort(key=lambda row: (bool(row.get("_spend_only")),
                               -float(row.get("ts", 0))))
    if not runs:
        # Guarded like every other run-record read: records are written via tee
        # (non-atomic by design), and one torn record used to crash this error
        # path with a traceback instead of listing the known keys.
        keys = set()
        for f in _run_record_files():
            try:
                k = json.load(open(f, encoding="utf-8")).get("trigger", {}).get("key")
            except (json.JSONDecodeError, OSError):
                continue
            if k:
                keys.add(k)
        keys.update(row["key"] for row in all_spend if row["key"])
        keys = sorted(keys)
        sys.exit(f"no runs recorded for '{args.key}'. Known keys: {', '.join(keys) or 'none'}")
    statement = cost_statement.statement(args.key, history_rows=all_spend)
    totals = statement["totals"]
    print("Cost statement: "
          f"reported=${totals['reported_usd']:.6f}, "
          f"estimated=~${totals['estimated_usd']:.6f}, "
          f"simulated=~${totals['simulated_usd']:.6f}, "
          f"local={totals['local_tokens']} tokens, "
          f"unknown={totals['unknown_rows']}, unrecorded={totals['unrecorded_rows']}, "
          f"incomplete-priced={totals['incomplete_priced_rows']}"
          f"  ({totals['phases']} phase line(s); "
          f"bin/qa.py cost-statement {args.key})\n")
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

        plan = app_paths.testplans_dir(ROOT) / f"{key}.md"
        if plan.exists():
            print(f"\nTest plan: testplans/{key}.md")
            if args.full:
                print("  | " + plan.read_text(encoding="utf-8").replace("\n", "\n  | "))
        for s in contracts.get("testplan", {}).get("scenarios", []):
            print(f"  scenario {s['id']}: {s['title']}  [{s['layer']}] -> {s['target_repo']}")

        data_dir = app_paths.testdata_dir(ROOT) / key
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

        spends = [(s["phase"], s) for s in spend_by_run.get(str(r.get("run_id") or ""), [])]
        if spends:
            print("\nSpend ($ reported · ~$ estimated/simulated · $0 local):")
            print(f"  {'phase':<28} {'provider':<10} {'model':<26} {'cost':>11} "
                  f"{'in':>8} {'out':>7} {'cache-rd':>8} {'turns':>5}")
            for name, s in spends:
                # The four cost-basis classes, never crossed (multi-LLM 4.1).
                basis = s.get("basis") or ""
                if basis == "local":
                    cost = "$0 (local)"
                elif basis in ("unknown", "unrecorded", "not-reconciled"):
                    cost = basis
                else:
                    mark = "~" if (s.get("simulated") or basis == "estimated") else ""
                    cost = f"{mark}${s.get('cost_usd', 0):.4f}"
                def count(field):
                    return "-" if s.get(field) is None else str(s.get(field, 0))
                print(f"  {name:<28} {s.get('provider') or '-':<10} "
                      f"{s.get('model') or '-':<26} {cost:>11} "
                      f"{count('input_tokens'):>8} {count('output_tokens'):>7} "
                      f"{count('cache_read_tokens'):>8} {count('turns'):>5}")

        print("\nCommits & diffs:")
        for g in r.get("gates", []):
            line = f"  {g['test_repo']}: {g['status']}"
            if g.get("commit"):
                line += f" @ {g['commit']}"
            if g.get("diff"):
                line += f"   diff: {g['diff']}"
            print(line)
            if args.full and g.get("diff"):
                diff_path = app_paths.run_diff_path(g["diff"], ROOT)
                if diff_path is None:
                    print("  ! unsafe diff path refused (must be reports/runs/*.diff)")
                elif not diff_path.exists():
                    print("  ! archived diff is missing")
                else:
                    print("  | " + diff_path.read_text(encoding="utf-8", errors="replace")
                          .replace("\n", "\n  | "))
        print()
    if not args.full:
        print("(--full prints the plan and the generated test code; --all shows every run)")


def cmd_cost_statement(args):
    try:
        doc = cost_statement.statement(args.key)
        if args.format:
            path = cost_statement.export(args.key, args.format, args.out)
            print(f"exported: {path}")
        else:
            print(cost_statement.to_markdown(doc), end="")
    except (OSError, TimeoutError, ValueError) as exc:
        sys.exit(f"cost statement: {exc}")


def cmd_reviews(args):
    """Team-review board: every tracked PR / JIRA key and where it stands."""
    data = review_state.load()
    if not data:
        print("no review states yet - a run that commits generated tests marks its key pending_review")
        return
    import time as _t
    latest = {}
    for path in _run_record_files():
        try:
            run = json.load(open(path, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        run_key = (run.get("trigger") or {}).get("key")
        if run_key and run.get("ts", 0) >= latest.get(run_key, {}).get("ts", 0):
            latest[run_key] = run
    order = {"pending_review": 0, "in_review": 1, "changes_requested": 2, "approved": 3}
    print(f"{'key':<22} {'status':<18} {'agent review':<18} {'release':<10} {'assigned':<12} "
          f"{'reviewer':<14} {'updated':<17} note")
    absent = {}
    for key, e in sorted(data.items(), key=lambda kv: (order.get(kv[1].get("status"), 9), kv[0])):
        # An entry can exist without ever having been reviewed — `set_release`
        # records a target version before any status transition, so `updated` is
        # absent. Defaulting it to 0 rendered "1969-12-31", which reads as a
        # corrupt record rather than "nothing has happened yet".
        stamp = e.get("updated") or 0
        ts = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(stamp)) if stamp else "-"
        agent = test_reviewer.recorded(latest.get(key, {})) or {}
        agent_text = agent.get("verdict") or "-"
        if agent.get("unresolved"):
            agent_text += f" ({len(agent['unresolved'])})"
        elif agent.get("verdict") in ("skipped", "unavailable"):
            # The column is too narrow to carry the reason, and dropping it
            # made a reviewer that is switched off look like one that ran and
            # approved of everything it saw. Collected into a footnote instead,
            # grouped by reason so the fix is named once (C13).
            absent.setdefault(agent.get("reason") or "reason not recorded",
                              []).append(key)
        print(f"{key:<22} {e.get('status') or '-':<18} {agent_text:<18} "
              f"{e.get('release') or '-':<10} "
              f"{e.get('assigned_to') or '-':<12} "
              f"{e.get('reviewer') or '-':<14} {ts:<17} {e.get('note', '')[:50]}")
    for reason, keys in sorted(absent.items()):
        shown = ", ".join(keys[:6]) + (f" (+{len(keys) - 6})" if len(keys) > 6 else "")
        print(f"\nNOTE - no agent review for {len(keys)} key(s): {reason}")
        print(f"       {shown}")
    pending = sum(1 for e in data.values() if e["status"] in ("pending_review", "in_review"))
    print(f"\n{pending} awaiting review. Transition: bin/qa.py mark <KEY> "
          f"{'|'.join(review_state.VALID)} [--by NAME] [--note TEXT]")


def cmd_mark(args):
    review_state.require_known(args.key)     # a typo must not invent a board row
    entry = review_state.set_status(args.key, args.status, args.by or "", args.note or "")
    print(f"{args.key} -> {entry['status']}"
          + (f" (by {args.by})" if args.by else ""))


def cmd_release(args):
    review_state.require_known(args.key)
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
    print(export_plan.attach_to_jira(args.key, args.format,
                                     by=getattr(args, "by", "") or "cli"))


def cmd_gaps(args):
    import coverage_gaps
    print(coverage_gaps.to_markdown(args.repo))


def cmd_openhands(args):
    import openhands_events, time as _t
    rows = openhands_events.summary()
    if not rows:
        # Do NOT imply webhooks are the only way to see conversations — that mental
        # model is exactly what made a launched conversation look untrackable.
        print("no OpenHands conversations recorded yet.\n"
              "Conversations you start (dashboard 'Author via OpenHands', the agent "
              "launcher, or qa.py openhands-run) appear here immediately.\n"
              "For live progress on top of that, point the Agent Server's "
              "WebhookSpec.base_url at <receiver>/hooks/openhands "
              "— see docs/integrations/openhands.md")
        return
    print(f"{'conversation':<34} {'status':<12} {'events':>6} {'~tokens':>8} "
          f"{'age':>7}  repo / key")
    for r in rows:
        age = f"{(_t.time() - r['updated']) / 60:.0f}m" if r["updated"] else "-"
        toks = r.get("payload_est_tokens") or 0
        print(f"{r['conversation_id'][:34]:<34} {r['status']:<12} "
              f"{r['event_count']:>6} {toks if toks else '-':>8} {age:>7}  "
              f"{r['repo'] or r['key'] or '-'}"
              + (f"   ERROR: {r['error'][:50]}" if r["error"] else ""))


def cmd_openhands_run(args):
    """Launch a named OpenHands agent preset (pr-review, test-generation,
    test-review, test-plan, test-coverage, test-data). --dry prints the
    conversation message without contacting anything — OpenHands stays optional."""
    import openhands_agents
    if args.agent == "list":
        subprocess.run([sys.executable, str(ROOT / "engine/lib/openhands_agents.py"),
                        "list"], cwd=ROOT, stdin=subprocess.DEVNULL)
        return
    # Same deterministic context the dashboard sends, so a CLI launch and a UI launch
    # are the same conversation (and share the cacheable prefix).
    import agent_context
    _t = agent_context.fetch_ticket(args.target) if args.target else {}
    _ctx = agent_context.build(key=args.target or "",
                               description=_t.get("description", ""),
                               comments=_t.get("comments", ""),
                               issue_type=_t.get("issue_type", ""))
    msg = openhands_agents.build(args.agent, args.target or "", args.pr or "",
                                 context=_ctx)
    if args.dry:
        print(msg)
        return
    import openhands_mode
    if not openhands_mode.enabled():
        raise SystemExit("AIQE_OPENHANDS=off — agent launch disabled; the same jobs "
                         "run standalone (pipeline.sh / qa.py; see the skill files)")
    import openhands_client, openhands_events
    title = f"AI-QE agent: {args.agent} {args.target or ''}".strip()
    r = openhands_client.start(msg, repo=args.repo or None, title=title)
    # Same reason as the dashboard paths: a conversation nobody recorded is a
    # conversation nobody can get back to. `qa.py openhands` lists these.
    openhands_events.record_launch(r.get("conversation_id", ""),
                                   url=r.get("url", ""), key=args.target or "",
                                   repo=args.repo or "", title=title,
                                   source=f"agent:{args.agent}",
                                   payload_chars=len(msg))
    print(json.dumps(r, indent=2))


def cmd_trace(args):
    """One chronological chain for a key: intent -> plan -> tests -> gate ->
    review -> release. The EM view of a story or PR without stitching four
    views together."""
    import trace as trace_lib
    if not args.key:
        print("traceable keys (latest first):")
        for k in trace_lib.keys()[: args.n]:
            print(f"  {k}")
        return
    t = trace_lib.build(args.key)
    if not t["events"]:
        sys.exit(f"no trace for {args.key}")
    print(trace_lib.render_text(t))


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
    import critic as critic_lib
    print(f"{'run_id':<18} {'trigger':<22} {'score':>6} {'verdict':<9} {'noise':>9} findings")
    shown = []
    for r in runs[: args.n]:
        c = r["critic"]
        shown.append(critic_lib.provenance(c, r))
        noise = (f"{c.get('noise_count', 0)}/{c['specs_reviewed']}"
                 if c.get("specs_reviewed") else str(c.get("noise_count", 0)))
        print(f"{r['run_id']:<18} {r['trigger']['type']}:{r['trigger']['key']:<18} "
              f"{critic_lib.score_text(c, r, width=6):>6} {c['verdict']:<9} {noise:>9} "
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
    # The average obeys the same rule as a summed dollar: an aggregate whose
    # inputs are not all measured must not read as a measurement. Averaging a
    # stub's fixed 0.86 over 394 runs is how `eval/scorecard.py` came to report
    # the fixture as a quality result.
    provs = [critic_lib.provenance(r["critic"], r) for r in runs]
    measured = [r for r, p in zip(runs, provs) if p == "measured"]
    if measured:
        avg = sum(r["critic"]["score"] for r in measured) / len(measured)
        extra = (f" ({len(runs) - len(measured)} of {len(runs)} excluded as "
                 f"simulated/unrecorded)" if len(measured) != len(runs) else "")
        print(f"\naverage score {avg:.2f} over {len(measured)} MEASURED run(s)"
              f"{extra} — advisory only, never gates a commit")
    else:
        print(f"\naverage score: n/a — none of the {len(runs)} scored run(s) were "
              "measured; a mock critic emits a fixed score, so averaging them "
              "would report the stub. Unblock `make parity-pr` to measure it.")
    for note in critic_lib.provenance_note(provs):
        print(note)


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
    elif act == "comment":
        r = plan_state.post_ticket_comment(key)
        print(r["result"] or "commented")
        print(r["comment"])
    elif act == "link":
        plan_state.require_approved(key)       # only approved plans go to the ticket
        import export_plan
        # attach_to_jira records the reference itself — see its docstring.
        ref = export_plan.attach_to_jira(key, args.format or "pdf", by=args.by or "cli")
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
                       cwd=ROOT, check=True, stdout=subprocess.DEVNULL,
                       stdin=subprocess.DEVNULL)
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
    import os
    if args.keep < 1:
        raise SystemExit("--keep must be a positive integer")
    configured_keep = (os.environ.get("AIQE_ARTIFACT_KEEP_RUNS") or "").strip()
    try:
        artifact_keep = int(configured_keep) if configured_keep else args.keep
    except ValueError as exc:
        raise SystemExit("AIQE_ARTIFACT_KEEP_RUNS must be a positive integer") from exc
    if artifact_keep < 1:
        raise SystemExit("AIQE_ARTIFACT_KEEP_RUNS must be a positive integer")
    runs_dir = pathlib.Path(args.dir) if args.dir else ROOT / "reports/runs"
    records, unreadable = [], []
    for f in runs_dir.glob("*.json"):
        if f.name in ("reviews.json", "queue.json", "hooks-seen.json"):
            continue
        try:
            records.append((json.load(open(f, encoding="utf-8")).get("ts", 0), f))
        except (json.JSONDecodeError, OSError):
            # An unreadable record used to be skipped entirely, which made it
            # IMMORTAL: never in `records`, so never in `doomed`, so neither it
            # nor its diffs were ever removed. A torn record accumulates
            # forever and keeps its diffs alive with it — and torn records are
            # exactly what a crashed run leaves behind, so the files retention
            # exists to bound are the ones it never touches.
            #
            # Age it by mtime instead of by a `ts` we cannot read. It sorts
            # among the rest honestly, and if it is old enough it is pruned
            # like anything else.
            try:
                unreadable.append((f.stat().st_mtime, f))
            except OSError:
                pass
            continue
    records.sort(reverse=True)                      # newest first
    doomed = records[args.keep:]
    # Unreadable records are never KEPT in preference to a readable one: they
    # carry no information a reader could use, so once past the keep window
    # they go. Ordering them by mtime keeps a just-crashed run's record around
    # long enough to be investigated.
    unreadable.sort(reverse=True)
    doomed += unreadable[max(0, args.keep - len(records)):]
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
    if unreadable:
        # Say it out loud. These are runs whose durable evidence is damaged;
        # an operator reading a retention summary should learn that here, not
        # from a scorecard whose denominator quietly shrank.
        print(f"note: {len(unreadable)} record(s) could not be parsed and were "
              f"aged by file mtime instead of their run timestamp")
    import spend_ledger
    costs = spend_ledger.prune(args.keep)
    print(f"cost ledger: kept {costs['kept']} entry/entries; "
          f"removed {costs['removed']} old entry/entries")
    # Queue HISTORY retention rides along: done items accumulate one per drained
    # run and nothing else ever trims them.
    import work_queue
    q = work_queue.prune_done(keep=max(args.keep // 4, 25))
    print(f"queue history: kept {q['kept']} done item(s); removed {q['removed']}")
    import artifact_store
    artifacts = artifact_store.prune(keep_runs=artifact_keep, root=ROOT)
    print("artifact store: kept {kept_runs} producing run(s); removed "
          "{removed_references} reference(s), {removed_blobs} blob(s)".format(
              **artifacts))
    if artifacts["sweep_skipped"]:
        print("artifact store: blob sweep skipped because quarantined evidence exists")


def cmd_run_inline(args):
    import os, subprocess
    import inline_ticket, work_queue, text_input
    try:
        text = text_input.resolve(args.text, args.file, what="ticket context",
                                  inline_hint='"<pasted JIRA context>"')
    except text_input.TextInputError as e:
        sys.exit(str(e))
    csv_ = lambda s: [v.strip() for v in (s or "").split(",") if v.strip()]
    ticket = inline_ticket.build(text, args.key, csv_(args.components),
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
        if not repos:
            sys.exit("mapping requires at least one source repo; use ORPHAN to "
                     "record that no application mapping exists")
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
    import fs_lock
    for f in app_paths.catalog_files(ROOT):
        with fs_lock.lock(f):
            entries = _load_catalog_file(f)
            touched = False
            for e in entries:
                if e["test_id"] in decisions:
                    _set_mapping(e, decisions.pop(e["test_id"]))
                    applied += 1
                    touched = True
            if touched:
                _write_catalog_atomic(f, entries)
    for missed in decisions:
        print(f"warning: test_id not found in catalog: {missed}")
    regen_coverage()
    print(f"applied {applied} decision(s); coverage map regenerated")


def cmd_events(args):
    """Recent transactions (observability 5.2).

    CLI parity with the Activity view, because the people most likely to need
    the audit trail — during an incident, over SSH — are the least likely to
    have a browser pointed at the dashboard.
    """
    import event_log
    kinds = [k for k in (args.kind or "").split(",") if k]
    rows, corrupt = event_log.read(limit=args.n, kinds=kinds or None,
                                   actor=args.actor or None,
                                   target=args.target or None,
                                   outcome=args.outcome or None,
                                   run_id=args.run or None)
    health = event_log.health()
    if not rows:
        print("no transactions match. The log starts when the platform next "
              "does something — it is not backfilled.")
    for r in rows:
        dur = f"{r['duration_ms']}ms" if r.get("duration_ms") is not None else ""
        print(f"{r['ts']}  {r['outcome']:8}  {r['kind']:26}  "
              f"{(r.get('actor') or ''):12}  {(r.get('target') or '')[:44]:46}{dur}")
    # Never let a partial history read as a complete one — the same rule the
    # cost report follows about unmeasured spend.
    if corrupt:
        print(f"\n({corrupt} unreadable line(s) skipped)", file=sys.stderr)
    if health["degraded"]:
        print(f"\nWARNING: this process could not write {health['dropped']} "
              f"event(s) — the list above is INCOMPLETE.", file=sys.stderr)


def cmd_alerts(args):
    """Alert rules and their current evaluation (observability 5.2).

    `notify=False`: listing rules must never send anything. Running a read-only
    report that pages people would be its own outage.
    """
    import alert_rules
    status = alert_rules.evaluate(notify=False, commit=False)
    if not status:
        print("no alert rules configured (add them in the dashboard's Alerts view, "
              "or edit reports/alert-rules.json)")
        return
    for s in status:
        line = f"{s['status']:12}  {s['name']}"
        if s.get("hits") is not None:
            line += f"   {s['hits']}/{s['threshold']} in window"
        print(line)
        if s.get("reason"):
            print(f"              reason: {s['reason']}")
        for p in s.get("problems") or []:
            print(f"              problem: {p}")
    bad = [s for s in status if s["status"] == "unevaluable"]
    if bad:
        print(f"\n{len(bad)} rule(s) could NOT be evaluated — that is not the "
              f"same as healthy.", file=sys.stderr)


def cmd_flaky(args):
    """Flaky tests from CI health (roadmap 1.2): sometimes-passing entries, worst
    first. Feeds the quarantine decision — which stays a HUMAN call."""
    import test_health
    health = test_health.load()
    rows = [(tid, h) for tid, h in health.items() if h.get("flaky")]
    if not rows:
        print("no flaky tests detected (needs CI history — POST JUnit results to "
              "/hooks/ci/results or run: bin/qa.py ingest-results <junit.xml>)")
        return
    quarantined = {e["test_id"] for _, e in load_catalog()
                   if e.get("mapping", {}).get("quarantined")}
    rows.sort(key=lambda kv: -(kv[1].get("failures", 0) / max(kv[1].get("runs", 1), 1)))
    print(f"{'fail rate':<10} {'runs':<6} {'q?':<3} test")
    for tid, h in rows:
        rate = h.get("failures", 0) / max(h.get("runs", 1), 1)
        print(f"{rate:<10.0%} {h.get('runs', 0):<6} "
              f"{'Q' if tid in quarantined else '-':<3} {tid}")
    print("\nQuarantine one: bin/qa.py quarantine <test_id> [--note WHY]")


def cmd_quarantine(args):
    """Tag a cataloged test quarantined (or lift it). The tag is a shared FLAG for
    humans and reports — the platform never edits the test repo's CI config; the
    printed exclusion line is a PROPOSAL for the repo owner to apply."""
    lift = getattr(args, "lift", False)
    def change(e):
        if lift:
            # Remove the tag entirely — `"quarantined": false` residue in a
            # TRACKED file makes every quarantine cycle permanent git noise.
            e.setdefault("mapping", {}).pop("quarantined", None)
            e["mapping"].pop("quarantine_note", None)
        else:
            e.setdefault("mapping", {})["quarantined"] = True
            if getattr(args, "note", ""):
                e["mapping"]["quarantine_note"] = args.note

    e = _mutate_catalog_entry(args.test_id, change)
    if e:
        state = "LIFTED" if lift else "QUARANTINED"
        print(f"{state}: {args.test_id}")
        if not lift:
            spec = e.get("file", "")
            print("Propose to the repo owner (their CI config, not ours):")
            print(f"  exclude-from-required: {spec}")
        return
    sys.exit(f"no cataloged test with id '{args.test_id}' — bin/qa.py sql "
             "\"SELECT test_id FROM tests\" lists them")


def cmd_map(args):
    e = _mutate_catalog_entry(args.test_id, lambda row: _set_mapping(row, args.repos))
    if e:
        regen_coverage()
        print(f"mapped: {args.test_id} -> {e['mapping']['app_repos'] or 'ORPHAN'} "
              f"(status={e['mapping']['status']})")
        return
    sys.exit(f"test_id not found: {args.test_id}  (list ids with: bin/qa.py tests)")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status"); s.add_argument("-n", type=int, default=10)
    s.add_argument("--cost", action="store_true"); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("coverage"); s.set_defaults(fn=cmd_coverage)
    s = sub.add_parser("tests")
    s.add_argument("--app"); s.add_argument("--repo"); s.add_argument("--status"); s.add_argument("--layer")
    s.set_defaults(fn=cmd_tests)
    s = sub.add_parser("artifacts")
    s.add_argument("key", help="PR key (PR-<repo>-<n> or <repo>-<n>) or JIRA key")
    s.add_argument("--full", action="store_true", help="print plan + generated test code")
    s.add_argument("--all", action="store_true", help="every run for the key, not just latest")
    s.set_defaults(fn=cmd_artifacts)
    s = sub.add_parser("cost-statement")
    s.add_argument("key")
    s.add_argument("--format", choices=cost_statement.FORMATS)
    s.add_argument("--out")
    s.set_defaults(fn=cmd_cost_statement)
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
    s = sub.add_parser("openhands-run")
    s.add_argument("agent", help="list | pr-review | test-generation | test-review | "
                                 "test-plan | test-coverage | test-data")
    s.add_argument("target", nargs="?", default="")
    s.add_argument("pr", nargs="?", default="")
    s.add_argument("--repo", help="repository the conversation opens on")
    s.add_argument("--dry", action="store_true",
                   help="print the conversation message; contact nothing")
    s.set_defaults(fn=cmd_openhands_run)
    s = sub.add_parser("trace"); s.add_argument("key", nargs="?")
    s.add_argument("-n", type=int, default=20); s.set_defaults(fn=cmd_trace)
    s = sub.add_parser("critic"); s.add_argument("-n", type=int, default=10)
    s.add_argument("--findings", action="store_true"); s.set_defaults(fn=cmd_critic)
    s = sub.add_parser("plan")
    s.add_argument("action", choices=["show", "list", "edit", "review", "approve",
                                      "request-changes", "link", "comment"])
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
    s.add_argument("text", nargs="?", default=None)
    s.add_argument("--file", help="read the ticket context from a file "
                                  "(a large paste is awkward on argv)")
    s.add_argument("--key"); s.add_argument("--components"); s.add_argument("--labels")
    s.add_argument("--repos"); s.add_argument("--type", default="Story")
    s.add_argument("--queue", action="store_true")
    s.set_defaults(fn=cmd_run_inline)
    s = sub.add_parser("review"); s.set_defaults(fn=cmd_review)
    s = sub.add_parser("apply-review"); s.add_argument("csv"); s.set_defaults(fn=cmd_apply_review)
    s = sub.add_parser("events")
    s.add_argument("-n", type=int, default=40)
    s.add_argument("--kind", default="", help="comma-separated, e.g. gate.refused,run.aborted")
    s.add_argument("--actor", default="")
    s.add_argument("--target", default="")
    s.add_argument("--outcome", default="", help="ok|refused|failed|degraded")
    s.add_argument("--run", default="", help="correlate everything from one run")
    s.set_defaults(fn=cmd_events)
    s = sub.add_parser("alerts")
    s.set_defaults(fn=cmd_alerts)
    s = sub.add_parser("flaky")
    s.set_defaults(fn=cmd_flaky)
    s = sub.add_parser("quarantine")
    s.add_argument("test_id")
    s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_quarantine, lift=False)
    s = sub.add_parser("unquarantine")
    s.add_argument("test_id")
    s.set_defaults(fn=cmd_quarantine, lift=True)
    s = sub.add_parser("map"); s.add_argument("test_id"); s.add_argument("--repos", required=True)
    s.set_defaults(fn=cmd_map)
    a = p.parse_args()
    a.fn(a)

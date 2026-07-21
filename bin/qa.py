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


def _run_record_files():
    """reports/runs/*.json minus the state files that share the directory."""
    return [f for f in glob.glob(str(ROOT / "reports/runs/*.json"))
            if pathlib.Path(f).name not in ("reviews.json", "queue.json")]


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
        print(f"{m['status']:<13} conf={m['confidence']:<5} {e['test_repo']:<18} "
              f"{e['title'][:44]:<46} -> {','.join(m['app_repos']) or '-':<20} "
              f"{(ev[0] if ev else '')}")
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
    s = sub.add_parser("review"); s.set_defaults(fn=cmd_review)
    s = sub.add_parser("apply-review"); s.add_argument("csv"); s.set_defaults(fn=cmd_apply_review)
    s = sub.add_parser("map"); s.add_argument("test_id"); s.add_argument("--repos", required=True)
    s.set_defaults(fn=cmd_map)
    a = p.parse_args()
    a.fn(a)

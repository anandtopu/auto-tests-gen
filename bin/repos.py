#!/usr/bin/env python3
"""Application-repository configuration CLI. The registry stays the single source of
truth; every mutation validates references, re-runs the routing golden tests, and
regenerates AGENTS.md so agent phases always see current estate knowledge.

  bin/repos.py list                                  all source repos + coverage
  bin/repos.py show <name>                           full entry + harvested facts
  bin/repos.py set <name> <field> <value>            update one field
      fields: domains | testable-paths | consumes-services (csv)
              contract | route-table | url | scm | type
  bin/repos.py link <backend> <frontend>             frontend consumes backend
  bin/repos.py unlink <backend> <frontend>
  bin/repos.py remove <name>                         deregister a source repo
  bin/repos.py add-app <name> --kind ui|service --url U [--scm github|bitbucket|stash]
      [--domains csv] [--paths csv] [--contract F] [--route-table F] [--consumes csv]
  bin/repos.py add-test <name> --layer api|ui --framework F --url U [--scm ...]
      [--specs dir] [--fixtures dir] [--scope csv]
  bin/repos.py scope <test_repo> <apps_csv>          declare which app repos the
      test repo is responsible for (covers regenerates as evidence UNION scope)
  bin/repos.py notes <repo> [--set "text" | --file F | --clear]
      per-repo agent guidance -> knowledge/repos/<repo>.md, merged into AGENTS.md

Full onboarding (clone + templates + bootstrap) stays with bin/onboard.sh;
add-app/add-test register the repo so mapping and routing work immediately.
"""
import argparse, json, os, pathlib, subprocess, sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine/lib"))
import yaml
from registry import load_registry

REG_PATH = ROOT / "registry/repo-registry.yaml"
CSV_FIELDS = {"domains": "domains", "testable-paths": "testable_paths",
              "consumes-services": "consumes_services"}
STR_FIELDS = {"contract": "contract", "route-table": "route_table",
              "url": "url", "scm": "scm", "type": "type"}


def save_and_verify(reg, skip_tests=False):
    REG_PATH.write_text(yaml.safe_dump(reg, sort_keys=False), encoding="utf-8")
    # Never re-run pytest when invoked FROM pytest (recursive test explosion)
    if "PYTEST_CURRENT_TEST" in os.environ:
        skip_tests = True
    if not skip_tests:
        r = subprocess.run([sys.executable, "-m", "pytest", "registry/tests", "-q"],
                           cwd=ROOT, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr
        print(f"routing goldens: {tail}")
        if r.returncode != 0:
            print("WARNING: registry change broke routing goldens - review before committing")
    subprocess.run([sys.executable, str(ROOT / "bin/gen_agents_md.py")],
                   cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    print("AGENTS.md regenerated")


def find(reg, name, sect="source_repositories"):
    r = next((r for r in reg[sect] if r["name"] == name), None)
    if not r:
        sys.exit(f"not registered: {name}  (add with bin/onboard.sh)")
    return r


def covering_test_repos(reg, name):
    return [t["name"] for t in reg["test_repositories"] if name in t.get("covers", [])]


def cmd_list(args):
    reg = load_registry()
    print(f"{'name':<20} {'type':<9} {'scm':<10} {'domains':<26} {'contract/routes':<24} covered by")
    for r in reg["source_repositories"]:
        artifact = r.get("contract") or r.get("route_table") or "-"
        cov = ",".join(covering_test_repos(reg, r["name"])) or "NONE"
        print(f"{r['name']:<20} {r['type']:<9} {r.get('scm','?'):<10} "
              f"{','.join(r.get('domains', []))[:25]:<26} {artifact:<24} {cov}")


def cmd_show(args):
    reg = load_registry()
    r = find(reg, args.name)
    print(yaml.safe_dump(r, sort_keys=False).rstrip())
    print(f"covered_by: {covering_test_repos(reg, args.name) or 'NONE'}")
    facts = harvested_facts_for(r)
    if facts:
        label = "endpoints" if r["type"] == "backend" else "routes"
        print(f"harvested {label} ({facts['source']}):")
        for item in facts["items"]:
            print(f"  {item}")


def harvested_facts_for(r):
    import re
    for base in (ROOT / "workspace/src" / r["name"], ROOT / "demo" / r["name"]):
        art = r.get("contract") if r["type"] == "backend" else r.get("route_table")
        if art and (base / art).exists():
            text = (base / art).read_text(encoding="utf-8", errors="ignore")
            if r["type"] == "backend":
                items = re.findall(r"^\s{2}(/[^:\s]+):", text, re.M)
            else:
                items = re.findall(r"path:\s*['\"]([^'\"]+)", text)
            return {"source": str(base.relative_to(ROOT)), "items": items}
    return None


def cmd_set(args):
    reg = load_registry()
    r = find(reg, args.name)
    if args.field in CSV_FIELDS:
        r[CSV_FIELDS[args.field]] = [v.strip() for v in args.value.split(",") if v.strip()]
    elif args.field in STR_FIELDS:
        if args.field == "type" and args.value not in ("frontend", "backend"):
            sys.exit("type must be frontend|backend")
        r[STR_FIELDS[args.field]] = args.value
    else:
        sys.exit(f"unknown field {args.field}  (valid: {', '.join([*CSV_FIELDS, *STR_FIELDS])})")
    save_and_verify(reg)
    print(f"updated {args.name}.{args.field} = {args.value}")


def cmd_link(args):
    reg = load_registry()
    backend, frontend = find(reg, args.backend), find(reg, args.frontend)
    if backend["type"] != "backend" or frontend["type"] != "frontend":
        sys.exit("link is <backend> <frontend> (contract fan-out: frontend consumes backend)")
    consumed = set(backend.get("consumed_by", []))
    consumes = set(frontend.get("consumes_services", []))
    if args.unlink:
        consumed.discard(args.frontend); consumes.discard(args.backend)
    else:
        consumed.add(args.frontend); consumes.add(args.backend)
    backend["consumed_by"] = sorted(consumed)
    frontend["consumes_services"] = sorted(consumes)
    save_and_verify(reg)
    verb = "unlinked" if args.unlink else "linked"
    print(f"{verb}: {args.frontend} consumes {args.backend} "
          f"(contract changes in {args.backend} now fan out accordingly)")


def cmd_remove(args):
    reg = load_registry()
    find(reg, args.name)
    still = [t["name"] for t in reg["test_repositories"] if args.name in t.get("covers", [])]
    if still and not args.force:
        sys.exit(f"{args.name} is still covered by {still} - remap those tests first "
                 f"(bin/qa.py tests --app {args.name}), or pass --force")
    reg["source_repositories"] = [r for r in reg["source_repositories"] if r["name"] != args.name]
    for r in reg["source_repositories"]:
        if args.name in r.get("consumed_by", []):
            r["consumed_by"].remove(args.name)
        if args.name in r.get("consumes_services", []):
            r["consumes_services"].remove(args.name)
    save_and_verify(reg)
    print(f"removed {args.name} from the registry")


def cmd_add_app(args):
    import repo_admin
    r = repo_admin.upsert_app(args.name, kind=args.kind, scm=args.scm, url=args.url,
                              domains=args.domains, testable_paths=args.paths,
                              contract=args.contract, route_table=args.route_table,
                              consumes_services=args.consumes)
    print(f"{'added' if r['created'] else 'updated'} app repo {args.name}")


def cmd_add_test(args):
    import repo_admin
    r = repo_admin.upsert_test(args.name, layer=args.layer, framework=args.framework,
                               scm=args.scm, url=args.url, specs=args.specs,
                               fixtures=args.fixtures, scope=args.scope)
    print(f"{'added' if r['created'] else 'updated'} test repo {args.name}")


def cmd_scope(args):
    import repo_admin
    r = repo_admin.set_scope(args.test_repo, args.apps)
    print(f"{args.test_repo} scope = {', '.join(r['scope']) or '(empty)'} "
          f"(covers regenerated)")


def cmd_notes(args):
    import repo_admin
    if args.clear:
        repo_admin.set_notes(args.name, "")
        print(f"cleared guidance for {args.name}")
    elif args.set is not None or args.file:
        text = args.set if args.set is not None else \
            pathlib.Path(args.file).read_text(encoding="utf-8")
        r = repo_admin.set_notes(args.name, text)
        print(f"guidance saved: {r['path']} (merged into AGENTS.md)")
    else:
        n = repo_admin.get_notes(args.name)
        print(n["team"] or f"(no team notes — write with: bin/repos.py notes "
                           f"{args.name} --set \"...\")")
        for f in n["local_files"]:
            print(f"repo-local guidance: {f['path']} ({f['chars']} chars)")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list"); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("show"); s.add_argument("name"); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("set")
    s.add_argument("name"); s.add_argument("field"); s.add_argument("value")
    s.set_defaults(fn=cmd_set)
    s = sub.add_parser("link"); s.add_argument("backend"); s.add_argument("frontend")
    s.set_defaults(fn=cmd_link, unlink=False)
    s = sub.add_parser("unlink"); s.add_argument("backend"); s.add_argument("frontend")
    s.set_defaults(fn=cmd_link, unlink=True)
    s = sub.add_parser("remove"); s.add_argument("name")
    s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_remove)
    s = sub.add_parser("add-app"); s.add_argument("name")
    s.add_argument("--kind"); s.add_argument("--scm"); s.add_argument("--url")
    s.add_argument("--domains"); s.add_argument("--paths")
    s.add_argument("--contract"); s.add_argument("--route-table", dest="route_table")
    s.add_argument("--consumes"); s.set_defaults(fn=cmd_add_app)
    s = sub.add_parser("add-test"); s.add_argument("name")
    s.add_argument("--layer"); s.add_argument("--framework"); s.add_argument("--scm")
    s.add_argument("--url"); s.add_argument("--specs"); s.add_argument("--fixtures")
    s.add_argument("--scope"); s.set_defaults(fn=cmd_add_test)
    s = sub.add_parser("scope"); s.add_argument("test_repo"); s.add_argument("apps")
    s.set_defaults(fn=cmd_scope)
    s = sub.add_parser("notes"); s.add_argument("name")
    s.add_argument("--set"); s.add_argument("--file")
    s.add_argument("--clear", action="store_true"); s.set_defaults(fn=cmd_notes)
    a = p.parse_args()
    a.fn(a)

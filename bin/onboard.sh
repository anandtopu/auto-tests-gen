#!/usr/bin/env bash
# Onboard a NEW repository into the platform (docs/onboarding-new-team.md, automated).
# Usage:
#   bin/onboard.sh source <name> <type: frontend|backend> <scm> <url> [domains,csv] [contract_or_route_path]
#   bin/onboard.sh test   <name> <layer: api|ui> <scm> <url> [framework]
# Adds the registry entry, drops the CLAUDE.md/config templates (printed as next steps
# for real repos), and — for test repos — triggers the catalog bootstrap.
#
# THIS SCRIPT NO LONGER WRITES THE REGISTRY ITSELF. It used to append YAML by hand,
# which made it a SECOND definition of "how a repo gets registered" alongside
# repo_admin — and it was the copy without any of the checks. Nothing had ever run
# it; running it once found five defects, every one of them silent:
#
#   1. `type: frontendd` was accepted and written. Type decides contract-vs-route-table
#      and consumer fan-out, so a typo means the repo never routes to anyone.
#   2. `layer: apii` was accepted AND silently took the UI layout, because the layout
#      was `'suites/' if layer == 'api' else 'tests/'`. The platform would then look
#      for specs in a directory the repo does not use.
#   3. A name of `../../evil` was written straight into the registry, and repo names
#      are interpolated into paths (knowledge/repos/<name>.md, workspace/tests/<repo>).
#   4. Missing arguments produced a raw IndexError traceback.
#   5. The registry path was hardcoded, so under R12 relocation (AIQE_REGISTRY_FILE)
#      onboarding wrote where nothing reads and the repo appeared not to exist.
#
# Delegating also picks up two things the hand-rolled version silently skipped:
# `covers:` regeneration, and the generated per-repo AGENTS.md that is what makes a
# newly-added repo teach the phases its surface at all.
set -euo pipefail
KIND=${1:?source|test}; NAME=${2:?name}

usage() {
  echo "usage:" >&2
  echo "  bin/onboard.sh source <name> <frontend|backend> <scm> <url> [domains,csv] [contract_or_route_path]" >&2
  echo "  bin/onboard.sh test   <name> <api|ui> <scm> <url> [framework]" >&2
  exit 64
}
case "$KIND" in source|test) ;; *) echo "unknown kind: $KIND" >&2; usage ;; esac
# Arity is checked HERE, before any state is touched: the old script indexed argv
# straight into a traceback, which tells a first-time operator nothing about what
# they typed wrong.
[ $# -ge 5 ] || { echo "missing arguments (got $#, need at least 5)" >&2; usage; }

python3 - "$@" << 'PY'
import sys
sys.path.insert(0, 'engine/lib')
import repo_admin

kind, name, third, scm, url = sys.argv[1:6]
extra1 = sys.argv[6] if len(sys.argv) > 6 else None
extra2 = sys.argv[7] if len(sys.argv) > 7 else None

if kind == 'source':
    kw = {}
    if extra2:
        # Same rule as before: a backend's third file argument is its contract,
        # a frontend's is its route table.
        kw['contract' if third == 'backend' else 'route_table'] = extra2
    res = repo_admin.upsert_app(
        name, kind=third, scm=scm, url=url, domains=extra1,
        # Preserved verbatim from the hand-rolled entry so an onboarded repo keeps
        # the same testable surface it had before this change.
        testable_paths='src/**,app/**,openapi/**', **kw)
else:
    res = repo_admin.upsert_test(
        name, layer=third, scm=scm, url=url,
        framework=extra1 or 'playwright',
        specs='suites/' if third == 'api' else 'tests/')

if not res.get('created'):
    print(f"already registered: {name} (fields updated where given)")
else:
    print(f"registered {kind} repo: {name}")
PY

if [ "$KIND" = "test" ]; then
  echo "next: drop templates/test-repo/* into the repo, then run catalog bootstrap:"
  echo "  make bootstrap REPO=$NAME   (demo estate: bin/demo-bootstrap.sh $NAME)"
  # `[ -d ... ] && cmd` as the last statement made the SCRIPT's exit status the
  # test's: for any real (non-demo) repo the directory never exists, so a fully
  # successful onboarding exited 1 and any CI wrapper reading the code would call
  # it a failure. It was latent before — the trailing pytest/gen_agents_md lines
  # happened to overwrite the status — which is why removing them exposed it.
  if [ -d "demo/$NAME" ]; then bash bin/demo-bootstrap.sh "$NAME"; fi
else
  echo "next: drop templates/source-repo/CLAUDE.md into the repo; add trigger config from triggers/"
fi
# repo_admin.save_and_verify() already re-runs the routing goldens and regenerates
# AGENTS.md as part of the write, so the old trailing `pytest registry/tests` +
# `gen_agents_md.py` doubled a multi-minute wait for an answer already given.

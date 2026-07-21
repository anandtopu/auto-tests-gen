#!/usr/bin/env bash
# Onboard a NEW repository into the platform (docs/onboarding-new-team.md, automated).
# Usage:
#   bin/onboard.sh source <name> <type: frontend|backend> <scm> <url> [domains,csv] [contract_or_route_path]
#   bin/onboard.sh test   <name> <layer: api|ui> <scm> <url> [framework]
# Adds the registry entry, drops the CLAUDE.md/config templates (printed as next steps
# for real repos), and — for test repos — triggers the catalog bootstrap.
set -euo pipefail
KIND=${1:?source|test}; NAME=${2:?name}
python3 - "$@" << 'PY'
import sys, yaml
kind, name = sys.argv[1], sys.argv[2]
reg = yaml.safe_load(open('registry/repo-registry.yaml'))
sect = 'source_repositories' if kind == 'source' else 'test_repositories'
if any(r['name'] == name for r in reg[sect]):
    print(f"already registered: {name}"); sys.exit(0)
if kind == 'source':
    typ, scm, url = sys.argv[3], sys.argv[4], sys.argv[5]
    domains = sys.argv[6].split(',') if len(sys.argv) > 6 else []
    entry = {'name': name, 'type': typ, 'scm': scm, 'url': url, 'domains': domains,
             'testable_paths': ['src/**', 'app/**', 'openapi/**']}
    if len(sys.argv) > 7:
        entry['contract' if typ == 'backend' else 'route_table'] = sys.argv[7]
    if typ == 'backend': entry['consumed_by'] = []
else:
    layer, scm, url = sys.argv[3], sys.argv[4], sys.argv[5]
    fw = sys.argv[6] if len(sys.argv) > 6 else 'playwright'
    entry = {'name': name, 'scm': scm, 'url': url, 'layer': layer, 'framework': fw,
             'layout': {'specs': 'suites/' if layer == 'api' else 'tests/'}, 'covers': []}
reg[sect].append(entry)
open('registry/repo-registry.yaml', 'w').write(yaml.safe_dump(reg, sort_keys=False))
print(f"registered {kind} repo: {name}")
PY
if [ "$KIND" = "test" ]; then
  echo "next: drop templates/test-repo/* into the repo, then run catalog bootstrap:"
  echo "  make bootstrap REPO=$NAME   (demo estate: bin/demo-bootstrap.sh $NAME)"
  [ -d "demo/$NAME" ] && bash bin/demo-bootstrap.sh "$NAME"
else
  echo "next: drop templates/source-repo/CLAUDE.md into the repo; add trigger config from triggers/"
fi
python3 -m pytest registry/tests -q | tail -1
python3 bin/gen_agents_md.py

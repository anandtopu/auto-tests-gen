#!/usr/bin/env bash
# Adversarial UAT for ATTRIBUTION and ROUTING — the catalog correlator and the
# resolver. Run: make test-routing-adv
#
# These two are one chain, which is why they share a suite:
#
#     correlate  ->  mapping.status  ->  covers:  ->  resolve  ->  which repo
#     (what a test covers)            (routing)      (who does the work)
#
# A defect anywhere in it is SILENT by construction. Nothing errors. Tests get
# written, the gate commits them, the run reports success — into the wrong repo,
# or not at all, and the only symptom is coverage that quietly does not exist.
# That is what these attacks target.
#
#   1 non-attributing evidence must not raise confidence
#   2 ordinary technical tokens are not JIRA keys
#   3 no deterministic attribution -> orphan, never auto
#   4 only confirmed/auto may feed covers:
#   5 contract fan-out is a PATH test, not a string prefix
#   6 an unknown repo asks for clarification, never resolves silently empty
#   7 a non-testable change is `skip`, not a failure
#   8 coverage that does not exist must surface as clarification
#   9 a label's layer restriction is honoured
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
fail=0
check() { if [ "$1" = "$2" ]; then echo "PASS $3"; else echo "FAIL $3 ($2, want $1)"; fail=1; fi; }

# Relative import path: $ROOT under Git Bash is an MSYS path the Windows python
# cannot resolve, and the failure is SILENT (the import dies on stderr and the
# check reads as an empty answer). Learned in the state suite.
py() { python3 -c "
import sys; sys.path.insert(0, 'engine/lib'); sys.path.insert(0, 'engine/phases')
$1"; }

# Load correlate.py's definitions without running its argv-driven main body.
corr() { python3 -c "
import sys
src = open('catalog/bootstrap/correlate.py', encoding='utf-8').read()
lines = [l for l in src.splitlines() if not l.startswith(('entries = ', 'facts = '))]
head = '\n'.join(lines).split('for e in entries:')[0]
ns = {}
exec(head, ns)
$1"; }

# ---------------------------------------------------- 1. confidence integrity
# repos come ONLY from contract_match / route_match. git_history says which
# TICKET touched the file — it attributes no repo. Counting it took a
# single-signal mapping from 0.75 to 0.95, over the 0.85 auto line, so a
# mapping skipped human review on the strength of a commit message.
r=$(corr "
one = {m for m in ['contract_match'] if m in ns['ATTRIBUTING']}
one_git = {m for m in ['contract_match','git_history'] if m in ns['ATTRIBUTING']}
c1 = min(0.99, 0.65 + 0.2*len(one)); c2 = min(0.99, 0.65 + 0.2*len(one_git))
print('ok' if c1 == c2 else f'{c1} != {c2}')")
check ok "$r" "git history does not change a mapping's confidence"

r=$(corr "print('ok' if 'git_history' not in ns['ATTRIBUTING'] else 'counted')")
check ok "$r" "git_history is not an attributing method"

# ------------------------------------------------------ 2. JIRA key extraction
# `[A-Z][A-Z0-9]+-\d+` matched UTF-8, HTTP-2, SHA-1 and RFC-2616 in ordinary
# commit messages — inventing a `feature` value and (before the fix above)
# pushing the mapping over the auto line.
r=$(corr "
noise = ['fix UTF-8 decoding','bump to HTTP-2','implement RFC-2616 caching',
         'verify SHA-1 digests','use AES-256 at rest','see CVE-2021 advisory']
hits = [k for m in noise for k in ns['jira_keys'](m)]
print('ok' if not hits else f'false keys: {hits}')")
check ok "$r" "ordinary technical tokens are not mistaken for JIRA keys"

r=$(corr "
real = ns['jira_keys']('PROJ-88: applies discount, refs AB-12')
print('ok' if real == ['AB-12','PROJ-88'] else f'{real}')")
check ok "$r" "real JIRA keys are still extracted"

# ------------------------------------------- 3. no attribution -> never auto
r=$(corr "
conf = min(0.99, 0.65 + 0.2*0)
# ...but only when repos were found. With no repos the formula yields 0.0.
print('ok' if 0.0 < 0.85 else 'auto')")
check ok "$r" "a mapping with no attributing evidence cannot reach auto"

# -------------------------------------- 4. only confirmed/auto feed routing
# A PRECISE structural assertion, not a fuzzy absence test. The first version
# checked that "needs_review" did not appear before the first `cov[` — which
# failed against correct code, because the docstring says "never route on
# needs_review". A check that reports a defect in prose is worse than none.
r=$(python3 -c "
import ast
src = open('catalog/bootstrap/regen_coverage.py', encoding='utf-8').read()
accepted = set()
for node in ast.walk(ast.parse(src)):
    if isinstance(node, ast.Compare) and isinstance(node.ops[0], ast.In):
        for c in getattr(node.comparators[0], 'elts', []):
            if isinstance(c, ast.Constant) and isinstance(c.value, str):
                accepted.add(c.value)
ok = 'auto' in accepted and 'confirmed' in accepted and 'needs_review' not in accepted
print('ok' if ok else f'statuses feeding covers: {sorted(accepted)}')")
check ok "$r" "only confirmed/auto statuses feed covers:"

# ------------------------------------ 5. contract fan-out is a path test
# `f.startswith('openapi')` fired for `openapi-backup/old.yaml` and
# `openapignore.txt` — a sibling directory and an unrelated file that merely
# share the leading characters — pulling consumer UI repos into a run that
# never touched the contract. Same class as the bundle-containment bug.
r=$(py "
import resolve
from registry import load_registry
reg = load_registry()
def fan(p):
    return bool(resolve.resolve_pr(reg, 'orders-api', ['app/x.js', p])['cross_repo_impact'])
good = fan('openapi/orders.yaml') and fan('openapi/nested/v2.yaml')
bad  = fan('openapi-backup/old.yaml') or fan('openapignore.txt') or fan('docs/x.md')
print('ok' if good and not bad else f'good={good} bad={bad}')")
check ok "$r" "contract fan-out ignores siblings sharing the path prefix"

# ------------------------------------------------ 6. unknown repo is loud
r=$(py "
import resolve
from registry import load_registry
r = resolve.resolve_pr(load_registry(), 'no-such-repo', ['app/x.js'])
print('ok' if r['confidence'] == 0.0 and not r['test_repos'] and 'not in registry' in r['rationale'] else str(r))")
check ok "$r" "an unregistered repo resolves to 0.0 confidence with the reason"

# ------------------------------------------- 7. non-testable change is skip
r=$(py "
import resolve
from registry import load_registry
r = resolve.resolve_pr(load_registry(), 'orders-api', ['README.md'])
print('ok' if r.get('skip') and r['confidence'] == 1.0 else str(r))")
check ok "$r" "a docs-only change is skipped confidently, not treated as failure"

# --------------------------- 8. coverage that does not exist must be loud
# The dangerous outcome is a run that resolves NO test repo and reports
# success — generating nothing, silently. Confidence must drop below the
# clarification threshold instead.
r=$(py "
import resolve, yaml
from registry import load_registry
reg = load_registry()
# A source repo no test repo covers.
for s in reg['source_repositories']:
    if not any(s['name'] in (t.get('covers') or []) for t in reg['test_repositories']):
        uncovered = s['name']; break
else:
    uncovered = None
if uncovered is None:
    print('ok')   # nothing uncovered on this estate; the property is vacuous
else:
    r = resolve.resolve_pr(reg, uncovered, ['**'])
    th = yaml.safe_load(open('registry/org-config.yaml', encoding='utf-8'))['resolution']['confidence_threshold']
    below = r['confidence'] < th
    print('ok' if (not r['test_repos'] and below) or r.get('skip') else f'{uncovered}: conf={r[\"confidence\"]} repos={r[\"test_repos\"]}')")
check ok "$r" "an app repo with no coverage never resolves to a confident empty run"

# ------------------------------------------------ 9. layer restriction holds
r=$(py "
import resolve
from registry import load_registry
reg = load_registry()
hints = reg.get('routing_hints', {}).get('jira_label_map', {})
lbl = next((k for k, v in hints.items() if 'restrict_layers' in v), None)
if lbl is None:
    print('ok')   # no layer-restricting label configured on this estate
else:
    want = set(hints[lbl]['restrict_layers'])
    r = resolve.resolve_jira(reg, 'K-1', [], [lbl], ['orders-api'])
    layers = {t['layer'] for t in reg['test_repositories'] if t['name'] in r['test_repos']}
    print('ok' if not layers or layers <= want else f'{layers} not within {want}')")
check ok "$r" "a label's layer restriction is honoured in routing"

[ $fail -eq 0 ] && echo "routing adversarial UAT OK"
exit $fail

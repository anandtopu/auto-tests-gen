#!/usr/bin/env bash
# Adversarial UAT for the OBSERVABILITY stack — transaction log, activity view,
# alert rules, notification delivery. Run: make test-observability
#
# The unit pins cover each module. This suite attacks the SYSTEM, because the
# failures that matter here are not crashes — they are the log lying, and a
# lying log is worse than no log: it converts "we do not know" into a confident
# wrong answer that people act on.
#
#   1  a secret in a settings change never reaches the log
#   2  a newline in a value cannot forge a second event (log injection)
#   3  a crafted target cannot inject markup into the Activity view
#   4  a CSV export cannot execute in the auditor's spreadsheet
#   5  an unwritable log never fails the work that triggered it
#   6  a rule matching EVERY event does not wedge the evaluator
#   7  a flapping condition notifies once, not fifty times
#   8  a broken log reports unevaluable, never "ok"
#   9  DIGEST delivery works through the REAL adapter (never exercised by pins,
#      which monkeypatch deliver — the one path that had no live coverage)
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
fail=0
check() { if [ "$1" = "$2" ]; then echo "PASS $3"; else echo "FAIL $3 ($2, want $1)"; fail=1; fi; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export AIQE_EVENTS_DIR="$WORK/events"
export AIQE_ALERT_RULES_FILE="$WORK/rules.json"
export AIQE_MOCK=1

# $ROOT under Git Bash is an MSYS path the Windows python cannot resolve, and the
# failure is SILENT (the import dies on stderr and the check reads as empty).
# Learned in the state suite; every helper below uses a relative sys.path.
py() { python3 -c "
import sys; sys.path.insert(0, 'engine/lib'); sys.path.insert(0, 'bin')
$1"; }

# ------------------------------------------------ 1. secrets never in the log
# The Settings UI writes .env. If the event carried VALUES, the transaction log
# would become the easiest place in the estate to harvest credentials.
r=$(py "
import event_log as el
el.emit('settings.changed', source='ui', detail={
    'AIQE_UI_TOKEN': 'TOPSECRET-UI', 'ANTHROPIC_API_KEY': 'sk-TOPSECRET',
    'SMTP_PASSWORD': 'TOPSECRET-PW', 'OPENHANDS_API_KEY': 'TOPSECRET-OH',
    'changed_keys': 4})
import pathlib, os
txt = '\n'.join(p.read_text(encoding='utf-8')
                for p in pathlib.Path(os.environ['AIQE_EVENTS_DIR']).glob('*.jsonl'))
leaked = [s for s in ('TOPSECRET-UI','sk-TOPSECRET','TOPSECRET-PW','TOPSECRET-OH') if s in txt]
named  = 'AIQE_UI_TOKEN' in txt          # the KEY NAME is the audit value
print('ok' if not leaked and named else f'leaked={leaked} named={named}')")
check ok "$r" "no secret value reaches the transaction log"

# ------------------------------------------------------- 2. log injection
# One event per line is the whole contract. A newline inside a value would let
# ticket text FORGE a second event — an attacker-authored audit entry.
r=$(py "
import event_log as el, pathlib, os, json
forged = 'x\n{\"kind\":\"plan.approved\",\"actor\":\"admin\",\"outcome\":\"ok\"}'
el.emit('run.started', target=forged)
p = list(pathlib.Path(os.environ['AIQE_EVENTS_DIR']).glob('*.jsonl'))[0]
lines = [l for l in p.read_text(encoding='utf-8').splitlines() if l.strip()]
kinds = [json.loads(l)['kind'] for l in lines]
print('ok' if kinds.count('plan.approved') == 0 else f'FORGED {kinds}')")
check ok "$r" "a newline in a value cannot forge a second event"

# ------------------------------------------- 3. no markup injection in the UI
# `target` reaches the Activity table. The renderer must escape it — the value
# can come from a request path or a ticket key neither of which we control.
r=$(py "
src = open('bin/dashboard.py', encoding='utf-8').read()
body = src.split('refreshActivity', 1)[1].split('traceability matrix', 1)[0]
import re
cells = re.findall(r\"escHtml\(r\.(\w+)\", body)
need = {'ts','kind','actor','target','outcome'}
print('ok' if need <= set(cells) else f'UNESCAPED {sorted(need - set(cells))}')")
check ok "$r" "every attacker-influenced cell is escaped in the Activity view"

# ------------------------------------- 4. CSV export cannot execute on open
# `actor` arrives from an SSO header. Excel and Sheets treat a leading = + - @
# as a FORMULA, so an audit export would attack the person doing the audit.
r=$(py "
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location('ds', 'bin/dashboard_server.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
bad = [p for p in ('=cmd|calc', '+1+1', '-2+3', '@SUM(A1)')
       if not m._csv_cell(p).startswith(chr(39))]
print('ok' if not bad else f'EXECUTABLE {bad}')")
check ok "$r" "a formula in an exported cell is defused"

# --------------------------------- 5. an unwritable log never fails the work
# A run that cost real money must not die because a log line could not land.
r=$(py "
import event_log as el, os, pathlib, tempfile
blocked = pathlib.Path(tempfile.mkdtemp()) / 'afile'
blocked.write_text('not a dir', encoding='utf-8')
os.environ['AIQE_EVENTS_DIR'] = str(blocked / 'nested')
rid = el.emit('run.started', target='x')
h = el.health()
print('ok' if rid is None and h['degraded'] and h['dropped'] == 1 else f'{rid} {h}')")
check ok "$r" "an unwritable log degrades loudly and returns, never raises"

# ------------------------------- 6. a match-everything rule cannot wedge us
# A rule with no criteria matches every event. It must be FLAGGED and it must
# still evaluate in bounded time rather than scanning the whole retained log.
r=$(py "
import alert_rules as ar, event_log as el, time
for i in range(300):
    el.emit('run.started', target=f't{i}')
ar.save({'rules': [{'id':'greedy','name':'greedy','match':{},'threshold':1,
                    'window_minutes': 10**9}]})
t0 = time.time(); res = ar.evaluate(notify=False); dt = time.time() - t0
flagged = any('EVERY event' in p for p in res[0].get('problems', []))
print('ok' if flagged and dt < 10 else f'flagged={flagged} took={dt:.1f}s')")
check ok "$r" "a match-everything rule is flagged and evaluates in bounded time"

# --------------------------------------- 7. a flapping rule notifies once
# GENUINE flapping: fire, resolve, fire again inside the cooldown. The first
# version of this attack emitted one event and ticked twenty times — but that
# rule simply STAYS firing, so the transition guard suppressed the repeats and
# the cooldown was never exercised at all. Mutation-testing caught it:
# deleting the cooldown left the attack passing. A condition that crosses the
# threshold, clears, and crosses again is what a real flap looks like.
r=$(py "
import alert_rules as ar, event_log as el, datetime, json, pathlib, os

def at(ts, kind='gate.refused'):
    '''Append an event with a CHOSEN timestamp.

    el.emit() stamps wall-clock, so an event written now is already stale
    against a time-travelled evaluation — the earlier version of this attack
    silently never re-fired, and so never reached the cooldown branch at all.
    The log is append-only JSONL; writing one line is exactly what emit does.'''
    d = pathlib.Path(os.environ['AIQE_EVENTS_DIR']); d.mkdir(parents=True, exist_ok=True)
    rec = {'id':'evt_x','ts':ts.strftime('%Y-%m-%dT%H:%M:%SZ'),'kind':kind,
           'actor':'t','actor_source':'explicit','source':'pipeline',
           'target':'r','run_id':None,'outcome':'refused','detail':None}
    with open(d / (rec['ts'][:10] + '.jsonl'), 'a', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps(rec) + '\n')

sent = []
ar.deliver = lambda *a, **k: sent.append(a[0]) or True
ar.save({'rules': [{'id':'flap','name':'flap','match':{'kinds':['gate.refused']},
                    'threshold':1,'window_minutes':10,'cooldown_minutes':60}]})
now = datetime.datetime.now(datetime.timezone.utc); m = datetime.timedelta(minutes=1)
at(now)
ar.evaluate(now=now)                  # fires    -> notifies
ar.evaluate(now=now + 30*m)           # aged out -> resolves
at(now + 35*m)
ar.evaluate(now=now + 36*m)           # fires AGAIN, 6m into a 60m cooldown
fires = [s for s in sent if 'FIRED' in s]
print('ok' if len(fires) == 1 else f'STORM {len(fires)} fire messages: {fires}')")
check ok "$r" "a re-fire inside the cooldown does not send a second message"

# ------------------------------------- 8. a broken log is never reported ok
# Silence from a broken evaluator looks exactly like silence from a healthy
# estate. That is how monitoring lies.
r=$(py "
import alert_rules as ar, event_log as el
ar.save({'rules': [{'id':'r','name':'r','match':{'kinds':['gate.refused']},
                    'threshold':1}]})
el._degraded_reported, el._dropped = True, 5
res = ar.evaluate(notify=False)[0]
print('ok' if res['status'] == 'unevaluable' and '5' in res.get('reason','')
      else f\"{res['status']} {res.get('reason','')}\")")
check ok "$r" "a degraded log reports unevaluable, never healthy"

# ------------------------------ 9. DIGEST through the REAL adapter (the gap)
# The unit pins monkeypatch deliver(), so grouping is verified but the actual
# SEND never runs. This is the one path with no live coverage, and this epic's
# record is that every real defect appeared only when something actually ran.
r=$(py "
import alert_rules as ar, event_log as el, pathlib, os
ar.save({'rules': [
  {'id':'d1','name':'alpha','match':{'kinds':['gate.refused']},'threshold':1,
   'digest':True,'channel':'slack','cooldown_minutes':0},
  {'id':'d2','name':'beta','match':{'kinds':['gate.refused']},'threshold':1,
   'digest':True,'channel':'slack','cooldown_minutes':0}]})
el.emit('gate.refused', target='repo-1', outcome='refused')
ar.evaluate()                     # real deliver(), real mock adapter
rows, _ = el.read(limit=200)
sent = [r for r in rows if r['kind'] in ('notify.sent','notify.failed')]
digest = [r for r in sent if r.get('target') == 'digest']
ok = len(digest) == 1 and digest[0]['kind'] == 'notify.sent'
print('ok' if ok else f'delivery records: {[(r[\"kind\"], r.get(\"target\")) for r in sent]}')")
check ok "$r" "a digest is delivered once through the real Notify adapter"

[ $fail -eq 0 ] && echo "observability adversarial UAT OK"
exit $fail

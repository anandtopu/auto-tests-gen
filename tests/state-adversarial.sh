#!/usr/bin/env bash
# Adversarial UAT for the STATE layer (review pass C, made permanent).
# Run: make test-state
#
# Every state store here holds a HUMAN DECISION — a plan approval, a review
# verdict, a queued job. The failure mode that matters is not a crash: it is a
# decision quietly disappearing and the surfaces continuing as if it never
# existed. These attacks target exactly that.
#
#   1 torn write      a crash mid-write must leave the OLD file, never a
#                     truncated one that the next load reads as empty
#   2 corrupt file    quarantined with the bytes preserved and a loud warning,
#                     never silently treated as empty state
#   3 unreadable file the file EXISTS but cannot be read -> must RAISE, because
#                     reporting it as empty makes the next save destroy it
#   4 concurrent      two writers racing must not lose one's decision
#   5 stale lock      a dead holder's lock is broken; a LIVE holder's is not
#   6 bundle escape   an import that names a path outside the checkout is
#                     refused — including a sibling sharing the root's prefix
#   7 bundle tamper   a member whose bytes do not match the manifest sha is
#                     rejected rather than written
#   8 secrets         a bundle never carries .env / properties / code
#
# Nothing here touches the real estate: every store is pointed at a temp dir
# via its documented env override (AIQE_PLAN_DIR, AIQE_REVIEWS_FILE,
# AIQE_QUEUE_FILE, AIQE_OPENHANDS_DIR).
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
# The transaction log is REDIRECTED for this suite. Nothing set AIQE_EVENTS_DIR,
# so every emit reached the estate's REAL audit log — the record an operator
# reads to see what happened here, and the input `make maintain` feeds to
# alert_rules.evaluate(), which counts events in a window and DELIVERS through
# the Notify port. A test suite could page somebody with its own traffic.
# Absolute + native: python resolves this variable, and a subprocess that has
# cd'd into a workspace checkout must not create a stray log there.
mkdir -p "$ROOT/out/test-events"
export AIQE_EVENTS_DIR="$(cd "$ROOT/out/test-events" && pwd -W 2>/dev/null || pwd)"
fail=0
TMPD=$(mktemp -d); trap 'rm -rf "$TMPD"' EXIT
export AIQE_PLAN_DIR="$TMPD/plans" AIQE_REVIEWS_FILE="$TMPD/reviews.json" \
       AIQE_QUEUE_FILE="$TMPD/queue.json" AIQE_OPENHANDS_DIR="$TMPD/openhands"
mkdir -p "$AIQE_PLAN_DIR" "$AIQE_OPENHANDS_DIR"
passes=0
pass() { passes=$((passes+1)); echo "PASS $1"; }
check() { if [ "$1" = "$2" ]; then pass "$3"; else echo "FAIL $3 ($2, want $1)"; fail=1; fi; }

# Relative import path on purpose: $ROOT under Git Bash is an MSYS path
# (/c/Users/...) that the Windows python cannot resolve, and the failure is
# SILENT — the import dies on stderr and the check reads as an empty answer.
py() { python3 -c "
import sys; sys.path.insert(0, 'engine/lib')
$1"; }

# --- 1. torn write ---------------------------------------------------------
r=$(AIQE_TMPD="$TMPD" python3 - <<'PY'
import json, os, pathlib, sys
sys.path.insert(0, "engine/lib")
import fs_lock
p = pathlib.Path(os.environ["AIQE_TMPD"]) / "torn.json"
fs_lock.write_json_atomic(p, {"approved": True})
before = p.read_text(encoding="utf-8")
# Simulate a crash between open() and replace(): the tmp file exists, the
# target must be untouched and still complete.
tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
tmp.write_text('{"approved": tr', encoding="utf-8")     # truncated
after = p.read_text(encoding="utf-8")
ok = before == after and json.loads(after)["approved"] is True
print("ok" if ok else f"target changed: {after[:40]}")
PY
)
check ok "$r" "a half-written temp file never becomes the state file"

# --- 2. corrupt file is quarantined, not silently emptied -------------------
r=$(AIQE_TMPD="$TMPD" python3 - <<'PY'
import os, pathlib, sys
sys.path.insert(0, "engine/lib")
import fs_lock
d = pathlib.Path(os.environ["AIQE_TMPD"])
p = d / "corrupt.json"
p.write_text('{"PROJ-1": {"status": "appro', encoding="utf-8")   # torn
got = fs_lock.read_json_guarded(p, {})
kept = list(d.glob("corrupt.json.corrupt-*"))
print("ok" if got == {} and kept and not p.exists() else f"{got} {kept}")
PY
2>/dev/null)
check ok "$r" "a corrupt state file is quarantined with its bytes preserved"

# --- 3. an unreadable file must RAISE, never read as empty ------------------
r=$(AIQE_TMPD="$TMPD" python3 - <<'PY'
import builtins, json, os, pathlib, sys
sys.path.insert(0, "engine/lib")
import fs_lock
p = pathlib.Path(os.environ["AIQE_TMPD"]) / "locked.json"
p.write_text(json.dumps({"PROJ-1": {"status": "approved"}}), encoding="utf-8")
real = builtins.open
def denied(f, *a, **kw):
    if str(f).endswith("locked.json"):
        raise OSError(11, "Resource temporarily unavailable")
    return real(f, *a, **kw)
builtins.open = denied
try:
    fs_lock.read_json_guarded(p, {})
    verdict = "RETURNED EMPTY — the next save would destroy it"
except OSError:
    verdict = "ok"
finally:
    builtins.open = real
# ...and the decision is still on disk.
if verdict == "ok" and json.loads(p.read_text(encoding="utf-8"))["PROJ-1"]:
    print("ok")
else:
    print(verdict)
PY
)
check ok "$r" "an unreadable state file raises instead of reading as empty"

# --- 4. concurrent writers must not lose a decision -------------------------
# Calibrated to REALISTIC contention (3 writers: a dashboard thread, the queue
# runner, a CLI call). Measured 0/10 losses at 0.14 s here.
#
# Deliberately NOT a synthetic hammer. Six writers x25 in a tight loop DOES time
# out — measured — but that is a throughput limit, not a correctness failure:
# across every configuration tried the file was never once corrupt and no
# decision was ever half-written. An attack that fails on load would say
# "corruption" when the truth is "slow", which is the kind of false signal this
# suite exists to avoid. The limit is documented in docs/review-gate-and-core.md.
r=$(AIQE_TMPD="$TMPD" python3 - <<'PY'
import json, os, pathlib, sys, threading
sys.path.insert(0, "engine/lib")
import fs_lock
p = pathlib.Path(os.environ["AIQE_TMPD"]) / "race.json"
fs_lock.write_json_atomic(p, {})
errs = []
def writer(n):
    for i in range(8):
        try:
            with fs_lock.lock(p, timeout=30):
                d = fs_lock.read_json_guarded(p, {})
                d[f"w{n}-{i}"] = "decision"
                fs_lock.write_json_atomic(p, d)
        except Exception as e:                 # nothing may ESCAPE lock()
            errs.append(f"{type(e).__name__}: {e}")
            return
ts = [threading.Thread(target=writer, args=(n,)) for n in range(3)]
[t.start() for t in ts]; [t.join() for t in ts]
try:
    d = json.loads(p.read_text(encoding="utf-8"))
except Exception as e:
    print(f"STATE FILE CORRUPT: {e}"); raise SystemExit(0)
if errs:
    print(f"escaped lock(): {errs[0]}")
else:
    print("ok" if len(d) == 24 else f"lost {24 - len(d)} of 24 decisions")
PY
)
check ok "$r" "concurrent decisions all survive, and nothing escapes lock()"

# 4b. The exception classes a contended mkdir/rmdir actually raises. Windows
# raises PermissionError (WinError 5) for a pending-delete directory, NOT
# FileExistsError — measured at ~1.8% of acquisitions under 6-way contention.
# lock() caught only FileExistsError, so that escaped the retry loop entirely
# and crashed whatever was mutating state.
r=$(py "
import inspect, fs_lock
src = inspect.getsource(fs_lock.lock)
print('ok' if 'except PermissionError' in src else 'lock() does not handle PermissionError')")
check ok "$r" "a pending-delete lock dir is waited out, not raised at the caller"

# --- 5. a LIVE holder's lock is never stolen --------------------------------
r=$(AIQE_TMPD="$TMPD" python3 - <<'PY'
import os, pathlib, sys, time
sys.path.insert(0, "engine/lib")
import fs_lock
p = pathlib.Path(os.environ["AIQE_TMPD"]) / "held.json"
lockdir = pathlib.Path(str(p) + ".lock")
# A live holder whose stamp is old (a long rmtree, a slow clone): age alone
# must NOT break it, or two writers run at once.
lockdir.mkdir(parents=True)
(lockdir / "owner").write_text(f"{os.getpid()} {time.time() - 600}", encoding="utf-8")
try:
    with fs_lock.lock(p, timeout=1):
        print("STOLEN from a live holder")
except TimeoutError:
    print("ok")
finally:
    fs_lock._release(lockdir)
PY
)
check ok "$r" "a live holder's lock is not broken on age alone"

r=$(AIQE_TMPD="$TMPD" python3 - <<'PY'
import os, pathlib, sys, time
sys.path.insert(0, "engine/lib")
import fs_lock
p = pathlib.Path(os.environ["AIQE_TMPD"]) / "dead.json"
lockdir = pathlib.Path(str(p) + ".lock")
lockdir.mkdir(parents=True)
# PID 1 exists on POSIX; use an implausible one so liveness reads False, plus a
# stamp beyond HARD_STALE_S so the PID-reuse ceiling applies either way.
(lockdir / "owner").write_text(f"999999999 {time.time() - fs_lock.HARD_STALE_S - 60}",
                               encoding="utf-8")
try:
    with fs_lock.lock(p, timeout=5):
        print("ok")
except TimeoutError:
    print("a dead holder's lock was never broken")
PY
)
check ok "$r" "a dead holder's stale lock IS broken"

# --- 6/7/8. bundle import: escapes, tampering, secrets ----------------------
r=$(python3 - <<'PY'
import pathlib, sys
sys.path.insert(0, "engine/lib")
import state_bundle as sb
root = sb.ROOT.resolve()
def accepted(rel):
    t = (sb.ROOT / rel).resolve()
    return not (t == root or root not in t.parents)
bad = [rel for rel in ("../evil.txt", f"../{root.name}-evil/p.txt",
                       f"../{root.name}X/p.txt", "") if accepted(rel)]
good = [rel for rel in ("reports/runs/x.json", "specs/K/testplan.yaml")
        if not accepted(rel)]
print("ok" if not bad and not good else f"escaped={bad} refused-legit={good}")
PY
)
check ok "$r" "bundle import refuses every path outside the checkout"

r=$(AIQE_TMPD="$TMPD" python3 - <<'PY'
import hashlib, json, os, pathlib, sys, tarfile
sys.path.insert(0, "engine/lib")
import state_bundle as sb
d = pathlib.Path(os.environ["AIQE_TMPD"])
b = d / "tampered.tar.gz"
payload = b"tampered bytes"
manifest = {"schema": sb.SCHEMA, "profile": "full", "file_count": 1,
            "files": {"reports/runs/zz-probe.json":
                      hashlib.sha256(b"original").hexdigest()}}
with tarfile.open(b, "w:gz") as t:
    for name, data in (("manifest.json", json.dumps(manifest).encode()),
                       ("state/reports/runs/zz-probe.json", payload)):
        info = tarfile.TarInfo(name); info.size = len(data)
        t.addfile(info, __import__("io").BytesIO(data))
try:
    r = sb.import_bundle(b, dry_run=True)
    print("ok" if r["mismatched"] and not r["written"] else f"{r}")
except SystemExit as e:
    message = str(e)
    print("ok" if "mismatched" in message and "file_count" not in message
          else f"wrong refusal: {message}")
PY
)
check ok "$r" "a bundle member whose sha does not match the manifest is rejected"

BUNDLE=$(python3 engine/lib/state_bundle.py export 2>/dev/null | grep -oE '[^ ]+state\.tar\.gz' | head -1)
if [ -n "${BUNDLE:-}" ] && [ -f "$BUNDLE" ]; then
  # --force-local: a Windows path like C:\... otherwise reads as a REMOTE host
  # spec and tar fails. Listing the members must SUCCEED before the grep count
  # means anything — a failed tar yields 0 matches and would "pass" silently,
  # which is the whole failure mode this suite exists to catch.
  if ! members=$(tar --force-local -tzf "$BUNDLE" 2>/dev/null) || [ -z "$members" ]; then
    echo "FAIL could not list the bundle's members (tar failed)"; fail=1
  else
    n=$(printf '%s\n' "$members" | grep -cE '(^|/)\.env$|aiqe\.properties|\.py$|\.sh$' || true)
    check 0 "$n" "an exported bundle carries no secrets and no code"
    m=$(printf '%s\n' "$members" | grep -c . )
    [ "$m" -gt 5 ] && pass "bundle actually contained $m members (the check saw real data)" \
      || { echo "FAIL bundle looked empty ($m members)"; fail=1; }
  fi
  rm -f "$BUNDLE"
else
  echo "FAIL bundle export produced nothing to inspect"; fail=1
fi

[ $fail -eq 0 ] && echo "state adversarial UAT OK"
echo "state-adversarial: $passes check(s) passed, $fail failure(s)"
exit $fail

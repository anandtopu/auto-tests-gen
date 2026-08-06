"""Suite-wide setup.

The only thing here is a session-start sweep of state a CRASHED or KILLED run
can leave behind on the shared demo estate. Both leftovers below cost real
debugging time once: the symptom lands in an unrelated test, hours later.

  out/.pipeline.lock       held for up to STALE_LOCK_MINUTES (90) after a
                           killed run, so every pipeline-running test crawls
                           or fails on a lock nobody holds
  fixture repos            a test that registers a throwaway repo cleans up in
                           `finally` — which never runs if the process is
                           killed, leaving the repo in the TRACKED registry
                           where the next run's fan-out test resolves it

The sweep CLEANS AND SAYS SO. It deliberately does not run silently: a leak
that a test itself caused is a bug worth seeing, and a printed line keeps it
visible instead of making the suite quietly self-healing.
"""
import os
import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

# --- the transaction log is REDIRECTED for the whole suite ------------------
#
# `event_log` resolves its directory from AIQE_EVENTS_DIR at call time, and no
# test set it — so every emit during a run landed in the estate's REAL audit
# log, including the ~1400 refusals and failures the adversarial suites provoke
# on purpose. A measured run left 2516 events there, 56% of them failures.
#
# Two things break as a result, and the second is the sharp one:
#
#   * The log stops being evidence. docs/observability-epic.md frames it as the
#     record of what happened to this estate; an auditor reading it saw 41
#     attempts to remove a repo and 44 factory-clears that no human ever made.
#   * `make maintain` runs alert_rules.evaluate(), which counts matching events
#     in a window and DELIVERS through the Notify port. So `make review` — the
#     thing you run before claiming anything works — could page somebody with
#     its own attack traffic.
#
# Set at import, not in a fixture: it must be in place before any module-level
# code in a test file reads it, and subprocesses (the dashboard server, the
# receiver) inherit it. An explicit AIQE_EVENTS_DIR from the caller still wins,
# so a test that wants its own directory keeps it.
TEST_EVENTS_DIR = ROOT / "out/test-events"
if not (os.environ.get("AIQE_EVENTS_DIR") or "").strip():
    os.environ["AIQE_EVENTS_DIR"] = str(TEST_EVENTS_DIR)

# Retry RATE-LIMIT counters, redirected for exactly the reason the audit log is.
# retry_policy had no knob at all, so the suite's fixture attempts landed in the
# estate's reports/retries.json and spent a real operator's retry budget —
# measured: the fixture key PROJ-9 accumulated three genuine attempts and then
# `make review` failed because its own earlier runs had used up the limit. A
# limiter anything can fill refuses the wrong person. Set at IMPORT so module
# level code and subprocesses both see it; an explicit value still wins.
TEST_RETRIES_FILE = ROOT / "out/test-retries.json"
if not (os.environ.get("AIQE_RETRIES_FILE") or "").strip():
    os.environ["AIQE_RETRIES_FILE"] = str(TEST_RETRIES_FILE)

# The team REVIEW BOARD, for the fourth time in this shape. review_state has
# honoured AIQE_REVIEWS_FILE since it was written; nothing ever set it, so the
# pytest suite wrote into the estate's real review record. MEASURED: one pytest
# run added 14 history entries to PR-orders-api-201, every one a phantom
# {"release": "", "source": "manual"} — 505 had accumulated, burying the single
# genuine decision under test traffic. In a deployment, `make review` in CI does
# that to the team's board and its audit history.
TEST_REVIEWS_FILE = ROOT / "out/test-reviews.json"
if not (os.environ.get("AIQE_REVIEWS_FILE") or "").strip():
    os.environ["AIQE_REVIEWS_FILE"] = str(TEST_REVIEWS_FILE)

# The PLAN LIFECYCLE — approvals, spec signatures, generated_run links. Fifth
# store in this shape and the one with the most to lose: plan_state records who
# signed off on what. MEASURED by snapshotting the estate, running the suite and
# diffing: PROJ-301's history went from 2 entries to 82 and a stray test key
# `K-1` appeared in the operator's plan store. The status happened to survive
# here, but nothing stopped a test calling approve or revoke on a real key.
# selection.py derives its path from plan_state.DIR, so this redirects both.
TEST_PLAN_DIR = ROOT / "out/test-plans"
if not (os.environ.get("AIQE_PLAN_DIR") or "").strip():
    os.environ["AIQE_PLAN_DIR"] = str(TEST_PLAN_DIR)

# The remaining writable stores that already ship an env knob. The suite was not
# measured writing these (the estate snapshot/diff showed no change to
# queue.json, openhands/state.json or coverage-drift.json), so this is defence
# rather than a fix — but the knobs exist, redirecting costs nothing, and every
# one of the five leaks found this session was a store somebody assumed nothing
# wrote.
for _var, _dest in (("AIQE_QUEUE_FILE", "out/test-queue.json"),
                    ("AIQE_OPENHANDS_DIR", "out/test-openhands"),
                    ("AIQE_DRIFT_FILE", "out/test-coverage-drift.json"),
                    ("AIQE_TESTCASE_PROVENANCE_FILE",
                     "out/test-testcase-provenance.jsonl")):
    if not (os.environ.get(_var) or "").strip():
        os.environ[_var] = str(ROOT / _dest)
# Throwaway repos registered by tests. Keep in sync with the tests that create
# them; an unknown repo is NEVER removed — that would be this file quietly
# deleting somebody's real registry entry.
FIXTURE_REPOS = ("zz-nofetch",)
STALE_LOCK_MINUTES = 90


def _sweep_lock(notes):
    lock = ROOT / "out/.pipeline.lock"
    if not lock.exists():
        return
    age_min = (time.time() - lock.stat().st_mtime) / 60
    if age_min < STALE_LOCK_MINUTES:
        notes.append(f"out/.pipeline.lock is {age_min:.0f} min old and may be "
                     f"LIVE — leaving it (pipeline tests will wait on it)")
        return
    shutil.rmtree(lock, ignore_errors=True)
    notes.append(f"removed a stale out/.pipeline.lock ({age_min:.0f} min old)")


def leftover_fixture_repos(registry_path=None):
    """Which FIXTURE_REPOS are currently registered. Detection is separate from
    removal so it can be tested without a test that edits the real registry."""
    try:
        import yaml
        reg = pathlib.Path(registry_path or ROOT / "registry/repo-registry.yaml")
        data = yaml.safe_load(reg.read_text(encoding="utf-8")) or {}
    except Exception:
        return []                    # an unreadable registry is not ours to fix
    # test_repositories is a LIST of entries, not a name->entry mapping.
    registered = {str((e or {}).get("name") or "")
                  for e in (data.get("test_repositories") or [])}
    return [n for n in FIXTURE_REPOS if n in registered]


def _sweep_registry(notes):
    present = leftover_fixture_repos()
    if not present:
        return
    try:
        import repo_admin
        for name in present:
            try:
                repo_admin.remove_test(name, force=True)
            except SystemExit:
                pass
        notes.append(f"removed leftover fixture repo(s) {', '.join(present)} "
                     f"— a previous run was killed before its cleanup")
    except Exception as e:
        notes.append(f"could NOT remove leftover fixture repo(s) "
                     f"{', '.join(present)}: {e}")


def fixture_tainted_runs(runs_dir=None):
    """Run records produced by a FIXTURE repo, not by real work.

    The fan-out containment test registers an unclonable repo on purpose, so the
    pipeline correctly records `overall: quarantined`. That record then lands in
    the estate's shared run history, where the scorecard counts it: every
    quarantined run in a measured estate was one of these, and the headline read
    "Commit rate: 81% of 16 runs (3 quarantined)". The platform's own quality
    metric was reporting its test scaffolding as product failure.

    Identified by CONTENT — a gate naming a fixture repo — never by age or by
    'looks synthetic'. A real run is never touched.
    """
    import json
    out = []
    d = pathlib.Path(runs_dir) if runs_dir else ROOT / "reports/runs"
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        if p.name in ("reviews.json", "queue.json", "hooks-seen.json"):
            continue                      # shared state files, not run records
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # Shape, not just name. `queue.json` is a LIST, so `rec.get` raised
        # AttributeError and — because this runs in pytest_sessionstart — the
        # whole suite died with an INTERNALERROR pointing here rather than at
        # the file that caused it. The name skip above already excludes today's
        # three; this makes an unexpected shape harmless instead of fatal.
        if not isinstance(rec, dict):
            continue
        gates = rec.get("gates") or []
        if any((g or {}).get("test_repo") in FIXTURE_REPOS for g in gates):
            out.append(p)
    return out


def _sweep_run_records(notes):
    stale = fixture_tainted_runs()
    if not stale:
        return
    removed = 0
    for rec in stale:
        for p in rec.parent.glob(f"{rec.stem}*"):   # record + its archived diffs
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    notes.append(f"removed {removed} file(s) from {len(stale)} run record(s) produced "
                 f"by fixture repo(s) {', '.join(FIXTURE_REPOS)} — they are the test "
                 f"suite's deliberate failures, and the scorecard counted them as ours")


def _sweep_test_events(notes):
    """Start each session with an empty redirected log, so a leak assertion is
    about THIS run and a failed run's events stay inspectable until the next."""
    if os.environ.get("AIQE_EVENTS_DIR") != str(TEST_EVENTS_DIR):
        return                                  # a caller chose their own dir
    if TEST_EVENTS_DIR.exists():
        shutil.rmtree(TEST_EVENTS_DIR, ignore_errors=True)
    TEST_EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    notes.append(f"transaction log redirected to {TEST_EVENTS_DIR.relative_to(ROOT)} "
                 f"— the estate's real audit log is not written by tests")


def pytest_sessionstart(session):
    notes = []
    _sweep_lock(notes)
    _sweep_registry(notes)
    _sweep_run_records(notes)
    _sweep_test_events(notes)
    for n in notes:
        print(f"[conftest] {n}")

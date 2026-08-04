#!/usr/bin/env python3
"""Per-run progress: where is THIS request right now, and if it failed, where.

`wizard_status` answers the JOURNEY question ("has this ticket got a plan, an
approval, tests, a review?"). It deliberately collapses the run itself into one
step — "the agent is analyzing and writing tests" — which is the right grain for
a guided flow and the wrong grain for the two questions a user actually asks
while waiting:

    how far along is it, and which step is it on?
    it failed — which step, why, and where do I look?

This module answers those from state the pipeline already writes. It is
READ-ONLY and adds no instrumentation: every signal below already existed.

  out/<phase>.contract.json     a phase COMPLETED and what it produced
  out/cost.tsv                  per-phase completion timestamp + model + spend
  out/gate_results.tsv          repo, status, exit code, commit
  out/clone_failures.tsv        repos whose clone failed (fan-out containment)
  out/phase-skips.tsv           phases deterministically skipped (no-op)
  out/.pipeline.lock            a run is in progress in this checkout
  out/run-context.json          which run holds it (run id, mode, key)
  reports/<KEY>-<repo>.log      the gate's own log, per repo
  reports/runs/<id>.json        the finished record — authoritative once written

THE STATE MODEL IS THE POINT (constitution C13). A step we cannot observe is
NOT reported as pending or done:

  pending   not started, and we can say so because the run has not reached it
  running   in progress, and the lock is held by a LIVE holder
  done      an artifact or record proves it completed
  failed    it ran and lost — with the exit code's documented meaning
  skipped   deliberately not run (no-op phase, or not in this mode's chain)
  unknown   WE CANNOT TELL. A stale lock, a torn record, a run that vanished.

`unknown` exists because the alternative is a progress bar that says "running"
forever about a process that died twenty minutes ago, which is exactly the
"inability to establish a fact reported as an established negative" this
codebase keeps paying for.
"""
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# Matches pipeline.sh's own break-a-crashed-lock threshold. Above this the
# holder is presumed gone, so its step becomes `unknown`, never `running`.
STALE_LOCK_MINUTES = 90

STATE_FILES = ("reviews.json", "queue.json", "hooks-seen.json")


def _stage(sid, label, why, artifact=None):
    """why = what this step is FOR, in a sentence a non-author can act on."""
    return {"id": sid, "label": label, "why": why, "artifact": artifact}


# The chains as engine/pipeline.sh actually runs them. `plan` stops after the
# adversarial review because that is where it hands off to a human; `tests`
# resumes from an approved plan.
_RESOLVE = _stage("resolve", "Route the request",
                  "Decide which app repos changed and which E2E repos own them. "
                  "Below the confidence threshold this asks a human instead of guessing.",
                  "out/resolve.contract.json")
_GENERATE = _stage("generate", "Write the tests",
                   "Author or extend specs in each resolved test repo, following that "
                   "repo's existing approach.", "out/generate.contract.json")
_VALIDATE = _stage("validate", "Validate and repair",
                   "Run the new specs and repair what does not pass.",
                   "out/validate.contract.json")
_CRITIC = _stage("critic", "Quality critic (advisory)",
                 "Score the specs for defects execution cannot reveal. Never gates a commit.",
                 "out/critic.contract.json")
_GATE = _stage("gate", "Quality gate",
               "The only step that commits or pushes. Scope, born-mapped catalog entry, "
               "lint, execute changed specs, secret scan.")

CHAINS = {
    "pr": [_RESOLVE,
           _stage("triage", "Triage the diff",
                  "Read the real patch hunks and decide what E2E coverage the change needs.",
                  "out/triage.contract.json"),
           _GENERATE, _VALIDATE, _CRITIC, _GATE],
    "jira": [_RESOLVE,
             _stage("analyze", "Analyze the ticket",
                    "Read the ticket, its acceptance criteria and any linked PRD.",
                    "out/analyze.contract.json"),
             _stage("testplan", "Author the test plan",
                    "Turn the ticket into scenarios a human can review.",
                    "out/testplan.contract.json"),
             _stage("planadversary", "Adversarial plan review",
                    "A read-only opponent hunts for what the author missed.",
                    "out/planadversary.contract.json"),
             _stage("planarbiter", "Arbitrate the findings",
                    "Judge each gap and fold accepted scenarios into the plan.",
                    "out/planarbiter.contract.json"),
             _stage("testdata", "Design the test data",
                    "Decide the fixtures the scenarios need.",
                    "out/testdata.contract.json"),
             _GENERATE, _VALIDATE, _CRITIC, _GATE],
}
CHAINS["plan"] = CHAINS["jira"][:5]          # stops at the human approval gate
CHAINS["tests"] = [_RESOLVE, CHAINS["jira"][5], _GENERATE, _VALIDATE, _CRITIC, _GATE]

# Exit codes and what they MEAN, read off engine/gate/gate.sh and
# engine/pipeline.sh rather than remembered. A number with no meaning attached
# is the failure message being withheld from the person who needs it.
EXIT_MEANINGS = {
    2: ("SCOPE_VIOLATION", "The run tried to write outside the test repo's allowed "
        "scope, or a generated filename used unsafe characters."),
    3: ("SECRET_PATTERN", "A secret-shaped string was found in the generated tests. "
        "Nothing was committed."),
    4: ("UNMAPPED_TEST", "A generated spec had no catalog sidecar entry in the same "
        "commit. Every test must be born-mapped."),
    5: ("TESTS_FAILED", "The generated specs did not pass when executed against the "
        "provisioned environment."),
    6: ("GATE_REFUSED", "The gate refused to run: the working copy is not a standalone "
        "test repo, or .ai-qe/config.yaml is not committed."),
    7: ("PUSH_FAILED", "Everything passed but the push was rejected — check credentials "
        "and branch protection on the test repo."),
    8: ("SPEC_UNSATISFIED", "An approved spec scenario is not covered and carries no "
        "valid waiver (spec.enforce is strict)."),
    64: ("INVALID_INPUT", "The mode or key was not accepted. Nothing ran."),
    65: ("NEEDS_CLARIFICATION", "The ticket does not say what should happen. A question "
         "was posted; answer it and re-run."),
    75: ("PIPELINE_BUSY", "Another run holds this checkout's lock, or the directory is "
         "not writable."),
    77: ("BUDGET_EXCEEDED", "The run hit its cost or wall-clock ceiling and was aborted "
         "BEFORE the gate, so nothing was committed."),
}


def explain_exit(code):
    """(name, meaning) for an exit code — `unknown`, never a guess."""
    try:
        code = int(code)
    except (TypeError, ValueError):
        return ("UNKNOWN", "No exit code was recorded for this step.")
    if code == 0:
        return ("OK", "Completed successfully.")
    name, why = EXIT_MEANINGS.get(code, (None, None))
    if name is None:
        return ("UNRECOGNIZED",
                f"Exit code {code} is not one this pipeline documents. Check the step's "
                f"log — it is most likely a crash rather than a refusal.")
    return (name, why)


def _read_json(p):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _lock_state(root=ROOT):
    """('held'|'stale'|'free', age_minutes|None).

    A held-but-stale lock is its own answer: pipeline.sh breaks locks older than
    90 minutes, so past that the holder is gone and anything that looked
    'running' is really `unknown`.
    """
    lock = pathlib.Path(root) / "out/.pipeline.lock"
    if not lock.exists():
        return ("free", None)
    age = (time.time() - lock.stat().st_mtime) / 60
    return ("stale" if age > STALE_LOCK_MINUTES else "held", age)


def _phase_times(root=ROOT):
    """phase -> completion epoch, from out/cost.tsv (column 4). The ledger is
    appended as each phase finishes, so it doubles as a progress trail."""
    out = {}
    p = pathlib.Path(root) / "out/cost.tsv"
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            cols = line.split("\t")
            if len(cols) >= 4 and cols[0]:
                try:
                    out[cols[0]] = int(cols[3])
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def _skipped(root=ROOT):
    out = {}
    try:
        for line in (pathlib.Path(root) / "out/phase-skips.tsv").read_text(
                encoding="utf-8").splitlines():
            cols = line.split("\t")
            if cols and cols[0]:
                out[cols[0]] = cols[1] if len(cols) > 1 else ""
    except OSError:
        pass
    return out


def _gate_rows(root=ROOT):
    rows = []
    try:
        for line in (pathlib.Path(root) / "out/gate_results.tsv").read_text(
                encoding="utf-8").splitlines():
            cols = line.split("\t")
            if len(cols) >= 2 and cols[0]:
                rows.append({"test_repo": cols[0], "status": cols[1],
                             "exit_code": cols[2] if len(cols) > 2 else "",
                             "commit": cols[3] if len(cols) > 3 else ""})
    except OSError:
        pass
    return rows


def log_tail(key, repo, lines=25, root=ROOT):
    """The end of a gate's own log — the thing a user would otherwise ssh for.

    Returns (path, text) with text None when the log is not readable, which is
    NOT the same as an empty log: one means we could not look, the other means
    the step said nothing.
    """
    p = pathlib.Path(root) / f"reports/{key}-{repo}.log"
    try:
        txt = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return (str(p.relative_to(root)), None)
    return (str(p.relative_to(root)), "\n".join(txt[-lines:]))


def _record_for(key=None, run_id=None, root=ROOT):
    """The newest finished record for a key, or one exact run. Defensive: a
    record is written non-atomically, so a racing read skips rather than
    crashing a poll."""
    best = None
    d = pathlib.Path(root) / "reports/runs"
    if not d.is_dir():
        return None
    for f in sorted(d.glob("*.json")):
        if f.name in STATE_FILES:
            continue
        if run_id and f.stem != run_id:
            continue
        rec = _read_json(f)
        if not isinstance(rec, dict):
            continue
        if key and (rec.get("trigger") or {}).get("key") != key:
            continue
        if best is None or rec.get("ts", 0) >= best.get("ts", 0):
            best = rec
    return best


def _summarize(sid, contract):
    if not isinstance(contract, dict):
        return ""
    if sid == "generate":
        tests = contract.get("tests") or []
        created = sum(1 for t in tests if t.get("action") == "created")
        return f"{created} created, {len(tests) - created} updated"
    if sid == "critic":
        return (f"score {contract.get('score')} {contract.get('verdict', '')}"
                f" - {contract.get('noise_count', 0)} flagged").strip()
    if sid == "validate":
        return f"{contract.get('repair_loops', 0)} repair loop(s)"
    if sid == "testplan":
        return f"{len(contract.get('scenarios') or [])} scenario(s)"
    if sid == "resolve":
        return ", ".join(contract.get("test_repos") or []) or "no test repo resolved"
    return ""


def record_mode(rec):
    """The chain a record was produced by. Records store `trigger.type`, not the
    pipeline mode, so a caller reading `rec["mode"]` gets None and renders
    "mode ?" — which is the view admitting it does not know something it can
    work out."""
    return (rec.get("mode")
            or ("jira" if (rec.get("trigger") or {}).get("type") == "jira" else "pr"))


def _steps_from_record(rec, root=ROOT):
    """Authoritative: the run is over and said what happened."""
    chain = CHAINS.get(record_mode(rec), CHAINS["pr"])
    key = (rec.get("trigger") or {}).get("key") or ""
    done = {p.get("name"): (p.get("contract") or {}) for p in rec.get("phases") or []}
    skipped = {s.get("phase"): s.get("reason", "")
               for s in rec.get("skipped_phases") or [] if isinstance(s, dict)}
    gates = rec.get("gates") or []
    steps = []
    for st in chain:
        s = dict(st)
        if st["id"] == "gate":
            if not gates:
                s.update(state="unknown",
                         detail="No gate result was recorded. The run ended before the "
                                "gate - check the earlier steps.")
            else:
                bad = [g for g in gates
                       if g.get("status") not in ("committed", "no_changes")]
                s["state"] = "failed" if bad else "done"
                s["repos"] = []
                for g in gates:
                    name, why = explain_exit(g.get("exit_code"))
                    path, tail = log_tail(key, g.get("test_repo"), root=root)
                    s["repos"].append({"test_repo": g.get("test_repo"),
                                       "status": g.get("status"),
                                       "exit_code": g.get("exit_code"),
                                       "meaning": name, "why": why,
                                       "commit": g.get("commit"),
                                       "log": path, "log_tail": tail})
                s["detail"] = ", ".join(f"{g.get('test_repo')}: {g.get('status')}"
                                        for g in gates)
        elif st["id"] in skipped:
            s.update(state="skipped",
                     detail=skipped[st["id"]] or "no work for this phase")
        elif st["id"] in done:
            s.update(state="done", detail=_summarize(st["id"], done[st["id"]]))
        else:
            # The record is complete, so a phase with no contract genuinely did
            # not run - but WHY is not recorded, so it is not "pending" either.
            s.update(state="unknown",
                     detail="This phase produced no contract and was not recorded as "
                            "skipped. The run most likely ended before reaching it.")
        steps.append(s)
    return steps


def _steps_live(ctx, root=ROOT):
    """A run is in flight. Completion is inferred from artifacts; the FIRST
    incomplete step is the one in progress - but only if a live holder still
    owns the lock."""
    chain = CHAINS.get(ctx.get("mode") or "pr", CHAINS["pr"])
    lock, age = _lock_state(root)
    times, skipped, gates = _phase_times(root), _skipped(root), _gate_rows(root)
    steps, current_taken = [], False
    for st in chain:
        s = dict(st)
        art = st.get("artifact")
        complete = bool(art) and (pathlib.Path(root) / art).exists()
        if st["id"] == "gate":
            complete = bool(gates)
        if st["id"] in skipped:
            s.update(state="skipped",
                     detail=skipped[st["id"]] or "no work for this phase")
        elif complete:
            s.update(state="done", detail="")
            if st["id"] == "gate":
                s["detail"] = ", ".join(f"{g['test_repo']}: {g['status']}" for g in gates)
            ts = times.get(st["id"])
            if ts:
                s["finished_ts"] = ts
        elif not current_taken:
            current_taken = True
            if lock == "held":
                s.update(state="running", detail="in progress")
            else:
                gone = "expired" if lock == "stale" else "released"
                s.update(state="unknown",
                         detail="the run holding this checkout is gone (lock " + gone
                                + ") and this step left no artifact - open the run "
                                  "record or the logs")
        else:
            s.update(state="pending", detail="")
        steps.append(s)
    return steps


def progress(key=None, run_id=None, root=ROOT):
    """Where is this request? `source` names WHERE the answer came from, so a
    caller never has to guess whether it is looking at live or historical
    state."""
    root = pathlib.Path(root)
    ctx = _read_json(root / "out/run-context.json") or {}
    lock, age = _lock_state(root)
    live = (lock in ("held", "stale") and bool(ctx)
            and (not key or ctx.get("key") == key)
            and (not run_id or ctx.get("run_id") == run_id))

    if live:
        return {"source": "live", "run_id": ctx.get("run_id"), "key": ctx.get("key"),
                "mode": ctx.get("mode"), "started_ts": ctx.get("started_ts"),
                "lock": lock, "lock_age_minutes": round(age or 0, 1),
                "busy": lock == "held", "steps": _steps_live(ctx, root),
                "overall": "running" if lock == "held" else "unknown"}

    rec = _record_for(key=key, run_id=run_id, root=root)
    if rec:
        return {"source": "record", "run_id": rec.get("run_id"),
                "key": (rec.get("trigger") or {}).get("key"),
                "mode": record_mode(rec), "started_ts": rec.get("ts"),
                "busy": False, "steps": _steps_from_record(rec, root),
                "overall": rec.get("overall") or "unknown"}

    return {"source": "none", "run_id": None, "key": key, "mode": None, "busy": False,
            "steps": [], "overall": "none",
            "detail": "No run has been recorded for this target in this checkout. "
                      "Queue one from Intake, or check that the key is spelled as the "
                      "run recorded it (PR runs use PR-<repo>-<number>)."}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    out = progress(key=args[0] if args else None)
    if "--json" in sys.argv:
        print(json.dumps(out, indent=2))
    else:
        print(f"{out['key'] or '(none)'}  source={out['source']}  "
              f"overall={out['overall']}")
        for s in out["steps"]:
            print(f"  [{s['state']:<8}] {s['label']:<26} {(s.get('detail') or '')[:70]}")

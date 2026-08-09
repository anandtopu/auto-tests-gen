#!/usr/bin/env python3
"""Wizard progress: ONE aggregated status per key for the guided UI flows.

The wizard (dashboard "Guided run" view) walks a user through the two long
journeys — PR -> analyze -> generate -> review, and JIRA -> plan -> approve ->
generate -> link — which are ASYNC: a queued run takes minutes and an OpenHands
conversation longer still. Rather than teach the page to stitch four stores
together on every poll, this module answers "where is <KEY> right now?" from the
same state everything else uses:

    work_queue  -> is a run queued / running / done for this key?
    run records -> did it produce tests, and what did the gate say?
    plan_state  -> plan drafted / approved / linked / generated
    review_state-> team review status + release

It is READ-ONLY and composition-only: the wizard's buttons call the EXISTING
endpoints (/api/queue, /api/plans/*, /api/review, /api/openhands/agent) — this
just tells the page which step to light up and when to stop polling.

CLI:  wizard_status.py <KEY>
"""
import glob, hashlib, hmac, json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

STATE_FILES = ("reviews.json", "queue.json", "hooks-seen.json")


def _runs_for(key):
    """Every run record for a key, oldest first. Defensive: records are written
    non-atomically (tee), so a racing read must skip, never crash the poll."""
    out = []
    for f in glob.glob(str(ROOT / "reports/runs/*.json")):
        if pathlib.Path(f).name in STATE_FILES:
            continue
        try:
            r = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(r, dict) and r.get("trigger", {}).get("key") == key:
            out.append(r)
    out.sort(key=lambda r: r.get("ts", 0))
    return out


def _queue_for(key):
    import work_queue
    items = [i for i in work_queue.load() if work_queue.key_of(i) == key]
    items.sort(key=lambda i: i.get("ts", 0))
    return items


def _step(state, label, detail="", action=None):
    step = {"state": state, "label": label, "detail": detail}
    if action:
        step["action"] = action
    return step


def build(key, mode="pr"):
    """Stage list + a terminal flag for the given key.

    state per step: pending | running | done | blocked | failed
    `busy` True means the wizard should keep polling.
    """
    import plan_state, review_state, spec_store, spec_workflow
    runs = _runs_for(key)
    queue = _queue_for(key)
    pending = [i for i in queue if i.get("status") in ("queued", "running")]
    failed = [i for i in queue if i.get("status") == "failed"]
    latest = runs[-1] if runs else None
    reviews = review_state.load().get(key, {})

    tests, gates = [], []
    if latest:
        contracts = {p.get("name"): p.get("contract") or {}
                     for p in latest.get("phases", [])}
        tests = contracts.get("generate", {}).get("tests", []) or []
        gates = latest.get("gates", []) or []

    steps = []
    plan_first = mode in ("jira", "pr-plan")
    if plan_first:
        plan = plan_state.get(key)
        status = plan.get("status", "")
        plan_pending = [i for i in pending if i["mode"] == "plan"]
        # PR-keyed plans remain requirements-exempt: their requirements are
        # fused ticket context while the PR diff is the change authority.
        # For JIRA, render this step only when the resolved gate is active.
        if mode == "jira" and spec_workflow.governance()["requirements_gate"]:
            req = spec_store.load_requirements(key)
            blocking = [a for a in (spec_store.ambiguities(key) or [])
                        if isinstance(a, dict) and a.get("blocking")]
            signed_sha = str(plan.get("requirements_sha") or "")
            rp = pathlib.Path(spec_store.requirements_path(key))
            current_sha = hashlib.sha256(rp.read_bytes()).hexdigest() \
                if rp.exists() else ""
            valid_approval = bool(
                plan.get("requirements_status") == "approved"
                and signed_sha and current_sha
                and hmac.compare_digest(signed_sha, current_sha))
            stale = bool(signed_sha and current_sha and not valid_approval)
            action = None
            if req and not blocking and not valid_approval:
                action = {"id": "approve-requirements",
                          "label": "Approve acceptance criteria"}
            if blocking:
                req_state = "blocked"
                detail = (f"planning is blocked by {len(blocking)} unanswered "
                          "acceptance-criteria question(s)")
            elif not req:
                req_state = "blocked"
                detail = "planning is blocked until acceptance criteria are authored and approved"
            elif valid_approval:
                req_state = "done"
                detail = "approved; planning may proceed"
            elif stale:
                req_state = "blocked"
                detail = "planning is blocked because approved acceptance criteria changed"
            elif plan.get("requirements_status") == "approved":
                req_state = "blocked"
                detail = "planning is blocked because acceptance-criteria approval is not signed"
            else:
                req_state = "blocked"
                detail = "planning is blocked until a human approves the acceptance criteria"
            steps.append(_step(req_state, "Validate acceptance criteria", detail, action))
        # Labels are STABLE across states — a step ladder whose rows rename
        # themselves mid-run is unreadable; the state carries the progress.
        if status:
            # The adversarial review is part of what the reviewer is approving, so it
            # belongs on the step they read — not buried in a run log they never open.
            adv = (plan.get("adversary") or "").strip()
            steps.append(_step("done", "Author the test plan",
                               f"testplans/{key}.md ({status})"
                               + (f" — {adv}" if adv else "")))
        elif plan_pending:
            steps.append(_step("running", "Author the test plan",
                               "queued run in progress"))
        else:
            steps.append(_step("pending", "Author the test plan",
                               "queue a plan-only run"))
        if status == "approved":
            steps.append(_step("done", "Human approval",
                               f"approved by {plan.get('by') or 'a reviewer'}"))
        elif status in ("draft", "changes_requested"):
            steps.append(_step("blocked", "Human approval",
                               "review and approve the plan to continue"))
        else:
            steps.append(_step("pending", "Human approval", ""))

    tests_pending = [i for i in pending if i["mode"] in ("pr", "jira", "tests")]
    # PLAN-FIRST KEYS ANSWER FROM THE PLAN, NOT FROM ANY RUN THAT EVER TOUCHED
    # THE KEY. `tests` above is the LATEST run's generate contract, so a run
    # from before the plan was (re-)authored made this step read "done" while
    # plan_state.generated_run was still None — the ladder showed the step
    # AFTER a blocked approval as complete, which reads as the approval gate
    # having been bypassed, and tells a reviewer tests exist for a plan nothing
    # has generated from yet.
    #
    # spec_workflow, agent_context, the Test plans table and `qa.py plans` all
    # read `generated_run`; this view was the last one inferring. Same defect
    # class CLAUDE.md records for spec_workflow ("`generated_run` NOT
    # `generated`, a key nothing writes"), fixed in one view and not the other.
    #
    # PR keys have no plan, so their run-record inference stays: it is the only
    # source there, and it is correct.
    if plan_first:
        gen_run = (plan_state.get(key) or {}).get("generated_run")
        match = next((r for r in runs if r.get("run_id") == gen_run), None) \
            if gen_run else None
        # All generated-run evidence is one correlated fact.  Clearing only
        # tests/gates left the old run id, overall result and agent review in the
        # page ("Last run: committed" on a newly drafted plan).  A dangling
        # generated_run reference must also fail closed instead of falling back
        # to whichever historical run happened to be newest.
        latest = match
        tests, gates = [], []
        if match:
            tests = ({p.get("name"): (p.get("contract") or {})
                      for p in match.get("phases", [])}
                     .get("generate", {}).get("tests", []) or [])
            gates = match.get("gates", []) or []
    if tests:
        created = sum(1 for t in tests if t.get("action") == "created")
        updated = len(tests) - created
        # Reduced-cost mode (cost-reduction 5.3): a degraded run must SAY so
        # where the user reads the result, not only in the raw record.
        degraded = " · reduced-cost mode (near budget)" \
            if (latest or {}).get("degradation") else ""
        skipped = (latest or {}).get("skipped_phases") or []
        skip_note = (" · skipped: " + ", ".join(s["phase"] for s in skipped)
                     if skipped else "")
        steps.append(_step("done", "Generate E2E tests",
                           f"{created} created · {updated} updated"
                           f"{degraded}{skip_note}"))
    elif tests_pending:
        steps.append(_step("running", "Generate E2E tests",
                           "the agent is analyzing and writing tests"))
    elif failed:
        # Say WHY. "run failed — re-queue it" tells the user to repeat the thing
        # that just failed, with no way to know what to change first; the queue now
        # records the actionable reason (work_queue.failure_reason).
        why = (failed[-1].get("error") or "").strip()
        steps.append(_step("failed", "Generate E2E tests",
                           why or "run failed — re-queue it from Intake & queue"))
    else:
        steps.append(_step("pending", "Generate E2E tests", ""))

    # B4 keeps the machine judgement visibly separate from Team review. A
    # needs_work/unavailable verdict is evidence, not a human board transition.
    agent_review = (latest or {}).get("review") or (latest or {}).get("reviewer")
    review_refused = ((latest or {}).get("review_delivery") or {}).get("outcome") == "refused"
    if isinstance(agent_review, dict):
        findings = agent_review.get("findings") or []
        unresolved = agent_review.get("unresolved") or []
        steps.append(_step(
            "failed" if review_refused else "done", "Agent review",
            f"{agent_review.get('verdict', 'unavailable')} · {len(findings)} finding(s) · "
            f"{len(unresolved)} unresolved · policy {agent_review.get('policy', 'not recorded')}"
        ))
    elif tests_pending:
        steps.append(_step("pending", "Agent review", "waiting for generated tests"))
    else:
        steps.append(_step("pending", "Agent review", "no review evidence recorded"))

    if gates:
        ok = [g for g in gates if g.get("status") == "committed"]
        bad = [g for g in gates if g.get("status") in ("quarantined", "clone_failed")]
        steps.append(_step("failed" if bad and not ok else "done", "Quality gate",
                           ", ".join(f"{g.get('test_repo')}: {g.get('status')}"
                                     for g in gates)))
    elif review_refused:
        steps.append(_step("blocked", "Quality gate",
                           "required agent review refused delivery before the gate"))
    else:
        steps.append(_step("pending", "Quality gate", ""))

    rev = reviews.get("status", "")
    if rev == "approved":
        steps.append(_step("done", "Team review", f"approved by {reviews.get('reviewer', '')}"))
    elif rev:
        steps.append(_step("blocked", "Team review", rev.replace("_", " ")))
    else:
        steps.append(_step("pending", "Team review", ""))

    if mode == "jira":
        # J6 is satisfied by TELLING the ticket — a posted comment. An attachment is
        # the richer form of the same step, so either completes it; requiring the
        # attachment alone left the step pending after the user had posted the
        # comment and been told it worked.
        entry = plan_state.get(key)
        linked, commented = entry.get("linked"), entry.get("commented")
        if linked:
            detail = linked.get("ref", "")[:80]
        elif commented:
            detail = (commented.get("result") or "comment posted on the ticket")[:80]
        else:
            detail = ""
        steps.append(_step("done" if (linked or commented) else "pending",
                           "Link plan + tests to the ticket", detail))

    return {"key": key, "mode": mode, "steps": steps,
            "busy": bool(pending),
            "run_id": (latest or {}).get("run_id", ""),
            "overall": (latest or {}).get("overall", ""),
            "release": reviews.get("release", ""),
            "tests": [{"file": t.get("file"), "action": t.get("action")}
                      for t in tests],
            "queue": [{"id": i["id"], "status": i.get("status"), "mode": i["mode"]}
                      for i in queue[-3:]]}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        raise SystemExit("usage: wizard_status.py <KEY> [pr|jira]")
    print(json.dumps(build(sys.argv[1],
                           sys.argv[2] if len(sys.argv) > 2 else "pr"), indent=2))

#!/usr/bin/env python3
"""Why did the AI do that?

Every answer here is ASSEMBLED FROM EVIDENCE THE RUN ALREADY RECORDED. Nothing
is inferred, and nothing is narrated: if the reason for a decision was not
written down, this says so and names what is missing rather than producing a
plausible story. A fabricated rationale is worse than no rationale — it is
confidently wrong about the one thing the reader came to check, and it is
indistinguishable from a real one.

The decisions a user actually asks about, and where each is evidenced:

  routing     which test repos, and how sure           resolve contract
              (confidence + rationale come from
              engine/phases/resolve.py, which is
              deterministic and rules-first)
  context     what the model was SHOWN, and what was   task artifact bundle
              deliberately withheld from it            (live fallback: out/)
  model       which model wrote each phase, and        out/cost.tsv + org-config
              whether a budget rung downgraded it      models: + cost-degrade.tsv
  skipped     phases that did not run, and why         out/phase-skips.tsv
  adversary   what the read-only opponent found and    plan_state.adversary
              what the arbiter accepted
  reuse       whether the plan was written fresh or    out/plan-reuse.json
              adapted from a prior approved plan
  impact      why an existing case was proposed for    run impact_candidates
              extend/replace, or why create was right  artifact
  review      what the agent found, repaired, and left run record review{}
  gate        why tests were or were not committed,    run record gates[] +
              per repo, with the exit code's meaning   run_progress.EXIT_MEANINGS

THE CONTEXT MANIFEST IS THE IMPORTANT ONE. It names every knowledge chunk the
phase was given AND every chunk that was dropped to fit the budget. "The model
never saw the payments-api surface" explains an omission that no amount of
staring at the output would.

For B2 runs the manifest is content-addressed and survives scratch cleanup.
Older/default-off runs still report it as unavailable and say why — because
"we did not keep it" and "nothing was dropped" are different facts.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import record_caveats  # noqa: E402  (what the record says it is missing)
import run_progress  # noqa: E402
import test_reviewer as reviewer_lib  # noqa: E402  (shared record reading + exit-code meanings)
import task_bundle  # noqa: E402
import ticket_discovery  # noqa: E402
import ticket_comment  # noqa: E402


def _decision(did, question, answer, because=None, evidence=None, caveat=None):
    """because = the facts that produced the answer, each already recorded."""
    return {"id": did, "question": question, "answer": answer,
            "because": because or [], "evidence": evidence, "caveat": caveat}


def _unknown(did, question, why_not):
    """A decision we cannot explain. Named, with the reason it is unanswerable —
    never omitted, because a missing row reads as 'nothing happened here'."""
    return {"id": did, "question": question, "answer": None,
            "because": [], "evidence": None, "not_recorded": why_not}


def _read_state(p, sink=None):
    """(data, state) where state is ``ok`` | ``absent`` | ``unreadable``.

    Absent and unreadable are DIFFERENT answers and this module exists to keep
    answers honest. Collapsing them made every reader of a damaged file hear
    "it was never recorded" — so an operator went looking for a phase that had
    failed to persist something, when the file was sitting right there with a
    truncated last line. That is the C13 shape in the surface whose whole job
    is explaining what happened.

    Unreadable paths are appended to `sink` so a caller can NAME them even
    where no message claims absence: six call sites fold a damaged file into
    `{}`, which silently costs a decision row rather than producing a wrong
    one. The sink is passed in rather than kept on the module because
    /api/explain is served from a threaded server — a module-level list would
    let one request report another request's damaged files.
    """
    path = pathlib.Path(p)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "absent"
    except OSError:
        # Exists (or we cannot even tell), but unreadable. NOT the same as absent.
        if sink is not None:
            sink.append(str(path))
        return None, "unreadable"
    try:
        return json.loads(raw), "ok"
    except ValueError:
        if sink is not None:
            sink.append(str(path))
        return None, "unreadable"


def _read_json(p, sink=None):
    return _read_state(p, sink)[0]


def context_manifests(root=ROOT):
    """Per phase: which knowledge chunks were kept, and which were dropped.

    context_scope writes the manifest into the file's own header precisely so
    this is answerable. Returns {} when no scoped context exists — that is a
    real answer (the phase got the FULL estate), distinct from "we lost it".
    """
    out = {}
    for p in sorted(pathlib.Path(root).glob("out/context-*.md")):
        phase = p.stem.replace("context-", "")
        try:
            head = p.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            continue
        if "context-scope" not in head:
            continue
        kept = dropped = ""
        budget = used = None
        for line in head.splitlines():
            if "kept=" in line:
                kept = line.split("kept=", 1)[1].strip()
            elif "dropped=" in line:
                dropped = line.split("dropped=", 1)[1].strip()
            if "budget_tokens=" in line:
                try:
                    budget = int(line.split("budget_tokens=")[1].split()[0])
                except (ValueError, IndexError):
                    budget = None
            if "used_chars=" in line:
                try:
                    used = int(line.split("used_chars=")[1].split()[0])
                except (ValueError, IndexError):
                    used = None
        def clean(value):
            return [
                component.strip()
                for component in value.replace("-->", "").split(",")
                if component.strip()
            ]

        out[phase] = {"kept": clean(kept), "dropped": clean(dropped),
                      "budget_tokens": budget, "used_chars": used}
    return out


def _phase_models(root=ROOT):
    """phase -> model, from the budget ledger the run wrote as it went."""
    out = {}
    try:
        for line in (pathlib.Path(root) / "out/cost.tsv").read_text(
                encoding="utf-8").splitlines():
            c = line.split("\t")
            if len(c) >= 5 and c[0]:
                out[c[0]] = c[4]
    except OSError:
        pass
    return out


def _degradation(root=ROOT):
    rungs = []
    try:
        for line in (pathlib.Path(root) / "out/cost-degrade.tsv").read_text(
                encoding="utf-8").splitlines():
            c = line.split("\t")
            if c and c[0]:
                rungs.append(c)
    except OSError:
        pass
    return rungs


def explain(key=None, run_id=None, root=ROOT):
    """The decision list for one request, plus what could not be explained."""
    root = pathlib.Path(root)
    rec = run_progress._record_for(key=key, run_id=run_id, root=root)
    damaged = []          # inputs that exist but could not be parsed
    ctx = _read_json(root / "out/run-context.json", damaged) or {}
    live = bool(ctx) and (not key or ctx.get("key") == key)
    if rec:
        # A current scratch directory for another run of the SAME key is still
        # another run. Historical explain must never borrow it.
        live = live and ctx.get("run_id") == rec.get("run_id")

    if not rec and not live:
        # "We found no record" and "we could not read the records" are different
        # answers, and this is the outermost one — the sibling of the same bug
        # fixed in _read_state below. The record search skips a damaged file
        # silently (it cannot match a key it cannot parse), so without this a
        # run that DID happen is reported as one that never did.
        broken = run_progress.unreadable_records(root)
        detail = ("No run has been recorded for this target, so there is "
                  "nothing to explain yet.")
        if broken:
            detail = (f"No READABLE run record matched this target, but "
                      f"{len(broken)} run record(s) exist that could not be "
                      f"parsed: {', '.join(broken)}. One of them may be the run "
                      f"you are asking about — this is not the same as the run "
                      f"never having happened.")
        return {"key": key, "run_id": run_id, "source": "none", "decisions": [],
                "unexplained": [], "unreadable_records": broken,
                "detail": detail}

    contracts = {}
    if rec:
        contracts = {p.get("name"): (p.get("contract") or {})
                     for p in rec.get("phases") or []}
    key = key or (rec or {}).get("trigger", {}).get("key") or ctx.get("key")
    decisions, unexplained = [], []

    # --- 1. routing ---------------------------------------------------------
    resolve = contracts.get("resolve")
    resolve_state = "ok" if resolve else None
    if not resolve:
        resolve, resolve_state = _read_state(
            root / "out/resolve.contract.json", damaged)
        resolve = resolve or {}
    trepos = resolve.get("test_repos") or []
    conf, why = resolve.get("confidence"), resolve.get("rationale")
    if trepos or resolve:
        because = [f"resolved test repo: {r}" for r in trepos] or \
                  ["no test repo resolved for this change"]
        if resolve.get("source_repos"):
            because.insert(0, "changed app repo(s): "
                              + ", ".join(resolve["source_repos"]))
        for repo in resolve.get("uncovered_sources") or []:
            because.append(f"{repo}: NO test repo covers it, so this run "
                           f"generated nothing for it (onboard a test repo, or "
                           f"extend an existing repo's `scope`)")
        for repo in resolve.get("layer_filtered_sources") or []:
            because.append(f"{repo}: covered, but excluded by a restrict_layers "
                           f"label on this ticket - deliberate, not a gap")
        if why:
            because.append(f"rule that fired: {why}")
        else:
            because.append("the rule that fired was not recorded on this run "
                           "(resolve.py emits `rationale`; this record predates "
                           "it or the phase did not persist it)")
        decisions.append(_decision(
            "routing",
            "Which E2E test repositories were chosen, and how sure was it?",
            (", ".join(trepos) or "none")
            + (f"  (confidence {conf})" if conf is not None else "  (confidence not recorded)"),
            because, "resolve contract (deterministic, rules-first)",
            caveat=None if conf is not None else
            "Confidence was not recorded, so 'how sure' cannot be answered from "
            "this run — only WHICH repos it picked."))
    else:
        unexplained.append(_unknown(
            "routing", "Which E2E test repositories were chosen, and why?",
            "No resolve contract was kept for this run."
            if resolve_state != "unreadable" else
            "The resolve contract for this run EXISTS but could not be read "
            "(out/resolve.contract.json is damaged), so routing cannot be "
            "explained from it. This is not the same as it never having been "
            "recorded — do not go looking for a phase that failed to persist "
            "it."))

    # --- PR ticket discovery -----------------------------------------------
    discovery = (rec or {}).get("ticket_discovery") or (
        _read_json(root / "out/ticket-discovery.json", damaged) if live else {}) or {}
    if discovery.get("artifact") == "pr-ticket-discovery":
        outcome = discovery.get("outcome") or "unexplained"
        selected = discovery.get("selected_key")
        because = []
        for row in discovery.get("candidates") or []:
            if not isinstance(row, dict):
                continue
            because.append(f"{row.get('key', '?')}: "
                           f"signals={','.join(row.get('signals') or []) or 'none'}, "
                           f"validation={row.get('validation') or 'not recorded'}")
        because.append(f"selection rule: {discovery.get('reason') or 'not recorded'}")
        selected_ticket = ticket_discovery.recorded_selected_ticket(discovery)
        if selected:
            because.append(
                "selected ticket status: "
                f"{selected_ticket.get('status') or 'unavailable'} "
                f"(state={selected_ticket.get('status_state') or 'unavailable'}, "
                f"terminal={bool(selected_ticket.get('terminal'))})"
            )
        terminal_caveat = (
            ticket_discovery.TERMINAL_WARNING
            if selected_ticket.get("terminal") else None
        )
        decisions.append(_decision(
            "ticket-discovery",
            "Why did this PR run use, or refuse to use, a JIRA ticket?",
            selected or outcome.replace("_", " "), because,
            "run-record ticket_discovery (SCM signals + Tracker validation)",
            caveat=(terminal_caveat or
                    "No inferred ticket text is trusted until Tracker get_item "
                    "validates the key; ambiguity proceeds without a ticket.")))

    # --- PR ticket context fusion ------------------------------------------
    fusion = (rec or {}).get("ticket_context") or {}
    if live and not fusion:
        phases = {}
        expected_ticket = discovery.get("selected_key")
        for phase in ("triage", "generate"):
            manifest = _read_json(root / f"out/pr-ticket-fused-{phase}.json", damaged) or {}
            if (manifest.get("artifact") == "pr-ticket-context"
                    and manifest.get("phase") == phase
                    and manifest.get("selected_key") == expected_ticket):
                phases[phase] = manifest
        if expected_ticket:
            names = set(phases)
            state = ("fused" if names == {"triage", "generate"}
                     else "partial" if names else "unavailable")
            fusion = {"state": state, "selected_key": expected_ticket,
                      "phases": phases}
    if fusion.get("state") in ("fused", "partial", "unavailable"):
        because = []
        for phase, manifest in sorted((fusion.get("phases") or {}).items()):
            because.append(
                f"{phase}: included={','.join(manifest.get('included_fields') or []) or 'none'}; "
                f"omitted={','.join(manifest.get('omitted_fields') or []) or 'none'}; "
                f"scoped={bool(manifest.get('scoped'))}")
        selected_ticket = ticket_discovery.recorded_selected_ticket(discovery)
        decisions.append(_decision(
            "ticket-context-fusion",
            "Which validated ticket requirements were shown to PR authoring phases?",
            f"Ticket {fusion.get('selected_key') or 'unknown'} fusion state: "
            f"{fusion.get('state')}; phases: "
            f"{', '.join(sorted((fusion.get('phases') or {}).keys())) or 'none'}.",
            because, "run-record ticket_context manifests",
            caveat=((ticket_discovery.TERMINAL_WARNING + " "
                     "Acceptance criteria are mandatory; description/comments "
                     "may be omitted by budget.")
                    if selected_ticket.get("terminal") else
                    "Acceptance criteria are mandatory; description/comments may be omitted by budget.")))

    # --- context: what the model saw, and what it did NOT -------------------
    manifests, manifest_error = {}, None
    manifest_evidence = ""
    pointer = (rec or {}).get("artifact_bundle") or {}
    if rec and pointer.get("state") == "produced":
        manifests, manifest_error = task_bundle.context_manifests(
            pointer, root=root, expected_run_id=rec.get("run_id"),
            expected_key=(rec.get("trigger") or {}).get("key"))
        manifest_evidence = "content-addressed task artifact bundle"
    elif live:
        manifests = context_manifests(root)
        manifest_evidence = "live out/context-<phase>.md audit manifest"
    if manifests:
        for phase, m in sorted(manifests.items()):
            if m.get("full_estate"):
                decisions.append(_decision(
                    f"context:{phase}",
                    f"What was the model shown for the `{phase}` phase?",
                    "full estate guidance (scoped context was not used)",
                    ["the complete estate AGENTS.md was supplied",
                     "no scoped context manifest existed, so no dropped-chunk "
                     "claim is made"], manifest_evidence,
                    caveat="This is an explicit full-estate fallback, not a claim "
                           "that scoped retrieval kept every chunk."))
                continue
            because = [f"{len(m['kept'])} knowledge chunk(s) supplied"]
            if m["dropped"]:
                because.append("WITHHELD to fit the budget: " + ", ".join(m["dropped"]))
            else:
                because.append("nothing was dropped — everything relevant fit")
            if m["budget_tokens"]:
                because.append(f"budget {m['budget_tokens']} tokens, "
                               f"{m['used_chars']} chars used")
            decisions.append(_decision(
                f"context:{phase}",
                f"What was the model shown for the `{phase}` phase?",
                f"{len(m['kept'])} chunk(s) kept, {len(m['dropped'])} dropped",
                because, manifest_evidence,
                caveat=("A dropped chunk is knowledge the model DID NOT HAVE. If an "
                        "expected behaviour is missing from the output, look here "
                        "first.") if m["dropped"] else None))
    elif live or rec:
        if manifest_error:
            why = ("The recorded task bundle could not be verified: "
                   f"{manifest_error}. No live scratch was substituted.")
        elif pointer.get("state") in ("disabled", "unavailable"):
            why = (pointer.get("reason") or "The task bundle was unavailable") + \
                  ". No historical context manifest was retained."
        else:
            why = ("This record predates the task artifact bundle and its out/ "
                   "manifests are gone. Re-run with AIQE_ARTIFACT_STORE=1 to "
                   "retain them.")
        unexplained.append(_unknown(
            "context", "What knowledge was the model given, and what was withheld?",
            why))

    # --- 3. model per phase, and any budget downgrade ------------------------
    models = _phase_models(root)
    rungs = _degradation(root)
    if models:
        because = [f"{ph}: {mdl}" for ph, mdl in sorted(models.items())]
        if rungs:
            because.append("a budget rung DOWNGRADED non-judgement phases: "
                           + "; ".join(" ".join(r) for r in rungs))
        else:
            because.append("no budget rung fired — every phase ran at its "
                           "configured tier")
        decisions.append(_decision(
            "model", "Which model wrote each phase, and was it downgraded?",
            f"{len(models)} phase(s) recorded",
            because, "out/cost.tsv (the ledger the run wrote as it went) "
                     "+ org-config models:",
            caveat="Judgement phases (testplan, the adversary pair, generate) are "
                   "never downgraded by the ladder — only the others."))

    # --- 4. phases that did not run ------------------------------------------
    skips = (rec or {}).get("skipped_phases") or []
    if skips:
        decisions.append(_decision(
            "skipped", "Which phases were skipped, and why?",
            ", ".join(s.get("phase", "?") for s in skips if isinstance(s, dict)),
            [f"{s.get('phase')}: {s.get('reason') or 'no reason recorded'}"
             for s in skips if isinstance(s, dict)],
            "run record `skipped_phases`",
            caveat="A skip is a DETERMINISTIC decision (a no-op phase), not a "
                   "failure — the critic with zero generated tests has nothing "
                   "to score."))

    # --- 5. the adversarial plan review --------------------------------------
    try:
        import plan_state
        plan = plan_state.get(key) or {}
    except Exception:
        plan = {}
    if plan.get("adversary"):
        # The `because` list must not repeat the answer: `plan["adversary"]` is
        # already passed as the answer below, so listing it here printed the
        # same sentence twice and told the reader nothing the second time.
        because = []
        detail = plan.get("adversary_detail")
        if isinstance(detail, dict):
            # Render the gaps. This used to be `str(detail)[:400]`, which put a
            # raw Python dict repr in front of an operator and cut it off mid
            # structure -- while the thing they came to read (what did the
            # opponent actually find?) sat inside it, already structured.
            #
            # dict_rows() because adversary_detail is LLM output that reached
            # disk: CLAUDE.md records one malformed entry taking `bin/
            # dashboard.py` down for every run, not just the bad one. Each gap
            # is bounded individually, so a long rationale cannot truncate the
            # NEXT gap away -- the failure mode of a single blob cap.
            for g in run_progress.dict_rows(detail.get("gaps")):
                title = str(g.get("title") or "untitled gap")[:160]
                cat = str(g.get("category") or "uncategorised")[:40]
                sev = str(g.get("severity") or "unrated")[:20]
                why = str(g.get("rationale") or "")[:200]
                because.append(f"gap [{cat}, {sev}]: {title}"
                               + (f" — {why}" if why else ""))
            acc, rej = detail.get("accepted"), detail.get("rejected")
            if acc is not None or rej is not None:
                because.append(f"arbiter: {acc if acc is not None else '?'} "
                               f"accepted, {rej if rej is not None else '?'} "
                               f"rejected")
        elif detail:
            # Not a mapping. Say what it is rather than str()-ing it at the
            # reader; an unexpected shape is a fact about the record.
            because.append(f"the recorded adversary detail is a "
                           f"{type(detail).__name__}, not the expected mapping, "
                           f"so its findings cannot be listed")
        decisions.append(_decision(
            "adversary",
            "What did the adversarial reviewer find, and what was accepted?",
            plan["adversary"], because, "plan state (recorded at author time)",
            caveat="The adversary is READ-ONLY and may only ADD scenarios; it "
                   "runs BEFORE human approval, so it changes what you review, "
                   "never whether you are asked."))

    # --- 6. was the plan written fresh? --------------------------------------
    reuse = _read_json(root / "out/plan-reuse.json", damaged) or {}
    if reuse.get("reused_from"):
        decisions.append(_decision(
            "reuse", "Was this plan authored from scratch?",
            f"adapted from {reuse['reused_from']} "
            f"(similarity {reuse.get('similarity')})",
            ["no LLM call was made for the plan — that is the saving",
             "the adaptation is deterministic text surgery, not a model rewrite",
             "the adversary still challenged the adapted draft"],
            "out/plan-reuse.json",
            caveat="Reuse can never auto-approve: the result lands as a DRAFT."))
    elif plan.get("status"):
        decisions.append(_decision(
            "reuse", "Was this plan authored from scratch?",
            "yes — authored fresh by the testplan phase",
            ["no prior approved plan cleared the similarity threshold, or reuse "
             "is disabled (AIQE_PLAN_REUSE)"],
            "absence of out/plan-reuse.json"))

    # --- 7. change-to-test impact proposal ----------------------------------
    impact = (rec or {}).get("impact_candidates") or {}
    if not impact and live:
        impact = _read_json(root / "out/impact-candidates.json", damaged) or {}
    if impact.get("artifact") == "impact-candidates":
        threshold = impact.get("active_threshold")
        accepted = [c for c in impact.get("candidates") or []
                    if c.get("recommendation") in ("extend", "replace")
                    and (threshold is None or c.get("confidence", 0) >= threshold)]
        if accepted:
            answer = "; ".join(
                f"{c.get('recommendation')} {c.get('test_repo')}/{c.get('file')} "
                f"(confidence {c.get('confidence')})" for c in accepted[:5])
            because = [c.get("reason") or "reason not recorded" for c in accepted[:5]]
        else:
            none = impact.get("no_candidate") or {}
            answer = none.get("message") or "no candidate decision was recorded"
            because = [none.get("reason") or "the absence reason was not recorded"]
        because.append(f"retrieval mode: {impact.get('retrieval_mode')} "
                       f"(threshold {threshold})")
        caught = impact.get("should_have_caught")
        if caught:
            because.append("bug check: " + str(caught.get("message") or
                                                "result not recorded"))
        decisions.append(_decision(
            "impact", "Why was an existing test extended/replaced, or a new one created?",
            answer, because, "run record `impact_candidates`",
            caveat="This is a bounded PROPOSAL only; generation authors changes "
                   "and the deterministic gate alone commits them."))

    # --- 8. durable artifact reuse -------------------------------------------
    artifact_reuse = (rec or {}).get("artifact_reuse") or {}
    reuse_events = artifact_reuse.get("events") or []
    reuse_events = ([event for event in reuse_events if isinstance(event, dict)][:25]
                    if isinstance(reuse_events, list) else [])
    if reuse_events:
        hits = [event for event in reuse_events if event.get("outcome") == "hit"]
        because = []
        for event in reuse_events:
            detail = f"{event.get('phase', '?')}: {event.get('outcome', '?')} — " \
                     f"{event.get('reason') or 'no reason recorded'}"
            if event.get("outcome") == "hit":
                detail += (f"; {event.get('tokens_avoided', 0)} tokens avoided "
                           f"({event.get('token_basis') or 'estimated'})")
            because.append(detail)
        decisions.append(_decision(
            "artifact-reuse", "Were durable artifacts reused, missed, or refused?",
            f"{len(hits)} artifact(s) reused; "
            f"{artifact_reuse.get('tokens_avoided', 0)} tokens avoided",
            because, "run record `artifact_reuse`",
            caveat="Phase-cache-owned hits are named but count as zero artifact "
                   "reuse; generate/validate are always refused."))

    # --- 9. generated-test agent review -------------------------------------
    review = (rec or {}).get("review") or (rec or {}).get("reviewer")
    if isinstance(review, dict):
        findings = [f for f in review.get("findings") or [] if isinstance(f, dict)]
        unresolved = [f for f in review.get("unresolved") or [] if isinstance(f, dict)]
        try:
            loops = max(0, int(review.get("loops") or 0))
        except (TypeError, ValueError):
            loops = 0
        because = []
        for finding in findings[:10]:
            location = "/".join(filter(None, [str(finding.get("repo") or ""),
                                                str(finding.get("file") or "")]))
            because.append(
                f"finding{(' at ' + location) if location else ''}: "
                f"{str(finding.get('finding') or 'detail not recorded')[:400]} — "
                f"named fix: {str(finding.get('fix') or 'not recorded')[:400]}"
            )
        if not findings:
            because.append("the reviewer recorded no findings")
        because.append(
            f"repairs: {loops} review repair loop(s); "
            + (f"{len(unresolved)} finding(s) survived"
               if unresolved else "no unresolved finding was recorded")
        )
        decisions.append(_decision(
            "review", "What did the agent reviewer find, repair, and leave unresolved?",
            f"{reviewer_lib.verdict_text(review, marker='')} under policy "
            f"{review.get('policy', 'not_recorded')}"
            + (" - SIMULATED: a mock reviewer, not a real review"
               if reviewer_lib.simulated(review) else ""),
            because, "run record `review`",
            caveat="This verdict is context for a human. It never sets Approved or "
                   "Changes requested on the team review board."))

    # --- 10. requester notification -----------------------------------------
    comments = [c for c in ((rec or {}).get("comments") or [])
                if isinstance(c, dict)]
    if not comments and live:
        comments, _ = ticket_comment.read_attempts(
            root / "out/comment-attempts.jsonl", ctx.get("run_id"))
    if comments:
        failures = [c for c in comments if c.get("outcome") == "failed"]
        because = [f"{c.get('kind', '?')} -> {c.get('target', '?')}: "
                   f"{c.get('outcome', 'unknown')}"
                   + (f" ({c.get('failure_detail')})"
                      if c.get("failure_detail") else "")
                   for c in comments]
        answer = ("the requester was not notified — comment failed: "
                  + "; ".join(str(c.get("failure_detail") or "unknown failure")
                              for c in failures)) if failures else \
                 f"{len(comments)} ticket comment attempt(s) recorded; none failed"
        decisions.append(_decision(
            "notification", "Was the requester notified on the ticket?", answer,
            because, "run record `comments` + `ticket.comment` event",
            caveat="Ticket comments are best-effort and never alter the run verdict."))
    try:
        malformed_comments = max(
            0, int((rec or {}).get("malformed_comment_lines") or 0))
    except (TypeError, ValueError):
        malformed_comments = 0
    if malformed_comments:
        unexplained.append(_unknown(
            "notification-integrity",
            "Is the recorded ticket-notification history complete?",
            f"{malformed_comments} comment receipt line(s) were unreadable; valid "
            "attempts remain visible, but the history is explicitly incomplete."))

    # --- 11. the gate --------------------------------------------------------
    gates = (rec or {}).get("gates") or []
    if gates:
        because = []
        for g in gates:
            name, meaning = run_progress.explain_exit(g.get("exit_code"))
            because.append(f"{g.get('test_repo')}: {g.get('status')} "
                           f"({name}) — {meaning}")
        committed = [g for g in gates if g.get("status") == "committed"]
        # `len(gates)` is the DENOMINATOR of the headline answer, and it is the
        # count of rows that survived parsing — so a torn gate_results.tsv line
        # turns "1 of 2 committed" into "1 of 1 committed", which reads as a
        # complete run. This file already refuses to do that twelve lines above
        # for comment receipts; the gate block did not.
        short = record_caveats.gates_note(rec)
        answer = f"{len(committed)} of {len(gates)} repo(s) committed"
        if short:
            answer += " — OF THE RESULTS THAT SURVIVED PARSING, not of the run"
            because.append(short)
        gate_caveat = ("The gate is deterministic and is the ONLY step that "
                       "commits or pushes. No LLM phase can influence its "
                       "verdict.")
        if short:
            gate_caveat = short + ". " + gate_caveat
        decisions.append(_decision(
            "gate", "Why were the tests committed, or not?", answer,
            because, "run record gates[] + the gate's documented exit codes",
            caveat=gate_caveat))

    # Damaged inputs are named even where nothing above claimed absence. Most
    # of these reads fold a bad file into `{}`, which costs a decision row
    # silently — the reader simply never learns the question was askable. One
    # row saying WHICH file is unreadable beats several rows quietly missing.
    if damaged:
        seen = sorted(set(damaged))
        unexplained.append(_unknown(
            "inputs", "Were all the recorded inputs for this run readable?",
            f"{len(seen)} recorded input(s) exist but could not be parsed: "
            + ", ".join(seen)
            + ". Anything they would have explained is missing from this "
              "answer — these were NOT absent, so the phases that write them "
              "did run."))

    return {"key": key, "run_id": (rec or {}).get("run_id") or ctx.get("run_id"),
            "source": "record" if rec else "live",
            "decisions": decisions, "unexplained": unexplained}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    out = explain(key=args[0] if args else None)
    if "--json" in sys.argv:
        print(json.dumps(out, indent=2))
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(f"Why AI QE did what it did for {out.get('key')} "
              f"(source: {out['source']})\n")
        for d in out["decisions"]:
            print(f"  {d['question']}")
            print(f"    -> {d['answer']}")
            for b in d["because"]:
                print(f"       - {b}")
            if d.get("caveat"):
                print(f"       ({d['caveat']})")
            print()
        for u in out["unexplained"]:
            print(f"  {u['question']}")
            print(f"    -> NOT RECORDED: {u['not_recorded']}\n")
        # `detail` is the WHOLE answer when no readable record matched, and it
        # was never printed: `make explain KEY=x` for an unrecorded key emitted
        # the header above and then nothing at all. The C13 work that made this
        # message honest ("N run record(s) exist that could not be parsed",
        # rather than "no run was recorded") was invisible for the same reason.
        # A tool holding the answer and not saying it is the defect class this
        # repo keeps finding — see `make batch-drain` and `make maintain`.
        if out.get("detail"):
            print(f"  {out['detail']}\n")
        if not out["decisions"] and not out["unexplained"] \
                and not out.get("detail"):
            print("  Nothing was recorded for this target, and no reason for "
                  "that was recorded either.\n")

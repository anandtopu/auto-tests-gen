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

import run_progress  # noqa: E402  (shared record reading + exit-code meanings)
import task_bundle  # noqa: E402


def _decision(did, question, answer, because=None, evidence=None, caveat=None):
    """because = the facts that produced the answer, each already recorded."""
    return {"id": did, "question": question, "answer": answer,
            "because": because or [], "evidence": evidence, "caveat": caveat}


def _unknown(did, question, why_not):
    """A decision we cannot explain. Named, with the reason it is unanswerable —
    never omitted, because a missing row reads as 'nothing happened here'."""
    return {"id": did, "question": question, "answer": None,
            "because": [], "evidence": None, "not_recorded": why_not}


def _read_json(p):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


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
        clean = lambda s: [c.strip() for c in s.replace("-->", "").split(",") if c.strip()]
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
    ctx = _read_json(root / "out/run-context.json") or {}
    live = bool(ctx) and (not key or ctx.get("key") == key)
    if rec:
        # A current scratch directory for another run of the SAME key is still
        # another run. Historical explain must never borrow it.
        live = live and ctx.get("run_id") == rec.get("run_id")

    if not rec and not live:
        return {"key": key, "run_id": run_id, "source": "none", "decisions": [],
                "unexplained": [],
                "detail": "No run has been recorded for this target, so there is "
                          "nothing to explain yet."}

    contracts = {}
    if rec:
        contracts = {p.get("name"): (p.get("contract") or {})
                     for p in rec.get("phases") or []}
    key = key or (rec or {}).get("trigger", {}).get("key") or ctx.get("key")
    decisions, unexplained = [], []

    # --- 1. routing ---------------------------------------------------------
    resolve = contracts.get("resolve") or _read_json(root / "out/resolve.contract.json") or {}
    trepos = resolve.get("test_repos") or []
    conf, why = resolve.get("confidence"), resolve.get("rationale")
    if trepos or resolve:
        because = [f"resolved test repo: {r}" for r in trepos] or \
                  ["no test repo resolved for this change"]
        if resolve.get("source_repos"):
            because.insert(0, "changed app repo(s): "
                              + ", ".join(resolve["source_repos"]))
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
            "No resolve contract was kept for this run."))

    # --- PR ticket discovery -----------------------------------------------
    discovery = (rec or {}).get("ticket_discovery") or (
        _read_json(root / "out/ticket-discovery.json") if live else {}) or {}
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
        decisions.append(_decision(
            "ticket-discovery",
            "Why did this PR run use, or refuse to use, a JIRA ticket?",
            selected or outcome.replace("_", " "), because,
            "run-record ticket_discovery (SCM signals + Tracker validation)",
            caveat=("No inferred ticket text is trusted until Tracker get_item "
                    "validates the key; ambiguity proceeds without a ticket.")))

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
        because = [plan["adversary"]]
        detail = plan.get("adversary_detail")
        if detail:
            because.append(str(detail)[:400])
        decisions.append(_decision(
            "adversary",
            "What did the adversarial reviewer find, and what was accepted?",
            plan["adversary"], because, "plan state (recorded at author time)",
            caveat="The adversary is READ-ONLY and may only ADD scenarios; it "
                   "runs BEFORE human approval, so it changes what you review, "
                   "never whether you are asked."))

    # --- 6. was the plan written fresh? --------------------------------------
    reuse = _read_json(root / "out/plan-reuse.json") or {}
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
        impact = _read_json(root / "out/impact-candidates.json") or {}
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

    # --- 9. the gate ---------------------------------------------------------
    gates = (rec or {}).get("gates") or []
    if gates:
        because = []
        for g in gates:
            name, meaning = run_progress.explain_exit(g.get("exit_code"))
            because.append(f"{g.get('test_repo')}: {g.get('status')} "
                           f"({name}) — {meaning}")
        committed = [g for g in gates if g.get("status") == "committed"]
        decisions.append(_decision(
            "gate", "Why were the tests committed, or not?",
            f"{len(committed)} of {len(gates)} repo(s) committed",
            because, "run record gates[] + the gate's documented exit codes",
            caveat="The gate is deterministic and is the ONLY step that commits "
                   "or pushes. No LLM phase can influence its verdict."))

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

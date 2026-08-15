#!/usr/bin/env python3
"""Aggregate the PoC scorecard (architecture §8) from benchmark replays,
persisted run records, review states, and test health."""
import glob
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")   # Windows consoles default to cp1252
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine/lib"))
import app_paths  # noqa: E402  # R12: mutable paths resolve here
import review_state  # noqa: E402
import phase_provenance  # noqa: E402  (one definition of measured|simulated)


def _has_measured_spend(run):
    """Did a REAL model do the work in this run?

    The previous test -- `isinstance(run["cost_usd"], (int, float))` -- was a
    proxy for "metered", and it is wrong in the direction that matters:
    AIQE_MOCK_PHASE_COST makes a mock run record a numeric cost, so a fully
    simulated run reads as measured. Confirmed on this estate: a run with
    `cost_usd: 0.25` whose critic phase carries `spend.simulated: true`.

    So the exclusion the Update-vs-create metric already had was only dropping
    runs with NO cost, not runs with a FABRICATED one -- and both metrics went
    on measuring the fixture, just fewer of them. This asks the same question
    cost_report asks: is there a phase whose spend is real?
    """
    for p in run.get("phases") or []:
        s = p.get("spend") or {}
        if s.get("cost_usd") is not None and not s.get("simulated"):
            return True
    return False


def pct(x):
    return f"{x:.0%}"


def commit_rate_line(runs, measured=None):
    """The commit-rate sentence, with check-only runs excluded and NAMED.

    A `would_commit` run passed every gate check and was told not to push
    (AIQE_GATE_CHECK_ONLY). It is not evidence about whether the gate would
    commit, so counting it in the denominator reports an operator's dry-run
    flag as a quality problem — the same error this scorecard already made once
    by counting a fixture repo's quarantine.

    Excluded and SAID, never dropped quietly: a denominator that shrinks with
    no explanation is its own lie. Extracted from the main body so the rule is
    mutation-testable against fabricated runs rather than only against whatever
    the estate happens to contain today.
    """
    committed = sum(1 for r in runs if r.get("overall") == "committed")
    quarantined = sum(1 for r in runs if r.get("overall") == "quarantined")
    review_refused = sum(1 for r in runs if r.get("overall") == "review_refused")
    withheld = sum(1 for r in runs if r.get("overall") == "would_commit")
    scored = len(runs) - withheld
    if not scored:
        return (f"Commit rate: n/a — all {len(runs)} run(s) ran with the gate in "
                f"check-only mode (AIQE_GATE_CHECK_ONLY), which commits nothing "
                f"by design. Unset it to measure.")
    # This figure is NOT downgraded to n/a on a simulated estate, and that is
    # deliberate: `make demo-pr` is "mock LLM, real gate/env/git", so the gate
    # genuinely lints, executes the changed specs and decides. A `committed`
    # status is real evidence — about THE GATE. What it is not is evidence
    # about model quality, and sitting beside three `n/a` lines that ARE about
    # model quality invites exactly that reading. So the scope is named rather
    # than the number hedged; marking a real measurement `~` is the lie that
    # teaches readers to ignore the marker everywhere else.
    scope = ""
    if measured is not None and not measured:
        scope = ("; this measures THE GATE's verdict (which runs for real even "
                 "on mock runs), not the quality of what the model wrote")
    return (f"Commit rate: {pct(committed / scored)} of {scored} runs "
            f"({quarantined} quarantined, {review_refused} review-refused)"
            + (f"; {withheld} excluded: the gate ran in check-only mode "
               f"(AIQE_GATE_CHECK_ONLY), so they say nothing about commit rate"
               if withheld else "") + scope)

# --- routing accuracy (benchmark replays) ---------------------------------------
# Only a row carrying routing_ok is a replay result. Eval outputs share this
# directory and A5 adds another one; name-based exclusions drift at every slice.
res = []
for f in glob.glob("eval/results/*.json"):
    try:
        row = json.load(open(f, encoding="utf-8"))
        if isinstance(row, dict) and "routing_ok" in row:
            res.append(row)
    except (OSError, ValueError):
        pass
if res:
    routing = sum(r["routing_ok"] for r in res) / len(res)
    print(f"Routing accuracy: {pct(routing)} across {len(res)} fixtures (target ≥95%)")
else:
    print("Routing accuracy: n/a — run `make eval` after adding benchmark fixtures")

# --- retrieval-scoped context (cost-reduction 7.2 guardrail) --------------------
try:
    cs = json.load(open("eval/results/context-scope.json", encoding="utf-8"))
    red = cs.get("avg_reduction_vs_full")
    print(f"Context scoping: retention {'OK' if cs.get('retention_ok') else 'FAILED'}"
          f" across {len(cs.get('fixtures', []))} fixture(s)"
          + (f", avg size reduction {red:.0%} vs full estate" if red is not None else "")
          + " (token-counted; quality delta awaits parity runs)")
except (OSError, ValueError):
    pass

# --- PR ticket discovery quality (successor PRD v2 A4) --------------------------
try:
    dq = json.load(open("eval/results/discovery-quality.json", encoding="utf-8"))
    state = str(dq.get("measurement_state") or "unavailable").upper()
    m1 = dq.get("m1") or {}
    refusal = dq.get("correct_refusal") or {}
    print(
        f"Ticket discovery ({state}): {dq.get('overall', 'unknown').upper()} "
        f"across {(dq.get('label_set') or {}).get('fixtures', 0)} labelled fixtures; "
        f"M1 precision={m1.get('precision', 0):.2f}, "
        f"recall={m1.get('recall', 0):.2f}; correct refusal "
        f"{refusal.get('correct', 0)}/{refusal.get('total', 0)}"
    )
    for signal in ("explicit", "branch", "title_description", "commits"):
        row = (dq.get("per_signal") or {}).get(signal) or {}
        print(
            f"  {signal}: precision={row.get('precision', 0):.2f}, "
            f"recall={row.get('recall', 0):.2f}"
        )
except (OSError, ValueError, KeyError, TypeError):
    print("Ticket discovery: n/a — run make discovery-eval")

# --- change-to-test retrieval quality (PRD A5) ---------------------------------
try:
    rq = json.load(open("eval/results/retrieval-quality.json", encoding="utf-8"))
    print(f"Retrieval quality: {rq.get('overall', 'unknown').upper()} across "
          f"{(rq.get('label_set') or {}).get('changes', 0)} labelled changes")
    for mode in ("deterministic", "lexical", "semantic"):
        row = (rq.get("modes") or {}).get(mode) or {}
        metrics = row.get("metrics")
        detail = (f" precision@5={metrics['precision_at_5']:.2f}, "
                  f"recall@5={metrics['recall_at_5']:.2f}, MRR={metrics['mrr']:.2f}"
                  if metrics else f" {row.get('reason', '')}")
        print(f"  {mode}: {row.get('state', 'unavailable')}{detail}")
except (OSError, ValueError, KeyError, TypeError):
    print("Retrieval quality: n/a — run `make retrieval-eval`")

# --- generated-test reviewer attack quality (PRD B6) ---------------------------
try:
    reviewer_quality = json.load(
        open("eval/results/reviewer-quality.json", encoding="utf-8")
    )
    simulated = reviewer_quality.get("simulated") or {}
    print(
        f"Reviewer quality (SIMULATED): "
        f"{simulated.get('overall', 'unknown').upper()}; catch rate "
        f"{simulated.get('caught', 0)}/{simulated.get('total', 0)} "
        f"({simulated.get('catch_rate', 0):.0%}); clean control "
        f"{'PASS' if (simulated.get('clean_control') or {}).get('passed') else 'FAIL'}"
    )
    for category, row in sorted((simulated.get("per_defect_class") or {}).items()):
        print(
            f"  {category}: {row.get('caught', 0)}/{row.get('total', 0)} "
            f"({row.get('catch_rate', 0):.0%})"
        )
    real = reviewer_quality.get("real_model") or {}
    real_detail = real.get("reason", "no measurement reason recorded")
    if real.get("state") == "measured":
        clean = (real.get("clean_control") or {}).get("passed")
        real_detail = (
            f"{real.get('overall', 'unknown').upper()}; catch rate "
            f"{real.get('caught', 0)}/{real.get('total', 0)} "
            f"({real.get('catch_rate', 0):.0%}); clean control "
            f"{'PASS' if clean else 'FAIL'}; provider={real.get('provider', 'unknown')} "
            f"model={real.get('model', 'unknown')}"
        )
    print(
        f"Reviewer quality (REAL MODEL): {real.get('state', 'unavailable').upper()}"
        f" — {real_detail}"
    )
except (OSError, ValueError, KeyError, TypeError):
    print("Reviewer quality: n/a — run `make reviewer-eval`")

# --- run outcomes + generation behavior (persisted run records) -----------------
runs = []
for f in glob.glob(str(ROOT / "reports/runs/*.json")):
    if pathlib.Path(f).name in ("reviews.json", "queue.json", "hooks-seen.json"):
        continue
    try:
        runs.append(json.load(open(f, encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        pass
if runs:
    print(commit_rate_line(runs, measured=any(_has_measured_spend(r) for r in runs)))
    loops, validated, created, updated = [], 0, 0, 0
    sim_validated = 0          # runs whose validate phase was a mock
    sim_actions = 0            # generated tests from runs that were SIMULATED
    for r in runs:
        # The same "metered" test the cost line uses: a real LLM call reports a
        # cost, a mock one does not. Only a real generate phase CHOSE between
        # extending and creating; the mock stub's action is scripted, so
        # counting it measures the fixture, not the platform.
        metered = _has_measured_spend(r)
        for p in r.get("phases", []):
            c = p["contract"]
            if p["name"] == "validate" and "repair_loops" in c:
                # `mock_phase.sh` emits the CONSTANT repair_loops: 0, so
                # averaging every run reported the stub as a measurement —
                # `Repair loops: 0.00 avg over 552 validated runs` on an estate
                # where nothing measured a repair loop. team_report was fixed
                # for exactly this and the scorecard, the platform's own
                # quality report, was missed. The correct test was already
                # computed three lines above for the generate branch.
                #
                # Asked PER PHASE, not per run: a run whose generate was real
                # and whose validate was mocked has a simulated repair count,
                # which is the rule phase_provenance exists to answer.
                if phase_provenance.of("validate", record=r) == "measured":
                    loops.append(c["repair_loops"])
                    validated += 1
                else:
                    sim_validated += 1
            if p["name"] == "generate":
                for t in c.get("tests", []):
                    if not metered:
                        sim_actions += 1
                        continue
                    created += t.get("action") == "created"
                    updated += t.get("action") == "updated"
    if loops:
        print(f"Repair loops: {sum(loops) / len(loops):.2f} avg over "
              f"{validated} MEASURED validated run(s)")
    elif sim_validated:
        # Naming the excluded count, because a denominator that shrinks in
        # silence is the failure this rule exists to prevent.
        print(f"Repair loops: n/a - no run with a MEASURED validate phase "
              f"({sim_validated} simulated run(s) excluded). Unblock "
              f"`make parity-pr` to measure it.")
    # Update-vs-create is a claim about JUDGEMENT — did the agent extend an
    # existing suite instead of duplicating it? It was computed over every run,
    # and on a mock estate the generate stub always reports "created", so the
    # figure read a flat 0% "of 341 generated tests" and invited the conclusion
    # that duplicate prevention does not work. It was measuring the fixture.
    # (The scout itself is fine: run on this estate it correctly emits
    # `EXTEND suites/orders/discount.spec.js`.) Same rule as every cost figure —
    # a simulated number is never reported as a measurement.
    if created + updated:
        print(f"Update-vs-create: {pct(updated / (created + updated))} of "
              f"{created + updated} generated tests extended existing suites "
              f"(higher = better duplicate prevention)")
        if sim_actions:
            print(f"  ({sim_actions} test(s) from simulated runs excluded — a mock "
                  f"stub's create/extend choice is scripted, not judgement)")
    elif sim_actions:
        print(f"Update-vs-create: n/a — {sim_actions} generated test(s), all from "
              f"SIMULATED runs whose create/extend choice is scripted. Nothing has "
              f"been measured; unblock `make parity-pr` to measure it.")
    # Escaped noise (§8): the advisory critic is the only automated source for this —
    # the gate proves specs pass, not that they assert anything worth asserting.
    # SAME RULE AS UPDATE-VS-CREATE, three lines up, and it was not applied
    # here. Both of these are claims about JUDGEMENT -- did the critic find
    # weak assertions? -- and the mock critic emits a hardcoded
    # `score: 0.86, noise_count: 0` (engine/phases/mock_phase.sh). So on this
    # estate "Critic score: 0.86 avg over 394 scored runs" was reading the stub
    # constant back and calling it a measurement, and "Escaped noise: 0%" was
    # reporting that a stub which always emits 0 found nothing. CLAUDE.md
    # quotes both figures, so the repo's own record of its quality was a mock's
    # default value.
    real = [r for r in runs if r.get("critic") and _has_measured_spend(r)]
    sim_critic = sum(1 for r in runs
                     if r.get("critic") and not _has_measured_spend(r))
    noise = sum(r["critic"].get("noise_count", 0) for r in real)
    reviewed = sum(r["critic"].get("specs_reviewed", 0) for r in real)
    scored = [r["critic"]["score"] for r in real
              if r["critic"].get("score") is not None]
    if reviewed:
        print(f"Escaped noise: {pct(noise / reviewed)} of {reviewed} generated specs "
              f"flagged trivial/duplicate/weak by the advisory critic (target ≤10%)")
    if scored:
        print(f"Critic score: {sum(scored) / len(scored):.2f} avg over {len(scored)} "
              f"scored runs (advisory — never gates a commit)")
    if sim_critic and scored:
        # Only alongside a real figure. With nothing measured the n/a branch
        # below already carries the count, and printing both made an indented
        # "excluded" note dangle under a metric that was never shown.
        print(f"  ({sim_critic} run(s) from SIMULATED critic phases excluded — the "
              f"mock emits a fixed score and zero noise, so averaging them "
              f"reports the stub's default as a quality measurement)")
    if not scored:
        print(f"Escaped noise / Critic score: n/a — no MEASURED critic signal yet"
              + (f" ({sim_critic} simulated run(s) carry a scripted score, which "
                 f"measures nothing). Unblock `make parity-pr` to measure it."
                 if sim_critic else
                 " (critic.enabled in org-config)"))
    # Third metric on the same broken proxy, and the one that reads most like a
    # bill. `cost_usd is a number` counted the 50 runs whose $0.25 came from
    # AIQE_MOCK_PHASE_COST -- a figure invented so tests can exercise the
    # budget ladder -- and called them "metered". Same iron rule the cost
    # report follows: a simulated number never prints as a measured dollar.
    costs = [r["cost_usd"] for r in runs
             if isinstance(r.get("cost_usd"), (int, float))
             and _has_measured_spend(r)]
    sim_costs = [r["cost_usd"] for r in runs
                 if isinstance(r.get("cost_usd"), (int, float))
                 and not _has_measured_spend(r)]
    if costs:
        print(f"Cost per run: ${sum(costs) / len(costs):.2f} avg over {len(costs)} "
              f"metered run(s) (limit enforced at exit 77 — see engine/lib/budget.py)")
    elif sim_costs:
        print(f"Cost per run: n/a — {len(sim_costs)} run(s) carry a SIMULATED "
              f"cost (~${sum(sim_costs) / len(sim_costs):.2f} avg, from "
              f"AIQE_MOCK_PHASE_COST). No real spend has been measured; unblock "
              f"`make parity-pr` to measure it.")
else:
    print("Run outcomes: n/a — no run records yet")

# --- team acceptance (review states) --------------------------------------------
reviews = review_state.load()
decided = [h for e in reviews.values() for h in e.get("history", [])
           if h.get("status") in ("approved", "changes_requested")]
if decided:
    approved = sum(1 for h in decided if h["status"] == "approved")
    print(f"Acceptance rate: {pct(approved / len(decided))} of {len(decided)} "
          f"team decisions (target ≥70%)")
else:
    print("Acceptance rate: n/a — no team review decisions yet (bin/qa.py mark ...)")

# --- flakiness (post-merge results ingest) --------------------------------------
health_file = app_paths.catalog_health(ROOT)
if health_file.exists():
    health = json.load(open(health_file, encoding="utf-8"))
    flaky = [t for t, h in health.items() if h.get("flaky")]
    tracked = len(health)
    print(f"Test health: {tracked} test(s) tracked from CI results; "
          f"{len(flaky)} flaky" + (f" -> {', '.join(flaky)}" if flaky else ""))
else:
    print("Test health: n/a — ingest CI results with bin/qa.py ingest-results <junit.xml>")

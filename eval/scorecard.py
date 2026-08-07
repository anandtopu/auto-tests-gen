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


def pct(x):
    return f"{x:.0%}"

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
    committed = sum(1 for r in runs if r.get("overall") == "committed")
    quarantined = sum(1 for r in runs if r.get("overall") == "quarantined")
    print(f"Commit rate: {pct(committed / len(runs))} of {len(runs)} runs "
          f"({quarantined} quarantined)")
    loops, validated, created, updated = [], 0, 0, 0
    for r in runs:
        for p in r.get("phases", []):
            c = p["contract"]
            if p["name"] == "validate" and "repair_loops" in c:
                loops.append(c["repair_loops"])
                validated += 1
            if p["name"] == "generate":
                for t in c.get("tests", []):
                    created += t.get("action") == "created"
                    updated += t.get("action") == "updated"
    if loops:
        print(f"Repair loops: {sum(loops) / len(loops):.2f} avg over {validated} validated runs")
    if created + updated:
        print(f"Update-vs-create: {pct(updated / (created + updated))} of "
              f"{created + updated} generated tests extended existing suites "
              f"(higher = better duplicate prevention)")
    # Escaped noise (§8): the advisory critic is the only automated source for this —
    # the gate proves specs pass, not that they assert anything worth asserting.
    noise = sum(r["critic"].get("noise_count", 0) for r in runs if r.get("critic"))
    reviewed = sum(r["critic"].get("specs_reviewed", 0) for r in runs if r.get("critic"))
    scored = [r["critic"]["score"] for r in runs
              if r.get("critic") and r["critic"].get("score") is not None]
    if reviewed:
        print(f"Escaped noise: {pct(noise / reviewed)} of {reviewed} generated specs "
              f"flagged trivial/duplicate/weak by the advisory critic (target ≤10%)")
    if scored:
        print(f"Critic score: {sum(scored) / len(scored):.2f} avg over {len(scored)} "
              f"scored runs (advisory — never gates a commit)")
    if not scored:
        print("Escaped noise: n/a — no critic signal yet (critic.enabled in org-config)")
    costs = [r["cost_usd"] for r in runs if isinstance(r.get("cost_usd"), (int, float))]
    if costs:
        print(f"Cost per run: ${sum(costs) / len(costs):.2f} avg over {len(costs)} "
              f"metered run(s) (limit enforced at exit 77 — see engine/lib/budget.py)")
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

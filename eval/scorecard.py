#!/usr/bin/env python3
"""Aggregate the PoC scorecard (architecture §8) from benchmark replays,
persisted run records, review states, and test health."""
import glob, json, pathlib, sys

sys.stdout.reconfigure(encoding="utf-8")   # Windows consoles default to cp1252
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine/lib"))
import app_paths                      # R12: mutable paths resolve here
import review_state


def pct(x):
    return f"{x:.0%}"

# --- routing accuracy (benchmark replays) ---------------------------------------
# context-scope.json is the retrieval guardrail's output (context_check.py),
# not a replay result — the same shared-directory rule as reports/runs/.
res = [json.load(open(f)) for f in glob.glob("eval/results/*.json")
       if pathlib.Path(f).name != "context-scope.json"]
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
                loops.append(c["repair_loops"]); validated += 1
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

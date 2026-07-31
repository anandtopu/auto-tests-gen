#!/usr/bin/env python3
"""Compare parity runs ACROSS LLM providers (multi-LLM story 2.5).

`make parity-pr LLM_PROVIDER=ollama` routes a real run at one provider. This
reads the run records those produced and puts the providers side by side on the
things that actually decide whether a cheaper model can be trusted:

  commit rate     did the gate accept what it generated
  critic score    advisory quality of the generated specs
  spend           per run, with its cost basis carried through
  turns           how much work it needed to get there

THE IRON RULE APPLIES. A simulated run (mock phases, AIQE_MOCK_PHASE_COST) is
not evidence about a provider, so simulated runs are EXCLUDED from the
comparison and reported separately — a table that silently averaged them would
say a provider is cheap when nothing was measured.

Grouping is by the provider that ran the run's phases, taken from the spend
blocks the record already carries; a run whose phases used more than one
provider is labelled `mixed:<a>+<b>` rather than attributed to either.

    python3 engine/lib/parity_compare.py [DAYS]
"""
import glob
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS = ROOT / "reports/runs"
SKIP = {"reviews.json", "queue.json", "hooks-seen.json"}


def _records(days=0):
    cutoff = time.time() - days * 86400 if days else 0
    for f in sorted(glob.glob(str(RUNS / "*.json"))):
        if os.path.basename(f) in SKIP:
            continue
        try:
            rec = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue                      # a torn record is not a data point
        if cutoff and float(rec.get("ts") or 0) < cutoff:
            continue
        yield rec


def _provider_of(rec):
    """Which provider ran this run's phases — `mixed:...` when more than one."""
    provs = {(p.get("spend") or {}).get("provider") or ""
             for p in rec.get("phases") or []}
    provs.discard("")
    if not provs:
        return "unknown"
    if len(provs) == 1:
        return provs.pop()
    return "mixed:" + "+".join(sorted(provs))


def _simulated(rec):
    return any((p.get("spend") or {}).get("simulated")
               for p in rec.get("phases") or [])


def compare(days=0):
    rows, simulated = {}, {}
    for rec in _records(days):
        prov = _provider_of(rec)
        bucket = simulated if _simulated(rec) else rows
        e = bucket.setdefault(prov, {"provider": prov, "runs": 0, "committed": 0,
                                     "cost_usd": 0.0, "turns": 0,
                                     "critic": [], "bases": {}})
        e["runs"] += 1
        gates = rec.get("gates") or rec.get("repos") or []
        if any(str(g.get("status", "")).upper() == "COMMITTED" for g in gates):
            e["committed"] += 1
        for ph in rec.get("phases") or []:
            sp = ph.get("spend") or {}
            e["cost_usd"] += float(sp.get("cost_usd") or 0)
            e["turns"] += int(sp.get("turns_used") or 0)
            basis = sp.get("cost_basis") or "unknown"
            e["bases"][basis] = e["bases"].get(basis, 0) + 1
        score = ((rec.get("critic") or {}).get("overall")
                 if isinstance(rec.get("critic"), dict) else None)
        if isinstance(score, (int, float)):
            e["critic"].append(float(score))

    def finish(bucket):
        out = []
        for e in bucket.values():
            e = dict(e)
            e["commit_rate"] = (round(e["committed"] / e["runs"], 3)
                                if e["runs"] else None)
            e["critic_avg"] = (round(sum(e["critic"]) / len(e["critic"]), 3)
                               if e["critic"] else None)
            e["cost_per_run"] = (round(e["cost_usd"] / e["runs"], 4)
                                 if e["runs"] else None)
            e.pop("critic")
            out.append(e)
        return sorted(out, key=lambda r: r["provider"])

    return {"measured": finish(rows), "simulated_excluded": finish(simulated),
            "days": days}


def to_text(rep):
    lines = []
    measured = rep["measured"]
    if not measured:
        lines.append("No MEASURED parity runs yet — nothing to compare.")
        lines.append("Run: make parity-pr LLM_PROVIDER=<provider>  (needs real")
        lines.append("LLM auth; see REVIEW.md open item 5).")
    else:
        lines.append(f"Provider parity ({len(measured)} provider(s), measured runs only)")
        lines.append("")
        lines.append(f"{'provider':16} {'runs':>5} {'commit':>7} {'critic':>7} "
                     f"{'$/run':>9}  {'turns':>6}  bases")
        for e in measured:
            cr = "n/a" if e["commit_rate"] is None else f"{e['commit_rate']:.0%}"
            cv = "n/a" if e["critic_avg"] is None else f"{e['critic_avg']:.2f}"
            bases = ",".join(f"{k}x{v}" for k, v in sorted(e["bases"].items()))
            lines.append(f"{e['provider']:16} {e['runs']:>5} {cr:>7} {cv:>7} "
                         f"{e['cost_per_run']:>9.4f}  {e['turns']:>6}  {bases}")
    if rep["simulated_excluded"]:
        n = sum(e["runs"] for e in rep["simulated_excluded"])
        lines.append("")
        lines.append(f"({n} simulated run(s) excluded — a mock run is not "
                     f"evidence about a provider.)")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    days = int(arg) if arg.strip().isdigit() else 0
    rep = compare(days)
    if "--json" in sys.argv:
        print(json.dumps(rep, indent=2))
    else:
        print(to_text(rep))

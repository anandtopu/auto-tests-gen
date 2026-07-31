#!/usr/bin/env python3
"""Cost attribution across run records (cost-reduction stories 1.2, 1.5, 4.2).

Pure aggregation — no LLM, no network. Reads the `spend` blocks that
run_record.py attaches to each phase (fed by the budget ledger, which harvests
the claude -p result JSON the pipeline already saves), and answers the questions
an EM actually asks: what does a run cost, where does it go by workflow / key /
phase / model tier, what did the caches save, and are the turn ceilings earning
their risk.

The one iron rule: a SIMULATED number (mock runs, AIQE_MOCK_PHASE_COST) may
inform a trend but must never masquerade as a measured dollar. Every rollup
carries `simulated_share`, and savings lines print `n/a` rather than a figure
derived from simulation.

CLI:
  cost_report.py report [--days N] [--md]
"""
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fs_lock

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS = ROOT / "reports/runs"
# The run-history directory holds more than run records — the invariant every
# glob in this codebase honours.
SKIP = ("reviews.json", "queue.json", "hooks-seen.json")


def collect(days=None):
    """[{run_id, key, mode, ts, phases: [{name, spend}...]}] oldest-first,
    spend-carrying phases only. Torn records are skipped, never fatal."""
    cutoff = time.time() - days * 86400 if days else 0
    out = []
    if not RUNS.is_dir():
        return out
    for f in sorted(RUNS.glob("*.json")):
        if f.name in SKIP:
            continue
        rec = fs_lock.read_json_guarded(f, None) if hasattr(fs_lock, "read_json_guarded") else None
        if rec is None:
            try:
                rec = json.load(open(f, encoding="utf-8"))
            except Exception:
                continue
        if not isinstance(rec, dict) or rec.get("ts", 0) < cutoff:
            continue
        phases = [{"name": p.get("name", ""), "spend": p["spend"]}
                  for p in rec.get("phases", [])
                  if isinstance(p, dict) and isinstance(p.get("spend"), dict)]
        out.append({"run_id": rec.get("run_id", f.stem),
                    "key": (rec.get("trigger") or {}).get("key", ""),
                    "mode": (rec.get("trigger") or {}).get("type", ""),
                    "ts": rec.get("ts", 0), "phases": phases})
    return out


def _policy_phase(label):
    return label.split("-", 1)[0]


def _pct(values, q):
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def report(days=None):
    runs = collect(days)
    by_mode, by_key, by_phase, by_model = {}, {}, {}, {}
    total, spend_rows, simulated_rows = 0.0, 0, 0
    for r in runs:
        run_cost = 0.0
        for p in r["phases"]:
            s = p["spend"]
            cost = float(s.get("cost_usd") or 0)
            run_cost += cost
            spend_rows += 1
            if s.get("simulated"):
                simulated_rows += 1
            ph = by_phase.setdefault(_policy_phase(p["name"]), {
                "calls": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "turns": [], "max_turns": 0,
                "measured_costs": []})
            ph["calls"] += 1
            ph["cost_usd"] += cost
            ph["input_tokens"] += int(s.get("input_tokens") or 0)
            ph["output_tokens"] += int(s.get("output_tokens") or 0)
            ph["cache_read_tokens"] += int(s.get("cache_read_tokens") or 0)
            if s.get("turns_used"):
                ph["turns"].append(int(s["turns_used"]))
            ph["max_turns"] = max(ph["max_turns"], int(s.get("max_turns") or 0))
            if not s.get("simulated"):
                ph["measured_costs"].append(cost)
            mdl = s.get("model") or "unknown"
            m = by_model.setdefault(mdl, {"calls": 0, "cost_usd": 0.0,
                                          "input_tokens": 0, "output_tokens": 0})
            m["calls"] += 1
            m["cost_usd"] += cost
            m["input_tokens"] += int(s.get("input_tokens") or 0)
            m["output_tokens"] += int(s.get("output_tokens") or 0)
        total += run_cost
        by_mode.setdefault(r["mode"] or "?", {"runs": 0, "cost_usd": 0.0})
        by_mode[r["mode"] or "?"]["runs"] += 1
        by_mode[r["mode"] or "?"]["cost_usd"] += run_cost
        if r["key"]:
            k = by_key.setdefault(r["key"], {"runs": 0, "cost_usd": 0.0})
            k["runs"] += 1
            k["cost_usd"] += run_cost

    # Turn calibration (1.5): observed usage vs the configured ceiling. suggested
    # is advice for a human editing org-config, never auto-applied.
    for name, ph in by_phase.items():
        turns = ph.pop("turns")
        measured = ph.pop("measured_costs")
        ph["turns_p50"] = _pct(turns, 0.50)
        ph["turns_p95"] = _pct(turns, 0.95)
        ph["suggested_max_turns"] = (min(ph["max_turns"], ph["turns_p95"] + 2)
                                     if turns and ph["max_turns"] else ph["max_turns"])
        ph["median_measured_cost"] = _pct(measured, 0.5) if measured else None
        denom = ph["input_tokens"] + ph["cache_read_tokens"]
        ph["cache_hit_rate"] = round(ph["cache_read_tokens"] / denom, 3) if denom else 0.0
        ph["cost_usd"] = round(ph["cost_usd"], 4)

    # Phase-cache savings: hits x that phase's MEDIAN MEASURED cost. Without a
    # single measured run there is no honest number, only "n/a".
    cache_savings, cache_hits = None, 0
    try:
        import phase_cache
        stats = phase_cache.stats()
        hits_by_phase = stats.get("by_phase") or {}
        if not hits_by_phase and isinstance(stats.get("hits"), int):
            cache_hits = stats["hits"]
        est = 0.0
        priced = False
        for phz, hits in hits_by_phase.items():
            cache_hits += hits
            med = (by_phase.get(_policy_phase(phz)) or {}).get("median_measured_cost")
            if med:
                est += hits * med
                priced = True
        cache_savings = round(est, 4) if priced else None
    except Exception:
        pass

    # OpenHands launch payloads (1.5a): estimated, billed elsewhere — reported
    # separately and labelled so, never folded into `total`.
    oh_payload_chars = 0
    try:
        import openhands_events
        for e in (openhands_events.load() or {}).values():
            oh_payload_chars += int(e.get("message_chars") or 0)
    except Exception:
        pass

    top10 = sorted(by_key.items(), key=lambda kv: -kv[1]["cost_usd"])[:10]
    return {"window_days": days, "runs": len(runs),
            "total_cost_usd": round(total, 4),
            "simulated_share": round(simulated_rows / spend_rows, 3) if spend_rows else None,
            "by_mode": {k: {"runs": v["runs"], "cost_usd": round(v["cost_usd"], 4)}
                        for k, v in by_mode.items()},
            "by_key_top10": [{"key": k, **{**v, "cost_usd": round(v["cost_usd"], 4)}}
                             for k, v in top10],
            "by_phase": by_phase,
            "by_model": {k: {**v, "cost_usd": round(v["cost_usd"], 4)}
                         for k, v in by_model.items()},
            "phase_cache_hits": cache_hits,
            "phase_cache_savings_usd": cache_savings,
            "openhands_payload_chars": oh_payload_chars,
            "openhands_payload_est_tokens": oh_payload_chars // 4}


def to_markdown(rep):
    sim = rep["simulated_share"]
    label = ("all simulated" if sim == 1.0 else "measured" if sim == 0.0
             else f"{int((sim or 0) * 100)}% simulated" if sim is not None else "no spend data")
    lines = [f"# LLM cost report ({rep['runs']} run(s)"
             + (f", last {rep['window_days']}d" if rep['window_days'] else "")
             + f") — {label}",
             f"", f"Total: ${rep['total_cost_usd']:.4f}", ""]
    if rep["by_mode"]:
        lines.append("## By workflow")
        for k, v in sorted(rep["by_mode"].items()):
            lines.append(f"- {k}: {v['runs']} run(s), ${v['cost_usd']:.4f}")
        lines.append("")
    if rep["by_key_top10"]:
        lines.append("## Top keys")
        for e in rep["by_key_top10"]:
            lines.append(f"- {e['key']}: {e['runs']} run(s), ${e['cost_usd']:.4f}")
        lines.append("")
    if rep["by_phase"]:
        # Hit-rate floor (4.2): a configured minimum makes a prefix-breaking
        # prompt edit visible as a flagged falling rate, not just a bigger bill.
        floor = 0.0
        try:
            import yaml
            floor = float(((yaml.safe_load(open(ROOT / "registry/org-config.yaml",
                                                encoding="utf-8")) or {})
                           .get("budgets") or {}).get("min_cache_hit_rate") or 0)
        except Exception:
            floor = 0.0
        lines.append("## By phase (turn calibration + cache hit rate)")
        lines.append("phase | calls | cost | in-tok | cache-read | hit-rate | "
                     "turns p50/p95 | ceiling | suggested")
        lines.append("---|---|---|---|---|---|---|---|---")
        for k, v in sorted(rep["by_phase"].items()):
            flag = " (BELOW FLOOR)" if floor and v["cache_hit_rate"] < floor else ""
            lines.append(f"{k} | {v['calls']} | ${v['cost_usd']:.4f} | "
                         f"{v['input_tokens']} | {v['cache_read_tokens']} | "
                         f"{v['cache_hit_rate']:.0%}{flag} | "
                         f"{v['turns_p50']}/{v['turns_p95']} | {v['max_turns']} | "
                         f"{v['suggested_max_turns']}")
        lines.append("")
    if rep["by_model"]:
        lines.append("## By model tier")
        for k, v in sorted(rep["by_model"].items()):
            lines.append(f"- {k}: {v['calls']} call(s), ${v['cost_usd']:.4f}, "
                         f"{v['input_tokens']} in / {v['output_tokens']} out tokens")
        lines.append("")
    sav = rep["phase_cache_savings_usd"]
    lines.append(f"Phase-cache hits: {rep['phase_cache_hits']} — estimated saving: "
                 + (f"${sav:.4f}" if sav is not None else "n/a (no measured runs yet)"))
    if rep["openhands_payload_chars"]:
        lines.append(f"OpenHands launch payloads: ~{rep['openhands_payload_est_tokens']}"
                     f" tokens (estimated; billed on the OpenHands side, not here)")
    return "\n".join(lines) + "\n"


BASELINE = ROOT / "reports/cost-baseline.json"


def snapshot_baseline():
    """Freeze the current per-phase MEASURED medians as the regression baseline
    (story 1.3). Refuses without a single measured run — a baseline built from
    simulations would be worse than none (it would alarm on the first real
    dollar, or worse, never alarm)."""
    rep = report(None)
    phases = {name: {"median_cost": v["median_measured_cost"],
                     "calls": v["calls"]}
              for name, v in rep["by_phase"].items()
              if v.get("median_measured_cost")}
    if not phases:
        raise SystemExit("no measured runs to baseline — run a real (or parity) "
                         "pipeline first; simulated spend never enters a baseline")
    fs_lock.write_json_atomic(BASELINE, {"created": time.time(),
                                         "phases": phases})
    return phases


def check_regression(threshold=None, days=7):
    """Trailing-window medians vs the baseline (story 1.4). Returns a list of
    regression strings (empty = healthy). No baseline file -> [] silently —
    the alarm needs an armed baseline, not a guess."""
    base = fs_lock.read_json_guarded(BASELINE, None)
    if not base or not isinstance(base.get("phases"), dict):
        return []
    if threshold is None:
        try:
            import yaml
            threshold = float(((yaml.safe_load(
                open(ROOT / "registry/org-config.yaml", encoding="utf-8")) or {})
                .get("budgets") or {}).get("cost_regression_threshold") or 0.25)
        except Exception:
            threshold = 0.25
    rep = report(days)
    out = []
    for name, b in base["phases"].items():
        cur = (rep["by_phase"].get(name) or {}).get("median_measured_cost")
        if not cur or not b.get("median_cost"):
            continue
        ratio = cur / b["median_cost"]
        if ratio > 1 + threshold:
            out.append(f"phase '{name}' median cost ${cur:.4f} is "
                       f"{(ratio - 1) * 100:.0f}% over its baseline "
                       f"${b['median_cost']:.4f} — likely causes: a prompt edit "
                       f"broke the cache prefix, or the phase's model tier "
                       f"drifted (check org-config models:)")
    return out


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8")
    if argv and argv[0] == "baseline":
        phases = snapshot_baseline()
        print(f"cost baseline frozen: {len(phases)} phase(s) -> {BASELINE}")
        return 0
    if argv and argv[0] == "check-regression":
        regs = check_regression()
        if not regs:
            print("cost regression check: healthy (or no baseline armed)")
            return 0
        for r in regs:
            print(f"COST REGRESSION: {r}")
        # Notify (best-effort, mock-aware) — the nightly's whole point.
        try:
            import os
            import subprocess
            import work_queue
            adapter = ROOT / ("adapters/mock/notify.sh"
                              if os.environ.get("AIQE_MOCK", "1") == "1"
                              else "adapters/notify/slack.sh")
            subprocess.run([work_queue.bash_exe(), str(adapter), "post",
                            "[ai-qe] " + "; ".join(regs)[:500]],
                           cwd=ROOT, capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=30)
        except Exception:
            pass
        return 1
    days = None
    if "--days" in argv:
        days = int(argv[argv.index("--days") + 1])
    rep = report(days)
    if "--json" in argv:
        print(json.dumps(rep, indent=1))
    else:
        print(to_markdown(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

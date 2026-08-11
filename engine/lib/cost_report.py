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
import math
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app_paths
import env_flag  # AIQE_MOCK means what it says
import fs_lock
import spend_history

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS = ROOT / "reports/runs"
# The run-history directory holds more than run records — the invariant every
# glob in this codebase honours.
SKIP = ("reviews.json", "queue.json", "hooks-seen.json")
MAX_WINDOW_DAYS = 36500


def _window_days(days):
    """Return a bounded positive reporting window, or ``None`` for all time."""
    if days is None:
        return None
    try:
        value = int(days)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("days must be a whole number") from exc
    if value < 1 or value > MAX_WINDOW_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_WINDOW_DAYS}")
    return value


def collect(days=None):
    """[{run_id, key, mode, ts, phases: [{name, spend}...]}] oldest-first,
    spend-carrying phases only. Torn records are skipped, never fatal."""
    days = _window_days(days)
    # Tests and embedders historically override RUNS. Keep their history
    # isolated by resolving its sibling costs directory; production continues
    # to honor AIQE_COSTS_DIR through app_paths.
    costs = app_paths.costs_dir(ROOT) if RUNS == ROOT / "reports/runs" else RUNS.parent / "costs"
    grouped = {}
    # Run records still own non-spend metrics such as artifact reuse. Seed that
    # metadata without inspecting their spend blocks, then overlay the accessor.
    if RUNS.is_dir():
        cutoff = time.time() - days * 86400 if days is not None else 0
        for path in sorted(RUNS.glob("*.json")):
            if path.name in SKIP:
                continue
            rec = fs_lock.read_json_guarded(path, None)
            trigger = rec.get("trigger") if isinstance(rec, dict) else None
            phases = rec.get("phases") if isinstance(rec, dict) else None
            try:
                ts = float(rec.get("ts", 0))
            except (AttributeError, TypeError, ValueError, OverflowError):
                continue
            if (not math.isfinite(ts) or ts < cutoff or not isinstance(trigger, dict)
                    or not isinstance(phases, list)):
                continue
            # Spend-bearing records are admitted only after spend_history has
            # validated their rows. Seed metadata-only records here so metrics
            # such as artifact reuse remain visible without allowing a corrupt
            # spend mapping to inflate the run count.
            if any(isinstance(phase, dict) and "spend" in phase for phase in phases):
                continue
            run_id = str(rec.get("run_id") or path.stem)
            grouped[run_id] = {
                "run_id": run_id, "key": str(trigger.get("key") or ""),
                "mode": str(trigger.get("type") or ""), "ts": ts, "phases": [],
                "artifact_reuse": rec.get("artifact_reuse") or {}}
    for row in spend_history.spend_rows(days, runs_dir=RUNS, costs_dir=costs):
        run = grouped.setdefault(row["run_id"], {
            "run_id": row["run_id"], "key": row["key"], "mode": row["mode"],
            "ts": row["ts"], "phases": [], "artifact_reuse": row["artifact_reuse"]})
        run["phases"].append({"name": row["phase"], "spend": {
            "provider": row["provider"], "model": row["model"],
            "cost_basis": row["basis"], "cost_usd": row["cost_usd"],
            "input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
            "cache_read_tokens": row["cache_read_tokens"],
            "cache_creation_tokens": row["cache_creation_tokens"],
            "turns_used": row["turns"], "max_turns": row["max_turns"],
            "simulated": row["simulated"], "attempts": row["attempts"],
            "attribution": row.get("attribution") or "user"}})
    return sorted(grouped.values(), key=lambda row: (row["ts"], row["run_id"]))


def _policy_phase(label):
    return label.split("-", 1)[0]


def _nonnegative_int(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _pct(values, q):
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def report(days=None, keys=None):
    """`keys` restricts the USER-TASK rollups to those trigger keys — what a
    release-scoped readout needs. It deliberately does NOT touch the embedding
    and probe sections: those spend rows carry no key, are already reported
    separately from the task total, and pretending they belong to one release
    would be the same attribution error this parameter exists to fix.

    An EMPTY collection means "no key matched", which must yield zero runs — it
    is not the same as None (no filter), and defaulting an empty set to "all"
    would turn a filter that matched nothing into the whole estate.
    """
    runs = collect(days)
    if keys is not None:
        wanted = set(keys)
        runs = [r for r in runs if r.get("key") in wanted]
    by_mode, by_key, by_phase, by_model, by_provider = {}, {}, {}, {}, {}
    by_basis = {}
    local_tokens = cloud_tokens = 0
    total, spend_rows, simulated_rows = 0.0, 0, 0
    user_run_ids = set()
    probe = {"rows": 0, "calls": 0, "costs_by_basis": {}, "providers": set()}
    unmeterable_phases, unmeterable_tasks, unmeterable_providers = set(), set(), set()
    artifacts_reused, reuse_tokens_by_basis = 0, {}

    def add_rollup(provider, basis, cost, calls, input_tokens=0, output_tokens=0,
                   calls_known=True):
        """One basis-preserving rollup shared by phases, probes and embeddings."""
        pv = by_provider.setdefault(provider or "unknown", {
            "calls": 0, "cost_usd": 0.0, "input_tokens": 0,
            "output_tokens": 0, "bases": {}, "calls_unknown_rows": 0})
        if calls_known:
            pv["calls"] += calls
        else:
            pv["calls_unknown_rows"] += 1
        pv["cost_usd"] += float(cost or 0)
        pv["input_tokens"] += int(input_tokens or 0)
        pv["output_tokens"] += int(output_tokens or 0)
        count = calls if calls_known and calls else 1
        pv["bases"][basis] = pv["bases"].get(basis, 0) + count
        bv = by_basis.setdefault(basis, {"rows": 0, "calls": 0,
                                         "cost_usd": 0.0,
                                         "incomplete_rows": 0})
        bv["rows"] += 1
        bv["calls"] += calls if calls_known else 0
        bv["cost_usd"] += float(cost or 0)
        if cost is None and basis not in ("local",):
            bv["incomplete_rows"] += 1

    for r in runs:
        reuse = r.get("artifact_reuse")
        reuse = reuse if isinstance(reuse, dict) else {}
        artifacts_reused += _nonnegative_int(reuse.get("artifacts_reused"))
        basis_rows = reuse.get("tokens_by_basis")
        basis_rows = basis_rows if isinstance(basis_rows, dict) else {}
        for basis in ("reported", "estimated"):
            tokens = _nonnegative_int(basis_rows.get(basis))
            if tokens:
                reuse_tokens_by_basis[basis] = reuse_tokens_by_basis.get(basis, 0) + tokens
        run_cost, user_run = 0.0, not r["phases"]
        # Measured spend tracked SEPARATELY per run, so a per-key total can
        # be compared against a real budget. work_queue warns "expect the run
        # to degrade or abort" off this figure, and on a mock-heavy estate
        # the simulated total drove that prediction.
        run_measured = 0.0
        for p in r["phases"]:
            s = p["spend"]
            raw_cost = s.get("cost_usd")
            cost = float(raw_cost or 0)
            attempts = max(1, int(s.get("attempts") or 1))
            prov = s.get("provider") or ("mock" if s.get("simulated") else "unknown")
            basis = s.get("cost_basis") or ("simulated" if s.get("simulated") else "unknown")
            add_rollup(prov, basis, raw_cost, attempts,
                       s.get("input_tokens"), s.get("output_tokens"))
            attribution = s.get("attribution") or "user"
            if attribution != "user":
                probe["rows"] += 1
                probe["calls"] += attempts
                probe["providers"].add(prov)
                if raw_cost is not None:
                    probe["costs_by_basis"][basis] = round(
                        probe["costs_by_basis"].get(basis, 0.0) + cost, 6)
                continue
            user_run = True
            spend_rows += attempts
            if s.get("simulated"):
                simulated_rows += attempts
            else:
                run_measured += cost
            run_cost += cost
            if basis == "unknown":
                unmeterable_phases.add((r["run_id"], p["name"]))
                unmeterable_tasks.add(r["key"] or r["run_id"])
                unmeterable_providers.add(prov)
            ph = by_phase.setdefault(_policy_phase(p["name"]), {
                "calls": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "turns": [], "max_turns": 0,
                "measured_costs": []})
            ph["calls"] += attempts
            ph["cost_usd"] += cost
            ph["input_tokens"] += int(s.get("input_tokens") or 0)
            ph["output_tokens"] += int(s.get("output_tokens") or 0)
            ph["cache_read_tokens"] += int(s.get("cache_read_tokens") or 0)
            if s.get("turns_used"):
                ph["turns"].append(int(s["turns_used"]))
            ph["max_turns"] = max(ph["max_turns"], int(s.get("max_turns") or 0))
            if not s.get("simulated"):
                ph["measured_costs"].append(cost)
            toks = int(s.get("input_tokens") or 0) + int(s.get("output_tokens") or 0)
            if basis == "local":
                local_tokens += toks
            elif basis in ("reported", "estimated"):
                cloud_tokens += toks
            mdl = s.get("model") or "unknown"
            m = by_model.setdefault(mdl, {"calls": 0, "cost_usd": 0.0,
                                          "input_tokens": 0, "output_tokens": 0})
            m["calls"] += attempts
            m["cost_usd"] += cost
            m["input_tokens"] += int(s.get("input_tokens") or 0)
            m["output_tokens"] += int(s.get("output_tokens") or 0)
        total += run_cost
        if user_run:
            user_run_ids.add(r["run_id"])
        if user_run:
            by_mode.setdefault(r["mode"] or "?", {"runs": 0, "cost_usd": 0.0})
            by_mode[r["mode"] or "?"]["runs"] += 1
            by_mode[r["mode"] or "?"]["cost_usd"] += run_cost
        if user_run and r["key"]:
            k = by_key.setdefault(r["key"], {"runs": 0, "cost_usd": 0.0,
                                             "measured_usd": 0.0})
            k["runs"] += 1
            k["cost_usd"] += run_cost
            k["measured_usd"] = round(k.get("measured_usd", 0.0)
                                      + run_measured, 6)

    # B1.1: the embedding cap remains in vector_index.  This is a read-only
    # normalization of its daily ledger, kept separate from the task total.
    try:
        import vector_index
        embedding_rows = vector_index.spend_rows(days)
    except (OSError, TimeoutError, TypeError, ValueError, OverflowError):
        embedding_rows = []
    embedding_costs = {}
    for row in embedding_rows:
        basis, cost = row["basis"], row["cost_usd"]
        calls = row.get("calls")
        add_rollup(row["provider"], basis, cost, calls or 0,
                   row.get("tokens") or 0, 0, calls_known=calls is not None)
        if cost is not None:
            embedding_costs[basis] = round(
                embedding_costs.get(basis, 0.0) + float(cost), 6)

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
    return {"window_days": days, "runs": len(user_run_ids),
            "total_cost_usd": round(total, 4),
            "simulated_share": round(simulated_rows / spend_rows, 3) if spend_rows else None,
            "by_mode": {k: {"runs": v["runs"], "cost_usd": round(v["cost_usd"], 4)}
                        for k, v in by_mode.items()},
            "by_key_top10": [{"key": k, **{**v, "cost_usd": round(v["cost_usd"], 4)}}
                             for k, v in top10],
            "by_phase": by_phase,
            "by_model": {k: {**v, "cost_usd": round(v["cost_usd"], 4)}
                         for k, v in by_model.items()},
            "by_provider": {k: {**v, "cost_usd": round(v["cost_usd"], 4)}
                            for k, v in by_provider.items()},
            "by_basis": {k: {**v, "cost_usd": round(v["cost_usd"], 4)}
                         for k, v in by_basis.items()},
            "local_tokens": local_tokens, "cloud_tokens": cloud_tokens,
            # Spend that could not be priced at all. The total EXCLUDES it, so
            # every surface that prints the total must print this beside it or
            # a partial figure reads as the whole bill.
            "unpriced_calls": sum(
                sum(v["bases"].get(b, 0) for b in ("unknown", "unrecorded", "not-reconciled"))
                for v in by_provider.values()),
            "unpriced_providers": sorted(
                k for k, v in by_provider.items()
                if any(v["bases"].get(b) for b in ("unknown", "unrecorded", "not-reconciled"))),
            "embeddings": {"rows": embedding_rows,
                           "costs_by_basis": embedding_costs},
            "probe": {**probe, "providers": sorted(probe["providers"])},
            "unmeterable": {"phases": len(unmeterable_phases),
                            "tasks": len(unmeterable_tasks),
                            "providers": sorted(unmeterable_providers)},
            "phase_cache_hits": cache_hits,
            "phase_cache_savings_usd": cache_savings,
            # B3 stays separate from phase-cache dollars. Tokens are the work
            # unit the durable artifact actually avoided and retain their
            # reported/estimated basis; no synthetic dollar claim is made.
            "artifacts_reused": artifacts_reused,
            "artifact_reuse_tokens_avoided": sum(reuse_tokens_by_basis.values()),
            "artifact_reuse_tokens_by_basis": reuse_tokens_by_basis,
            "openhands_payload_chars": oh_payload_chars,
            "openhands_payload_est_tokens": oh_payload_chars // 4}


def to_markdown(rep):
    sim = rep["simulated_share"]
    label = ("all simulated" if sim == 1.0 else "measured" if sim == 0.0
             else f"{int((sim or 0) * 100)}% simulated" if sim is not None else "no spend data")
    # THE IRON RULE, applied to the one line everybody reads. This printed a
    # bare `$11.7500` on an estate whose spend rows are 99% simulated: the
    # title carried "99% simulated", but the NUMBER was formatted exactly like
    # a measured dollar, and a number is what gets quoted out of a report. The
    # module docstring says a simulated figure must never masquerade as a
    # measured one; the `~` prefix is how every other basis says so.
    if sim:                       # any simulated share at all, including 1.0
        total = (f"~${rep['total_cost_usd']:.4f}  (SIMULATED — not a measured "
                 f"dollar)" if sim == 1.0 else
                 f"~${rep['total_cost_usd']:.4f}  ({int(sim * 100)}% of spend "
                 f"rows are simulated; the measured part cannot be separated "
                 f"from this figure)")
    else:
        total = f"${rep['total_cost_usd']:.4f}"
    lines = [f"# LLM cost report ({rep['runs']} run(s)"
             + (f", last {rep['window_days']}d" if rep['window_days'] else "")
             + f") — {label}",
             "", f"User-task LLM total: {total}", ""]
    if rep.get("unpriced_calls"):
        lines += [(f"> **This total is incomplete.** {rep['unpriced_calls']} "
                   f"call(s) on {', '.join(rep['unpriced_providers'])} have no "
                   f"`pricing:` entry. Their cost is excluded, not treated as a "
                   f"known zero, and is NOT weighed against any budget ceiling."), ""]
    unmeterable = rep.get("unmeterable") or {}
    providers = ", ".join(unmeterable.get("providers") or []) or "none"
    lines += [(f"Unmeterable: {unmeterable.get('phases', 0)} phase(s) across "
               f"{unmeterable.get('tasks', 0)} task(s) "
               f"(unknown basis; providers: {providers})."), ""]
    embeddings = rep.get("embeddings") or {}
    if embeddings.get("rows"):
        lines += ["## Embedding spend (separate from task LLM total)",
                  "day | provider | basis | calls | tokens | cost",
                  "---|---|---|---:|---:|---:"]
        for row in embeddings["rows"]:
            calls = "unknown" if row.get("calls") is None else row["calls"]
            tokens = "unknown" if row.get("tokens") is None else row["tokens"]
            cost = "unknown" if row.get("cost_usd") is None else f"${row['cost_usd']:.6f}"
            lines.append(f"{row['day']} | {row['provider']} | {row['basis']} | "
                         f"{calls} | {tokens} | {cost}")
        lines.append("")
    probe = rep.get("probe") or {}
    if probe.get("rows"):
        costs = ", ".join(f"{basis} ${value:.6f}" for basis, value in
                          sorted((probe.get("costs_by_basis") or {}).items()))
        lines += ["## Probe spend (excluded from user-task totals)",
                  (f"- {probe['rows']} row(s), {probe['calls']} call(s); "
                   f"providers: {', '.join(probe.get('providers') or []) or 'unknown'}; "
                   f"{costs or 'cost unknown'}"), ""]
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
    if rep.get("by_provider"):
        lines.append("## By provider (all consumers)")
        for k, v in sorted(rep["by_provider"].items()):
            bases = ", ".join(f"{n}x {b}" for b, n in sorted(v["bases"].items()))
            unknown_calls = (f", {v['calls_unknown_rows']} row(s) with unknown call count"
                             if v.get("calls_unknown_rows") else "")
            lines.append(f"- {k}: {v['calls']} known call(s){unknown_calls}, "
                         f"${v['cost_usd']:.4f} "
                         f"({bases or 'no basis recorded'}), "
                         f"{v['input_tokens']} in / {v['output_tokens']} out")
        if rep.get("local_tokens"):
            lines.append(f"- local vs cloud tokens: {rep['local_tokens']} local "
                         f"(no cloud spend) vs {rep['cloud_tokens']} cloud")
        lines.append("")
    if rep.get("by_basis"):
        lines.append("## All consumers by basis")
        for basis, value in sorted(rep["by_basis"].items()):
            lines.append(f"- {basis}: {value['rows']} row(s), "
                         f"{value['calls']} known call(s), ${value['cost_usd']:.4f}, "
                         f"{value['incomplete_rows']} incomplete row(s)")
        lines.append("")
    sav = rep["phase_cache_savings_usd"]
    lines.append(f"Phase-cache hits: {rep['phase_cache_hits']} — estimated saving: "
                 + (f"${sav:.4f}" if sav is not None else "n/a (no measured runs yet)"))
    bases = ", ".join(
        f"{tokens} {basis}" for basis, tokens in sorted(
            (rep.get("artifact_reuse_tokens_by_basis") or {}).items()))
    lines.append(
        f"Artifacts reused: {rep.get('artifacts_reused', 0)} — "
        f"tokens avoided: {rep.get('artifact_reuse_tokens_avoided', 0)}"
        + (f" ({bases})" if bases else " (none recorded)"))
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


def armed():
    """Is there a baseline to compare against at all?

    This exists because `check_regression()` returning [] meant two different
    things — "every phase is within threshold" and "nothing was compared" — and
    the nightly log printed `healthy (or no baseline armed)` for both. The
    parenthetical was an admission, not a distinction: an operator scanning a
    nightly job reads the first word. An estate that never ran `make
    cost-baseline` would see "healthy" every night forever while no cost alarm
    could possibly fire. Constitution C13: not-checked gets its own state.
    """
    base = fs_lock.read_json_guarded(BASELINE, None)
    return bool(base and isinstance(base.get("phases"), dict) and base["phases"])


def check_regression(threshold=None, days=7):
    """Trailing-window medians vs the baseline (story 1.4). Returns a list of
    regression strings (empty = healthy). Callers that need to tell "healthy"
    from "nothing to check against" must ask armed() — see its docstring."""
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
        # Three outcomes, not two. "Nothing was compared" exits 0 like a healthy
        # check — an unarmed estate is not a failure — but it must never PRINT
        # like one, or a nightly log reads "healthy" forever while no cost alarm
        # can fire at all.
        if not armed():
            print("cost regression check: NOT CHECKED - no baseline is armed, so "
                  "no cost regression can be detected on this estate.")
            print("  Arm one with: make cost-baseline   (it refuses an "
                  "all-simulated estate, which is why this can stay unarmed)")
            return 0
        regs = check_regression()
        if not regs:
            print("cost regression check: healthy - every phase is within "
                  "threshold of its armed baseline")
            return 0
        for r in regs:
            print(f"COST REGRESSION: {r}")
        # Notify (best-effort, mock-aware) — the nightly's whole point.
        try:
            import subprocess

            import work_queue
            adapter = ROOT / ("adapters/mock/notify.sh"
                              if env_flag.mock()
                              else "adapters/notify/slack.sh")
            subprocess.run([work_queue.bash_exe(), str(adapter), "post",
                            "[ai-qe] " + "; ".join(regs)[:500]],
                           cwd=ROOT, capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=30)
        except Exception:
            pass
        return 1
    days = None
    try:
        if "--days" in argv:
            days = int(argv[argv.index("--days") + 1])
        rep = report(days)
    except (IndexError, ValueError, OverflowError) as exc:
        print(f"cost report: invalid window: {exc}", file=sys.stderr)
        return 2
    if "--json" in argv:
        print(json.dumps(rep, indent=1))
    else:
        print(to_markdown(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

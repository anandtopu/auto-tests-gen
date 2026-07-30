#!/usr/bin/env python3
"""Per-run budget ENFORCEMENT — cost and wall-clock, checked before every phase.

MAX_COST_USD_PER_RUN and MAX_WALLCLOCK_MIN were settings the UI displayed and
nothing read (docs/product-direction.md flags this as the #1 enterprise objection to
agentic tools). They are now controls: each `claude -p` phase's actual spend — the
`total_cost_usd` the CLI reports in its result JSON — lands in a per-run ledger
(out/cost.tsv), and the pipeline calls `check` BEFORE starting the next phase. Over
either limit, the run aborts with exit 77 (BUDGET_EXCEEDED) and a notification, and
the gate is never reached — a runaway loop can overshoot by at most one phase, never
by a run.

Limits, in precedence order (matching AIQE_MOCK / AIQE_CRITIC semantics):
  cost:  MAX_COST_USD_PER_RUN env (the Settings page writes it) if set, else
         org-config `budgets.max_cost_usd_single_suite` — or `_cross_repo` when the
         resolved run targets more than one test repo. 0 or unset-everywhere means
         unlimited (mock/demo runs meter nothing and must never abort).
  time:  MAX_WALLCLOCK_MIN env, else 25. Applies in every mode — a hung run burns a
         runner slot even when it burns no tokens.

Mock phases spend nothing; AIQE_MOCK_PHASE_COST simulates a per-phase cost so the
enforcement path itself is testable end-to-end without API spend.

CLI (called from engine/pipeline.sh):
  budget.py record <phase> [json_file]   append the phase's cost to the ledger
  budget.py check --start <epoch>        exit 1 + reason when over a limit
  budget.py total                        ledger sum (used by run_record.py)
"""
import json, os, pathlib, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

ROOT = pathlib.Path(__file__).resolve().parents[2]
LEDGER = pathlib.Path(os.environ.get("AIQE_COST_LEDGER", "out/cost.tsv"))

EXIT_BUDGET = 77          # distinct from gate 2-7, invalid-key 64, busy 75


def phase_cost(json_file):
    """(cost_usd, metered) from a claude -p result JSON. Total: unreadable or
    cost-free output is (0.0, False), never an exception."""
    try:
        raw = json.load(open(json_file, encoding="utf-8"))
    except Exception:
        return 0.0, False
    if not isinstance(raw, dict):
        return 0.0, False
    for key in ("total_cost_usd", "cost_usd"):
        v = raw.get(key)
        if isinstance(v, (int, float)):
            return float(v), True
    return 0.0, False


def phase_usage(json_file):
    """Token/turn usage from a claude -p result JSON. Total: anything unreadable
    is zeros, never an exception. The CLI already reports all of this — telemetry
    is harvesting, not instrumentation (cost-reduction story 1.1)."""
    zeros = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
             "cache_creation_tokens": 0, "turns": 0}
    try:
        raw = json.load(open(json_file, encoding="utf-8"))
    except Exception:
        return zeros
    if not isinstance(raw, dict):
        return zeros
    u = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}

    def _i(*keys):
        for k in keys:
            v = u.get(k)
            if isinstance(v, (int, float)):
                return int(v)
        return 0
    return {"input_tokens": _i("input_tokens"),
            "output_tokens": _i("output_tokens"),
            "cache_read_tokens": _i("cache_read_input_tokens", "cache_read_tokens"),
            "cache_creation_tokens": _i("cache_creation_input_tokens",
                                        "cache_creation_tokens"),
            "turns": int(raw.get("num_turns") or 0)}


def _model_for(label):
    """The configured model tier for a ledger label. Fan-out labels like
    generate-e2e-api-tests-1 resolve their POLICY phase (generate), mirroring
    run_phase.sh's AIQE_PHASE_LABEL split."""
    try:
        import yaml
        models = (yaml.safe_load(open(ROOT / "registry/org-config.yaml",
                                      encoding="utf-8")) or {}).get("models") or {}
        if label in models:
            return str(models[label])
        base = label.split("-", 1)[0]
        if base in models:
            return str(models[base])
    except Exception:
        pass
    return ""


def record(phase, json_file=None):
    """Append one phase's spend to the ledger. Never fails the caller.

    Row format (columns 5+ added by cost-reduction story 1.1; readers of the
    original 4 columns — total(), check() — are unaffected):
      phase  cost  metered  ts  model  in  out  cache_read  cache_create  turns
    """
    cost, metered = (0.0, False)
    usage = phase_usage(json_file) if json_file and \
        pathlib.Path(json_file).exists() else phase_usage("/nonexistent")
    if json_file and pathlib.Path(json_file).exists():
        cost, metered = phase_cost(json_file)
    if not metered:
        # Mock phases produce no cost JSON; a simulated cost keeps the whole
        # enforcement path testable without API spend.
        sim = os.environ.get("AIQE_MOCK_PHASE_COST", "").strip()
        if sim:
            try:
                cost, metered = float(sim), True
            except ValueError:
                pass
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"{phase}\t{cost:.6f}\t{int(metered)}\t{time.time():.0f}"
                     f"\t{_model_for(phase)}\t{usage['input_tokens']}"
                     f"\t{usage['output_tokens']}\t{usage['cache_read_tokens']}"
                     f"\t{usage['cache_creation_tokens']}\t{usage['turns']}\n")
    except OSError:
        pass
    return cost, metered


def read_ledger(ledger=None):
    """Parsed ledger rows as dicts. Old 4-column rows parse with zero usage —
    a crashed run's leftover file must never break the next run's record."""
    p = pathlib.Path(ledger) if ledger else LEDGER
    rows = []
    try:
        for line in open(p, encoding="utf-8"):
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            try:
                cost = float(parts[1])
            except ValueError:
                continue

            def _i(idx):
                try:
                    return int(parts[idx])
                except (IndexError, ValueError):
                    return 0
            rows.append({"phase": parts[0], "cost_usd": cost,
                         "metered": parts[2] == "1", "ts": _i(3),
                         "model": parts[4] if len(parts) > 4 else "",
                         "input_tokens": _i(5), "output_tokens": _i(6),
                         "cache_read_tokens": _i(7),
                         "cache_creation_tokens": _i(8), "turns": _i(9)})
    except OSError:
        pass
    return rows


def total(ledger=None):
    """(total_usd, metered_phases, unmetered_phases) from the ledger."""
    p = pathlib.Path(ledger) if ledger else LEDGER
    tot, metered, unmetered = 0.0, 0, 0
    try:
        for line in open(p, encoding="utf-8"):
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            try:
                tot += float(parts[1])
            except ValueError:
                continue
            if parts[2] == "1":
                metered += 1
            else:
                unmetered += 1
    except OSError:
        pass
    return tot, metered, unmetered


def _cross_repo():
    try:
        d = json.load(open("out/resolve.contract.json", encoding="utf-8"))
        return len(d.get("test_repos", [])) > 1
    except Exception:
        return False


def limits():
    """(cost_limit_usd, wallclock_min, cost_source). A limit of 0 disables it."""
    env = os.environ.get("MAX_COST_USD_PER_RUN", "").strip()
    cost_limit, source = 0.0, "unset"
    if env:
        try:
            cost_limit, source = float(env), "MAX_COST_USD_PER_RUN"
        except ValueError:
            pass
    if source == "unset":
        try:
            import yaml
            cfg = yaml.safe_load(open(ROOT / "registry/org-config.yaml",
                                      encoding="utf-8")) or {}
            b = cfg.get("budgets") or {}
            key = ("max_cost_usd_cross_repo" if _cross_repo()
                   else "max_cost_usd_single_suite")
            v = b.get(key)
            if isinstance(v, (int, float)):
                cost_limit, source = float(v), f"org-config {key}"
        except Exception:
            pass
    try:
        wall_min = float(os.environ.get("MAX_WALLCLOCK_MIN", "") or 25)
    except ValueError:
        wall_min = 25.0
    return cost_limit, wall_min, source


def check(start_epoch):
    """None when within budget; otherwise a human-readable reason string.

    Cost is enforced only once at least one phase was METERED — mock/demo runs
    (nothing metered, total 0) must never abort on cost. Wall-clock always applies.
    """
    cost_limit, wall_min, source = limits()
    tot, metered, _ = total()
    if cost_limit > 0 and metered > 0 and tot > cost_limit:
        return (f"BUDGET_EXCEEDED: cost ${tot:.2f} over the ${cost_limit:.2f} "
                f"limit ({source}) after {metered} metered phase(s)")
    if wall_min > 0 and start_epoch:
        elapsed_min = (time.time() - float(start_epoch)) / 60
        if elapsed_min > wall_min:
            return (f"BUDGET_EXCEEDED: wall-clock {elapsed_min:.1f} min over the "
                    f"{wall_min:.0f} min limit (MAX_WALLCLOCK_MIN)")
    return None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "record":
        phase = sys.argv[2]
        jf = sys.argv[3] if len(sys.argv) > 3 else None
        cost, metered = record(phase, jf)
        if metered and cost:
            tot, _, _ = total()
            print(f"[budget] {phase}: ${cost:.4f} (run total ${tot:.2f})")
    elif cmd == "check":
        start = 0
        if "--start" in sys.argv:
            start = float(sys.argv[sys.argv.index("--start") + 1])
        reason = check(start)
        if reason:
            print(reason)
            sys.exit(1)
    elif cmd == "total":
        tot, metered, unmetered = total()
        print(f"{tot:.4f}\t{metered}\t{unmetered}")
    else:
        sys.exit(__doc__)

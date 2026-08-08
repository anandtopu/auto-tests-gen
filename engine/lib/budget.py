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


def provider_of(json_file):
    """The provider that produced this result, from the normalized JSON the
    adapters emit (multi-LLM 4.1). Unknown -> "" rather than a guess."""
    try:
        raw = json.load(open(json_file, encoding="utf-8"))
        return str(raw.get("provider") or "") if isinstance(raw, dict) else ""
    except Exception:
        return ""


def _pricing():
    try:
        import yaml
        return (yaml.safe_load(open(ROOT / "registry/org-config.yaml",
                                    encoding="utf-8")) or {}).get("pricing") or {}
    except Exception:
        return {}


def priced(provider, model, usage):
    """(cost_usd, basis) for a provider that reports TOKENS but no cost.

    basis is the honesty label the whole cost stack keys on:
      reported  the provider gave a dollar figure (handled by phase_cost)
      estimated computed here from org-config `pricing:` list prices — shown
                with a ~ so it can never read as a billed number
      local     local inference: $0, tokens still tracked
      unknown   tokens exist but no price table entry — cost stays unknown,
                NEVER silently 0 (that would understate a real bill)
    """
    table = _pricing().get(provider)
    # CONFIGURATION WINS over the provider's name. `ollama` used to be forced
    # to `local` here regardless of the price table — but that adapter speaks
    # plain OpenAI-compatible HTTP and happily serves a PAID hosted gateway,
    # so an operator who priced it got "$0 (local)" for a real bill. Exactly
    # the understatement this module exists to prevent. org-config carries
    # `ollama: local` as the DEFAULT; an explicit table now overrides it.
    if table == "local":
        return 0.0, "local"
    if not isinstance(table, dict):
        return None, "unknown"
    row = table.get(model) or table.get("*")
    if not isinstance(row, dict):
        return None, "unknown"
    try:
        cost = (int(usage.get("input_tokens") or 0) / 1_000_000 * float(row.get("in", 0))
                + int(usage.get("output_tokens") or 0) / 1_000_000 * float(row.get("out", 0)))
    except (TypeError, ValueError):
        return None, "unknown"
    return round(cost, 6), "estimated"


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


def record(phase, json_file=None, allow_simulation=True):
    """Append one phase's spend to the ledger. Never fails the caller.

    Row format (columns 5+ added by cost-reduction story 1.1; readers of the
    original 4 columns — total(), check() — are unaffected):
      phase  cost  metered  ts  model  in  out  cache_read  cache_create  turns
    """
    cost, metered = (0.0, False)
    exists = bool(json_file) and pathlib.Path(json_file).exists()
    usage = phase_usage(json_file) if exists else phase_usage("/nonexistent")
    provider, basis = (provider_of(json_file) if exists else ""), ""
    if exists:
        cost, metered = phase_cost(json_file)
        if metered:
            basis = "reported"
        elif provider:
            # The provider gave tokens but no dollar figure (local models,
            # codex, openhands-delegated): price it from the org-config table
            # or leave it UNKNOWN. Never a silent 0 for a real bill.
            priced_cost, basis = priced(provider, _model_for(phase), usage)
            if priced_cost is not None:
                cost, metered = priced_cost, (basis == "estimated")
    if not metered and allow_simulation:
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
                     f"\t{usage['cache_creation_tokens']}\t{usage['turns']}"
                     f"\t{provider}\t{basis}\n")
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
                         "cache_creation_tokens": _i(8), "turns": _i(9),
                         # multi-LLM 4.1: which provider produced this, and
                         # how its cost figure was arrived at.
                         "provider": parts[10] if len(parts) > 10 else "",
                         "cost_basis": parts[11] if len(parts) > 11 else ""})
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


def unpriced(ledger=None):
    """(calls, providers) whose cost basis is `unknown` — tokens were spent but
    no price could be attached.

    This is the hole that made the whole spend-control stack silently inert: an
    unpriced provider records cost 0 and metered=0, so `check()` never fires the
    exit-77 ceiling and `grade()` never starts the degradation ladder. A run
    could burn tens of millions of tokens and report $0.00 "within budget".
    Cost cannot be invented — but the INABILITY to enforce must be visible.
    """
    calls, provs = 0, set()
    for r in read_ledger(ledger):
        if r.get("cost_basis") == "unknown":
            calls += 1
            provs.add(r.get("provider") or "?")
    return calls, sorted(provs)


def ledger_problem():
    """Why the ledger cannot be counted, or "" when it can.

    `record()` and `total()` both swallow OSError, so an unwritable or
    unreadable ledger silently reports $0.00 spent — and `enforceability()`
    then answered "enforced" while counting nothing. Demonstrated: $25.00 of
    real spend against a $1.00 ceiling reported as enforced and within budget.

    Checked at VERDICT time rather than flagged at record time, because each
    phase records in its own process and an in-memory flag would not survive to
    the process that renders the verdict.

    A MISSING ledger is normal — no phase has run yet — and is not a problem.
    """
    p = LEDGER
    try:
        if p.exists():
            if not p.is_file():
                return f"{p} exists but is not a regular file"
            p.read_text(encoding="utf-8", errors="replace")
            return ""
        parent = p.parent
        if parent.exists() and not os.access(parent, os.W_OK):
            return f"{parent} is not writable, so no spend can be recorded"
    except OSError as e:
        return f"{p} cannot be read ({type(e).__name__})"
    return ""


def enforceability(ledger=None):
    """(state, message). state is 'enforced' | 'partial' | 'unenforceable'.

    Callers must never render a budget verdict without this: "within budget"
    is only true when the spend it covers could actually be priced.
    """
    cost_limit, _, source = limits()
    calls, provs = unpriced(ledger)
    _, metered, _ = total(ledger)
    if cost_limit <= 0:
        return "unenforceable", "no cost limit configured (only wall-clock applies)"
    # Before anything about prices: can the spend be COUNTED at all? An
    # unreadable ledger makes every total $0.00, which reads as a cheap run.
    broken = ledger_problem()
    if broken:
        return "unenforceable", (
            f"BUDGET_UNENFORCEABLE: {broken}. Spend cannot be counted, so the "
            f"${cost_limit:.2f} ceiling ({source}) is NOT being applied — a "
            f"$0.00 total here means 'not measured', not 'nothing spent'.")
    if not calls:
        return "enforced", ""
    who = ", ".join(provs)
    if metered:
        return "partial", (
            f"BUDGET_PARTIAL: {calls} phase(s) on {who} have no price entry, so "
            f"their spend is NOT counted against the ${cost_limit:.2f} limit "
            f"({source}). Add `pricing:` entries for {who} in "
            f"registry/org-config.yaml to enforce the ceiling on them.")
    return "unenforceable", (
        f"BUDGET_UNENFORCEABLE: every metered phase ran on {who}, which has no "
        f"price entry — the ${cost_limit:.2f} ceiling ({source}) and the "
        f"degradation ladder cannot fire at all. Add `pricing:` entries for "
        f"{who} in registry/org-config.yaml, or accept an unbounded run.")


def _cross_repo():
    try:
        d = json.load(open("out/resolve.contract.json", encoding="utf-8"))
        return len(d.get("test_repos", [])) > 1
    except Exception:
        return False


def workflow_envelope(mode, cfg=None):
    """Return (effective, base, review_uplift) for one workflow.

    The B5 uplift is planning headroom, not a measured cost claim. It applies
    only when the generated-test reviewer can actually run; default-disabled
    review therefore preserves the original envelope byte-for-byte. Keeping
    this calculation here gives runtime enforcement and queue intake one
    answer instead of two config interpretations that can drift.
    """
    try:
        import yaml
        cfg = cfg if isinstance(cfg, dict) else (
            yaml.safe_load(open(ROOT / "registry/org-config.yaml",
                                encoding="utf-8")) or {}
        )
        budgets = cfg.get("budgets") or {}
        base = (budgets.get("envelopes") or {}).get(mode)
        if (isinstance(base, bool) or not isinstance(base, (int, float))
                or base <= 0):
            return 0.0, 0.0, 0.0
        uplift = 0.0
        if mode in {"pr", "jira", "tests"}:
            import test_reviewer
            review_cfg = cfg.get("review") or {}
            raw = (budgets.get("review_uplift_usd") or {}).get(mode, 0)
            if (test_reviewer.enabled(review_cfg)
                    and not isinstance(raw, bool)
                    and isinstance(raw, (int, float)) and raw > 0):
                uplift = float(raw)
        return float(base) + uplift, float(base), uplift
    except Exception:
        return 0.0, 0.0, 0.0


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
            # Per-workflow envelopes (cost-reduction 5.2) beat the generic
            # single/cross-repo pair: a PR triage-and-generate run should not
            # get a JIRA plan+generate chain's allowance. Explicit env still
            # wins (checked above) — the layering rule everywhere.
            mode = os.environ.get("AIQE_RUN_MODE", "").strip()
            effective, _base, uplift = workflow_envelope(mode, cfg)
            if effective > 0:
                cost_limit = effective
                source = f"org-config envelopes.{mode}"
                if uplift:
                    source += " + review uplift"
            else:
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


def grade(start_epoch=0):
    """Degradation ladder (cost-reduction 5.3): where this run sits against its
    cost envelope — 'ok' (<60%), 'degrade_tier' (60-80%: non-judgement phases
    drop to the cheap tier), 'degrade_context' (80-100%: scoped context budgets
    halve as well), 'abort' (>100%; `check` turns this into exit 77).

    Judgement phases (testplan, adversary pair, generate, reviewer and
    reviewrepair) NEVER downgrade — they run full-quality or the run aborts;
    a silently cheaper plan or rubber-stamping reviewer is worse than no
    result. Cost grading needs a metered phase, like check; wall-clock is not
    graded (a slow run is aborted, not degraded).
    """
    cost_limit, _, _ = limits()
    tot, metered, _ = total()
    if cost_limit <= 0 or metered == 0:
        return "ok"
    ratio = tot / cost_limit
    if ratio > 1.0:
        return "abort"
    if ratio >= 0.8:
        return "degrade_context"
    if ratio >= 0.6:
        return "degrade_tier"
    return "ok"


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
        succeeded = len(sys.argv) < 5 or sys.argv[4] == "0"
        cost, metered = record(phase, jf, allow_simulation=succeeded)
        if metered and cost:
            tot, _, _ = total()
            print(f"[budget] {phase}: ${cost:.4f} (run total ${tot:.2f})")
    elif cmd == "grade":
        print(grade())
    elif cmd == "check":
        start = 0
        if "--start" in sys.argv:
            start = float(sys.argv[sys.argv.index("--start") + 1])
        reason = check(start)
        if reason:
            print(reason)
            sys.exit(1)
        # Within budget — but say so only about spend that could be PRICED.
        # An unpriced provider records $0, so silence here would read as
        # "under the ceiling" when nothing was ever weighed against it.
        state, msg = enforceability()
        if state in ("partial", "unenforceable") and msg:
            print(msg, file=sys.stderr)
    elif cmd == "enforceability":
        state, msg = enforceability()
        print(state)
        if msg:
            print(msg, file=sys.stderr)
    elif cmd == "total":
        tot, metered, unmetered = total()
        print(f"{tot:.4f}\t{metered}\t{unmetered}")
    else:
        sys.exit(__doc__)

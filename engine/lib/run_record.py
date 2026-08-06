#!/usr/bin/env python3
"""Assemble the structured run record (architecture §8) from out/*.json.
Includes per-test-repo gate outcomes (out/gate_results.tsv) so persisted records
in reports/runs/ carry everything the QA monitoring surfaces need."""
import glob, json, os, pathlib, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import critic as critic_lib
import artifact_reuse
import env_flag                     # AIQE_MOCK means what it says
import task_bundle

run_id, mode, key = sys.argv[1:4]

# Per-phase spend from the budget ledger (cost-reduction 1.1). Keyed by the ledger
# label, which for fan-out calls is the AIQE_PHASE_LABEL (generate-<repo>) — the
# same name the contract file carries, so the join below is exact.
_simulated_run = (env_flag.mock()
                  or bool(os.environ.get("AIQE_MOCK_PHASE_COST", "").strip()))
spend_by_phase = {}
try:
    import budget

    def _turns_ceiling(label):
        try:
            import yaml
            cfg = yaml.safe_load(open(pathlib.Path(budget.ROOT) /
                                      "registry/org-config.yaml",
                                      encoding="utf-8")) or {}
            ph = cfg.get("phases") or {}
            base = label if label in ph else label.split("-", 1)[0]
            return int(ph.get(base, {}).get("max_turns") or 0)
        except Exception:
            return 0
    for row in budget.read_ledger():
        spend_by_phase[row["phase"]] = {
            "model": row["model"], "cost_usd": row["cost_usd"],
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "cache_read_tokens": row["cache_read_tokens"],
            "cache_creation_tokens": row["cache_creation_tokens"],
            "turns_used": row["turns"], "max_turns": _turns_ceiling(row["phase"]),
            # multi-LLM 4.1: which provider ran the phase, and how its cost
            # figure was arrived at (reported | estimated | local | simulated
            # | unknown). The four label classes must never cross.
            # A mock run has no provider to name; call it `mock` rather than
            # blank so the per-phase table and the by_provider rollup (which
            # already falls back to `mock`) tell the same story.
            "provider": row.get("provider") or ("mock" if _simulated_run else ""),
            "cost_basis": ("simulated" if _simulated_run
                           else row.get("cost_basis", "")),
            "simulated": _simulated_run or not row["metered"]}
except Exception:
    pass

phases = []
for f in sorted(glob.glob("out/*.contract.json")):
    name = os.path.basename(f).replace(".contract.json", "")
    entry = {"name": name, "contract": json.load(open(f, encoding="utf-8"))}
    if name in spend_by_phase:
        entry["spend"] = spend_by_phase[name]
    phases.append(entry)

gates = []
malformed_gate_lines = 0
if os.path.exists("out/gate_results.tsv"):
    for line in open("out/gate_results.tsv", encoding="utf-8"):
        if not line.strip():
            continue
        repo, status, exit_code, sha = (line.rstrip("\n").split("\t") + ["", "", "", ""])[:4]
        # A torn line must not cost the whole record. `int("")` raised here, and
        # because this script assembles the ONLY durable copy of a run, one
        # partial write threw away every gate result in the file — including
        # repos that had committed successfully. The work happened and the
        # evidence went missing, which is the worst trade this file can make.
        if not repo or not status:
            malformed_gate_lines += 1
            continue
        try:
            exit_code = int(exit_code)
        except ValueError:
            # Recorded as UNKNOWN rather than 0: a missing exit code is not a
            # successful one, and `exit_code: 0` here would read as a clean run.
            exit_code = None
        diff = f"reports/runs/{run_id}-{repo}.diff"
        gates.append({"test_repo": repo, "status": status, "exit_code": exit_code,
                      "commit": sha or None,
                      "log": f"reports/{key}-{repo}.log",
                      "diff": diff if os.path.exists(diff) else None})

overall = ("quarantined" if any(g["status"] in ("quarantined", "clone_failed")
                                for g in gates)
           else "committed" if any(g["status"] == "committed" for g in gates)
           else "no_changes")
# Advisory critic score lifted to the top level so the scorecard and dashboard don't
# have to dig through phases[]. `overall` above is computed purely from gate outcomes —
# the critic never contributes to it (openhands-review §3.2).
record = {"run_id": run_id, "trigger": {"type": mode, "key": key},
          "ts": time.time(), "overall": overall,
          "gates": gates, "phases": phases}
# Successor PRD A1 discovery provenance. Optional and total: malformed scratch
# cannot cost the otherwise durable gate record.
try:
    discovery = json.load(open("out/ticket-discovery.json", encoding="utf-8"))
    if isinstance(discovery, dict) and discovery.get("artifact") == "pr-ticket-discovery":
        record["ticket_discovery"] = discovery
except (OSError, ValueError):
    pass
# A3's proposal must survive the ephemeral out/ directory so a historical
# `make explain` can answer why generation extended rather than created. A bad
# optional artifact cannot destroy the otherwise durable run record.
try:
    impact = json.load(open("out/impact-candidates.json", encoding="utf-8"))
    if isinstance(impact, dict) and impact.get("artifact") == "impact-candidates":
        record["impact_candidates"] = impact
except (OSError, ValueError):
    pass
# A4 advisory evidence for historical PR comments/metrics. It is independent
# of gate outcome and cannot influence `overall` above.
try:
    duplicates = json.load(open("out/duplicate-warnings.json", encoding="utf-8"))
    if isinstance(duplicates, dict) and duplicates.get("artifact") == "duplicate-warnings":
        record["duplicate_warnings"] = duplicates
except (OSError, ValueError):
    pass
# A6 same-run indexing evidence. A failed optional read cannot destroy the gate
# record, but the learning hook itself writes `state=unavailable` on failure so
# an index outage never masquerades as "no commits".
try:
    learning = json.load(open("out/learning-loop.json", encoding="utf-8"))
    if isinstance(learning, dict) and learning.get("artifact") == "testcase-learning":
        record["testcase_learning"] = learning
except (OSError, ValueError):
    pass
if malformed_gate_lines:
    # Present only when something was lost, and never inferred away: a record
    # showing three gates when the file held four must SAY that it is short,
    # or "gates: [...]" reads as the complete set.
    record["malformed_gate_lines"] = malformed_gate_lines
# Context-scope retries (cost-reduction 2.3): phases that reported missing
# context and re-ran on the full estate — the tuning signal for the scoping
# policy, and an honest marker that this run paid the retry.
# No-op phase skips (5.1) and degradation-ladder rungs (5.3): a reduced or
# shortened run must say so in its own record — "skipped (nothing to do)" and
# "ran in reduced-cost mode" are facts the reviewer needs, not noise.
if os.path.exists("out/phase-skips.tsv"):
    skips = []
    for line in open("out/phase-skips.tsv", encoding="utf-8"):
        parts = line.rstrip("\n").split("\t", 1)
        if parts[0]:
            skips.append({"phase": parts[0],
                          "reason": parts[1] if len(parts) > 1 else ""})
    if skips:
        record["skipped_phases"] = skips
if os.path.exists("out/cost-degrade.tsv"):
    rungs = []
    for line in open("out/cost-degrade.tsv", encoding="utf-8"):
        parts = line.rstrip("\n").split("\t", 1)
        if parts[0] and len(parts) > 1:
            rungs.append({"phase": parts[0], "grade": parts[1]})
    if rungs:
        record["degradation"] = rungs
if os.path.exists("out/context-retries.tsv"):
    retries = []
    for line in open("out/context-retries.tsv", encoding="utf-8"):
        parts = line.rstrip("\n").split("\t", 1)
        if parts[0]:
            retries.append({"phase": parts[0],
                            "missing": parts[1] if len(parts) > 1 else ""})
    if retries:
        record["context_retries"] = retries
signal = critic_lib.load()
if signal:
    record["critic"] = signal
# Spend from the budget ledger (real phases meter; mock runs record 0/simulated).
try:
    import budget
    _tot, _metered, _ = budget.total()
    if _metered:
        record["cost_usd"] = round(_tot, 4)
    # Whether `cost_usd` is the WHOLE bill. An unpriced provider records $0, so
    # without this a reader (and the report, and the Cost view) would take a
    # partial figure for the total and the ceiling as having been applied.
    _calls, _provs = budget.unpriced()
    if _calls:
        _state, _msg = budget.enforceability()
        record["budget"] = {"enforceability": _state,
                            "unpriced_calls": _calls,
                            "unpriced_providers": _provs,
                            "detail": _msg}
except Exception:
    pass
try:
    reuse = artifact_reuse.summary()
    if reuse.get("events"):
        record["artifact_reuse"] = reuse
except Exception:
    pass
try:
    record["artifact_bundle"] = task_bundle.finalize(run_id, mode, key)
except Exception as exc:  # the gate result must survive an optional archive failure
    record["artifact_bundle"] = {
        "state": "unavailable", "schema": task_bundle.SCHEMA,
        "reason": f"task bundle finalization failed: {exc.__class__.__name__}"}
print(json.dumps(record))

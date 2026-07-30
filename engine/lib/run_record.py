#!/usr/bin/env python3
"""Assemble the structured run record (architecture §8) from out/*.json.
Includes per-test-repo gate outcomes (out/gate_results.tsv) so persisted records
in reports/runs/ carry everything the QA monitoring surfaces need."""
import glob, json, os, pathlib, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import critic as critic_lib

run_id, mode, key = sys.argv[1:4]

# Per-phase spend from the budget ledger (cost-reduction 1.1). Keyed by the ledger
# label, which for fan-out calls is the AIQE_PHASE_LABEL (generate-<repo>) — the
# same name the contract file carries, so the join below is exact.
_simulated_run = (os.environ.get("AIQE_MOCK", "1") == "1"
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
if os.path.exists("out/gate_results.tsv"):
    for line in open("out/gate_results.tsv", encoding="utf-8"):
        if not line.strip():
            continue
        repo, status, exit_code, sha = (line.rstrip("\n").split("\t") + ["", "", "", ""])[:4]
        diff = f"reports/runs/{run_id}-{repo}.diff"
        gates.append({"test_repo": repo, "status": status, "exit_code": int(exit_code),
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
signal = critic_lib.load()
if signal:
    record["critic"] = signal
# Spend from the budget ledger (real phases meter; mock runs record 0/simulated).
try:
    import budget
    _tot, _metered, _ = budget.total()
    if _metered:
        record["cost_usd"] = round(_tot, 4)
except Exception:
    pass
print(json.dumps(record))

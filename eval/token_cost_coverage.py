#!/usr/bin/env python3
"""TCA-A2 instrumented exit-path sweep for durable token-cost accounting.

The evaluator copies the current tracked working tree into a temporary sandbox
and drives the real pipeline entry point there. That keeps run records, plan
approvals, generated tests, and regenerated estate guidance out of the operator's
checkout while still testing the same shell orchestration and mock adapters.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine/lib"))
import work_queue

KEY = "PROJ-301"
SCENARIOS = (
    ("requirements", "requirements", (KEY,), 0, {}),
    ("plan", "plan", (KEY,), 0, {}),
    ("tests", "tests", (KEY,), 0, {"approve_plan": True}),
    ("jira", "jira", (KEY,), 0, {}),
    ("pr", "pr", ("orders-api", "201"), 0, {}),
    ("clarification_65", "jira", (KEY,), 65,
     {"AIQE_MOCK_BLOCKING_CLARIFICATION": "1"}),
    ("budget_abort_77", "jira", (KEY,), 77,
     {"AIQE_MOCK_PHASE_COST": "0.60", "MAX_COST_USD_PER_RUN": "0.50"}),
    ("mid_phase_kill", "jira", (KEY,), 143,
     {"AIQE_MOCK_KILL_PHASE": "analyze"}),
)


class CoverageFailure(RuntimeError):
    pass


def _tracked_snapshot(target: pathlib.Path) -> None:
    """Copy current tracked contents, including unstaged modifications."""
    proc = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True,
        capture_output=True,
    )
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        rel = pathlib.Path(os.fsdecode(raw))
        source = ROOT / rel
        if not source.is_file():
            continue
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _base_env(sandbox: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "AIQE_MOCK": "1",
        "AIQE_MOCK_PHASE_COST": "0.01",
        "AIQE_SPEND_LEDGER": "1",
        "AIQE_COSTS_DIR": str(sandbox / "reports/eval-costs"),
        "AIQE_COST_ATTRIBUTION": "eval",
        "AIQE_EVENTS_DIR": str(sandbox / "out/eval-events"),
        "AIQE_RETRIES_FILE": str(sandbox / "out/eval-retries.json"),
        "AIQE_REVIEWS_FILE": str(sandbox / "out/eval-reviews.json"),
        "AIQE_QUEUE_FILE": str(sandbox / "out/eval-queue.json"),
        "AIQE_ARTIFACTS_DIR": str(sandbox / "out/eval-artifacts"),
        "AIQE_REQUIREMENTS_GATE": "0",
        "MAX_COST_USD_PER_RUN": "20",
        "MAX_WALLCLOCK_MIN": "10",
    })
    for name in ("AIQE_MOCK_BLOCKING_CLARIFICATION", "AIQE_MOCK_KILL_PHASE"):
        env.pop(name, None)
    return env


def _ledger_files(sandbox: pathlib.Path) -> set[pathlib.Path]:
    return set((sandbox / "reports/eval-costs").glob("*.json"))


def validate_entry(name: str, mode: str, expected_status: int,
                   result: subprocess.CompletedProcess[str], doc: dict,
                   lock_exists: bool) -> dict:
    """Validate one observed invocation and return compact scorecard evidence."""
    if result.returncode != expected_status:
        tail = (result.stdout + "\n" + result.stderr)[-2000:]
        raise CoverageFailure(
            f"{name}: exit {result.returncode}, expected {expected_status}\n{tail}")
    rows = doc.get("rows") if isinstance(doc, dict) else None
    if doc.get("mode") != mode or not isinstance(rows, list) or not rows:
        raise CoverageFailure(f"{name}: missing/non-matching durable ledger entry")
    if lock_exists:
        raise CoverageFailure(f"{name}: out/.pipeline.lock survived the invocation")
    if any(row.get("attribution") != "eval" for row in rows):
        raise CoverageFailure(f"{name}: evaluator rows lack eval attribution")

    phases = [str(row.get("phase") or "") for row in rows]
    bases = [str(row.get("basis") or "") for row in rows]
    if name == "clarification_65" and phases != ["analyze"]:
        raise CoverageFailure(f"{name}: expected only recorded analyze, got {phases}")
    if (name == "budget_abort_77"
            and (phases != ["analyze"] or bases != ["simulated"])):
        raise CoverageFailure(
            f"{name}: guarded never-started phase was recorded: {phases}/{bases}")
    if (name == "mid_phase_kill"
            and (phases != ["analyze"] or bases != ["unrecorded"])):
        raise CoverageFailure(
            f"{name}: child death must be one unrecorded analyze row: {phases}/{bases}")

    return {
        "scenario": name,
        "mode": mode,
        "exit": result.returncode,
        "run_id": doc.get("run_id"),
        "phases": phases,
        "bases": bases,
        "durable_entry": True,
        "lock_released": True,
    }


def _approve_plan(sandbox: pathlib.Path, env: dict[str, str]) -> None:
    result = subprocess.run(
        [sys.executable, "engine/lib/plan_state.py", "set", KEY, "approved",
         "--by", "token-cost-eval", "--note", "TCA-A2 instrumented sweep"],
        cwd=sandbox, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
        timeout=60, check=False,
    )
    if result.returncode:
        raise CoverageFailure(f"tests setup: plan approval failed: {result.stderr}")


def run_sweep(sandbox: pathlib.Path) -> dict:
    env = _base_env(sandbox)
    evidence = []
    for name, mode, args, expected, controls in SCENARIOS:
        scenario_env = dict(env)
        if controls.get("approve_plan"):
            _approve_plan(sandbox, scenario_env)
        scenario_env.update({k: v for k, v in controls.items()
                             if isinstance(v, str)})
        before = _ledger_files(sandbox)
        started = time.monotonic()
        result = subprocess.run(
            [work_queue.bash_exe(), "engine/pipeline.sh", mode, *args],
            cwd=sandbox, env=scenario_env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
            timeout=600, check=False,
        )
        created = _ledger_files(sandbox) - before
        if len(created) != 1:
            raise CoverageFailure(
                f"{name}: expected exactly one new ledger entry, got {len(created)}")
        doc = json.loads(next(iter(created)).read_text(encoding="utf-8"))
        row = validate_entry(name, mode, expected, result, doc,
                             (sandbox / "out/.pipeline.lock").exists())
        row["duration_seconds"] = round(time.monotonic() - started, 3)
        evidence.append(row)
        print(f"[TCA-A2] {name}: exit={row['exit']} phases={','.join(row['phases'])} "
              f"ledger={row['run_id']} lock=released")

    covered = sum(1 for row in evidence if row["durable_entry"])
    return {
        "schema": 1,
        "metric": "M1",
        "covered": covered,
        "eligible_invocations": len(evidence),
        "coverage_percent": round(100 * covered / len(evidence), 1),
        "target_percent": 100.0,
        "status": "pass" if covered == len(evidence) else "fail",
        "scenarios": evidence,
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="aiqe-tca-a2-") as raw:
        sandbox = pathlib.Path(raw) / "estate"
        sandbox.mkdir()
        _tracked_snapshot(sandbox)
        report = run_sweep(sandbox)
    target = ROOT / "eval/results/token-cost-coverage.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(f"[TCA-A2] M1 {report['covered']}/{report['eligible_invocations']} "
          f"({report['coverage_percent']:.1f}%) -> {target}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CoverageFailure, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"[TCA-A2] FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)

#!/usr/bin/env python3
"""Persist the live per-run cost TSV as auditable history on pipeline exit.

``budget.py`` remains the live enforcement authority. This module only records
provider-call starts and atomically snapshots completed/unrecorded spend.
"""
from __future__ import annotations

import json
import math
import os
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app_paths
import budget
import env_flag
import fs_lock

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA = 1
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def costs_dir(root=None):
    return app_paths.costs_dir(root or ROOT)


def starts_file(root=None):
    raw = (os.environ.get("AIQE_PHASE_STARTS_FILE") or "").strip()
    return pathlib.Path(raw) if raw else pathlib.Path(root or ROOT) / "out/phase-starts.jsonl"


def enabled():
    return env_flag.flag("AIQE_SPEND_LEDGER", True)


def attribution():
    value = (os.environ.get("AIQE_COST_ATTRIBUTION") or "user").strip()
    if not value or len(value) > 64 or any(ord(ch) < 32 for ch in value):
        return "user"
    return value


def _safe_run_id(run_id):
    value = str(run_id or "")
    return value if RUN_ID_RE.fullmatch(value) and value not in (".", "..") else ""


def mark_start(run_id, mode, key, phase, provider="", model="", marker_path=None):
    """Append one fact immediately before a provider call actually starts."""
    if not enabled() or not _safe_run_id(run_id) or not str(phase or "").strip():
        return False
    row = {
        "run_id": str(run_id), "mode": str(mode or ""), "key": str(key or ""),
        "phase": str(phase), "provider": str(provider or ""),
        "model": str(model or ""), "ts": time.time(),
    }
    path = pathlib.Path(marker_path) if marker_path else starts_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except OSError:
        return False


def _starts(path):
    out = {}
    try:
        lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(row, dict) or not isinstance(row.get("phase"), str):
            continue
        phase = row["phase"].strip()
        if phase:
            out.setdefault(phase, row)
    return out


def _finite_cost(value):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _completed_rows(path):
    """Consolidate repeats under the PRD's (run, phase) union identity."""
    grouped = {}
    for raw in budget.read_ledger(path):
        phase = str(raw.get("phase") or "").strip()
        if phase:
            grouped.setdefault(phase, []).append(raw)
    out = {}
    for phase, rows in grouped.items():
        bases = []
        attempt_details = []
        for row in rows:
            basis = str(row.get("cost_basis") or "").strip()
            if not basis:
                # A mock success has a metered simulated amount. A failed child
                # has only the start marker plus budget.record's empty fallback
                # row. Mock mode alone must not promote that unknown call into a
                # completed simulated charge.
                basis = ("simulated" if row.get("metered") and (
                         env_flag.mock(default=False) or
                         (os.environ.get("AIQE_MOCK_PHASE_COST") or "").strip())
                         else "unrecorded")
            bases.append(basis)
            attempt_details.append({
                "ts": int(row.get("ts") or 0),
                "provider": str(row.get("provider") or ""),
                "model": str(row.get("model") or ""),
                "basis": basis,
                "cost_usd": (_finite_cost(row.get("cost_usd"))
                             if basis not in ("unknown", "unrecorded") else None),
            })
        basis = bases[0] if len(set(bases)) == 1 else "unknown"
        known_costs = [_finite_cost(row.get("cost_usd")) for row in rows]
        cost = (round(sum(known_costs), 6)
                if basis not in ("unknown", "unrecorded")
                and all(v is not None for v in known_costs) else None)
        last = rows[-1]
        out[phase] = {
            "phase": phase, "provider": str(last.get("provider") or ""),
            "model": str(last.get("model") or ""), "basis": basis,
            "input_tokens": sum(max(0, int(r.get("input_tokens") or 0)) for r in rows),
            "output_tokens": sum(max(0, int(r.get("output_tokens") or 0)) for r in rows),
            "cache_read_tokens": sum(max(0, int(r.get("cache_read_tokens") or 0)) for r in rows),
            "cache_creation_tokens": sum(max(0, int(r.get("cache_creation_tokens") or 0)) for r in rows),
            "turns": sum(max(0, int(r.get("turns") or 0)) for r in rows),
            "cost_usd": cost,
            "ts": min((int(r.get("ts") or 0) for r in rows), default=0),
            "attempts": len(rows),
            # Reconciliation windows may cut through retries around midnight.
            # Preserve each call's timestamp/basis/cost while keeping the
            # existing phase aggregate as the history union identity.
            "attempt_details": attempt_details,
        }
    return out


def flush(run_id, mode, key, ledger_path=None, marker_path=None, target_dir=None):
    """Persist one run atomically, or return None when disabled/empty."""
    run_id = _safe_run_id(run_id)
    if not enabled() or not run_id:
        return None
    live = pathlib.Path(ledger_path) if ledger_path else budget.LEDGER
    markers = _starts(marker_path or starts_file())
    completed = _completed_rows(live)
    # ``PHASE`` calls budget.record even when its child fails before reaching a
    # provider (for example, invalid provider configuration). That produces an
    # empty TSV row but is still never-started. Only a marker may promote such a
    # row to unrecorded; completed rows with a real basis stand on their own.
    completed_calls = {phase for phase, row in completed.items()
                       if row["basis"] != "unrecorded"}
    phases = sorted(set(markers) | completed_calls)
    if not phases:
        return None
    stamp = attribution()
    rows = []
    for phase in phases:
        row = completed.get(phase)
        if row is None or row["basis"] == "unrecorded":
            started = markers[phase]
            attempts = row["attempts"] if row is not None else 1
            row = {
                "phase": phase, "provider": str(started.get("provider") or ""),
                "model": str(started.get("model") or ""), "basis": "unrecorded",
                "input_tokens": None, "output_tokens": None,
                "cache_read_tokens": None, "cache_creation_tokens": None,
                "turns": None, "cost_usd": None, "ts": started.get("ts"),
                "attempts": attempts,
            }
        elif phase in markers:
            row["provider"] = row["provider"] or str(markers[phase].get("provider") or "")
            row["model"] = row["model"] or str(markers[phase].get("model") or "")
        rows.append({"run_id": run_id, "mode": str(mode or ""),
                     "key": str(key or ""), **row, "attribution": stamp})
    doc = {"schema": SCHEMA, "run_id": run_id, "mode": str(mode or ""),
           "key": str(key or ""), "attribution": stamp,
           "flushed_at": time.time(), "rows": rows}
    target = (pathlib.Path(target_dir) if target_dir else costs_dir()) / f"{run_id}.json"
    try:
        with fs_lock.lock(target, timeout=30):
            fs_lock.write_json_atomic(target, doc, sort_keys=True)
    except (OSError, TimeoutError) as exc:
        raise RuntimeError(f"durable spend flush failed for {run_id}: {exc}") from exc
    return target


def prune(keep, target_dir=None):
    """Keep the newest durable entries by flush time; malformed files survive."""
    if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
        raise ValueError("keep must be a positive integer")
    directory = pathlib.Path(target_dir) if target_dir else costs_dir()
    records = []
    if directory.is_dir():
        for path in directory.glob("*.json"):
            try:
                doc = fs_lock.read_json_guarded(path, None)
                stamp = float((doc or {}).get("flushed_at", 0))
            except (OSError, TypeError, ValueError, OverflowError):
                continue
            if isinstance(doc, dict) and _safe_run_id(doc.get("run_id")) == path.stem:
                records.append((stamp, path))
    records.sort(reverse=True)
    removed = 0
    for _, path in records[keep:]:
        with fs_lock.lock(path, timeout=30):
            path.unlink(missing_ok=True)
        removed += 1
    return {"kept": min(len(records), keep), "removed": removed}


def main(argv=None):
    argv = list(argv or sys.argv)
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "mark-start" and len(argv) in (6, 7, 8):
        ok = mark_start(argv[2], argv[3], argv[4], argv[5],
                        argv[6] if len(argv) > 6 else "",
                        argv[7] if len(argv) > 7 else "")
        return 0 if ok or not enabled() else 1
    if cmd == "flush" and len(argv) == 5:
        path = flush(argv[2], argv[3], argv[4])
        if path:
            print(path)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

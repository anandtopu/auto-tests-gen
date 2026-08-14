#!/usr/bin/env python3
"""Sole read accessor for durable historical provider spend.

Live budget enforcement deliberately remains in :mod:`budget`. This module
unions the durable exit ledger with enriched run-record spend, deduplicating by
``(run_id, phase)``. Run records win descriptive fields; a retry aggregate in
the ledger wins quantitative fields so repeated calls are not lost.
"""
from __future__ import annotations

import math
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app_paths
import fs_lock

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS = ROOT / "reports/runs"
SKIP = {"reviews.json", "queue.json", "hooks-seen.json"}
MAX_WINDOW_DAYS = 36500
_COUNTS = ("input_tokens", "output_tokens", "cache_read_tokens",
           "cache_creation_tokens", "turns", "max_turns", "attempts")


def _window(days):
    if days is None or days == 0:
        return None
    try:
        value = int(days)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("days must be a whole number") from exc
    if value < 1 or value > MAX_WINDOW_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_WINDOW_DAYS}")
    return value


def _number(value):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _count(value, default=0):
    if value is None:
        return default
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if value >= 0 else None


def _attempt_details(raw):
    value = raw.get("attempt_details")
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if not isinstance(item, dict):
            return []
        ts = _number(item.get("ts"))
        basis = str(item.get("basis") or "").strip()
        cost = _number(item.get("cost_usd"))
        if ts is None or not basis:
            return []
        if basis in ("unknown", "unrecorded", "not-reconciled"):
            cost = None
        out.append({"ts": ts, "provider": str(item.get("provider") or ""),
                    "model": str(item.get("model") or ""), "basis": basis,
                    "cost_usd": cost})
    return out


def _base(raw, source):
    run_id = str(raw.get("run_id") or "").strip()
    phase = str(raw.get("phase") or raw.get("name") or "").strip()
    if not run_id or not phase:
        return None
    basis = str(raw.get("basis") or raw.get("cost_basis") or "").strip()
    simulated = raw.get("simulated", basis == "simulated")
    if not isinstance(simulated, bool):
        return None
    cost = _number(raw.get("cost_usd"))
    if basis in ("unknown", "unrecorded", "not-reconciled"):
        cost = None
    row = {
        "run_id": run_id, "phase": phase,
        "key": str(raw.get("key") or ""), "mode": str(raw.get("mode") or ""),
        "provider": str(raw.get("provider") or ""), "model": str(raw.get("model") or ""),
        "basis": basis or ("simulated" if simulated else "unknown"),
        "cost_usd": cost, "simulated": simulated,
        "attribution": str(raw.get("attribution") or ""), "source": source,
        "artifact_reuse": raw.get("artifact_reuse") or {},
        "attempt_details": _attempt_details(raw),
    }
    for field in _COUNTS:
        default = 1 if field == "attempts" else (None if basis == "unrecorded" else 0)
        row[field] = _count(raw.get(field), default)
        if row[field] is None and not (basis == "unrecorded" and field != "attempts"):
            return None
    row["ts"] = _number(raw.get("ts")) or 0.0
    return row


def _ledger_rows(directory):
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        doc = fs_lock.read_json_guarded(path, None)
        if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
            continue
        for raw in doc["rows"]:
            if not isinstance(raw, dict):
                continue
            raw = {**raw, "run_id": raw.get("run_id") or doc.get("run_id"),
                   "key": raw.get("key") or doc.get("key"),
                   "mode": raw.get("mode") or doc.get("mode"),
                   "attribution": raw.get("attribution") or doc.get("attribution"),
                   "ts": raw.get("ts") or doc.get("flushed_at")}
            row = _base(raw, "ledger")
            if row:
                yield row


def _record_rows(directory):
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        if path.name in SKIP:
            continue
        doc = fs_lock.read_json_guarded(path, None)
        if not isinstance(doc, dict) or not isinstance(doc.get("phases"), list):
            continue
        trigger = doc.get("trigger") if isinstance(doc.get("trigger"), dict) else {}
        for phase in doc["phases"]:
            if not isinstance(phase, dict) or not isinstance(phase.get("spend"), dict):
                continue
            raw = {**phase["spend"], "run_id": doc.get("run_id") or path.stem,
                   "phase": phase.get("name"), "key": trigger.get("key"),
                   "mode": trigger.get("type"), "ts": doc.get("ts"),
                   "turns": phase["spend"].get("turns_used"),
                   "artifact_reuse": doc.get("artifact_reuse") or {}}
            row = _base(raw, "run_record")
            if row:
                yield row


def _merge(ledger, record):
    """Enriched record wins, except ledger aggregates multiple call attempts."""
    merged = {**ledger, **record, "source": "run_record"}
    attempts = ledger.get("attempts") or 1
    merged["attempts"] = attempts
    # A run record has enriched phase metadata but not call-level timestamps.
    # Never let its normalized empty list erase the ledger evidence C3 needs.
    merged["attempt_details"] = ledger.get("attempt_details") or []
    if attempts > 1:
        for field in ("cost_usd", "input_tokens", "output_tokens",
                      "cache_read_tokens", "cache_creation_tokens", "turns"):
            merged[field] = ledger.get(field)
        merged["basis"] = ledger.get("basis") or merged["basis"]
        merged["simulated"] = ledger.get("simulated", merged["simulated"])
    return merged


def spend_rows(days=None, runs_dir=None, costs_dir=None):
    """Return normalized, oldest-first rows from both durable sources."""
    days = _window(days)
    run_path = pathlib.Path(runs_dir) if runs_dir is not None else RUNS
    cost_path = pathlib.Path(costs_dir) if costs_dir is not None else app_paths.costs_dir(ROOT)
    rows = {}
    for row in _ledger_rows(cost_path) or ():
        rows[(row["run_id"], row["phase"])] = row
    for row in _record_rows(run_path) or ():
        identity = (row["run_id"], row["phase"])
        rows[identity] = _merge(rows[identity], row) if identity in rows else row
    cutoff = time.time() - days * 86400 if days is not None else 0
    return sorted((row for row in rows.values() if row["ts"] >= cutoff),
                  key=lambda row: (row["ts"], row["run_id"], row["phase"]))


def phase_simulated(record, phase):
    """Was this phase's spend simulated? True / False / None if not established.

    Spend resolution lives in this module by design -- `test_spend_history`
    fails the build on a ninth consumer that reads `["spend"]` off a record --
    and `critic.provenance` needs exactly one bit out of it: did a real model
    do the work. So the raw access stays here and the caller asks a question.

    `None` is a state, not a fallback (C13). A phase recorded with NO spend
    block cannot vouch for anything, and reading its silence as "not simulated"
    is how a stub's fixed score comes to look like a measurement.
    """
    for entry in (record or {}).get("phases") or []:
        if entry.get("name") == phase:
            spend = entry.get("spend") or {}
            return bool(spend.get("simulated")) if spend else None
    return None

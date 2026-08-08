#!/usr/bin/env python3
"""Compare provider-reported cost with same-window reported ledger evidence.

The provider figure comes only from :mod:`provider_usage`; this module has no
vendor branch. It performs TCA-C3 arithmetic but deliberately does not persist,
notify, auto-correct, or choose an alert threshold (all TCA-C4 operations).
"""
from __future__ import annotations

import argparse
import datetime as dt
import decimal
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import provider_usage
import spend_history


def _money(value):
    try:
        number = decimal.Decimal(str(value))
    except (decimal.InvalidOperation, ValueError) as exc:
        raise ValueError("cost evidence must be a decimal number") from exc
    if not number.is_finite() or number < 0:
        raise ValueError("cost evidence must be finite and non-negative")
    return number


def _text(value):
    # Decimal formatting preserves exact arithmetic and remains safe for values
    # larger than the default Decimal context's 28 digits.
    return format(value, ".6f")


def _window(window):
    try:
        start = dt.datetime.fromisoformat(window["starting_at"].replace("Z", "+00:00"))
        end = dt.datetime.fromisoformat(window["ending_at"].replace("Z", "+00:00"))
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("provider window is invalid") from exc
    if start.utcoffset() != dt.timedelta(0) or end.utcoffset() != dt.timedelta(0) or end <= start:
        raise ValueError("provider window must be increasing UTC timestamps")
    return start.timestamp(), end.timestamp()


def _entries(row):
    """Return call-level evidence when complete, otherwise one legacy aggregate."""
    attempts = int(row.get("attempts") or 1)
    details = row.get("attempt_details") or []
    if len(details) == attempts and attempts > 0:
        return details, False
    return ([{"ts": row.get("ts"), "provider": row.get("provider"),
              "basis": row.get("basis"), "cost_usd": row.get("cost_usd"),
              "attempts": attempts}], attempts > 1)


def reconcile(usage, rows):
    """Pure reconciliation model over normalized provider and history rows."""
    provider = str(usage.get("provider") or "")
    provider_usage._validate(usage, provider)
    if usage.get("state") != "available":
        return {"schema": 1, "state": "unavailable", "provider": provider,
                "provider_usage": usage, "auto_corrected": False}
    start, end = _window(usage["window"])
    provider_cost = _money(usage["cost"]["amount_usd"])
    reported_cost = decimal.Decimal(0)
    basis_costs = {}
    basis_calls = {}
    total_calls = 0
    reported_calls = 0
    legacy_rows = 0

    for row in rows:
        entries, legacy = _entries(row)
        row_matched = False
        for entry in entries:
            entry_provider = str(entry.get("provider") or row.get("provider") or "")
            try:
                stamp = float(entry.get("ts"))
            except (TypeError, ValueError, OverflowError):
                continue
            if entry_provider != provider or not start <= stamp < end:
                continue
            row_matched = True
            weight = int(entry.get("attempts") or 1)
            if weight < 1:
                continue
            basis = str(entry.get("basis") or "unknown")
            total_calls += weight
            basis_calls[basis] = basis_calls.get(basis, 0) + weight
            cost = entry.get("cost_usd")
            if cost is not None and basis not in ("unknown", "unrecorded", "not-reconciled"):
                amount = _money(cost)
                basis_costs[basis] = basis_costs.get(basis, decimal.Decimal(0)) + amount
            if basis == "reported" and cost is not None:
                reported_cost += _money(cost)
                reported_calls += weight
        if legacy and row_matched:
            legacy_rows += 1

    delta = provider_cost - reported_cost
    absolute = abs(delta)
    if provider_cost == 0:
        drift_pct = decimal.Decimal(0) if reported_cost == 0 else None
    else:
        drift_pct = absolute / provider_cost * decimal.Decimal(100)
    direction = ("none" if delta == 0 else
                 "platform-under-reported" if delta > 0 else
                 "platform-over-reported")
    fraction = (decimal.Decimal(reported_calls) / decimal.Decimal(total_calls)
                * decimal.Decimal(100) if total_calls else None)
    return {
        "schema": 1, "state": "compared", "provider": provider,
        "window": usage["window"],
        "provider_reported_usd": _text(provider_cost),
        "platform_reported_usd": _text(reported_cost),
        "drift_usd": _text(absolute),
        "drift_pct": _text(drift_pct) if drift_pct is not None else None,
        "drift_direction": direction,
        "reconcilable_fraction_pct": _text(fraction) if fraction is not None else None,
        "fraction_basis": "same-provider call attempts in provider UTC window",
        "reported_calls": reported_calls, "recorded_calls": total_calls,
        "calls_by_basis": dict(sorted(basis_calls.items())),
        # Separate dollar buckets are evidence; no blended cross-basis total.
        "usd_by_basis": {key: _text(value) for key, value in sorted(basis_costs.items())},
        "window_precision": "legacy-aggregate" if legacy_rows else "exact-per-attempt",
        "legacy_aggregate_rows": legacy_rows,
        "auto_corrected": False,
    }


def run(days=30, provider=None, rows=None):
    usage = provider_usage.retrieve(days, provider)
    history = list(rows) if rows is not None else spend_history.spend_rows()
    return reconcile(usage, history)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compare provider and platform reported spend")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--provider", choices=provider_usage.llm_runner.PROVIDERS)
    args = parser.parse_args(argv)
    try:
        result = run(args.days, args.provider)
    except (ValueError, RuntimeError) as exc:
        print(f"COST_RECONCILE: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

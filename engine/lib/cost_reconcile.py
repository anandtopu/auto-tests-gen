#!/usr/bin/env python3
"""Compare provider-reported cost with same-window reported ledger evidence.

The provider figure comes only from :mod:`provider_usage`; this module has no
vendor branch. Reconciliation is evidence, never a correction: the latest
three-state result is persisted for operators and threshold breaches notify
through the existing Notify port, but ledger dollars are never rewritten.
"""
from __future__ import annotations

import argparse
import datetime as dt
import decimal
import json
import os
import pathlib
import subprocess
import sys
import time

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import alert_rules
import app_paths
import fs_lock
import provider_usage
import spend_history

EXTERNAL_UNAVAILABLE = 75
BADGE_STATES = ("not-reconciled", "reconciled-no-drift", "reconciled-drift")


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


def latest_path():
    """Durable, relocatable state consumed by the Cost view."""
    return app_paths.costs_dir(ROOT) / "reconciliation.json"


def _not_reconciled(reason="no reconciliation has run"):
    return {"schema": 1, "status": "not-reconciled", "reason": reason,
            "auto_corrected": False}


def load_latest():
    """Read the latest badge state, failing closed on absent/corrupt evidence."""
    try:
        doc = fs_lock.read_json_guarded(latest_path(), _not_reconciled())
    except (OSError, TimeoutError) as exc:
        return _not_reconciled(f"reconciliation state is unreadable: {exc}")
    if not isinstance(doc, dict) or doc.get("status") not in BADGE_STATES:
        return _not_reconciled("reconciliation state is invalid")
    return doc


def _threshold():
    path = ROOT / "registry/org-config.yaml"
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise TypeError("org-config must be an object")
    budgets = loaded.get("budgets") or {}
    if not isinstance(budgets, dict):
        raise TypeError("org-config budgets must be an object")
    raw = budgets.get("reconcile_drift_pct", 10)
    return _money(raw)


def _is_drift(result, threshold):
    if _money(result["drift_usd"]) == 0:
        return False
    pct = result.get("drift_pct")
    # A non-zero mismatch against a zero provider denominator has no finite
    # percentage; treating it as healthy would turn undefined into zero.
    return pct is None or _money(pct) > threshold


def _message(result, threshold):
    window = result["window"]
    pct = (f"{result['drift_pct']}%" if result.get("drift_pct") is not None
           else "undefined (provider total is zero)")
    return (f"[AI-QE] cost reconciliation drift above {_text(threshold)}%\n"
            f"Provider reported: ${result['provider_reported_usd']}\n"
            f"Platform reported: ${result['platform_reported_usd']}\n"
            f"Window: {window['starting_at']} to {window['ending_at']} UTC [start, end)\n"
            f"Drift: ${result['drift_usd']} ({pct}; {result['drift_direction']})\n"
            "Likely causes: missed harvests can make the platform under-report; "
            "other workloads on the same API key can make the provider figure "
            "include non-platform spend. This alarm never auto-corrects history.")


def _save(doc):
    path = latest_path()
    with fs_lock.lock(path):
        fs_lock.write_json_atomic(path, doc, sort_keys=True)


def operate(days=30, provider=None, rows=None, notifier=None, now=None):
    """Run, classify, notify when required, and durably publish one result.

    Returns ``(document, external_degradation)`` so maintenance can distinguish
    an unavailable provider/channel from a local persistence/configuration bug.
    """
    try:
        result = run(days, provider, rows)
    except subprocess.TimeoutExpired as exc:
        # The adapter is the only component allowed to contact the provider.
        # A port timeout is operational unavailability, not malformed local
        # evidence; persist that state so the dashboard cannot remain green.
        result = {
            "schema": 1, "state": "unavailable",
            "provider": str(provider or "configured"),
            "provider_usage": {
                "schema": 1, "state": "unavailable",
                "provider": str(provider or "configured"),
                "reason_code": "provider-timeout",
                "reason": f"usage adapter exceeded {exc.timeout}s",
            },
            "auto_corrected": False,
        }
    threshold = _threshold()
    checked_at = int(time.time() if now is None else now)
    doc = dict(result)
    doc.update({"checked_at": checked_at, "threshold_pct": _text(threshold)})
    external = False
    if result.get("state") != "compared":
        doc["status"] = "not-reconciled"
        doc["reason"] = ((result.get("provider_usage") or {}).get("message")
                         or (result.get("provider_usage") or {}).get("reason")
                         or (result.get("provider_usage") or {}).get("reason_code")
                         or "provider usage unavailable")
        doc["notification"] = {"required": False, "state": "not-required"}
        external = True
    elif _is_drift(result, threshold):
        doc["status"] = "reconciled-drift"
        channel = (os.environ.get("NOTIFY_KIND") or "slack").strip().lower()
        if channel not in alert_rules.CHANNELS:
            raise ValueError(f"NOTIFY_KIND must be one of {alert_rules.CHANNELS}")
        send = notifier or alert_rules.deliver
        delivered = bool(send(_message(result, threshold), channel=channel,
                              rule_name="cost-reconciliation"))
        doc["notification"] = {"required": True,
                               "state": "sent" if delivered else "failed",
                               "channel": channel}
        external = not delivered
    else:
        doc["status"] = "reconciled-no-drift"
        doc["notification"] = {"required": False, "state": "not-required"}
    _save(doc)
    return doc, external


def _explain(doc):
    """Human lines for an exit-75 result: WHICH situation, and the fix.

    Exit 75 covers two things a reader must not confuse, so the message names
    which one rather than pretending the code is finer-grained than it is:

    * nothing was reconciled (no credential, an unreachable billing API, a
      provider that cannot report) -- benign, and the fix is configuration;
    * spend DID reconcile, drift was found, and the alarm could not be
      delivered -- the urgent one, because the number is real and nobody was
      told. (It is still published, and the Cost view shows the drift state.)
    """
    status = str(doc.get("status") or "")
    reason = str(doc.get("reason") or "").strip()
    # The reason the PORT supplied already names the vendor's own credential;
    # branching on that name here would put a vendor branch in engine code,
    # which the port boundary forbids and a pin enforces (it caught exactly
    # that in the first version of this function). Classify on the neutral
    # `reason_code` instead and let the echoed reason do the naming.
    code = str(((doc.get("provider_usage") or {}).get("reason_code") or "")).strip()
    lines = []
    if status == "not-reconciled":
        lines.append(f"COST_RECONCILE: nothing was reconciled - {reason or 'reason not recorded'}")
        if code == "credential-missing":
            lines.append("  fix: configure the provider billing credential named above "
                         "(Settings, or the same key in .env) and re-run")
        else:
            lines.append("  fix: check the provider's billing API is reachable, then re-run")
    else:
        channel = (doc.get("notification") or {}).get("channel") or "the configured channel"
        lines.append("COST_RECONCILE: spend reconciled and DRIFT was found, but the alarm "
                     f"could not be delivered via {channel} - nobody was notified")
        lines.append("  the drift figures are published and the dashboard Cost view shows them")
    lines.append("  exit 75 means an external system was unavailable, not that this command "
                 "failed; `make maintain` reports it as DEGRADED and keeps going")
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compare provider and platform reported spend")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--provider", choices=provider_usage.llm_runner.PROVIDERS)
    args = parser.parse_args(argv)
    try:
        result, external = operate(args.days, args.provider)
    except (OSError, TimeoutError, TypeError, ValueError, RuntimeError,
            yaml.YAMLError) as exc:
        print(f"COST_RECONCILE: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    if external:
        # The JSON carries the reason and nothing says it OUT LOUD, so an
        # operator running this gets a blob and `make: *** Error 75`, which
        # reads as a crash. Exit 75 is deliberate -- "could not reconcile" is
        # not success (C13) -- but a non-zero code nobody can look up is how a
        # correct refusal gets mistaken for a broken command. `make maintain`
        # already learned this lesson: it used to discard this text and leave a
        # CronJob log saying only "exit 75".
        for line in _explain(result):
            print(line, file=sys.stderr)
    return EXTERNAL_UNAVAILABLE if external else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Provider-reported cost through the LLM adapter port (TCA-C2).

Vendor endpoints, credentials, pagination and unit conversion remain in the
adapter. Provider-aligned comparison with the spend ledger belongs to TCA-C3.
"""
import argparse
import datetime as dt
import decimal
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import llm_runner
import settings_store
import work_queue


def _validate(payload, provider):
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise ValueError("adapter usage response must be a schema-1 object")
    if payload.get("provider") != provider:
        raise ValueError("adapter usage response provider does not match request")
    state = payload.get("state")
    if state not in {"available", "unavailable"}:
        raise ValueError("adapter usage response has an invalid state")
    if state == "unavailable":
        if "cost" in payload or "cost_usd" in payload:
            raise ValueError("unavailable usage must not contain a zero-like cost")
        return payload
    window, cost = payload.get("window"), payload.get("cost")
    if not isinstance(window, dict) or not window.get("starting_at") or not window.get("ending_at"):
        raise ValueError("available usage must name its provider-aligned window")
    if not isinstance(cost, dict) or not isinstance(cost.get("amount_usd"), str):
        raise ValueError("available usage must contain a decimal-string USD amount")
    if cost.get("currency") != "USD" or cost.get("basis") != "provider-reported":
        raise ValueError("available usage must explicitly identify its cost basis")
    try:
        amount = decimal.Decimal(cost["amount_usd"])
        start = dt.datetime.fromisoformat(window["starting_at"].replace("Z", "+00:00"))
        end = dt.datetime.fromisoformat(window["ending_at"].replace("Z", "+00:00"))
    except (decimal.InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("available usage contains an invalid amount or window") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("available usage amount must be finite and non-negative")
    if start.utcoffset() != dt.timedelta(0) or end.utcoffset() != dt.timedelta(0) or end <= start:
        raise ValueError("available usage window must be increasing UTC timestamps")
    return payload


def retrieve(days=30, provider=None):
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
        raise ValueError("days must be an integer from 1 through 365")
    settings_store.load_env_into(os.environ)
    provider = (provider or llm_runner.default_provider()).strip().lower()
    if provider not in llm_runner.PROVIDERS:
        raise ValueError(f"unknown LLM provider '{provider}'")
    adapter = llm_runner.adapter_path(provider)
    if not adapter.is_file():
        raise ValueError(f"provider '{provider}' has no usage adapter")
    result = subprocess.run(
        [work_queue.bash_exe(), str(adapter), "usage", str(days)],
        cwd=ROOT, text=True, capture_output=True, encoding="utf-8", timeout=90,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "adapter failed").strip()
        raise RuntimeError(f"provider usage adapter failed ({result.returncode}): {detail[:300]}")
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("provider usage adapter returned malformed JSON") from exc
    return _validate(payload, provider)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read provider-reported cost through the adapter port")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--provider", choices=llm_runner.PROVIDERS)
    args = parser.parse_args(argv)
    try:
        result = retrieve(args.days, args.provider)
    except (ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"PROVIDER_USAGE: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""TCA-C3 provider-aligned, basis-honest reconciliation arithmetic."""
import datetime as dt
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import cost_reconcile


def ts(text):
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def usage(amount="3.00", provider="mock"):
    return {"schema": 1, "state": "available", "provider": provider,
            "window": {"starting_at": "2026-01-01T00:00:00Z",
                       "ending_at": "2026-01-03T00:00:00Z", "bucket_width": "1d"},
            "cost": {"amount_usd": amount, "currency": "USD",
                     "basis": "provider-reported"}}


def row(stamp, basis="reported", cost=1, provider="mock", attempts=1, **extra):
    value = {"run_id": f"r-{stamp}", "phase": "generate", "ts": ts(stamp),
             "provider": provider, "basis": basis, "cost_usd": cost,
             "attempts": attempts}
    value.update(extra)
    return value


def test_window_is_start_inclusive_end_exclusive_and_provider_scoped():
    rows = [
        row("2025-12-31T23:59:59Z", cost=9),
        row("2026-01-01T00:00:00Z", cost=1),
        row("2026-01-02T23:59:59Z", basis="estimated", cost=5),
        row("2026-01-03T00:00:00Z", cost=9),
        row("2026-01-02T00:00:00Z", cost=8, provider="claude"),
    ]
    result = cost_reconcile.reconcile(usage(), rows)
    assert result["state"] == "compared"
    assert result["provider_reported_usd"] == "3.000000"
    assert result["platform_reported_usd"] == "1.000000"
    assert result["drift_usd"] == "2.000000"
    assert result["drift_pct"] == "66.666667"
    assert result["drift_direction"] == "platform-under-reported"
    assert result["reconcilable_fraction_pct"] == "50.000000"
    assert result["calls_by_basis"] == {"estimated": 1, "reported": 1}
    assert result["usd_by_basis"] == {"estimated": "5.000000",
                                       "reported": "1.000000"}
    assert "total" not in result and result["auto_corrected"] is False


def test_per_attempt_evidence_splits_a_retry_across_utc_boundary_once():
    aggregate = row("2025-12-31T23:59:59Z", cost=.3, attempts=2,
                    attempt_details=[
                        {"ts": ts("2025-12-31T23:59:59Z"), "provider": "mock",
                         "basis": "reported", "cost_usd": .1},
                        {"ts": ts("2026-01-01T00:00:00Z"), "provider": "mock",
                         "basis": "reported", "cost_usd": .2},
                    ])
    result = cost_reconcile.reconcile(usage("0.20"), [aggregate])
    assert result["platform_reported_usd"] == "0.200000"
    assert result["reported_calls"] == result["recorded_calls"] == 1
    assert result["drift_pct"] == "0.000000"
    assert result["window_precision"] == "exact-per-attempt"


def test_legacy_retry_aggregate_is_counted_once_and_disclosed_as_imprecise():
    aggregate = row("2026-01-01T00:00:00Z", cost=.3, attempts=2)
    result = cost_reconcile.reconcile(usage("0.30"), [aggregate])
    assert result["platform_reported_usd"] == "0.300000"
    assert result["recorded_calls"] == result["reported_calls"] == 2
    assert result["legacy_aggregate_rows"] == 1
    assert result["window_precision"] == "legacy-aggregate"


def test_only_reported_basis_is_compared_and_unknown_is_never_zero():
    rows = [row("2026-01-01T01:00:00Z", "reported", 1),
            row("2026-01-01T02:00:00Z", "simulated", 30),
            row("2026-01-01T03:00:00Z", "unknown", None),
            row("2026-01-01T04:00:00Z", "unrecorded", None)]
    result = cost_reconcile.reconcile(usage("1.00"), rows)
    assert result["platform_reported_usd"] == "1.000000"
    assert result["drift_pct"] == "0.000000"
    assert result["recorded_calls"] == 4 and result["reported_calls"] == 1
    assert result["reconcilable_fraction_pct"] == "25.000000"
    assert result["calls_by_basis"]["unknown"] == 1
    assert "unknown" not in result["usd_by_basis"]


def test_zero_provider_denominator_is_explicit_not_fake_percentage():
    clean = cost_reconcile.reconcile(usage("0"), [])
    assert clean["drift_pct"] == "0.000000" and clean["drift_direction"] == "none"
    mismatch = cost_reconcile.reconcile(
        usage("0"), [row("2026-01-01T00:00:00Z", cost=1)])
    assert mismatch["drift_pct"] is None
    assert mismatch["drift_usd"] == "1.000000"
    assert mismatch["drift_direction"] == "platform-over-reported"


def test_unavailable_usage_stays_unavailable_and_never_corrects_history():
    missing = {"schema": 1, "state": "unavailable", "provider": "mock",
               "reason_code": "unsupported"}
    result = cost_reconcile.reconcile(missing, [row("2026-01-01T00:00:00Z")])
    assert result["state"] == "unavailable"
    assert result["auto_corrected"] is False
    assert "platform_reported_usd" not in result
    poisoned = {**missing, "cost": {"amount_usd": "0"}}
    try:
        cost_reconcile.reconcile(poisoned, [])
    except ValueError:
        pass
    else:
        raise AssertionError("accepted zero-like cost on unavailable provider evidence")


def test_large_provider_amount_does_not_overflow_decimal_rendering():
    result = cost_reconcile.reconcile(usage("123456789012345678901234567890.12"), [])
    assert result["provider_reported_usd"].endswith(".120000")


def test_malformed_ledger_cost_is_rejected_not_coerced_to_zero():
    bad = row("2026-01-01T00:00:00Z", cost="not-money")
    try:
        cost_reconcile.reconcile(usage(), [bad])
    except ValueError:
        pass
    else:
        raise AssertionError("malformed ledger dollars were accepted")


def test_reconciliation_engine_uses_ports_and_has_no_vendor_or_persistence_branch():
    source = (ROOT / "engine/lib/cost_reconcile.py").read_text(encoding="utf-8")
    assert "provider_usage.retrieve" in source
    assert "spend_history.spend_rows" in source
    for forbidden in ("api.anthropic.com", "ANTHROPIC_ADMIN_KEY", "cost_report?",
                      "write_json", "import notify", "reconcile_drift_pct"):
        assert forbidden not in source

#!/usr/bin/env python3
"""A4 PR-ticket discovery evaluation over a versioned QE-owned label set.

The evaluator invokes the production extraction and resolution functions. SCM
metadata and Tracker verdicts come from labelled synthetic fixtures, so results
are always rendered as simulated rather than real-estate quality evidence.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import pathlib
import subprocess
import sys
from collections.abc import Hashable, Iterable

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine/lib"))

import ticket_discovery as discovery  # noqa: E402

SCHEMA_VERSION = 1
M1_TARGET_PRECISION = 0.95
DEFAULT_LABELS = ROOT / "eval/discovery/v1/labels.json"
RESULT = ROOT / "eval/results/discovery-quality.json"
REQUIRED_SCENARIOS = frozenset({
    "branch-only", "commits-only", "no-key", "invalid-key", "conflicting-keys",
})
ALLOWED_OUTCOMES = frozenset({
    "selected", "ambiguous", "not_found", "discovered_invalid",
    "validation_unavailable",
})


class FixtureError(ValueError):
    pass


def _read_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FixtureError(f"cannot read {path}: {exc}") from exc


def _sha(path: pathlib.Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise FixtureError(f"cannot hash {path}: {exc}") from exc


def _commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, timeout=10, stdin=subprocess.DEVNULL,
        )
        return result.stdout.strip() if result.returncode == 0 else "unavailable"
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def _sibling(base: pathlib.Path, name: str) -> pathlib.Path:
    if not name or pathlib.Path(name).name != name:
        raise FixtureError(f"fixture reference must be a file name, got {name!r}")
    path = (base.parent / name).resolve()
    if path.parent != base.parent.resolve():
        raise FixtureError(f"fixture reference escapes label directory: {name}")
    return path


def _string_keys(value, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(key, str) for key in value):
        raise FixtureError(f"{field} must be a string list")
    if len(value) != len(set(value)):
        raise FixtureError(f"{field} must not contain duplicate keys")
    for key in value:
        if discovery.normalize_explicit(key) != key:
            raise FixtureError(f"{field} contains invalid key {key!r}")
    return value


def load_gold(path: pathlib.Path = DEFAULT_LABELS) -> tuple[dict, list[dict], list[dict]]:
    path = pathlib.Path(path)
    labels = _read_json(path)
    if not isinstance(labels, dict) or labels.get("schema_version") != SCHEMA_VERSION:
        raise FixtureError("label schema_version must be 1")
    owner = labels.get("owner") or {}
    if owner.get("maintainer_role") != "QE Lead" or not owner.get("team"):
        raise FixtureError("labels require a QE team and maintainer_role=QE Lead")
    target = labels.get("m1_target_precision")
    if target != M1_TARGET_PRECISION:
        raise FixtureError(
            f"m1_target_precision must remain the PRD threshold "
            f"{M1_TARGET_PRECISION:.2f}"
        )

    fixture_ref = labels.get("fixtures") or {}
    fixture_path = _sibling(path, str(fixture_ref.get("file") or ""))
    actual_sha = _sha(fixture_path)
    if actual_sha != fixture_ref.get("sha256"):
        raise FixtureError(
            f"label drift: fixtures sha256 is {actual_sha}, "
            f"labels expect {fixture_ref.get('sha256')}"
        )
    fixture_doc = _read_json(fixture_path)
    fixtures = fixture_doc.get("fixtures") if isinstance(fixture_doc, dict) else None
    expected = labels.get("labels")
    if not isinstance(fixture_doc, dict) or \
            fixture_doc.get("schema_version") != SCHEMA_VERSION:
        raise FixtureError("fixture schema_version must be 1")
    if not isinstance(fixtures, list) or not isinstance(expected, list):
        raise FixtureError("fixtures and labels must be lists")
    if not all(isinstance(row, dict) for row in fixtures + expected):
        raise FixtureError("every fixture and label must be an object")

    fixture_ids = [str(row.get("id") or "") for row in fixtures]
    label_ids = [str(row.get("id") or "") for row in expected]
    if not all(fixture_ids) or len(fixture_ids) != len(set(fixture_ids)):
        raise FixtureError("fixture ids must be present and unique")
    if not all(label_ids) or len(label_ids) != len(set(label_ids)):
        raise FixtureError("label ids must be present and unique")
    if set(fixture_ids) != set(label_ids):
        raise FixtureError("fixture and label ids must match exactly")

    scenarios = {str(row.get("scenario") or "") for row in expected}
    missing = REQUIRED_SCENARIOS - scenarios
    if missing:
        raise FixtureError(f"required A4 scenarios are missing: {sorted(missing)}")

    for fixture in fixtures:
        ident = fixture["id"]
        if not isinstance(fixture.get("metadata"), dict):
            raise FixtureError(f"{ident}: metadata must be an object")
        if not isinstance(fixture.get("explicit", ""), str):
            raise FixtureError(f"{ident}: explicit must be a string")
        validations = fixture.get("validations")
        if not isinstance(validations, dict):
            raise FixtureError(f"{ident}: validations must be an object")
        for key, verdict in validations.items():
            if discovery.normalize_explicit(key) != key:
                raise FixtureError(f"{ident}: invalid validation key {key!r}")
            if not isinstance(verdict, dict) or \
                    verdict.get("state") not in discovery.VALIDATION_STATES:
                raise FixtureError(f"{ident}: invalid validation state for {key}")

    for label in expected:
        ident = label["id"]
        outcome = label.get("expected_outcome")
        selected = label.get("expected_selected_key")
        if outcome not in ALLOWED_OUTCOMES:
            raise FixtureError(f"{ident}: invalid expected outcome {outcome!r}")
        if outcome == "selected":
            if discovery.normalize_explicit(selected) != selected:
                raise FixtureError(f"{ident}: selected outcome requires one valid key")
        elif selected is not None:
            raise FixtureError(f"{ident}: non-selected outcome must use null selected key")
        signals = label.get("expected_signal_keys")
        if not isinstance(signals, dict) or set(signals) != set(discovery.SIGNALS):
            raise FixtureError(f"{ident}: expected_signal_keys must name every signal")
        for signal in discovery.SIGNALS:
            _string_keys(signals[signal], f"{ident}.{signal}")

    return labels, fixtures, expected


def metric(predicted: Iterable[Hashable], expected: Iterable[Hashable]) -> dict:
    predicted_set, expected_set = set(predicted), set(expected)
    true_positive = len(predicted_set & expected_set)
    false_positive = len(predicted_set - expected_set)
    false_negative = len(expected_set - predicted_set)
    predicted_total = true_positive + false_positive
    expected_total = true_positive + false_negative
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": round(
            true_positive / predicted_total if predicted_total
            else (1.0 if not expected_total else 0.0), 4,
        ),
        "recall": round(
            true_positive / expected_total if expected_total
            else (1.0 if not predicted_total else 0.0), 4,
        ),
    }


def refusal_token(fixture_id: str) -> str:
    return f"correct-refusal:{fixture_id}"


def decision_token(outcome: str, selected_key: str | None, fixture_id: str):
    if outcome == "selected" and selected_key:
        return f"ticket:{selected_key}"
    if outcome == "ambiguous" and selected_key is None:
        return refusal_token(fixture_id)
    return None


def evaluate(labels_path: pathlib.Path = DEFAULT_LABELS) -> dict:
    labels_path = pathlib.Path(labels_path)
    labels, fixtures, expected_rows = load_gold(labels_path)
    expected_by_id = {row["id"]: row for row in expected_rows}
    signal_predicted = {signal: [] for signal in discovery.SIGNALS}
    signal_expected = {signal: [] for signal in discovery.SIGNALS}
    predicted_decisions, expected_decisions = [], []
    rows, failures = [], []
    correct_refusals = 0
    refusal_total = 0

    for fixture in fixtures:
        ident = fixture["id"]
        expected = expected_by_id[ident]
        artifact = discovery.extract(fixture["metadata"], fixture.get("explicit", ""))
        candidates = set(discovery.candidate_keys(artifact))
        labelled_validations = set(fixture["validations"])
        if candidates != labelled_validations:
            failures.append(
                f"{ident}: extracted candidates {sorted(candidates)} do not match "
                f"labelled validations {sorted(labelled_validations)}"
            )
        resolved = discovery.resolve(artifact, fixture["validations"])
        actual_signals = {signal: [] for signal in discovery.SIGNALS}
        for candidate in resolved.get("candidates") or []:
            if candidate.get("validation") != "valid":
                continue
            for signal in candidate.get("signals") or []:
                if signal in actual_signals:
                    actual_signals[signal].append(candidate["key"])
        for signal in discovery.SIGNALS:
            actual_signals[signal] = sorted(set(actual_signals[signal]))
            expected_keys = expected["expected_signal_keys"][signal]
            signal_predicted[signal].extend(
                (ident, key) for key in actual_signals[signal]
            )
            signal_expected[signal].extend((ident, key) for key in expected_keys)
            if actual_signals[signal] != expected_keys:
                failures.append(
                    f"{ident}: {signal} keys {actual_signals[signal]} "
                    f"!= expected {expected_keys}"
                )

        actual_outcome = resolved.get("outcome")
        actual_selected = resolved.get("selected_key")
        if actual_outcome != expected["expected_outcome"] or \
                actual_selected != expected["expected_selected_key"]:
            failures.append(
                f"{ident}: outcome {actual_outcome}/{actual_selected} != expected "
                f"{expected['expected_outcome']}/{expected['expected_selected_key']}"
            )
        actual_decision = decision_token(actual_outcome, actual_selected, ident)
        expected_decision = decision_token(
            expected["expected_outcome"], expected["expected_selected_key"], ident,
        )
        if actual_decision is not None:
            predicted_decisions.append((ident, actual_decision))
        if expected_decision is not None:
            expected_decisions.append((ident, expected_decision))
        if expected["expected_outcome"] == "ambiguous":
            refusal_total += 1
            correct_refusals += int(actual_decision == refusal_token(ident))
        rows.append({
            "id": ident,
            "scenario": expected["scenario"],
            "outcome": actual_outcome,
            "selected_key": actual_selected,
            "signals": actual_signals,
            "expected_outcome": expected["expected_outcome"],
            "expected_selected_key": expected["expected_selected_key"],
        })

    per_signal = {
        signal: metric(signal_predicted[signal], signal_expected[signal])
        for signal in discovery.SIGNALS
    }
    m1 = metric(predicted_decisions, expected_decisions)
    m1["target_precision"] = M1_TARGET_PRECISION
    if m1["precision"] < m1["target_precision"]:
        failures.append(
            f"M1 precision {m1['precision']:.4f} < {m1['target_precision']:.4f}"
        )
    exact = len(fixtures) - sum(
        row["outcome"] != row["expected_outcome"]
        or row["selected_key"] != row["expected_selected_key"]
        for row in rows
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact": "ticket-discovery-quality",
        "evaluated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source_commit": _commit(),
        "measurement_state": "simulated",
        "measurement_reason": (
            "labelled synthetic SCM metadata and Tracker verdicts prove "
            "deterministic discovery plumbing, not real-estate accuracy"
        ),
        "label_set": {
            "version": labels.get("version"),
            "owner": labels.get("owner"),
            "labels_sha256": _sha(labels_path),
            "fixtures_sha256": labels["fixtures"]["sha256"],
            "fixtures": len(fixtures),
            "scenarios": sorted({row["scenario"] for row in expected_rows}),
        },
        "m1": m1,
        "per_signal": per_signal,
        "correct_refusal": {
            "correct": correct_refusals,
            "total": refusal_total,
            "rate": round(correct_refusals / refusal_total, 4)
            if refusal_total else None,
        },
        "exact_outcomes": {
            "correct": exact,
            "total": len(fixtures),
            "rate": round(exact / len(fixtures), 4) if fixtures else None,
        },
        "fixtures": rows,
        "overall": "pass" if not failures else "fail",
        "failures": failures,
    }


def main(argv: list[str]) -> int:
    labels_path = pathlib.Path(argv[1]) if len(argv) > 1 else DEFAULT_LABELS
    try:
        result = evaluate(labels_path)
    except FixtureError as exc:
        result = {
            "schema_version": SCHEMA_VERSION,
            "artifact": "ticket-discovery-quality",
            "measurement_state": "simulated",
            "overall": "fail",
            "failures": [str(exc)],
        }
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n",
    )
    label_set = result.get("label_set") or {}
    state = str(result.get("measurement_state") or "unavailable").upper()
    print(
        f"ticket discovery eval ({state}): {label_set.get('fixtures', 0)} "
        f"fixture(s) — {result['overall'].upper()}"
    )
    m1 = result.get("m1") or {}
    if m1:
        print(
            f"  M1: precision={m1.get('precision', 0):.2f}, "
            f"recall={m1.get('recall', 0):.2f}, "
            f"target precision>={m1.get('target_precision', 0):.2f}"
        )
    for signal in discovery.SIGNALS:
        row = (result.get("per_signal") or {}).get(signal)
        if row:
            print(
                f"  {signal}: precision={row['precision']:.2f}, "
                f"recall={row['recall']:.2f} "
                f"(tp={row['true_positive']}, fp={row['false_positive']}, "
                f"fn={row['false_negative']})"
            )
    refusal = result.get("correct_refusal") or {}
    if refusal:
        print(
            f"  correct refusal: {refusal.get('correct', 0)}/"
            f"{refusal.get('total', 0)}"
        )
    for failure in result.get("failures") or []:
        print(f"  REGRESSION: {failure}")
    return 0 if result.get("overall") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

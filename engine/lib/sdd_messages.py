#!/usr/bin/env python3
"""User-facing contracts for spec-driven workflow refusals.

Every refusal says the same three things regardless of whether it reaches a
person through Bash, a queue record, the dashboard API, or a notification:
what refused, why, and exactly one next action.  This module owns wording
only.  The state machines and enforcement decisions remain in plan_state,
spec_check, and spec_drift.
"""
from __future__ import annotations

import pathlib
import sys


KINDS = (
    "requirements_gate",
    "plan_approval",
    "coverage_uncovered",
    "waiver_expired",
    "drift_stale",
)


def _value(value, fallback="unknown"):
    text = " ".join(str(value or "").split()).strip()[:160]
    return text if text else fallback


def refusal(kind, *, key, status="", scenario="", expiry="", surfaces=None):
    """Return the closed refusal contract for ``kind``.

    The returned fields are deliberately presentation-neutral.  CLI callers
    print ``text``; APIs return the whole dict; browser code escapes each field.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown SDD refusal kind: {kind}")
    key = _value(key)
    status = _value(status, "absent")
    scenario = _value(scenario)
    expiry = _value(expiry)
    surfaces = sorted({_value(item) for item in (surfaces or []) if str(item).strip()})[:4]

    if kind == "requirements_gate":
        if status == "absent":
            what = "Test-plan authoring refused."
            why = f"{key} has no formalized acceptance criteria."
            action = "Formalize the acceptance criteria."
            command = f"make requirements KEY={key}"
        else:
            what = "Test-plan authoring refused."
            why = f"{key} acceptance criteria are {status}, not approved."
            action = "Validate and approve the acceptance criteria."
            command = f"make requirements-approve KEY={key}"
    elif kind == "plan_approval":
        if status == "absent":
            what = "Test generation refused."
            why = f"{key} has no test plan."
            action = "Author the test plan."
            command = f"make plan KEY={key}"
        else:
            what = "Test generation refused."
            why = f"{key} test plan is {status}, not approved."
            action = "Review and approve the test plan."
            command = f"make plan-approve KEY={key}"
    elif kind == "coverage_uncovered":
        what = "Delivery refused."
        why = (f"Approved scenario {scenario} has no generated test and no active "
               "waiver.")
        action = (f"Cover {scenario}, or add a time-bounded waiver at "
                  f"specs/{key}/waivers.yaml.")
        command = f"make plan-tests KEY={key}"
    elif kind == "waiver_expired":
        what = "Delivery refused."
        why = f"The waiver for approved scenario {scenario} expired on {expiry}."
        action = f"Renew the waiver with a new expiry, or cover {scenario}."
        command = f"make plan-tests KEY={key}"
    else:
        what = "Approved test plan is stale."
        surface_text = ", ".join(surfaces) if surfaces else "a vanished application surface"
        why = f"Scenario {scenario} references {surface_text}, which no longer exists."
        action = f"Re-approve or retire scenario {scenario}."
        command = f"make spec-verify KEY={key}"

    text = (f"SDD_REFUSAL[{kind}] {what} Why: {why} "
            f"Next action: {action} Command: {command}")
    return {"kind": kind, "what": what, "why": why, "action": action,
            "command": command, "text": text}


def _gate_cli(argv):
    """Bash-facing wrapper which preserves plan_state as the gate authority."""
    if len(argv) != 2 or argv[0] not in ("require-approved", "require-requirements"):
        print("usage: sdd_messages.py require-approved|require-requirements KEY",
              file=sys.stderr)
        return 64
    import plan_state
    try:
        if argv[0] == "require-approved":
            plan_state.require_approved(argv[1])
        else:
            plan_state.require_requirements(argv[1])
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    raise SystemExit(_gate_cli(sys.argv[1:]))

"""Named SDD adoption levels over the engine's existing controls.

This module is deliberately only a mapping and a derivation function.  The
three controls remain the authority for behaviour; a level cannot acquire a
new side effect without changing the pinned mapping below.
"""
from types import MappingProxyType

MAPPED_ENV_KEYS = (
    "AIQE_SPEC_MODE",
    "AIQE_REQUIREMENTS_GATE",
    "AIQE_SPEC_ENFORCE",
)

_LEVELS = (
    {
        "id": "off",
        "name": "Off",
        "consequence": "Plans remain prose; nothing is signed or enforced.",
        "knobs": {"spec_mode": False, "requirements_gate": False,
                  "spec_enforce": "off"},
    },
    {
        "id": "reviewed",
        "name": "Reviewed plans",
        "consequence": "Plans are structured and signed by a human before generation.",
        "knobs": {"spec_mode": True, "requirements_gate": False,
                  "spec_enforce": "off"},
    },
    {
        "id": "validated",
        "name": "Validated criteria",
        "consequence": "Acceptance criteria are formalized and approved before planning.",
        "knobs": {"spec_mode": True, "requirements_gate": True,
                  "spec_enforce": "off"},
    },
    {
        "id": "enforced",
        "name": "Enforced coverage",
        "consequence": ("The gate checks signed plans' approved scenarios; prose plans "
                        "remain exempt."),
        "knobs": {"spec_mode": True, "requirements_gate": True,
                  "spec_enforce": ("warn", "strict")},
    },
)

LEVELS = MappingProxyType({row["id"]: MappingProxyType(row) for row in _LEVELS})

_ENV_BY_KNOB = MappingProxyType({
    "spec_mode": "AIQE_SPEC_MODE",
    "requirements_gate": "AIQE_REQUIREMENTS_GATE",
    "spec_enforce": "AIQE_SPEC_ENFORCE",
})


def definitions():
    """JSON-safe definitions for presentation surfaces."""
    return [{**row, "knobs": dict(row["knobs"])} for row in _LEVELS]


def _raw(governance):
    return {
        "spec_mode": bool(governance.get("spec_mode")),
        "requirements_gate": bool(governance.get("requirements_gate")),
        "spec_enforce": str(governance.get("spec_enforce") or "off"),
    }


def derive(governance):
    """Derive a display level from already-resolved engine truth.

    Unusable configuration is Custom even when its fallback happens to equal a
    named tuple. Calling an ignored typo "Reviewed plans" would describe a
    choice nobody made.
    """
    raw = _raw(governance)
    if not governance.get("problems"):
        for row in _LEVELS:
            expected = row["knobs"]
            enforce = expected["spec_enforce"]
            if (raw["spec_mode"] == expected["spec_mode"]
                    and raw["requirements_gate"] == expected["requirements_gate"]
                    and (raw["spec_enforce"] in enforce
                         if isinstance(enforce, tuple)
                         else raw["spec_enforce"] == enforce)):
                substate = raw["spec_enforce"] if row["id"] == "enforced" else ""
                badge = ({
                    "warn": "Dry run — reporting, not refusing",
                    "strict": "Enforcing — uncovered scenarios are refused",
                }.get(substate, ""))
                return {
                    "id": row["id"], "name": row["name"],
                    "consequence": row["consequence"], "custom": False,
                    "substate": substate, "badge": badge, "knobs": raw,
                }
    return {
        "id": "custom", "name": "Custom",
        "consequence": ("Resolved controls do not match a named level; review the "
                        "raw values before relying on enforcement."),
        "custom": True, "substate": "", "badge": "", "knobs": raw,
    }


def updates_for(level, substate=""):
    """Return the complete, closed env update for one named level."""
    if not isinstance(level, str) or level not in LEVELS:
        raise ValueError("level must be one of: " + ", ".join(LEVELS))
    if not isinstance(substate, str):
        raise TypeError("substate must be a string")
    row = LEVELS[level]
    knobs = dict(row["knobs"])
    if level == "enforced":
        if substate not in ("warn", "strict"):
            raise ValueError("Enforced coverage requires substate warn or strict")
        knobs["spec_enforce"] = substate
    elif substate:
        raise ValueError("substate applies only to Enforced coverage")
    values = {
        "spec_mode": "1" if knobs["spec_mode"] else "0",
        "requirements_gate": "1" if knobs["requirements_gate"] else "0",
        "spec_enforce": knobs["spec_enforce"],
    }
    return {_ENV_BY_KNOB[key]: values[key] for key in _ENV_BY_KNOB}

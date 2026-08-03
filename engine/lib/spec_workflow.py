"""The spec-driven workflow, as a state machine (SDD adoption S1).

Every piece of this workflow already existed — requirements, specs, plan
approval, the gate's spec check, drift, waivers — but only as CLI commands and
374 lines of markdown. `docs/sdd-for-e2e-adoption.md` calls that gap G2: a
process nobody can see is a process nobody follows, and an off-by-default
feature with no discoverability is indistinguishable from an unbuilt one.

This module answers three questions per ticket, in one place:

    where is it        (which of the six states)
    what is blocking   (the specific thing, not "not ready")
    what happens next  (a command, and who is meant to run it)

**It computes; it never mutates.** Rendering a workflow view must not advance a
workflow. Every transition stays behind the existing approve/edit commands,
which already sign, snapshot and record an actor.

**Governance is reported, not assumed.** `requirements_gate` and `spec.enforce`
ship OFF (gap G1), so the same ticket is "blocked on approval" in one estate and
"free to proceed" in another. Each row carries the setting that produced its
answer, because a workflow view that silently reflects configuration teaches
people a rule the platform is not actually enforcing.
"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app_paths                      # R12: mutable paths resolve here
import plan_state
import spec_store

ROOT = app_paths.ROOT

# The six states, in order. `order` drives the UI's progress rendering; a state
# is reached when every earlier one is satisfied.
STATES = ("requirements", "plan", "approved", "tests", "committed", "live")


def _org():
    try:
        import yaml
        return yaml.safe_load(
            (ROOT / "registry/org-config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:                          # noqa: BLE001
        return {}


def governance():
    """The settings that decide whether this workflow is advisory or enforced.

    Surfaced with the rows because 'blocked' means different things under
    different configuration, and a reader deserves to know which they are
    looking at.
    """
    spec = (_org().get("spec") or {})
    gate = spec.get("requirements_gate")
    enforce = str(spec.get("enforce") or "off")
    return {
        "requirements_gate": bool(gate),
        "requirements_gate_effect": (
            "planning REFUSES until requirements are approved" if gate
            else "advisory — planning proceeds without approved requirements"),
        "spec_enforce": enforce,
        "spec_enforce_effect": {
            "off": "the gate ignores uncovered scenarios",
            "warn": "uncovered scenarios are reported; the gate still commits",
            "strict": "the gate REFUSES (exit 8) on an uncovered, unwaived scenario",
        }.get(enforce, f"unknown mode {enforce!r}"),
        "spec_mode": os.environ.get("AIQE_SPEC_MODE", "1") != "0",
    }


def _keys():
    """Every ticket with any workflow artifact. Union, not intersection: a key
    with requirements but no plan is exactly the case this view exists to show."""
    # plan_state is keyed at the TOP level — there is no "plans" wrapper.
    keys = set(plan_state.load() or {})
    d = app_paths.specs_dir()
    if d.is_dir():
        keys |= {p.name for p in d.iterdir()
                 if p.is_dir() and p.name != "platform"}
    td = app_paths.testplans_dir()
    if td.is_dir():
        keys |= {p.stem for p in td.glob("*.md")}
    return sorted(keys)


def status(key):
    """One ticket's position, blocker and next action."""
    gov = governance()
    entry = plan_state.get(key) or {}
    plan_status = entry.get("status") or ""
    # Flat fields, not a nested dict: `requirements_status` and
    # `requirements_sha`. Reading entry["requirements"] returned None
    # forever, so approval never registered. Found while building S2.
    req_status = entry.get("requirements_status") or ""

    has_plan = app_paths.testplans_dir().joinpath(f"{key}.md").exists()
    spec_p = spec_store.spec_path(key) if hasattr(spec_store, "spec_path") else None
    has_spec = bool(spec_p and pathlib.Path(spec_p).exists())
    req_p = (spec_store.requirements_path(key)
             if hasattr(spec_store, "requirements_path") else None)
    has_req = bool(req_p and pathlib.Path(req_p).exists())

    ambiguities = []
    if has_req:
        try:
            ambiguities = spec_store.ambiguities(key) or []
        except Exception:                      # noqa: BLE001
            ambiguities = []
    blocking = [a for a in ambiguities
                if isinstance(a, dict) and a.get("blocking")]

    scenarios = []
    if has_spec:
        try:
            doc = spec_store.load(key) or {}
            scenarios = doc.get("scenarios") or []
        except Exception:                      # noqa: BLE001
            scenarios = []

    generated = bool(entry.get("generated"))
    committed = bool(entry.get("linked") or entry.get("committed"))

    # --- decide the state, blocker and next action -------------------------
    # Ordered so the FIRST unmet condition is the one reported. Reporting the
    # furthest-reached state instead would hide what is actually stopping it.
    if has_req and blocking:
        return _row(key, "requirements", gov,
                    blocker=f"{len(blocking)} blocking ambiguity/ambiguities — "
                            f"the ticket does not say what should happen",
                    action="answer them on the ticket, then re-run "
                           f"`make requirements KEY={key}`",
                    owner="BA / QE lead", detail=locals())
    if has_req and req_status != "approved":
        return _row(key, "requirements", gov,
                    blocker="requirements are drafted but not approved",
                    action=f"review, then `make requirements-approve KEY={key}`",
                    owner="QE lead",
                    # Honest about consequence: without the gate this is advisory.
                    soft=not gov["requirements_gate"], detail=locals())
    if not has_plan:
        return _row(key, "plan", gov,
                    blocker="no test plan authored yet",
                    action=f"`make plan KEY={key}`", owner="platform",
                    detail=locals())
    if plan_status != "approved":
        return _row(key, "approved", gov,
                    blocker=f"plan is `{plan_status or 'draft'}` — not approved",
                    action=f"review in the Test plans view, then `make plan-approve KEY={key}`",
                    owner="reviewer", detail=locals())
    if not generated:
        return _row(key, "tests", gov,
                    blocker="plan approved; tests not generated",
                    action=f"`make plan-tests KEY={key}`", owner="platform",
                    detail=locals())
    if not committed:
        return _row(key, "committed", gov,
                    blocker="tests generated but no gate commit recorded",
                    action="check the run in Activity / Runs — the gate may have refused",
                    owner="QE lead", detail=locals())
    return _row(key, "live", gov, blocker="", action="", owner="", detail=locals())


def _row(key, state, gov, blocker="", action="", owner="", soft=False, detail=None):
    d = detail or {}
    return {
        "key": key,
        "state": state,
        "state_index": STATES.index(state),
        "blocker": blocker,
        "action": action,
        "owner": owner,
        # `soft` = this step is not currently enforced by configuration. Shown
        # so nobody is taught a rule the platform is not applying (gap G1).
        "advisory": bool(soft),
        "has_requirements": bool(d.get("has_req")),
        "has_spec": bool(d.get("has_spec")),
        "scenarios": len(d.get("scenarios") or []),
        "blocking_ambiguities": len(d.get("blocking") or []),
        "plan_status": d.get("plan_status") or "",
        "requirements_status": d.get("req_status") or "",
        "governance": gov,
    }


def board():
    """Every ticket's position, plus the governance that produced it."""
    rows = [status(k) for k in _keys()]
    gov = governance()
    return {
        "states": list(STATES),
        "governance": gov,
        "rows": rows,
        "summary": {s: len([r for r in rows if r["state"] == s]) for s in STATES},
        # The adoption signal: how much of the workflow is actually enforced.
        "enforced": gov["requirements_gate"] or gov["spec_enforce"] != "off",
    }


if __name__ == "__main__":
    import json
    sys.stdout.reconfigure(encoding="utf-8")
    b = board()
    if "--json" in sys.argv:
        print(json.dumps(b, indent=1))
    else:
        g = b["governance"]
        print(f"requirements gate: {'ON' if g['requirements_gate'] else 'off'}"
              f"  ·  spec enforce: {g['spec_enforce']}")
        if not b["enforced"]:
            print("  NOTE: nothing here is enforced — every step below is advisory.")
        for r in b["rows"]:
            mark = " (advisory)" if r["advisory"] else ""
            print(f"  {r['state']:13} {r['key']:14} {r['blocker']}{mark}")
            if r["action"]:
                print(f"                 -> {r['owner']}: {r['action']}")

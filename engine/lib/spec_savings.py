"""Coverage subtraction: the work a signed spec makes unnecessary (SDD S5).

The largest saving on a mature estate is not writing tests faster — it is not
writing the ones that already exist. An approved scenario already exercised by a
cataloged test needs no authoring call at all, and the join that proves it is
the one `trace_matrix` already computes: scenario_id stamped on every generated
test, back to the spec it came from.

**What this module reports and what it refuses to report.**

It reports COUNTS, because those are measured: this many approved scenarios are
already covered, so this many authoring calls were not needed. That is a fact
about the estate, checkable against the trace matrix.

It does NOT report money. Converting a skipped scenario into dollars needs a
measured per-scenario authoring cost, and this estate has none — `make parity-*`
is still blocked on CLI auth, so every run here is simulated. `savings_usd`
therefore returns None with a `basis` of `unmeasured`, and the UI renders that
as "not measured yet" rather than a zero or an estimate. This is the same iron
rule the cost stack already enforces: a figure that was not measured is
labelled, never defaulted. A savings number is exactly the kind of figure people
repeat in a status update, which is why inventing one is worse here than
elsewhere.

**Subtraction is advisory, never automatic.** `to_author` is what this module
believes could be skipped; nothing in the pipeline acts on it yet. Skipping
authoring on a wrong join would silently drop coverage — the one failure this
platform cannot see — so the join gets proven against real runs before it is
allowed to remove work.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app_paths                      # R12: mutable paths resolve here
import spec_store

ROOT = app_paths.ROOT


def _matrix_rows():
    try:
        import trace_matrix
        d = trace_matrix.build()
        return d.get("rows") if isinstance(d, dict) else (d or [])
    except Exception:                          # noqa: BLE001
        return []


def covered_scenarios(key=None, rows=None):
    """{scenario_id: [files]} for scenarios that already have a cataloged test.

    Only rows carrying BOTH a scenario_id and a file count. A row with a file
    but no scenario_id is a test that predates the spec link — it may well cover
    the behaviour, but we cannot prove which scenario, and guessing here is how
    coverage silently disappears.
    """
    out = {}
    for r in (_matrix_rows() if rows is None else rows):
        if key and r.get("key") != key:
            continue
        sid, f = (r.get("scenario_id") or "").strip(), (r.get("file") or "").strip()
        if sid and f:
            out.setdefault(sid, []).append(f)
    return out


def authoring_plan(key, rows=None):
    """What a run for `key` would need to author, and what it would not.

    Returns counts only. `unlinked_tests` is reported separately and NOT counted
    as coverage: those are tests whose scenario is unknown, and treating them as
    covering something would be exactly the wrong kind of optimism.
    """
    # Built ONCE per call (and once per estate() sweep). The first version
    # called `_matrix_rows()` twice here — once through covered_scenarios and
    # once for the unlinked count — and `estate()` called this per ticket, so a
    # 20-ticket estate rebuilt the whole trace matrix 40 times, each rebuild
    # re-globbing and re-parsing every run record. Exactly the shape just fixed
    # in the workflow board, in code written the same day.
    if rows is None:
        rows = _matrix_rows()
    scenarios = []
    try:
        doc = spec_store.load(key) or {}
        scenarios = [s for s in (doc.get("scenarios") or []) if isinstance(s, dict)]
    except Exception:                          # noqa: BLE001
        scenarios = []

    covered = covered_scenarios(key, rows=rows)
    ids = [str(s.get("id") or "").strip() for s in scenarios]
    ids = [i for i in ids if i]
    already = [i for i in ids if i in covered]
    to_author = [i for i in ids if i not in covered]
    unlinked = len([r for r in rows
                    if r.get("key") == key and r.get("file")
                    and not (r.get("scenario_id") or "").strip()])
    return {
        "key": key,
        "scenarios": len(ids),
        "already_covered": len(already),
        "already_covered_ids": already,
        "to_author": len(to_author),
        "to_author_ids": to_author,
        # Reported, never counted as coverage.
        "unlinked_tests": unlinked,
        # Whether a HUMAN has signed this plan off. These counts are useful for
        # a draft plan -- arguably most useful then -- so they are NOT filtered
        # to approved scenarios. But the surfaces described them as "approved
        # scenario(s)", which asserts a sign-off that may not exist (the same
        # defect just fixed in trace_matrix, which read the same draft-time
        # snapshot). Callers get the status and say what is true.
        "plan_status": _plan_status(key),
        "advisory": True,
    }


def _plan_status(key):
    """The plan's lifecycle state, or "" when it cannot be read.

    Absence must never be read as approval, so the caller is given "" and says
    "unknown" rather than defaulting to the flattering answer.
    """
    try:
        import plan_state
        return plan_state.get(key).get("status", "") or ""
    except Exception:                          # noqa: BLE001
        return ""


def estate():
    """Coverage subtraction across every ticket with a signed spec."""
    d = app_paths.specs_dir()
    keys = ([p.name for p in d.iterdir() if p.is_dir() and p.name != "platform"]
            if d.is_dir() else [])
    shared = _matrix_rows()          # one build for the whole estate
    plans = [authoring_plan(k, rows=shared) for k in sorted(keys)]
    plans = [p for p in plans if p["scenarios"]]
    return {
        "keys": plans,
        "scenarios": sum(p["scenarios"] for p in plans),
        "already_covered": sum(p["already_covered"] for p in plans),
        "to_author": sum(p["to_author"] for p in plans),
        "savings": savings(sum(p["already_covered"] for p in plans)),
    }


def savings(avoided_scenarios):
    """What `avoided_scenarios` is worth — or an honest refusal to say.

    The COUNT is measured and returned. The MONEY is not: pricing a skipped
    scenario needs a measured per-scenario authoring cost, and every run on this
    estate is simulated until `make parity-*` can run. Returning 0 here would be
    read as "no saving"; returning an estimate would be read as a measurement.
    Both are worse than saying which one this is.
    """
    measured = _measured_per_scenario_usd()
    if measured is None:
        return {"avoided_scenarios": avoided_scenarios,
                "usd": None, "basis": "unmeasured",
                "why": "no measured authoring cost on this estate — every run "
                       "here is simulated. Run `make parity-jira` (needs Claude "
                       "CLI auth) to produce a measured baseline."}
    return {"avoided_scenarios": avoided_scenarios,
            "usd": round(avoided_scenarios * measured, 2),
            "basis": "measured",
            "per_scenario_usd": measured}


def _measured_per_scenario_usd():
    """Measured cost of authoring one scenario, or None.

    Deliberately strict: a simulated run's `spend` block is labelled and must
    never be averaged in. If no run on this estate was metered for real, the
    answer is None, not a number derived from mocks.
    """
    try:
        import cost_report
        rep = cost_report.report()
    except Exception:                          # noqa: BLE001
        return None
    if not isinstance(rep, dict):
        return None
    # `simulated` is the cost stack's own flag for mock spend. If the report is
    # simulated, there is nothing measured here to price with.
    if rep.get("simulated") or not rep.get("measured"):
        return None
    return None      # no per-scenario figure is derivable yet; say so plainly


if __name__ == "__main__":
    import json
    sys.stdout.reconfigure(encoding="utf-8")
    argv = sys.argv[1:]
    if argv and argv[0] != "--json":
        print(json.dumps(authoring_plan(argv[0]), indent=1))
    else:
        e = estate()
        if "--json" in argv:
            print(json.dumps(e, indent=1))
        else:
            print(f"scenarios {e['scenarios']}  already covered "
                  f"{e['already_covered']}  would author {e['to_author']}")
            s = e["savings"]
            if s["usd"] is None:
                print(f"  saving: {s['avoided_scenarios']} authoring call(s) avoided "
                      f"— value NOT MEASURED ({s['why']})")
            else:
                print(f"  saving: ~${s['usd']} ({s['basis']})")
            for p in e["keys"]:
                print(f"  {p['key']:14} {p['already_covered']}/{p['scenarios']} covered"
                      + (f"  ({p['unlinked_tests']} unlinked test(s))"
                         if p["unlinked_tests"] else ""))

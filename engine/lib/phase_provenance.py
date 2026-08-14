"""Did a real model produce this phase's numbers? One answer, for every signal.

The iron rule keeps arriving at new signals. It started on money (`cost_report.
money`), then the critic's quality score, and this module is the third arrival:
the VALIDATE phase's `passed` / `failed` counts.

Measured on this estate, 40 of 40 recent runs report `2 passed, 0 failed` while
the same record's generate contract holds ONE test -- because
`engine/phases/mock_phase.sh` emits the constant
`{"passed":2,"failed":0,"repair_loops":0,"flaky_reruns":0}`. So the number is
not merely unlabelled, it CONTRADICTS the run it describes, and
`pr_comment` renders it as `**Validation:** 2 passed, 0 failed` on the pull
request a human merges from.

What corroborates what, stated precisely, because overstating this would be its
own dishonesty: on a mock run the GATE really does execute the changed specs
(`make demo-pr` is "mock LLM, real gate/env/git"), so a `committed` gate status
IS evidence the tests ran and passed. The validate counts are a separate claim
-- the phase's own account of its repair loop -- and in mock mode they are a
constant. gate.sh already words its own version of this honestly: "tests were
never executed, so nothing is known about them".

THREE STATES (C13): measured / simulated / unknown. `unknown` is not a polite
way of saying measured -- it is what a live composer with no run record, or a
phase recorded with no spend block, actually knows.
"""

MEASURED, SIMULATED, UNKNOWN = "measured", "simulated", "unknown"


def of(phase, signal=None, record=None, cost_rows=None):
    """The provenance of `phase`'s numbers in this run.

    A `simulated` flag already stamped on the signal wins: stores that outlive
    the run record (the review board) carry it, and re-deriving would answer
    `unknown` there forever.
    """
    if signal is not None and isinstance(signal.get("simulated"), bool):
        return SIMULATED if signal["simulated"] else MEASURED
    if record:
        # Spend resolution belongs to spend_history -- the build fails on a new
        # module reading a record's spend directly.
        import spend_history
        sim = spend_history.phase_simulated(record, phase)
        if sim is not None:
            return SIMULATED if sim else MEASURED
        # No early return for "phase present but carrying no spend": it fell
        # through to UNKNOWN anyway, so the branch was equivalent -- a mutation
        # deleting it survived, and checking WHY showed it was not merely dead
        # but slightly wrong, since it also blocked the ledger fallback below.
        # A record that shows the phase ran and a ledger that knows its basis
        # is better evidence than refusing to look.
    for row in cost_rows or []:
        if row.get("phase") == phase and row.get("cost_basis"):
            return SIMULATED if row["cost_basis"] == "simulated" else MEASURED
    return UNKNOWN


def caveat(prov, *, what="these figures"):
    """Words, not a symbol, for prose surfaces (PR comments, exported plans).

    Empty for a measured phase: a caveat that fires on correct output is one
    readers learn to skip, which is how the real ones stop landing.
    """
    if prov == SIMULATED:
        return f" - SIMULATED ({what} come from a mock run, not from execution)"
    if prov == UNKNOWN:
        return f" - provenance not recorded, so it is not known whether {what} were produced by a real run"
    return ""


def mark(text, prov):
    """The same three-way marker the cost and critic renderers use."""
    if prov == SIMULATED:
        return f"~{text}"
    if prov == UNKNOWN:
        return f"{text}?"
    return str(text)

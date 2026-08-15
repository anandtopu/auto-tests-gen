#!/usr/bin/env python3
"""What a run record says it is MISSING, in words a human can act on.

`run_record.py` already records three "we could not read this" facts, and the
comments at those write sites state the requirement exactly:

  * `malformed_gate_lines` — "a record showing three gates when the file held
    four must SAY that it is short, or `gates: [...]` reads as the complete
    set."
  * `contract_unreadable` — the phase is still LISTED with the failure named,
    because "skipping it silently would make an unreadable contract
    indistinguishable from a phase that never ran (C13)".
  * `context_retries` — "an honest marker that this run paid the retry."

MEASURED: not one production module read any of the three. The writer stated
the guarantee and every reader broke it — the same shape as `alert_rules`,
whose docstring promised it never raises from `make maintain` while the line
below it did. So the gate list was rendered as the complete set on the
dashboard, in `qa.py`, in the run-summary email and in the comment posted on
the pull request, with nothing to say rows were lost.

ONE definition, because six surfaces marking themselves is how there came to
be six that did not (the precedent here is `critic.provenance`,
`phase_provenance.of` and `cost_report.money`). Renderers ask; they never
re-derive.

Silent on a healthy run BY CONSTRUCTION: every function returns empty when the
record carries none of these keys, and none of the 443 records in this estate
carries one today. A caveat that fires on a good run is one operators learn to
scroll past.
"""


def _int(value):
    """A count we can trust, or 0. The record is assembled from a TSV that may
    have been torn mid-write, so its own damage marker can be damaged."""
    return value if isinstance(value, int) and not isinstance(value, bool) \
        and value > 0 else 0


def gates_note_for(count):
    """The sentence for a known number of lost gate lines, or "".

    Taken by COUNT as well as by record because the live PR-comment composer
    parses `out/gate_results.tsv` itself and never had a record to consult --
    it simply dropped short lines. One wording, both paths, so a comment
    posted during the run cannot be more confident than the same comment
    replayed from the record afterwards.
    """
    n = _int(count)
    if not n:
        return ""
    return (f"{n} gate result line(s) could not be parsed, so this gate list "
            f"is INCOMPLETE — the run gated more repositories than are shown "
            f"here (their outcome is unrecoverable from this record)")


def gates_note(record):
    """The one-sentence warning that the gate list is SHORT, or "".

    For surfaces that render gates and nothing else. A reader who sees one
    repository gated draws a conclusion about the run; if a second repo's
    result was lost to a torn line, that conclusion is wrong and nothing on
    the page disagrees with them.
    """
    return gates_note_for((record or {}).get("malformed_gate_lines"))


def unreadable_phases(record):
    """[(phase, reason)] for phases whose contract could not be read.

    The phase RAN. What it reported is gone — a different fact from a phase
    that never ran, and from one that ran and reported nothing.
    """
    out = []
    for ph in (record or {}).get("phases") or []:
        if not isinstance(ph, dict):
            continue
        why = ph.get("contract_unreadable")
        if why:
            out.append((ph.get("name") or "?", str(why)))
    return out


def retried_phases(record):
    """[(phase, what was missing)] for phases re-run on the full estate."""
    out = []
    for r in (record or {}).get("context_retries") or []:
        if isinstance(r, dict) and r.get("phase"):
            out.append((r["phase"], str(r.get("missing") or "")))
    return out


def caveats(record):
    """Every sentence this record's own damage markers justify. [] when clean.

    Ordered by what changes a reader's conclusion soonest: a short gate list
    first (it makes a displayed set wrong), then phases whose output is gone,
    then retries (which cost money and explain a phase's context, but mislead
    nobody).
    """
    out = []
    note = gates_note(record)
    if note:
        out.append(note)
    for phase, why in unreadable_phases(record):
        out.append(f"phase '{phase}' RAN but its contract could not be read "
                   f"({why}) — what it reported is unavailable, which is not "
                   f"the same as it having reported nothing")
    retries = retried_phases(record)
    if retries:
        named = ", ".join(p for p, _ in retries)
        out.append(f"{len(retries)} phase(s) re-ran with the full estate after "
                   f"reporting missing context ({named}) — the scoped context "
                   f"was insufficient and this run paid for the retry")
    return out

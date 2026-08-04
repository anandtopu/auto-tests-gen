#!/usr/bin/env python3
"""Read out/ticket.json ONCE and emit every field the pipeline needs.

pipeline.sh's jira branch ran five separate `python3 -c` one-liners, each
starting an interpreter (~200ms on Windows) to parse the SAME file for one
field: components, labels, linked repos, fix versions, issue type. Five reads
of one document by five processes — the same duplicated-work shape as the
spec_exemplars 1+N scans, measured there at ~200ms per unnecessary start.

Output is shell assignments for pipeline.sh to `eval`, following the precedent
set by app_paths.sh_exports(): values are shlex-quoted, because ticket text is
UNTRUSTED DATA from JIRA and `eval` of unquoted output is the shortcut that
becomes an injection the day a component name carries a quote or a newline.
(The old one-liners captured via $(...) and were not eval'd, so they did not
need quoting; this one is, so it does.)

The field expressions are kept byte-identical to the one-liners they replace —
comma-joined lists, and issue_type defaulting to 'story' lowercased — pinned by
test_ticket_fields.py against edge-case tickets, because these values feed
resolve.py and the issue-guidance selection.
"""
import json
import shlex
import sys


def fields(ticket):
    """The five values, computed exactly as the one-liners did."""
    t = ticket if isinstance(ticket, dict) else {}
    return {
        "AIQE_T_COMP": ",".join(t.get("components", [])),
        "AIQE_T_LBL": ",".join(t.get("labels", [])),
        "AIQE_T_LINKED": ",".join(t.get("linked_repos", [])),
        "AIQE_T_FIXV": ",".join(t.get("fix_versions", [])),
        "AIQE_T_ITYPE": (t.get("issue_type") or "story").lower(),
    }


def sh_exports(path):
    try:
        ticket = json.loads(open(path, encoding="utf-8").read())
    except (OSError, ValueError) as e:
        # The old one-liners crashed the pipeline here too (set -e) — keep that:
        # a run without a readable ticket must not continue on empty fields and
        # route by guesswork.
        raise SystemExit(f"ticket_fields: cannot read {path}: {e}")
    return "\n".join(f"{k}={shlex.quote(str(v))}"
                     for k, v in fields(ticket).items())


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: ticket_fields.py <ticket.json>")
    print(sh_exports(sys.argv[1]))

#!/usr/bin/env python3
"""Name the repos a run will generate NOTHING for, at the moment it decides.

The resolver knows this and used to drop it. Measured on the shipped registry:
a `Catalog` ticket resolves THREE source repos and ONE test repo, because
admin-portal-ui and catalog-api are covered by nothing -- and the contract said
only `test_repos: [e2e-ui-tests-1], confidence 0.85`, so a reader reasonably
concludes the ticket is covered.

`make coverage` warns about uncovered repos at ESTATE level, which is not the
moment this matters: what a human needs is "this change touches catalog-api and
nothing will be generated for it", while they are looking at this run.

TWO STATES, because the fixes differ (C13):
  uncovered      no test repo covers it at all  -> onboard one, or extend `scope`
  layer-filtered covered, but a restrict_layers label excluded it on this
                 ticket -> deliberate, and usually correct

Prints nothing when every implicated repo is covered: a note that fires on a
healthy run is one operators learn to scroll past.

Never fatal. This is an observation about a run, not a gate -- pipeline.sh calls
it with `|| true` and a malformed contract must not take down a run that is
otherwise fine.
"""
import json
import pathlib
import sys


def lines(resolve):
    """The note, as zero or more lines. Empty when there is nothing to say."""
    if not isinstance(resolve, dict):
        return []
    def _names(key):
        # The list check is not decoration: a STRING is iterable and its
        # characters are strings, so a malformed `"catalog-api"` (not wrapped in
        # a list) filtered cleanly into ten one-letter "repos" and printed them.
        # Caught by this module's own defensive test.
        value = resolve.get(key)
        if not isinstance(value, (list, tuple)):
            return []
        return [r for r in value if isinstance(r, str) and r.strip()]

    out = []
    uncovered = _names("uncovered_sources")
    filtered = _names("layer_filtered_sources")
    if uncovered:
        out.append("[coverage] this run generates NOTHING for: "
                   + ", ".join(uncovered))
        out.append("[coverage]   no test repo covers them - onboard one, or add "
                   "them to an existing test repo's `scope`")
    if filtered:
        out.append("[coverage] excluded by a restrict_layers label (deliberate): "
                   + ", ".join(filtered))
    return out


def main(argv):
    if len(argv) != 2:
        print("usage: uncovered_note.py <resolve.contract.json>", file=sys.stderr)
        return 64
    try:
        resolve = json.loads(pathlib.Path(argv[1]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A contract we cannot read is not a finding about coverage.
        return 0
    for line in lines(resolve):
        print(line)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv))

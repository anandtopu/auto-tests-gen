#!/usr/bin/env python3
"""Wrap a mock stub's contract the way a provider actually returns one.

The mock phase harness used to write out/<phase>.contract.json directly, so the
whole mock path skipped `extract_contract.py` — the step that pulls the contract
out of the model's prose and checks the schema's required keys (REVIEW.md open
item 2). Two things followed: a stub could drift from its schema and no demo run
would notice, and every demo proved one step less of the real chain than it
looked like it did.

This writes the shape run_phase.sh gets back from a provider — a `result` string
with prose around a fenced JSON block — so the same extractor runs over it.

The prose is not decoration. Extraction's job is to find the contract among text
a model wrote, and handing it a bare JSON document would exercise the one input
it never actually receives.
"""
import json
import pathlib
import sys


def wrap(contract_text):
    """Provider-shaped result carrying `contract_text` as its trailing JSON."""
    return {
        # Mirrors a real reply: a sentence, the fenced contract, a sign-off.
        "result": ("Done. The contract for this phase follows.\n\n"
                   "```json\n" + contract_text.strip() + "\n```\n\n"
                   "Let me know if anything needs adjusting.\n"),
        "num_turns": 1,
        # NO total_cost_usd. A mock spends nothing, and a 0 here would be
        # harvested as a MEASURED zero — see the note in mock_phase.sh about why
        # this file is never named out/<phase>.json.
        "provider": "mock",
        "model": "mock",
    }


def main(argv):
    if len(argv) != 2:
        raise SystemExit("usage: mock_result.py <contract.json> <out.json>")
    src, dest = pathlib.Path(argv[0]), pathlib.Path(argv[1])
    text = src.read_text(encoding="utf-8")
    try:
        json.loads(text)
    except ValueError as e:
        # The stub itself emitted something unparseable. Say so here rather than
        # letting extraction report the confusing "no contract found".
        raise SystemExit(f"mock_result: {src} is not valid JSON: {e}")
    dest.write_text(json.dumps(wrap(text)), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

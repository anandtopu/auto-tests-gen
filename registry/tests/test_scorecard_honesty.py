"""Update-vs-create counts judgement, not scripted fixture output.

The scorecard reported `Update-vs-create: 0% of 341 generated tests extended
existing suites (higher = better duplicate prevention)` — a flat zero across
every run this estate has ever done, which reads as "the duplicate-prevention
feature does not work".

It was measuring the fixture. Virtually all 341 runs are mock, and the mock
generate stub always writes a NEW file, so `action` is scripted rather than
chosen. The scout is fine: run against this estate it correctly emits
`EXTEND suites/orders/discount.spec.js`, and pipeline.sh does feed
out/extend-candidates.md into the generate context.

Same rule the cost figures already follow — a simulated number is never
reported as a measurement — using the same "metered" test the cost line uses
(a real LLM call reports a cost; a mock one does not).
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = (ROOT / "eval/scorecard.py").read_text(encoding="utf-8")


def test_simulated_runs_are_excluded_from_the_judgement_metric():
    assert "sim_actions" in SRC, "the simulated/metered split is gone"
    block = SRC[SRC.index('if p["name"] == "generate"'):]
    block = block[:block.index("if loops:")]
    assert "if not metered:" in block, (
        "generate actions are counted from every run again — a mock stub's "
        "scripted 'created' would be read as a judgement to duplicate")


def test_the_metered_test_matches_the_one_cost_uses():
    """If the two drift, one figure counts runs the other calls simulated."""
    assert 'metered = isinstance(r.get("cost_usd"), (int, float))' in SRC, \
        "the metered test changed; cost and update-vs-create would disagree"
    assert 'costs = [r["cost_usd"] for r in runs if isinstance(r.get("cost_usd"), (int, float))]' in SRC


def test_an_all_simulated_estate_reports_na_not_zero():
    """The C13 case. Zero reads as 'measured, and the answer is none'."""
    branch = SRC[SRC.index("elif sim_actions:"):]
    branch = branch[:branch.index("\n    #") if "\n    #" in branch[:600] else 600]
    assert "n/a" in branch, "an unmeasured estate still reports a number"
    assert "SIMULATED" in branch, "the reason is not stated"
    assert "parity" in branch, "no route to actually measuring it is named"


def test_the_measured_line_still_reports_when_there_is_real_data():
    """The other direction — suppressing the figure entirely would satisfy the
    tests above while removing the metric."""
    assert re.search(r'print\(f"Update-vs-create: \{pct\(updated / \(created \+ updated\)\)\}', SRC), \
        "the measured figure is no longer printed"
    assert "excluded" in SRC, "the excluded simulated count is not disclosed"

"""Contradictory routing labels ask a human instead of picking one.

`resolve_jira` collected `restrict_layers` with last-one-wins:

    for l in labels:
        if "restrict_layers" in r:
            layers = r["restrict_layers"]      # every earlier restriction lost

The shipped registry maps `api-only -> [api]` and `ui-only -> [ui]`, so a
ticket carrying both routed to whichever label came LAST. Measured on the real
registry before the fix:

    ['api-only', 'ui-only'] -> e2e-ui-tests-1   confidence 0.85
    ['ui-only', 'api-only'] -> e2e-api-tests-1  confidence 0.85

Same ticket, same labels, different test repo -- decided by the order JIRA
happened to return them in, at a confidence well above the 0.8 threshold. The
platform was certain about a coin flip, and the layer it dropped never had
tests generated: the unrouting CLAUDE.md calls the one failure this platform
cannot see from the inside.

`restrict_layers` means "only these layers", so INTERSECTION is what the word
already promises. Contradictory labels intersect to empty -> no test repos ->
confidence capped at 0.4 -> below threshold -> needs_clarification. The
documented "we cannot tell" path, reached with no special case.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/phases"))
import resolve  # noqa: E402

REG = {
    "source_repositories": [{"name": "orders-api"}],
    "test_repositories": [
        {"name": "e2e-api", "layer": "api", "covers": ["orders-api"]},
        {"name": "e2e-ui", "layer": "ui", "covers": ["orders-api"]},
    ],
    "routing_hints": {
        "jira_component_map": {"Checkout": ["orders-api"]},
        "jira_label_map": {
            "api-only": {"restrict_layers": ["api"]},
            "ui-only": {"restrict_layers": ["ui"]},
            "both": {"restrict_layers": ["api", "ui"]},
        },
    },
}


def _r(labels):
    return resolve.resolve_jira(REG, "PROJ-1", ["Checkout"], labels, [])


def test_contradictory_labels_do_not_silently_pick_one():
    a = _r(["api-only", "ui-only"])
    b = _r(["ui-only", "api-only"])
    assert a["test_repos"] == [] and b["test_repos"] == [], (
        "a contradictory pair still routes somewhere -- to "
        f"{a['test_repos'] or b['test_repos']}")


def test_the_answer_does_not_depend_on_label_order():
    """The property that was broken. JIRA's label order is not stable, so an
    order-dependent answer means the same ticket routes differently on a
    re-run, with nothing in the output admitting it.

    Compares the DECISION, not the whole dict: `rationale` echoes the labels
    it was given, so it legitimately differs by order. Asserting dict equality
    failed on the corrected code -- a test that cannot pass makes its own
    mutation meaningless, since it fails either way.
    """
    decision = lambda o: (o["test_repos"], o["source_repos"], o["confidence"])
    assert decision(_r(["api-only", "ui-only"])) == decision(_r(["ui-only", "api-only"]))


def test_a_contradiction_drops_below_the_clarification_threshold():
    out = _r(["api-only", "ui-only"])
    assert out["confidence"] <= 0.4, (
        f"confidence {out['confidence']} on contradictory input -- the run "
        "will proceed on a guess rather than asking")


def test_a_single_restriction_is_unchanged():
    """The control. Refusing whenever two labels appear, or dropping layer
    restriction entirely, would satisfy the tests above and break routing."""
    assert _r(["api-only"])["test_repos"] == ["e2e-api"]
    assert _r(["ui-only"])["test_repos"] == ["e2e-ui"]


def test_no_restricting_label_still_routes_to_every_layer():
    assert _r([])["test_repos"] == ["e2e-api", "e2e-ui"]
    assert _r(["unmapped-label"])["test_repos"] == ["e2e-api", "e2e-ui"]


def test_compatible_restrictions_intersect_rather_than_union():
    """`both` allows api+ui; `api-only` narrows it. Restriction that widens on
    a second label would let a label ADD scope, which the name forbids."""
    assert _r(["both", "api-only"])["test_repos"] == ["e2e-api"]
    assert _r(["api-only", "both"])["test_repos"] == ["e2e-api"]

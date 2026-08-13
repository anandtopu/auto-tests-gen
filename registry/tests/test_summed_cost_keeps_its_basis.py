"""A summed dollar figure must carry the basis it was arrived at.

The iron rule -- a simulated number must never masquerade as a measured dollar
-- kept breaking in exactly one place, and this is the third time it has been
found: wherever the code renders PER BASIS it is right, and wherever bases are
SUMMED INTO ONE NUMBER the basis was dropped on the way.

Found by DRIVING the dashboard in a browser. The Cost view's provider table
showed `mock  438  $1.5000  simulated, unrecorded` -- a bare measured-dollar
rendering over money that is 100% not measured -- and the Top keys table showed
`PR-orders-api-201  60  $1.5000` with no basis awareness at all. Confirmed at
HEAD by executing the real renderer against the real payload, so it is not the
stale-server artifact it could have been: the estate's row is
`{simulated: 418, unrecorded: 20}` and the old rule only marked a figure whose
bases were EXACTLY ONE non-measured kind. Any MIXTURE fell through to `$`.

Four sites shared it -- the dashboard's two tables and the markdown report's
"By workflow" and "Top keys" sections -- so the fix is one decision function
(`cost_report.money`) plus the basis map travelling with the figure, rather
than four spot repairs.

The over-fix direction is pinned as hard as the defect: marking a genuinely
measured dollar `~$` is the lie that teaches people to ignore the tilde.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import cost_report                                             # noqa: E402


# ------------------------------------------------------------- the rule

def test_the_estate_row_that_exposed_this_is_marked():
    """{simulated, unrecorded} -- what this estate's provider table actually
    holds today, and what printed a bare `$`."""
    assert cost_report.money(1.5, {"simulated": 418, "unrecorded": 20}) == "~$1.5000"


def test_a_genuinely_measured_total_is_not_hedged():
    """The over-fix. A `~` on a real bill is worse than the defect, because it
    is what teaches a reader to ignore the marker everywhere else."""
    assert cost_report.money(1.5, {"reported": 12}) == "$1.5000"


@pytest.mark.parametrize("bases", [
    {"reported": 1, "simulated": 1},
    {"reported": 1, "estimated": 1},
    {"reported": 1, "unknown": 1},
    {"reported": 1, "unrecorded": 1},
    {"reported": 1, "not-reconciled": 1},
])
def test_one_unmeasured_basis_marks_the_whole_figure(bases):
    """A total mixing measured and unmeasured money cannot be separated after
    the fact, so it must not read as if it were measured."""
    assert cost_report.money(9.0, bases).startswith("~$")


def test_the_single_basis_cases_stay_byte_identical():
    """local and unknown already rendered correctly; this fix must not move
    them, or it trades one wrong label for another."""
    assert cost_report.money(0, {"local": 3}) == "$0 (local)"
    assert cost_report.money(2.0, {"unknown": 2}) == "unknown"


def test_provenance_we_could_not_establish_is_not_measured():
    """An empty basis map is not evidence of measurement (C13)."""
    assert cost_report.money(1.0, {}).startswith("~$")
    assert cost_report.money(1.0, None).startswith("~$")


def test_a_basis_present_with_a_zero_count_does_not_certify_a_figure():
    """`{reported: 0}` means no measured call contributed. Counting the KEY
    rather than the count would let an empty bucket certify a simulated
    total."""
    assert cost_report.money(1.0, {"reported": 0}).startswith("~$")
    assert cost_report.money(1.0, {"reported": 0, "simulated": 5}).startswith("~$")


# --------------------------------------------- the basis reaches the renderers

def _estate(tmp_path, basis, cost):
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "r1.json").write_text(json.dumps({
        "run_id": "r1", "ts": 2_000_000_000,
        "trigger": {"type": "pr", "key": "K-1"},
        "phases": [{"name": "triage", "contract": {}, "spend": {
            "provider": "p", "model": "m", "cost_basis": basis,
            "cost_usd": cost, "simulated": basis == "simulated",
            "attempts": 1, "attribution": "user"}}],
    }), encoding="utf-8")
    return runs


def _report(monkeypatch, runs):
    monkeypatch.setattr(cost_report, "RUNS", runs)
    import spend_history
    monkeypatch.setattr(spend_history, "RUNS", runs)
    return cost_report.report()


def test_the_rollups_carry_the_basis_to_the_renderer(tmp_path, monkeypatch):
    """by_provider always did; by_mode and by_key did not, so their renderers
    had nothing to obey the rule WITH."""
    rep = _report(monkeypatch, _estate(tmp_path, "simulated", 0.25))
    assert rep["by_key_top10"][0].get("bases"), "a key rollup lost its basis"
    assert rep["by_mode"]["pr"].get("bases"), "a mode rollup lost its basis"


def test_the_markdown_marks_simulated_workflow_and_key_lines(tmp_path, monkeypatch):
    rep = _report(monkeypatch, _estate(tmp_path, "simulated", 0.25))
    md = cost_report.to_markdown(rep)
    workflow = [l for l in md.splitlines() if l.startswith("- pr:")]
    keys = [l for l in md.splitlines() if l.startswith("- K-1:")]
    assert workflow and keys, md
    assert "~$" in workflow[0], "the By workflow line printed a bare $"
    assert "~$" in keys[0], "the Top keys line printed a bare $"


def test_the_markdown_does_not_hedge_a_measured_workflow(tmp_path, monkeypatch):
    """Same over-fix guard, at the renderer."""
    rep = _report(monkeypatch, _estate(tmp_path, "reported", 0.25))
    md = cost_report.to_markdown(rep)
    workflow = [l for l in md.splitlines() if l.startswith("- pr:")][0]
    keys = [l for l in md.splitlines() if l.startswith("- K-1:")][0]
    assert "~$" not in workflow and "$0.2500" in workflow, workflow
    assert "~$" not in keys, keys


# ------------------------------------------------------------- the dashboard

def _cost_js():
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    body = src.split("const fmt = (v, bases)")[1]
    # Through the SAVINGS block, not the local-split one: the keys table sits
    # after `const ls =`, so the narrower slice cut off the very sibling this
    # file exists to pin -- and it failed loudly rather than passing on an
    # empty string, which is the only reason it was caught here.
    return "const fmt = (v, bases)" + body.split("const sv =")[0]


def test_the_dashboard_rule_requires_every_basis_to_be_measured():
    js = _cost_js()
    assert "b.every(x => x === 'reported')" in js, \
        "the dashboard is back to keying on a single basis"


def test_the_top_keys_table_formats_through_the_same_rule():
    """It had no basis awareness at all -- the sibling that made this a class
    rather than an instance."""
    js = _cost_js()
    keys_block = js.split("cost-keys-table")[1]
    assert "fmt(e.cost_usd, e.bases)" in keys_block, \
        "the top-keys table prints an unqualified dollar again"


def test_the_dashboard_still_renders(tmp_path):
    """A JS edit that breaks the page produces no dashboard AT ALL, which is
    the failure mode this repo has already hit twice.

    AIQE_DASHBOARD_OUT is not optional here. The first version of this test
    wrote the SHARED reports/dashboard.html and raced
    test_docs_currency::test_every_stated_view_count_matches_the_dashboard,
    which reads it -- green alone, red in a full run. That knob exists because
    an earlier test overwrote the operator's real dashboard the same way.
    """
    import os
    import subprocess
    out = tmp_path / "dashboard.html"
    r = subprocess.run([sys.executable, str(ROOT / "bin/dashboard.py")],
                       cwd=ROOT, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=300,
                       env=dict(os.environ, AIQE_DASHBOARD_OUT=str(out)))
    assert r.returncode == 0, r.stderr[-2000:]
    assert out.exists() and out.stat().st_size > 0,         "the knob was ignored — this test is writing the shared dashboard"


def test_the_per_mode_summary_line_formats_through_the_rule():
    """THE SITE THE FIRST SWEEP MISSED, and the most prominent number in the
    view: the Cost headline reads `Total ~$3.0000 ... pr: 114 run(s) $3.0000`
    -- the total correctly marked and the per-mode figure beside it bare, over
    the same entirely-simulated money. Found by driving the page after fixing
    the two tables below it.
    """
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    modes = src.split("const modes = Object.entries")[1].split(".join(' · ')")[0]
    assert "fmt(v.cost_usd, v.bases)" in modes, \
        "the per-mode summary prints an unqualified dollar again"
    assert "' run(s) $'" not in modes


def test_the_formatter_is_declared_before_every_consumer():
    """It was block-scoped inside the provider table, which is why the line
    above it could not use it and kept a bare `$`. Declaration order IS the
    defect here, so pin it: `fmt` must come first."""
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    decl = src.index("const fmt = (v, bases)")
    for consumer in ("const modes = Object.entries",
                     "cost-provider-table", "cost-keys-table"):
        assert decl < src.index(consumer), \
            f"fmt is declared after {consumer} — that consumer cannot use it"

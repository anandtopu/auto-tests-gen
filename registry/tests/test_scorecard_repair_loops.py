"""The scorecard averaged a mock's constant and called it a measurement.

`mock_phase.sh` emits the CONSTANT `repair_loops: 0`, and `eval/scorecard.py`
averaged it across every run:

    Repair loops: 0.00 avg over 552 validated runs

on an estate where nothing has ever measured a repair loop. `team_report` was
fixed for EXACTLY this defect — its `_repair_loop_cell` docstring says "a mock
validate phase emits a constant, so averaging it reports the stub" — and the
scorecard, the platform's OWN quality report, was missed. Two surfaces over
one fact, one honest, which is the pattern this repo keeps recording.

The sharpest detail: the correct test was already computed THREE LINES ABOVE,
in the same loop, for the generate branch, with a comment explaining the rule
("a real LLM call reports a cost, a mock one does not... counting it measures
the fixture, not the platform"). The validate branch below it ignored the
variable that was already in hand.

Asked PER PHASE rather than per run: a run whose generate was real and whose
validate was mocked has a simulated repair count. That is what
`phase_provenance` exists to answer, and it is the rule already recorded for
the validation counts.

COMMIT RATE IS DELIBERATELY NOT DOWNGRADED, and the pins guard that direction
as hard as the defect. `make demo-pr` is "mock LLM, real gate/env/git", so the
gate genuinely lints, executes the changed specs and decides — a `committed`
status IS evidence, about THE GATE. Marking a real measurement `n/a` or `~` is
the lie that teaches readers to ignore the marker everywhere else. What it is
not is evidence about model quality, and sitting beside three `n/a` lines that
ARE about model quality invites that reading, so the SCOPE is named instead.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "engine/lib"))

import scorecard                                                # noqa: E402


def _run(overall="committed", *, simulated=True, loops=0):
    spend = {"cost_usd": 0.25, "simulated": True} if simulated else \
            {"cost_usd": 0.25, "cost_basis": "reported"}
    return {"overall": overall,
            "phases": [{"name": "validate", "spend": spend,
                        "contract": {"passed": 1, "failed": 0,
                                     "repair_loops": loops}}]}


# ------------------------------------------------------------ repair loops

def test_a_simulated_validate_phase_is_not_averaged():
    """THE DEFECT: the mock's constant reported as a measurement."""
    import phase_provenance
    assert phase_provenance.of("validate", record=_run()) != "measured"


def test_a_measured_validate_phase_still_counts():
    """The over-fix guard: the day parity is unblocked this must measure."""
    import phase_provenance
    assert phase_provenance.of("validate",
                               record=_run(simulated=False)) == "measured"


def test_the_scorecard_asks_per_phase_not_per_run():
    """A run whose generate was real and whose validate was mocked has a
    SIMULATED repair count — the phase asked about must be the phase answered
    about."""
    src = (ROOT / "eval/scorecard.py").read_text(encoding="utf-8")
    block = src[src.index('if p["name"] == "validate"'):]
    block = block[:block.index("if p[\"name\"] == \"generate\"")]
    assert 'phase_provenance.of("validate"' in block, \
        "the validate branch no longer asks about the validate phase"


def test_the_excluded_count_is_named():
    """A denominator that shrinks in silence is the failure this rule exists
    to prevent — the same wording team_report already uses."""
    src = (ROOT / "eval/scorecard.py").read_text(encoding="utf-8")
    assert "simulated run(s) excluded" in src
    assert "MEASURED validate phase" in src


# ------------------------------------------------------------- commit rate

def test_commit_rate_names_its_scope_on_a_simulated_estate():
    line = scorecard.commit_rate_line([_run(), _run()], measured=False)
    assert "THE GATE" in line, line
    assert "not the quality of what the model wrote" in line, line


def test_commit_rate_is_not_hedged_on_a_measured_estate():
    """The over-fix direction, and the one that would be worse than the
    defect: a real measurement must not gain a caveat."""
    line = scorecard.commit_rate_line([_run(simulated=False)], measured=True)
    assert "THE GATE" not in line, line
    assert line.startswith("Commit rate: 100% of 1 runs"), line


def test_commit_rate_is_never_downgraded_to_n_a_for_being_simulated():
    """The gate really runs on a mock run, so this figure stays a number."""
    line = scorecard.commit_rate_line([_run(), _run()], measured=False)
    assert line.startswith("Commit rate: 100%"), line
    assert "n/a" not in line.split(";")[0], line


def test_an_unknown_measured_flag_adds_nothing():
    """Callers that cannot say stay byte-identical to before this existed."""
    assert scorecard.commit_rate_line([_run()]) == \
        scorecard.commit_rate_line([_run()], measured=None)


def test_check_only_runs_are_still_excluded_and_named():
    """The rule this function already carried must survive the new clause."""
    line = scorecard.commit_rate_line([_run(), _run(overall="would_commit")],
                                      measured=False)
    assert "1 excluded" in line and "check-only" in line, line

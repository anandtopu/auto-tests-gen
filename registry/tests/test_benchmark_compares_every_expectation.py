"""The routing benchmark scored 100% on one field.

`eval/run_fixture.py` compared `test_repos` and nothing else, so
"Routing accuracy: 100%" was a claim about a single key. Two resolutions that
agree on `test_repos: []` can disagree about everything that matters: an EMPTY
change list (nothing was established, confidence 0.0) versus a change list
with nothing testable in it (an established negative, confidence 1.0). The
resolver was taught that distinction deliberately — and the benchmark that
measures routing could not see it.

MEASURED, the second half: `eval/benchmark/prs/example-contract-change.json`
has declared `"impact": "create"` all along and NOTHING read it. A declared
expectation enforced by nothing is the written-but-unread shape this benchmark
exists to catch in the product, sitting inside the benchmark itself.

It is reported rather than failed: a fixture may legitimately assert something
a future full-pipeline run will check. What it may not do is be invisible, so
the scorecard names it — a finding recorded in a JSON nobody reads is the same
defect one layer along.
"""
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
# Selected exactly the way eval/replay.sh selects them, which cost two wrong
# answers to get right. NOT rglob over eval/benchmark: its results/ directory
# holds expected OUTPUT and a sample CI payload, not fixtures. And NOT a bare
# pathlib glob either: pathlib MATCHES dot-prefixed files where shell globbing
# does not, so `.context-orders-api-201.json` and `.item-PROJ-301.json` --
# mock-adapter scratch sitting in the fixture directories -- were fed to
# run_fixture.py and died on KeyError: 'mode'. A test that selects a different
# set from the harness is measuring a different thing.
def _fixtures():
    out = []
    for sub in ("prs", "tickets"):
        out += sorted(p for p in (ROOT / "eval/benchmark" / sub).glob("*.json")
                      if not p.name.startswith("."))
    return out


FIXTURES = _fixtures()


def _run(fixture):
    r = subprocess.run([sys.executable, str(ROOT / "eval/run_fixture.py"), str(fixture)],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, env={**os.environ, "AIQE_MOCK": "1"})
    assert r.returncode == 0, r.stderr[-800:]
    return json.loads(r.stdout)


def test_there_are_fixtures_to_measure():
    """Without this every assertion below passes vacuously."""
    assert FIXTURES, "no benchmark fixtures found"


def test_every_declared_expectation_is_compared_or_named():
    """The invariant: a fixture's expectation is either checked or reported —
    never silently dropped."""
    for fx in FIXTURES:
        out = _run(fx)
        declared = set(json.loads(fx.read_text(encoding="utf-8")).get("expected") or {})
        accounted = set(out["compared"]) | set(out["unchecked_expectations"])
        assert declared == accounted, \
            f"{fx.name}: {declared - accounted} declared but neither compared nor named"


def test_the_skip_distinction_is_actually_measured():
    """THE DEFECT. This fixture's whole point is a resolution the old harness
    could not tell from its opposite, because both answer test_repos: []."""
    fx = ROOT / "eval/benchmark/prs/nothing-testable-changed.json"
    out = _run(fx)
    assert out["routing_ok"], out
    for field in ("skip", "empty_change_list", "confidence"):
        assert field in out["compared"], \
            f"{field} is declared but not compared — the benchmark is blind again"


def test_a_wrong_reason_fails_even_when_test_repos_match(tmp_path):
    """The property, driven: flip only the REASON and the fixture must fail
    while `test_repos` still agrees."""
    src = json.loads(
        (ROOT / "eval/benchmark/prs/nothing-testable-changed.json").read_text(encoding="utf-8"))
    src["expected"]["empty_change_list"] = True      # the opposite situation
    bad = tmp_path / "wrong-reason.json"
    bad.write_text(json.dumps(src), encoding="utf-8")
    out = _run(bad)
    assert out["routing_ok"] is False, \
        "the benchmark still cannot tell the two skip reasons apart"
    assert out["got"] == out["expected"], \
        "this pin only proves anything while test_repos AGREE"
    assert any(m["field"] == "empty_change_list" for m in out["mismatched"]), out


def test_a_fixture_that_compares_nothing_is_not_a_pass():
    """A fixture whose every expectation names an unanswerable key would
    otherwise score a vacuous 100%."""
    fx = {"mode": "jira", "key": "PROJ-123", "components": ["Checkout"],
                          "labels": [], "expected": {"impact": "create"}}
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "vacuous.json"
        p.write_text(json.dumps(fx), encoding="utf-8")
        out = _run(p)
    assert out["routing_ok"] is False, out
    assert out["unchecked_expectations"] == ["impact"], out


def test_the_scorecard_says_which_fields_the_score_rests_on(tmp_path):
    """DRIVEN, not read. The first version of this pin searched scorecard.py
    for the strings, and a mutation gutting the branch to `if False:` SURVIVED
    — the literals were still there, on a line that could never run. The same
    trap this repo already records for a dashboard panel pin.
    """
    # scorecard.py globs "eval/results/*.json" RELATIVE to the working
    # directory, so the fixture has to mirror that layout, not invent one.
    results = tmp_path / "eval" / "results"
    results.mkdir(parents=True)
    (results / "a.json").write_text(json.dumps({
        "fixture": "eval/benchmark/prs/x.json", "routing_ok": True,
        "compared": ["test_repos", "skip"], "mismatched": [],
        "unchecked_expectations": ["impact"],
        "got": [], "expected": []}), encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "eval/scorecard.py")],
                       cwd=tmp_path, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL,
                       env={**os.environ, "AIQE_ROOT": str(ROOT)})
    assert r.returncode == 0, r.stderr[-800:]
    line = next((l for l in r.stdout.splitlines()
                 if l.startswith("Routing accuracy")), "")
    assert line, r.stdout[:400]
    assert "comparing" in line and "skip" in line, \
        f"the routing line does not say what it compared: {line}"
    assert "NOT CHECKED" in r.stdout, \
        "an expectation nothing reads is invisible again"
    assert "impact" in r.stdout

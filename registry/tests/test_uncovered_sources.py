"""A repo the run will generate nothing for must be named when it is decided.

Found by driving `resolve.py jira` against the shipped registry. A `Catalog`
ticket resolves THREE source repos and ONE test repo:

    source_repos : admin-portal-ui, catalog-api, web-storefront-ui
    test_repos   : e2e-ui-tests-1
    confidence   : 0.85, needs_clarification: false

admin-portal-ui and catalog-api are covered by NOTHING, so this ticket produces
no tests for either -- and the contract said so nowhere. A reader seeing
"resolved e2e-ui-tests-1, confidence 0.85" reasonably concludes the ticket is
covered.

The routing is CORRECT: there is nowhere to generate tests for a repo no test
repo covers. What was missing is the SAYING. `make coverage` warns about
uncovered repos at ESTATE level, which is not the moment this matters -- what a
human needs is "this change touches catalog-api and nothing will be generated
for it", while they are looking at this run. That is the same gap already fixed
for coverage_gaps (a repo it could not observe) and the trace matrix (a test it
could not attribute): the platform knew and did not say.

TWO STATES, because the fixes differ (C13):
    uncovered       no test repo covers it   -> onboard one, or extend `scope`
    layer-filtered  covered, but a restrict_layers label excluded it here
                    -> deliberate, and usually correct
Collapsing them would send someone to onboard a repo that is already covered.
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import uncovered_note                                             # noqa: E402


def _jira(components="", labels="", linked=""):
    r = subprocess.run([sys.executable, str(ROOT / "engine/phases/resolve.py"),
                        "jira", "PROJ-1", "--components", components,
                        "--labels", labels, "--linked-repos", linked],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, timeout=120)
    assert r.returncode == 0, r.stderr[-400:]
    return json.loads(r.stdout)


# ------------------------------------------------------------ the resolver

def test_the_uncovered_repos_are_named(tmp_path):
    """The measured case: Catalog implicates three repos and covers one."""
    d = _jira(components="Catalog")
    assert d["test_repos"] == ["e2e-ui-tests-1"]
    assert d["uncovered_sources"] == ["admin-portal-ui", "catalog-api"], \
        "the repos this run generates nothing for are unnamed again"


def test_a_fully_covered_ticket_names_nobody():
    """The over-fix guard: a healthy resolution must stay quiet, or the note
    becomes something readers scroll past."""
    d = _jira(components="Checkout")
    assert d["uncovered_sources"] == []
    assert d["layer_filtered_sources"] == []


def test_layer_restriction_is_not_reported_as_missing_coverage():
    """orders-api IS covered (by an api repo); a ui-only label excluded it on
    purpose. Reporting that as a coverage gap sends someone to onboard a repo
    that already exists."""
    d = _jira(components="Checkout", labels="ui-only")
    assert d["layer_filtered_sources"] == ["orders-api"]
    assert d["uncovered_sources"] == [], \
        "a deliberate layer restriction is being reported as missing coverage"


def _pr(repo, lines, tmp_path):
    f = tmp_path / "changed.txt"
    f.write_text("".join(l + "\n" for l in lines), encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "engine/phases/resolve.py"),
                        "pr", repo, "--changed-files", str(f)],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, timeout=120)
    assert r.returncode == 0, r.stderr[-400:]
    return json.loads(r.stdout)


def test_the_pr_path_answers_the_same_question(tmp_path):
    """Both paths, or the next reader learns the field only sometimes exists.

    Asserting the VALUE, not merely that the key is present: the first version
    checked `"uncovered_sources" in d`, which stays true when the computation is
    replaced by an empty list -- and a mutation doing exactly that survived.
    """
    d = _pr("admin-portal-ui", ["src/app.tsx"], tmp_path)
    assert d["uncovered_sources"] == ["admin-portal-ui"], \
        "the PR path stopped naming a repo nothing covers"
    assert d["test_repos"] == []
    assert "layer_filtered_sources" in d


def test_the_pr_path_stays_quiet_when_the_repo_is_covered(tmp_path):
    """The over-fix on this path too."""
    d = _pr("orders-api", ["openapi/orders.yaml"], tmp_path)
    assert d["test_repos"], "the control lost its routing"
    assert d["uncovered_sources"] == []


# --------------------------------------------------------------- the note

def test_the_note_names_the_repos_and_the_fix():
    out = uncovered_note.lines({"uncovered_sources": ["catalog-api"],
                                "layer_filtered_sources": []})
    assert any("catalog-api" in l for l in out)
    assert any("scope" in l or "onboard" in l for l in out), \
        "the note names the problem without naming a fix"


def test_the_note_is_silent_on_a_healthy_run():
    assert uncovered_note.lines({"uncovered_sources": [],
                                 "layer_filtered_sources": []}) == []


def test_the_note_separates_the_two_states():
    out = " ".join(uncovered_note.lines(
        {"uncovered_sources": [], "layer_filtered_sources": ["orders-api"]}))
    assert "deliberate" in out, \
        "a layer-filtered repo reads as a coverage gap"
    assert "NOTHING for" not in out


def test_the_note_survives_a_contract_it_cannot_use():
    """It is an observation about a run, never a gate: a malformed contract
    must not take down a run that is otherwise fine."""
    assert uncovered_note.lines(None) == []
    assert uncovered_note.lines({"uncovered_sources": "not-a-list"}) == []
    assert uncovered_note.lines({"uncovered_sources": [None, 3]}) == []


def test_the_pipeline_prints_it_where_the_decision_is_made():
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert "uncovered_note.py" in src, \
        "the pipeline no longer reports repos it will generate nothing for"


def test_explain_answers_it_too():
    """`explain` exists to say why the AI did what it did, and a repo that
    received nothing is exactly what its output cannot show on its own."""
    src = (ROOT / "engine/lib/explain.py").read_text(encoding="utf-8")
    assert "uncovered_sources" in src

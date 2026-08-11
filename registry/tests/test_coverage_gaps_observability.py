""""We could not look at this repo" must never render as "this repo is fine".

`coverage_gaps.compute()` used to `continue` past any repo whose contract or
route table it could not harvest. The repo then left the report entirely, and
every consumer read that silence as an absence of gaps:

  * `make gaps` listed 3 of the estate's 5 app repos, with no hint the other
    two existed. The same file is injected into triage/generate/testplan/
    adversary as out/coverage-gaps.md, so the authoring phases were never told
    payments-api HAS a surface — the one repo whose gaps score highest on the
    risk ladder, since `_SENSITIVE` contains "payment".
  * `coverage_drift` dropped it from the nightly baseline (see
    test_coverage_drift.py for the blind-window reproduction).
  * `team_report` summed `uncovered` into "N uncovered surface(s)" — a number a
    lead reads as the size of the problem — with the unlooked-at repos folded
    in as nothing.

Measured on this estate before the fix: admin-portal-ui and payments-api both
DECLARE an artifact (`src/router/index.ts`, `openapi/payments.yaml`) that is
absent locally. So this is not "these repos have no surface"; it is "we could
not check", which is C13.

The honest rendering already existed one caller away: bin/gen_agents_md.py has
always printed "contract `openapi/payments.yaml` not available locally" for
exactly this case. Two harvesters, one honest, one silent — the sibling
pattern, again with the reference implementation already in the tree.
"""
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine" / "lib"))
import coverage_gaps


def _repo(name="svc", type_="backend", **kw):
    d = {"name": name, "type": type_}
    d.update(kw)
    return d


@pytest.fixture
def rooted(tmp_path, monkeypatch):
    monkeypatch.setattr(coverage_gaps, "ROOT", tmp_path)
    return tmp_path


def _write(root, repo, rel, text):
    p = root / "workspace/src" / repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# --------------------------------------------------------------- four states

def test_a_declared_but_absent_artifact_is_unreadable_not_empty(rooted):
    surface, status, detail = coverage_gaps.harvest(
        _repo(contract="openapi/payments.yaml"))
    assert surface == [] and status == "unreadable"
    assert "openapi/payments.yaml" in detail, "the reader is not told WHAT is missing"
    assert "NOT checked" in detail
    assert not coverage_gaps.observed({"status": status})


def test_no_registered_artifact_is_its_own_state_with_its_own_fix(rooted):
    """Different remedy from `unreadable`: register an artifact, don't fix a
    clone. Collapsing the two sends the reader to the wrong place."""
    surface, status, detail = coverage_gaps.harvest(_repo())
    assert surface == [] and status == "undeclared"
    assert "bin/repos.py" in detail
    assert not coverage_gaps.observed({"status": status})


def test_a_readable_artifact_with_no_surface_is_observed(rooted):
    """We DID look. That is an established negative and counts as observed —
    but the report still says the extractor may simply not know this shape."""
    _write(rooted, "svc", "openapi/svc.yaml", "openapi: 3.0.0\ninfo: {}\n")
    surface, status, detail = coverage_gaps.harvest(
        _repo(contract="openapi/svc.yaml"))
    assert surface == [] and status == "empty"
    assert coverage_gaps.observed({"status": status})
    assert "recognizes" in detail


def test_a_harvestable_artifact_is_unchanged(rooted):
    _write(rooted, "svc", "openapi/svc.yaml",
           "paths:\n  /v1/a:\n    get: {}\n  /v1/b:\n    get: {}\n")
    surface, status, _ = coverage_gaps.harvest(_repo(contract="openapi/svc.yaml"))
    assert surface == ["/v1/a", "/v1/b"] and status == "harvested"
    assert coverage_gaps.harvest_surface(
        _repo(contract="openapi/svc.yaml")) == surface, \
        "the back-compat wrapper drifted from harvest()"


def test_a_frontend_route_table_takes_the_route_table_key(rooted):
    _write(rooted, "ui", "src/routes.tsx", "path: '/checkout'\npath: '/cart'\n")
    surface, status, _ = coverage_gaps.harvest(
        _repo("ui", "frontend", route_table="src/routes.tsx"))
    assert surface == ["/cart", "/checkout"] and status == "harvested"
    # ...and an absent one names a route table, not a contract.
    _, status2, detail2 = coverage_gaps.harvest(
        _repo("ui2", "frontend", route_table="src/router/index.ts"))
    assert status2 == "unreadable" and "route table" in detail2


# ------------------------------------------------------- compute / rendering

def test_compute_keeps_the_repo_it_could_not_harvest(rooted, monkeypatch):
    """The actual defect: `continue` deleted the evidence that a question was
    even askable."""
    monkeypatch.setattr(coverage_gaps, "load_registry", lambda: {
        "source_repositories": [_repo("gone", contract="openapi/gone.yaml")]})
    monkeypatch.setattr(coverage_gaps, "catalog_evidence", dict)
    out = coverage_gaps.compute()
    assert "gone" in out, "an unharvestable repo vanished from the report"
    assert out["gone"]["status"] == "unreadable"
    assert out["gone"]["uncovered"] == []


def test_the_report_names_unchecked_repos_and_does_not_call_them_gaps(
        rooted, monkeypatch):
    _write(rooted, "seen", "openapi/seen.yaml", "paths:\n  /v1/seen:\n    get: {}\n")
    monkeypatch.setattr(coverage_gaps, "load_registry", lambda: {
        "source_repositories": [_repo("seen", contract="openapi/seen.yaml"),
                                _repo("gone", contract="openapi/gone.yaml")]})
    monkeypatch.setattr(coverage_gaps, "catalog_evidence", dict)
    md = coverage_gaps.to_markdown()

    assert "NOT checked" in md
    assert "gone" in md, "the unchecked repo is still invisible"
    assert "known to be gap-free" in md

    # It must NOT be dressed as a coverage gap: the file steers authoring
    # phases, and inventing a gap is as wrong as hiding one.
    unchecked = md[md.index("## Repos whose surface was NOT checked"):]
    assert "[NO TEST]" not in unchecked
    assert "prioritize a scenario here" not in unchecked
    # The observed repo still renders exactly as before.
    assert "- [NO TEST] (risk 1) /v1/seen" in md


def test_no_unchecked_section_when_everything_was_harvested(rooted, monkeypatch):
    """A clean estate must not grow a scary empty heading."""
    _write(rooted, "seen", "openapi/seen.yaml", "paths:\n  /v1/seen:\n    get: {}\n")
    monkeypatch.setattr(coverage_gaps, "load_registry", lambda: {
        "source_repositories": [_repo("seen", contract="openapi/seen.yaml")]})
    monkeypatch.setattr(coverage_gaps, "catalog_evidence", dict)
    assert "NOT checked" not in coverage_gaps.to_markdown()


# ------------------------------------------------------------- the invariant

# gen_agents_md is exempt WITH the evidence, not by assertion: it runs its own
# harvest() and already prints "contract `x` not available locally" for the
# unharvestable case (AGENTS.md lines 27 and 32 on this estate). Its only use of
# compute() is `gaps.get(name, {}).get("uncovered", [])` to mark [NO TEST] on
# surface it harvested ITSELF, so an unobserved repo contributes an empty set
# to a list that is empty anyway.
_EXEMPT = {"bin/gen_agents_md.py": "has its own harvest and its own honest "
                                   "not-available-locally branch"}


def test_every_consumer_of_compute_honours_observed():
    """The class, not the instance. Any module summing, counting or ranking
    compute()'s output has to ask whether the repo was LOOKED AT — otherwise
    the next consumer folds "unknown" into a total as a 0, which is the exact
    defect this file exists for and is now EASIER to write, because compute()
    returns those repos instead of hiding them.
    """
    offenders = []
    for path in list((ROOT / "engine/lib").rglob("*.py")) + \
            list((ROOT / "bin").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        src = path.read_text(encoding="utf-8", errors="ignore")
        if not re.search(r"coverage_gaps\.compute\(", src):
            continue
        if rel in _EXEMPT:
            continue
        if "coverage_gaps.observed(" not in src:
            offenders.append(rel)
    assert not offenders, (
        "these read coverage_gaps.compute() without asking whether each repo "
        f"was observed, so an unchecked repo counts as zero gaps: {offenders}")


def test_the_team_report_says_which_repos_its_gap_count_excludes():
    """The invariant pin above is TEXTUAL, and a mutation proved its limit: it
    catches a module that never asks `observed()`, not one that asks somewhere
    and ignores the answer where it counts. So the rendering gets its own
    behavioural pin.

    A team report is pasted into a status update, where "3 uncovered surfaces"
    travels as the size of the problem. If two repos were never looked at, that
    number is a floor, and the sentence has to say so.
    """
    import team_report
    stub = {"seen": {"kind": "endpoints", "uncovered": ["/a", "/b"],
                     "covered": [], "surface": ["/a", "/b"],
                     "uncovered_ranked": [], "status": "harvested", "detail": ""},
            "gone": {"kind": "endpoints", "uncovered": [], "covered": [],
                     "surface": [], "uncovered_ranked": [],
                     "status": "unreadable", "detail": "not available locally"}}
    real = coverage_gaps.compute
    try:
        coverage_gaps.compute = lambda *a, **k: stub
        md = team_report.to_markdown(7)
    finally:
        coverage_gaps.compute = real
    health = md[md.index("## Estate health"):]
    assert "**2** uncovered surface(s)" in health
    assert "NOT checked" in health and "gone" in health, \
        "the report gives one number and never says what it excludes"
    assert "seen" not in health.split("NOT checked")[1].split("\n")[0], \
        "an observed repo was listed as unchecked"


def test_the_gap_total_counts_only_repos_that_were_looked_at():
    """Pins the RULE, not today's arithmetic. compute() cannot currently emit an
    unobserved repo carrying `uncovered` entries — which is exactly why summing
    every value happens to give the right answer today, and why a future change
    (carrying the last known surface through an outage, say) would silently
    turn stale data into a current measurement."""
    import team_report
    stub = {"seen": {"uncovered": ["/a"], "status": "harvested"},
            "stale": {"uncovered": ["/x", "/y", "/z"], "status": "unreadable"}}
    real = coverage_gaps.compute
    try:
        coverage_gaps.compute = lambda *a, **k: stub
        d = team_report.build(7)
    finally:
        coverage_gaps.compute = real
    assert d["catalog"]["coverage_gaps"] == 1, \
        "surface from a repo nobody could look at was counted as measured"
    assert d["catalog"]["coverage_unchecked"] == ["stale"]


def test_the_exemption_list_still_describes_a_real_file():
    """An allow-list is a silencing mechanism. A renamed file would silently
    exempt nothing — or worse, keep exempting a name that no longer exists
    while the real consumer goes unchecked."""
    for rel in _EXEMPT:
        assert (ROOT / rel).exists(), f"exempted a file that is gone: {rel}"

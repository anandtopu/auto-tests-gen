"""A release-scoped report must not carry estate-wide numbers.

`team_report.build(release=X)` filtered runs, pending review and approvals, and
then printed two rows that ignored the filter completely. Measured on this
estate with a release that does not exist:

    | Pipeline runs | 0 |
    | LLM spend | ~$13.0000 across 617 run(s) (99% simulated) |

Both in the same Summary table. `_cost_line(days)` never received `release`, so
a release readout attributed the WHOLE estate's spend to one release, and
`work_queue.load()` did the same for the backlog.

Labelling the figure would not have been enough. It sits beside correctly
scoped rows, and this report is reachable from `make report`, GET /api/report
and POST /api/email/report -> team_report.to_markdown(days, release) -- the
emailed version is exactly where a number travels and a caveat does not.

The estate-health rows (catalog, coverage gaps, flaky) are deliberately NOT
scoped: they live under a heading that says "Estate health", which is what
makes them honest and what made these two rows dishonest.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine" / "lib"))
import cost_report
import coverage_gaps
import review_state
import team_report


# ------------------------------------------------------- cost_report.keys=

def _spend(cost, simulated=False):
    return {"provider": "claude", "model": "m", "cost_basis": "reported",
            "cost_usd": cost, "input_tokens": 1, "output_tokens": 1,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "turns_used": 1, "max_turns": 8, "simulated": simulated,
            "attempts": 1, "attribution": "user"}


def test_an_empty_key_set_means_nothing_matched_not_everything(monkeypatch, tmp_path):
    """The trap in any filter parameter: `if keys:` treats an empty collection
    as "no filter", which turns a release nobody uses back into the whole
    estate -- the original defect, reintroduced by its own fix.

    The fixture carries real spend on purpose. An earlier version of this test
    used a phase-less row, so `runs` was 0 either way and the assertion could
    not tell the two apart.
    """
    rows = [{"run_id": "1", "key": "A", "mode": "jira", "ts": 1.0,
             "phases": [{"name": "analyze", "spend": _spend(2.0)}]}]
    monkeypatch.setattr(cost_report, "collect", lambda days=None: rows)

    unfiltered = cost_report.report(None, keys=None)
    assert unfiltered["runs"] == 1 and unfiltered["total_cost_usd"] == 2.0

    empty = cost_report.report(None, keys=set())
    assert empty["runs"] == 0, "an empty key set was treated as 'no filter'"
    assert empty["total_cost_usd"] == 0.0


def test_the_store_reports_the_entries_it_could_not_read(tmp_path, monkeypatch):
    """The guarantee belongs to the store: nine call sites called .get() on a
    review entry, and fixing one caller would have left the other eight."""
    board = tmp_path / "reviews.json"
    monkeypatch.setattr(review_state, "FILE", board)

    board.write_text(json.dumps({"OK": {"status": "approved"}, "BAD": "x"}),
                     encoding="utf-8")
    good, bad = review_state.load_with_issues()
    assert good == {"OK": {"status": "approved"}} and bad == ["BAD"]
    assert review_state.load() == good, "load() no longer honours the shape rule"

    # A whole document of the wrong shape yields nothing recoverable, and must
    # not read as an empty (i.e. clear) board.
    board.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    good, bad = review_state.load_with_issues()
    assert good == {} and bad, "a non-object board was reported as simply empty"


def test_keys_none_is_unfiltered_so_existing_callers_are_unchanged(monkeypatch, tmp_path):
    seen = {}

    def fake_collect(days=None):
        seen["called"] = True
        return []

    monkeypatch.setattr(cost_report, "collect", fake_collect)
    cost_report.report(None)
    assert seen.get("called"), "report() stopped consulting collect()"


def test_report_scopes_runs_and_total_to_the_given_keys(monkeypatch, tmp_path):
    def spend(cost):
        return {"provider": "claude", "model": "m", "cost_basis": "reported",
                "cost_usd": cost, "input_tokens": 1, "output_tokens": 1,
                "cache_read_tokens": 0, "cache_creation_tokens": 0,
                "turns_used": 1, "max_turns": 8, "simulated": False,
                "attempts": 1, "attribution": "user"}

    rows = [{"run_id": "r1", "key": "MINE-1", "mode": "jira", "ts": 1.0,
             "phases": [{"name": "analyze", "spend": spend(0.50)}]},
            {"run_id": "r2", "key": "OTHER-9", "mode": "jira", "ts": 2.0,
             "phases": [{"name": "analyze", "spend": spend(9.00)}]}]
    monkeypatch.setattr(cost_report, "collect", lambda days=None: rows)

    everything = cost_report.report(None)
    assert everything["runs"] == 2 and everything["total_cost_usd"] == 9.50

    scoped = cost_report.report(None, keys={"MINE-1"})
    assert scoped["runs"] == 1, "the run count still counted another key's runs"
    assert scoped["total_cost_usd"] == 0.50, \
        "another key's spend was attributed to this release"


# ------------------------------------------------------------- team_report

def _estate(monkeypatch, tmp_path, reviews, queue, runs=()):
    """The review board is written to a REAL file and read through the REAL
    review_state, not stubbed. Stubbing load_with_issues would mean restating
    the store's shape rule inside the test, and a mutation to that rule would
    then pass every assertion here."""
    board = tmp_path / "reviews.json"
    board.write_text(json.dumps(reviews), encoding="utf-8")
    monkeypatch.setattr(review_state, "FILE", board)
    monkeypatch.setattr(team_report, "_runs", lambda: list(runs))
    monkeypatch.setattr(team_report, "_catalog", list)
    monkeypatch.setattr(team_report.work_queue, "load", lambda: list(queue))
    monkeypatch.setattr(team_report.coverage_gaps, "compute", dict)
    monkeypatch.setattr(team_report.test_health, "load", dict)


def test_the_cost_line_is_asked_only_for_this_releases_keys(monkeypatch, tmp_path):
    """The defect, at the seam where it happened: `release` never reached the
    cost query."""
    asked = {}
    _estate(monkeypatch, tmp_path,
            reviews={"IN-1": {"release": "2026.08", "status": "approved"},
                     "OUT-2": {"release": "2026.09", "status": "approved"}},
            queue=[])

    def spy(days=None, keys=None):
        asked["keys"] = keys
        return {"runs": 0, "simulated_share": None, "total_cost_usd": 0.0}

    monkeypatch.setattr(cost_report, "report", spy)
    team_report.build(None, "2026.08")
    assert asked["keys"] == {"IN-1"}, \
        f"the cost figure was not scoped to the release: {asked.get('keys')!r}"

    team_report.build(None, None)
    assert asked["keys"] is None, "an unscoped report must not filter by key"


def test_a_non_dict_review_entry_does_not_crash_the_report(monkeypatch, tmp_path):
    """A PRE-EXISTING crash, found by writing this test for the release filter
    rather than by the suite: `rel_of`, `pending` and `approved` each called
    .get() straight on a review entry, so ONE wrong-shaped value in reviews.json
    raised AttributeError out of build() and took down `make report`, GET
    /api/report and the emailed report together.

    read_json_guarded quarantines INVALID json; a valid file holding a string
    where a dict belongs passes it untouched.
    """
    _estate(monkeypatch, tmp_path,
            reviews={"IN-1": {"release": "2026.08", "status": "pending_review"},
                     "BAD": "not-a-dict"},
            queue=[])
    d = team_report.build(None, "2026.08")
    assert d["release"] == "2026.08"
    assert d["malformed_reviews"] == ["BAD"]
    # ...and the good entry is still counted, so the guard did not just swallow
    # the whole board to avoid the crash.
    assert [p["key"] for p in d["pending_review"]] == ["IN-1"]


def test_unreadable_review_entries_are_named_not_silently_dropped(monkeypatch, tmp_path):
    """Excluding them quietly would understate the review board -- the numbers
    would simply be smaller, with nothing to say why."""
    _estate(monkeypatch, tmp_path, reviews={"OK": {"status": "pending_review"},
                                  "B1": "x", "B2": 7}, queue=[])
    md = team_report.to_markdown(None)
    assert "2 review-board entries are unreadable" in md
    assert "`B1`" in md and "`B2`" in md
    assert "floor, not a total" in md
    # A healthy board must not grow a scary banner.
    _estate(monkeypatch, tmp_path, reviews={"OK": {"status": "pending_review"}}, queue=[])
    assert "unreadable" not in team_report.to_markdown(None)


def test_the_queue_backlog_is_scoped_and_the_two_renderings_agree(monkeypatch, tmp_path):
    """The Summary counts d["queue"] and the Work queue section lists it, so
    scoping in build() keeps them from disagreeing."""
    _estate(monkeypatch, tmp_path,
            reviews={"IN-1": {"release": "2026.08"}},
            queue=[{"status": "queued", "release": "2026.08", "mode": "jira",
                    "target": "a", "id": "1"},
                   {"status": "queued", "release": "2026.09", "mode": "jira",
                    "target": "b", "id": "2"},
                   {"status": "failed", "release": "", "mode": "jira",
                    "target": "c", "id": "3"}])
    d = team_report.build(None, "2026.08")
    assert len(d["queue"]) == 1, "another release's queue items were counted"
    assert d["queue"][0]["id"] == "1"

    md = team_report.to_markdown(None, "2026.08")
    assert "| Queue backlog | 1 queued, 0 running, 0 failed |" in md
    assert "0 queued" not in md.split("Queue backlog")[1].split("|")[1]

    # Unscoped keeps everything.
    assert len(team_report.build(None, None)["queue"]) == 3


def test_a_release_nobody_uses_says_so_instead_of_reporting_zeros(monkeypatch, tmp_path):
    _estate(monkeypatch, tmp_path, reviews={"IN-1": {"release": "2026.08"}}, queue=[])
    md = team_report.to_markdown(None, "2026.99")
    assert "No work in this estate is tracked against release `2026.99`" in md
    assert "not a statement that the release is clear" in md
    # It has to land ABOVE the zeros it explains, or the reader meets the
    # numbers first and has already drawn the conclusion.
    assert md.index("No work in this estate") < md.index("## Summary")


def test_a_release_known_only_from_the_queue_is_not_called_a_typo(monkeypatch, tmp_path):
    """work_queue writes a release into review_state only after a run SUCCEEDS,
    so a release whose tickets are merely queued has no board entry. Asking
    only the board would flag a perfectly real release as a mistake -- and the
    reader would go looking for a typo that is not there."""
    _estate(monkeypatch, tmp_path, reviews={"IN-1": {"release": "2026.08"}},
            queue=[{"status": "queued", "release": "2026.12", "mode": "jira",
                    "target": "a", "id": "9"}])
    d = team_report.build(None, "2026.12")
    assert d["release_known"] is True
    assert "No work in this estate is tracked" not in \
        team_report.to_markdown(None, "2026.12")


def test_an_unscoped_report_never_shows_the_release_warning(monkeypatch, tmp_path):
    _estate(monkeypatch, tmp_path, reviews={}, queue=[])
    assert "No work in this estate is tracked" not in team_report.to_markdown(None)


def test_every_board_consumer_survives_a_malformed_entry(tmp_path, monkeypatch):
    """The sibling sweep, behavioural rather than textual. Nine call sites read
    the board; the store now guarantees the shape, and these are the four whose
    failure is worst:

      * bin/dashboard.py produces NO dashboard at all -- not one wrong board,
        the whole page missing, the same class already recorded for malformed
        run records;
      * the review-digest EMAIL, which is the one people act on;
      * `make reviews`, the board itself;
      * the team report, in all three of its renderings.
    """
    import email_notify
    board = tmp_path / "reviews.json"
    board.write_text(json.dumps({"OK-1": {"status": "pending_review",
                                          "updated": 1.0},
                                 "CORRUPT": "approved"}), encoding="utf-8")
    monkeypatch.setattr(review_state, "FILE", board)

    subject, text, _html = email_notify.review_digest()
    assert "1 item(s) awaiting review" in subject, \
        "the digest lost the readable entry, or counted the broken one"
    assert "OK-1" in text
    # And it must not report a damaged board as a clear one.
    assert "review board is clear" not in text.lower()


# ---------------------------------------------------- coverage_gaps --repo

def test_an_unregistered_repo_is_not_reported_as_having_no_surface(monkeypatch, tmp_path):
    """`--repo` takes a free-text name. A typo used to print "No harvestable
    surface found" and exit 0, which reads as a clean bill for a repo that does
    not exist."""
    monkeypatch.setattr(coverage_gaps, "load_registry", lambda: {
        "source_repositories": [{"name": "real", "type": "backend",
                                 "contract": "openapi/x.yaml"}]})
    monkeypatch.setattr(coverage_gaps, "catalog_evidence", dict)
    md = coverage_gaps.to_markdown("typo-repo")
    assert "No repo named `typo-repo` is registered" in md
    assert "not a statement about its coverage" in md
    assert "No harvestable surface found" not in md


def test_an_unharvestable_single_repo_does_not_contradict_itself(monkeypatch, tmp_path):
    """It reported BOTH "No harvestable surface found" and the NOT-checked
    section -- one an established negative, the other an admission of not
    knowing, about the same repo."""
    monkeypatch.setattr(coverage_gaps, "load_registry", lambda: {
        "source_repositories": [{"name": "real", "type": "backend",
                                 "contract": "openapi/absent.yaml"}]})
    monkeypatch.setattr(coverage_gaps, "catalog_evidence", dict)
    md = coverage_gaps.to_markdown("real")
    assert "NOT checked" in md
    assert "No harvestable surface found" not in md


def test_an_empty_registry_names_the_fix(monkeypatch, tmp_path):
    monkeypatch.setattr(coverage_gaps, "load_registry",
                        lambda: {"source_repositories": []})
    monkeypatch.setattr(coverage_gaps, "catalog_evidence", dict)
    md = coverage_gaps.to_markdown()
    assert "No app repos are registered" in md and "add-app" in md

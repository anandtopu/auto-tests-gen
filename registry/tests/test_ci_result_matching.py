"""A CI result is attributed to a test only on real evidence.

Found by POSTing a throwaway JUnit case to the running receiver during
exploratory testing:

    <testsuite name="s"><testcase classname="c" name="t"/></testsuite>
    -> {"ok": true, "matched": 1, "unmatched": 0}

A case literally named "t" was attributed to the cataloged test
"PROJ-88: applies % discount", and the platform wrote a pass-rate against it.
The rule was `t in name or name in t` — a two-way substring test with no length
floor — and the letter t occurs in "discount".

Why it matters more than a wrong count: `catalog/health.json` feeds
`qa.py flaky`, the quarantine workflow, the catalog index and the dashboard's
test-health figure. A false attribution gives a real test a fabricated
pass-rate, and a quarantine decision made on it takes a healthy test out of
CI — or leaves a genuinely flaky one in. Nothing downstream can tell, because
the corrupted value is indistinguishable from a measured one.

Three rules, each pinned below: exact wins; the TITLE may appear inside the CI
name but never the reverse; a substring title must be distinctive AND unique,
and an ambiguous match is reported unmatched rather than guessed.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import test_health as th  # noqa: E402

TITLES = {
    "PROJ-88: applies % discount": "TID-88",
    "PROJ-61: gets an order by id": "TID-61",
    "ab": "TID-short",
}


@pytest.mark.parametrize("name,expected,why", [
    ("t", None, "the one-character name that started this"),
    ("x", None, "any short name matches something under a substring rule"),
    ("", None, "empty case name"),
    ("a very long unrelated ci test name here", None, "no shared title"),
    ("xx ab yy", None, "a SHORT title as a substring is not evidence"),
    ("PROJ-88: applies % discount", "TID-88", "exact title"),
    ("orders > PROJ-88: applies % discount", "TID-88", "CI suite prefix — the real shape"),
    ("ab", "TID-short", "an EXACT match wins regardless of length"),
])
def test_match_case(name, expected, why):
    assert th.match_case(name, TITLES) == expected, why


def test_a_ci_name_that_is_a_fragment_of_a_title_never_matches():
    """The reverse direction. A CI case called "applies" is LESS specific than
    "PROJ-88: applies % discount"; being a fragment is not identity."""
    assert th.match_case("applies", TITLES) is None
    assert th.match_case("discount", TITLES) is None


def test_an_ambiguous_substring_is_unmatched_not_guessed():
    titles = {"checkout flow works": "A", "checkout flow works end to end": "B"}
    assert th.match_case("suite: checkout flow works end to end", titles) is None, (
        "two candidate tests matched and one was picked — guessing here "
        "quarantines the wrong test")


def test_the_length_floor_exists_and_is_not_zero():
    assert getattr(th, "MIN_SUBSTRING_TITLE", 0) >= 4, (
        "the substring floor is gone or trivial; any short catalog title would "
        "swallow unrelated CI cases again")


def test_ingest_counts_an_unmatchable_case_as_unmatched(tmp_path, monkeypatch):
    """End to end through ingest(): the bogus case must land in `unmatched`,
    and health.json must stay empty rather than gaining a fabricated entry."""
    junit = tmp_path / "r.xml"
    junit.write_text('<testsuite name="s"><testcase classname="c" name="t"/></testsuite>',
                     encoding="utf-8")
    health = tmp_path / "health.json"
    monkeypatch.setattr(th, "FILE", health)
    monkeypatch.setattr(th, "catalog_titles", lambda: dict(TITLES))
    matched, unmatched = th.ingest(junit)
    assert (matched, unmatched) == (0, 1), (
        f"expected the junk case to be unmatched, got matched={matched}")
    assert json.loads(health.read_text(encoding="utf-8")) == {}, \
        "a fabricated health entry was written for a test that never ran"


def test_ingest_still_records_a_genuine_match(tmp_path, monkeypatch):
    """The other direction — a matcher that refuses everything would satisfy
    every test above and silently disable CI health tracking."""
    junit = tmp_path / "r.xml"
    junit.write_text(
        '<testsuite name="s"><testcase classname="orders" '
        'name="orders &gt; PROJ-88: applies % discount"/></testsuite>', encoding="utf-8")
    health = tmp_path / "health.json"
    monkeypatch.setattr(th, "FILE", health)
    monkeypatch.setattr(th, "catalog_titles", lambda: dict(TITLES))
    matched, unmatched = th.ingest(junit)
    assert (matched, unmatched) == (1, 0), "a real CI result stopped being tracked"
    rec = json.loads(health.read_text(encoding="utf-8"))["TID-88"]
    assert rec["runs"] == 1 and rec["last_status"] == "passed"

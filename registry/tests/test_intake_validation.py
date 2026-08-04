"""Intake refuses what it can already tell is wrong.

`work_queue.add` carries the rule in its own comment — "Validate at INTAKE, not
minutes later in a background runner nobody watches" — and applies it to
`target`, which is checked against the registry. `pr` was not checked at all, so
`-1`, `0` and a 200-digit string all queued 200 OK from the API.

That is not cosmetic. The key becomes `PR-<repo>-<pr>`, which passes the
pipeline's charset check (digits and `-` are legal), so the run STARTS, clones,
and dies at the SCM call with whatever the vendor says about a pull request that
cannot exist — minutes later, in a background process whose console nobody
reads, for input that was wrong the moment it was typed.

Found by exploratory probing of the live API, not by reading the code.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import work_queue  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Patch the module ATTRIBUTE, never reload the module.

    `work_queue.FILE` is captured at import, so the obvious isolation —
    setenv + importlib.reload — works during the test and then leaks: the
    teardown reload happens while monkeypatch's env override is STILL in place
    (monkeypatch finalises after the fixture that requested it), so the module
    is left pointing at a temp file that is about to be deleted. Every later
    test in the session then reads an empty queue. It cost a green suite:
    test_ui_features::test_queue_endpoint_accepts_plan_mode failed with "no
    such queue item" while passing in isolation.
    """
    monkeypatch.setattr(work_queue, "FILE", tmp_path / "queue.json")


@pytest.mark.parametrize("pr", ["-1", "0", "abc", "1.5", "2 01", "9" * 200, " ", "1e5"])
def test_a_non_pr_number_is_refused_at_intake(pr):
    with pytest.raises(SystemExit) as e:
        work_queue.add("pr", "orders-api", pr=pr)
    msg = str(e.value)
    assert "pull-request number" in msg, msg
    # Name the alternative, not just the rejection.
    assert "PR URL" in msg or "numbered from 1" in msg


@pytest.mark.parametrize("pr", ["1", "201", "999999999"])
def test_a_real_pr_number_is_accepted(pr):
    """The guard must not be so tight it refuses the domain it is protecting —
    every SCM here numbers PRs from 1, and pr_url.py parses `num` as digits."""
    item, queued = work_queue.add("pr", "orders-api", pr=pr)
    assert queued and item.get("pr") == pr


def test_the_refusal_happens_before_anything_is_queued():
    """A refusal that still leaves an item behind is worse than none: the user
    sees an error AND a queued run."""
    before = len(work_queue.load())
    with pytest.raises(SystemExit):
        work_queue.add("pr", "orders-api", pr="-1")
    assert len(work_queue.load()) == before


def test_ticket_keys_are_unaffected():
    """jira/plan/tests modes carry a key, not a PR number — the new guard must
    not reach them."""
    item, queued = work_queue.add("jira", "PROJ-301")
    assert queued and item.get("target") == "PROJ-301"

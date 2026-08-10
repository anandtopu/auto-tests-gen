"""The rich ticket comment degrades to the plain one, and says when it did.

Coverage put engine/lib/ticket_comment_render.py at 47.6%: the three body
builders were entirely uncovered. They are best-effort by design -- this text
is posted onto somebody's Jira ticket, so a rendering bug must never cost the
comment itself -- which means the FALLBACK paths are the ones worth holding,
and they were the untested half.

Exercised before pinning: a raising renderer returns the fallback and prints a
degradation line naming the exception class; an empty rich body falls back
rather than replacing usable text with nothing; the flag off returns the
fallback silently, which is right because "off" is a choice and not a
degradation.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import pr_comment  # noqa: E402
import ticket_comment_render as tcr  # noqa: E402

FALLBACK = "PLAIN FALLBACK BODY"


@pytest.fixture()
def rich_on(monkeypatch):
    monkeypatch.setenv("AIQE_TICKET_COMMENTS_RICH", "1")


def test_the_flag_off_returns_the_plain_body_without_complaining(monkeypatch, capsys):
    """Off is a configuration choice; announcing it every run would be noise
    that teaches people to ignore the line that DOES matter."""
    monkeypatch.delenv("AIQE_TICKET_COMMENTS_RICH", raising=False)
    assert tcr.delivery_body("r1", "PROJ-1", "orders-api", FALLBACK) == FALLBACK
    assert "degraded" not in capsys.readouterr().err


def test_a_renderer_that_raises_never_costs_the_comment(rich_on, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(pr_comment, "build_ticket", boom)
    assert tcr.delivery_body("r1", "PROJ-1", "orders-api", FALLBACK) == FALLBACK
    err = capsys.readouterr().err
    assert "degraded" in err, "the fallback happened silently"
    assert "RuntimeError" in err, (
        "the message does not name what failed -- a reader cannot tell a bug "
        "from a missing run record")


def test_an_empty_rich_body_does_not_replace_a_usable_one(rich_on, monkeypatch):
    """`or fallback`, not a bare return: posting an empty comment is worse
    than posting the plain one."""
    monkeypatch.setattr(pr_comment, "build_ticket", lambda *a, **k: "")
    assert tcr.delivery_body("r1", "PROJ-1", "orders-api", FALLBACK) == FALLBACK


def test_the_rich_body_is_used_when_it_renders(rich_on, monkeypatch):
    """The control. Always returning the fallback would satisfy every test
    above while making the whole feature a no-op."""
    monkeypatch.setattr(pr_comment, "build_ticket", lambda *a, **k: "RICH BODY")
    assert tcr.delivery_body("r1", "PROJ-1", "orders-api", FALLBACK) == "RICH BODY"


def test_the_refusal_body_carries_the_reason_and_the_fix(rich_on):
    """The refusal comment is the one a reader most needs: it says why the run
    was rejected and what to do about it.

    It does NOT go through pr_comment.build_ticket -- it renders its own body.
    My first version of this test assumed it did, patched that function, and
    therefore asserted nothing about the path it claimed to cover.
    """
    out = tcr.refusal_body("r1", "PROJ-1", "orders-api", FALLBACK,
                           "review refused", "fix the findings")
    assert out != FALLBACK, "the refusal fell back instead of rendering"
    assert "review refused" in out, "the reason is missing from the comment"
    assert "fix the findings" in out, "the fix is missing from the comment"


def test_the_refusal_body_respects_the_flag(monkeypatch):
    """Control: with the feature off it must still be the plain body, or the
    flag does not mean anything."""
    monkeypatch.delenv("AIQE_TICKET_COMMENTS_RICH", raising=False)
    assert tcr.refusal_body("r1", "PROJ-1", "orders-api", FALLBACK,
                            "review refused", "fix the findings") == FALLBACK


def test_an_unreadable_org_config_falls_back_to_the_default_limit(tmp_path):
    """max_chars reads config on a posting path; a broken file must not take
    the comment down with it."""
    bad = tmp_path / "org.yaml"
    bad.write_text("comments: [this is not a mapping\n", encoding="utf-8")
    assert tcr.max_chars(str(bad)) == tcr.DEFAULT_MAX_CHARS
    assert tcr.max_chars(str(tmp_path / "missing.yaml")) == tcr.DEFAULT_MAX_CHARS

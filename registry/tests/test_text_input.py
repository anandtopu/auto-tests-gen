"""`text_input.resolve` -- one definition of "inline or in a file".

The states it must keep apart are the C13 states: nothing supplied, both
supplied, a file that could not be read, and a file that was read and was
empty. Collapsing any pair of them sends the reader somewhere that is not the
problem, and collapsing "both supplied" into "use the inline one" is the defect
this module was extracted for.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import text_input                                                # noqa: E402


def test_inline_text_is_returned_verbatim():
    assert text_input.resolve("hello\nworld", None) == "hello\nworld"


def test_a_file_is_read(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("from the file", encoding="utf-8")
    assert text_input.resolve(None, str(f)) == "from the file"


def test_both_is_refused_rather_than_resolved_by_precedence():
    """The original defect. Picking a winner is what threw the file away."""
    with pytest.raises(text_input.TextInputError) as e:
        text_input.resolve("inline", "some.txt")
    assert "not both" in str(e.value)


def test_neither_names_both_ways_to_supply_it():
    with pytest.raises(text_input.TextInputError) as e:
        text_input.resolve(None, None)
    msg = str(e.value)
    assert "--file" in msg and "<text>" in msg, \
        "a refusal that names only one of the two forms sends half the users nowhere"


def test_a_missing_file_is_not_an_empty_one(tmp_path):
    with pytest.raises(text_input.TextInputError) as e:
        text_input.resolve(None, str(tmp_path / "nope.txt"))
    assert "no such file" in str(e.value)
    assert "empty" not in str(e.value)


def test_an_unreadable_file_says_so_rather_than_raising(tmp_path):
    """A directory where a file is expected: the portable way to provoke a
    read that fails on a path that EXISTS. The old code let the OSError escape
    as a traceback, which names no fix."""
    with pytest.raises(text_input.TextInputError) as e:
        text_input.resolve(None, str(tmp_path))
    assert str(tmp_path) in str(e.value)


def test_an_empty_file_is_refused_and_says_which_file(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("   \n\n", encoding="utf-8")
    with pytest.raises(text_input.TextInputError) as e:
        text_input.resolve(None, str(f))
    assert "empty" in str(e.value) and "empty.txt" in str(e.value)


def test_the_refusal_names_what_the_text_is_for():
    """A generic "no text supplied" from a CLI that takes two different kinds
    of text is a message the reader cannot act on."""
    with pytest.raises(text_input.TextInputError) as e:
        text_input.resolve(None, None, what="ticket context")
    assert "ticket context" in str(e.value)


def test_whitespace_only_inline_text_counts_as_absent():
    with pytest.raises(text_input.TextInputError):
        text_input.resolve("   ", None)


def test_every_refusal_is_console_safe():
    """This repo's console is cp1252 and its own rule forbids characters it
    cannot encode in operator-facing output (the cost_reconcile precedent)."""
    msgs = []
    for args in [(None, None), ("a", "b"), (None, "nope.txt"), ("  ", None)]:
        try:
            text_input.resolve(*args)
        except text_input.TextInputError as e:
            msgs.append(str(e))
    assert len(msgs) == 4
    for m in msgs:
        m.encode("cp1252")          # raises if a character cannot be rendered

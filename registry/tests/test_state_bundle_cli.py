"""The restore path answers to what an operator actually types.

Found by running it. `export` finishes by printing, in its own words:

    Import elsewhere with: python3 engine/lib/state_bundle.py import <name>.tar.gz

-- a BARE NAME. Neither `inspect` nor `import` resolved one, so following the
tool's own instruction from the repo root raised FileNotFoundError as an
unhandled traceback. On the restore path, which is used exactly when something
has already gone wrong and nobody is feeling patient.

Two halves, and the second is easy to stop short of: a bare name now resolves
against reports/exports/ (where export writes), AND a name that genuinely is
not there produces a NAMED error with a clean exit code rather than a stack
trace with one useful line in it.
"""
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import state_bundle  # noqa: E402


@pytest.fixture()
def estate(tmp_path, monkeypatch):
    """An isolated ROOT so nothing reads or writes the real exports."""
    (tmp_path / "reports/exports").mkdir(parents=True)
    monkeypatch.setattr(state_bundle, "ROOT", tmp_path)
    return tmp_path


def test_a_bare_name_resolves_against_the_export_directory(estate):
    b = estate / "reports/exports/20260101-000000-state.tar.gz"
    b.write_bytes(b"not really a tar, resolution does not read it")
    assert state_bundle.resolve_bundle(b.name) == b


def test_an_explicit_path_is_used_as_given(estate, tmp_path):
    """Resolution must not hijack a path the operator spelled out."""
    other = tmp_path / "elsewhere.tar.gz"
    other.write_bytes(b"x")
    assert state_bundle.resolve_bundle(str(other)) == pathlib.Path(str(other))


def test_a_missing_bundle_names_where_it_looked(estate):
    with pytest.raises(FileNotFoundError) as e:
        state_bundle.resolve_bundle("nope.tar.gz")
    msg = str(e.value)
    assert "BUNDLE_NOT_FOUND" in msg
    # The CANDIDATE PATH, not the words "reports"/"exports" -- those also occur
    # in the trailing `ls reports/exports/*.tar.gz` hint, so dropping the
    # looked-in list entirely still satisfied a substring check. Mutation found
    # that; assert the resolved path itself.
    candidate = str(estate / "reports/exports/nope.tar.gz")
    assert candidate in msg, \
        f"the message does not say it also looked in {candidate}"
    assert "ls reports/exports" in msg, "no way to find out what DOES exist"


def test_the_cli_exits_cleanly_rather_than_traceback(tmp_path):
    """A named message delivered as a traceback is still a traceback."""
    r = subprocess.run(
        [sys.executable, str(ROOT / "engine/lib/state_bundle.py"),
         "inspect", "definitely-not-here.tar.gz"],
        capture_output=True, text=True, cwd=str(tmp_path),
        stdin=subprocess.DEVNULL)
    assert r.returncode == 66, f"exited {r.returncode}"
    assert "BUNDLE_NOT_FOUND" in r.stderr
    assert "Traceback" not in r.stderr, "still raising instead of reporting"


def test_the_instruction_export_prints_is_the_one_that_works():
    """The pin that matters: export tells the operator what to run next, and
    that sentence must stay executable. It was not."""
    src = (ROOT / "engine/lib/state_bundle.py").read_text(encoding="utf-8")
    # Anchor on the print CALL, not the phrase: resolve_bundle's docstring
    # quotes the same sentence, and index() found the prose first -- the same
    # trap as a summary string containing "exit 124" satisfying an exit-code
    # check. Caught by this test failing on its own first run.
    i = src.index('print("Import elsewhere with:')
    printed = src[i:i + 260]
    assert "{out.name}" in printed, (
        "export no longer prints a bare name -- if it now prints a full path "
        "this test is stale, but check resolve_bundle is still reachable")
    # `out.name` is a bare filename, so resolution must handle bare filenames.
    assert "def resolve_bundle" in src, (
        "export prints a bare name and nothing resolves one -- following the "
        "printed instruction will raise FileNotFoundError again")
    for fn in ("def inspect(", "def import_bundle("):
        j = src.index(fn)
        # Scope to THIS function. A fixed 900-char window ran past the end of
        # `inspect` into `import_bundle`, whose own resolve_bundle() call
        # satisfied the check even with inspect's removed. Verified by mutation.
        nxt = src.find("\ndef ", j + 1)
        body = src[j:nxt if nxt != -1 else len(src)]
        assert "resolve_bundle(" in body, \
            f"{fn} does not resolve its argument -- a bare name will raise"

"""pr_url's CLI and describe(), which nothing exercised.

Coverage put engine/lib/pr_url.py at 57.4% with lines 72-77 and 81-91
uncovered -- `describe()` and `main()` entirely. `parse()` itself is well
covered, and it is the part that matters most, but describe() feeds error
hints an operator reads and main() is a documented command.

Driven by hand first: all three URL kinds parse, a scheme-less host works,
garbage exits 3 with a readable message, and no argument exits 64. These pin
that, so the next edit cannot quietly break the half nothing was watching.

Recorded while here, because it looks like a bug and is not: parse() accepts
`/pull/0`. It is a PARSER -- permissive by design, total on whatever a user
pastes -- and the domain rule lives at intake, where
`[1-9][0-9]{0,8}` refuses "0" with "PRs are numbered from 1". Verified end to
end rather than assumed; the first attempt tripped an EARLIER check ("pr mode
needs a PR number") and would have credited the wrong guard.
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
MOD = ROOT / "engine/lib/pr_url.py"
sys.path.insert(0, str(ROOT / "engine/lib"))
import pr_url  # noqa: E402

STASH = "https://stash.corp/projects/PAY/repos/orders-api/pull-requests/42"
BITBUCKET = "https://bitbucket.org/acme/orders-api/pull-requests/7"
GITHUB = "https://github.com/acme/orders-api/pull/99"


def _cli(*args):
    return subprocess.run([sys.executable, str(MOD), *args],
                          capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, cwd=str(ROOT))


def test_the_cli_prints_json_for_each_supported_host():
    for url, kind in ((STASH, "stash"), (BITBUCKET, "bitbucket"), (GITHUB, "github")):
        r = _cli(url)
        assert r.returncode == 0, f"{kind}: exit {r.returncode}"
        got = json.loads(r.stdout)
        assert got["kind"] == kind
        assert got["slug"] == "orders-api"


def test_an_unrecognised_string_exits_3_and_says_what_is_supported():
    r = _cli("not a url at all")
    assert r.returncode == 3
    for host in ("Stash", "Bitbucket", "GitHub"):
        assert host in r.stderr, f"the error does not mention {host}"


def test_no_argument_is_a_usage_error_not_a_crash():
    r = _cli()
    assert r.returncode == 64, f"exit {r.returncode}"
    assert "usage" in r.stderr.lower()
    assert "Traceback" not in r.stderr


def test_describe_names_the_right_container_for_each_host():
    """Stash calls it a project, Bitbucket a workspace, GitHub an owner --
    an error hint that uses the wrong word sends the reader to the wrong
    field of the wrong UI."""
    assert "project PAY" in pr_url.describe(STASH)
    assert "workspace acme" in pr_url.describe(BITBUCKET)
    assert "owner acme" in pr_url.describe(GITHUB)


def test_describe_is_empty_for_an_unparseable_url():
    """It feeds a hint; returning a half-sentence about None would be worse
    than saying nothing."""
    assert pr_url.describe("nonsense") == ""
    assert pr_url.describe("") == ""


def test_parse_is_total_on_whatever_a_user_pastes():
    """It runs on the contents of a text box, so it returns None rather than
    raising -- including on the types a form can hand it."""
    for junk in ("", "   ", "http://", "https://///", None, 42, "://broken"):
        assert pr_url.parse(junk) is None, f"{junk!r} did not return None"

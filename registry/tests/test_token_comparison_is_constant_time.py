"""The careful primitive guarded the values an attacker cannot submit.

FOUND BY ASKING which comparisons this codebase treats as sensitive. It reaches
for `hmac.compare_digest` in three places - `plan_state.revision`, `spec_sha`
and `requirements_sha` - and every one compares an INTERNAL INTEGRITY HASH
computed from bytes already on disk, which no attacker can influence directly.

The two HTTP servers compared their AUTHENTICATION TOKENS with `==`, at five
sites:

    bin/taskevent_receiver.py   X-AIQE-Token, Authorization: Bearer
    bin/dashboard_server.py     ?token=, Authorization: Bearer (x2), cookie

`str.__eq__` returns at the first differing byte, so the comparison time is a
function of how many leading characters are correct.

WHAT IS AND IS NOT CLAIMED, because overstating it would be its own dishonesty.
No timing attack was demonstrated, and a remote one against a Python HTTP
handler is genuinely hard - request handling dominates the comparison by orders
of magnitude. What IS true: both servers bind 0.0.0.0 in the deployed shape
(the Dockerfile and the OpenShift ConfigMap), the fix costs nothing, and the
weaker primitive sitting on the more exposed value reads as a decision when it
was an oversight.

ALSO NOT CLAIMED: that the cookie check was broken. The old form was a
membership test over `;`-split elements, so `aiqe_token=<token>EXTRA` never
matched - driven and confirmed 401 both before and after. The change there is
constant time, not a fix.

TWO DESIGN CHOICES, both pinned:
  * an EMPTY expected token never matches, so a caller that forgets its
    "auth is off" guard cannot silently authenticate everyone;
  * comparison is on BYTES, because `compare_digest` raises TypeError on `str`
    holding non-ASCII - a header with one such character would otherwise turn a
    bad token into an exception inside the auth check.
"""
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import token_auth                                         # noqa: E402


def test_a_correct_token_matches():
    assert token_auth.matches("s3cret", "s3cret") is True


@pytest.mark.parametrize("presented", ["", "s3cre", "s3cret ", "S3CRET",
                                       "s3cretX", "wrong"])
def test_a_wrong_token_never_matches(presented):
    assert token_auth.matches(presented, "s3cret") is False


def test_an_empty_expected_token_never_matches():
    """A caller that forgets its `if not TOKEN: return True` guard must not
    authenticate everyone. Both servers make that decision explicitly and warn
    at startup; folding it in here would make the dangerous case the
    convenient one."""
    for presented in ("", "anything", None):
        assert token_auth.matches(presented, "") is False
        assert token_auth.matches(presented, None) is False


def test_a_non_ascii_token_is_rejected_not_raised():
    """`hmac.compare_digest` raises TypeError on str with non-ASCII. A naive
    swap of `==` for it would turn one odd header character into an exception
    inside the auth check."""
    assert token_auth.matches("sécret", "s3cret") is False
    assert token_auth.matches("s3cret", "sécret") is False
    # And a genuinely non-ASCII token still works when it is the right one.
    assert token_auth.matches("sécret", "sécret") is True


@pytest.mark.parametrize("header,ok", [
    ("Bearer s3cret", True),
    ("Bearer  s3cret", False),        # the extra space is part of the token
    ("bearer s3cret", False),         # scheme is case-sensitive here, as before
    ("Basic s3cret", False),
    ("s3cret", False),
    ("Bearer ", False),
    ("", False),
    (None, False),
    # The scheme must be at the START, not merely present. A containment check
    # ("Bearer" in header) passes this header AND its 7-character prefix lines
    # up with the slice, so the token would authenticate under a wrong scheme.
    # Contrived, but it is exactly what distinguishes the two checks - and a
    # mutation to the loose form survived until this case existed.
    ("xBearers3cret", False),
])
def test_bearer_parsing(header, ok):
    assert token_auth.bearer_matches(header, "s3cret") is ok


def test_the_comparison_uses_the_constant_time_primitive():
    """The property this module exists for. Asserted on the source because a
    behavioural test cannot distinguish `==` from `compare_digest` - which is
    exactly why the weaker one survived unnoticed."""
    src = (ROOT / "engine/lib/token_auth.py").read_text(encoding="utf-8")
    assert "hmac.compare_digest" in src
    assert re.search(r"return\s+a\s*==\s*b", src) is None


SERVERS = {
    "bin/taskevent_receiver.py": ("TOKEN",),
    "bin/dashboard_server.py": ("UI_TOKEN",),
}


@pytest.mark.parametrize("rel,names", sorted(SERVERS.items()))
def test_no_server_compares_a_token_with_equals(rel, names):
    """THE INVARIANT, not today's five sites: a sixth carrier added later must
    go through the same helper. Scoped to lines that mention a token NAME, so
    ordinary equality elsewhere in these large files is not flagged - a pin
    that cries wolf on correct code is one somebody deletes."""
    src = (ROOT / rel).read_text(encoding="utf-8")
    offenders = []
    for i, line in enumerate(src.splitlines(), 1):
        code = line.split("#", 1)[0]
        if not any(n in code for n in names):
            continue
        if re.search(r"==|!=", code) and "token_auth" not in code:
            offenders.append(f"{rel}:{i}: {line.strip()}")
    assert not offenders, \
        "a token is compared with ==; use token_auth.matches:\n" + \
        "\n".join(offenders)


@pytest.mark.parametrize("rel", sorted(SERVERS))
def test_each_server_actually_calls_the_helper(rel):
    """The other direction: the invariant above passes trivially if a server
    stops checking the token at all."""
    src = (ROOT / rel).read_text(encoding="utf-8")
    assert "token_auth.matches" in src or "token_auth.bearer_matches" in src, \
        f"{rel} no longer authenticates through token_auth"


def test_the_cookie_carrier_is_matched_per_cookie():
    """The dashboard's cookie check was a membership test over `;`-split
    elements. It was NOT broken - `aiqe_token=<token>EXTRA` never matched, and
    that was driven both before and after - but it compared with `==`
    semantics. Pinned so the per-cookie form is not simplified back into a
    substring search, which WOULD be a real hole."""
    src = (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")
    assert 'f"aiqe_token={UI_TOKEN}" in cookies' not in src, \
        "the cookie check went back to a substring search over the header"
    assert 'c.startswith("aiqe_token=")' in src

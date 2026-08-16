#!/usr/bin/env python3
"""Constant-time comparison for the secrets an attacker actually submits.

This repo already reaches for `hmac.compare_digest` in three places -
`plan_state.revision`, `spec_sha` and `requirements_sha` - and every one of
them compares a value an attacker CANNOT submit: they are internal integrity
hashes computed from bytes already on disk. Meanwhile the two HTTP servers
compared their AUTHENTICATION TOKENS with `==`, at five sites:

    bin/taskevent_receiver.py   X-AIQE-Token, Authorization: Bearer
    bin/dashboard_server.py     ?token=, Authorization: Bearer (x2), cookie

So the careful primitive was used where it matters least and the naive one
where it matters most. Python's `str.__eq__` returns on the first differing
byte, which makes a comparison time a function of how many leading characters
are right.

WHAT IS AND IS NOT CLAIMED. No timing attack was demonstrated here, and a
remote one against a Python HTTP handler is genuinely hard - the request
handling dominates the comparison by orders of magnitude. What IS true is that
both servers are network-reachable in the deployed shape (`AIQE_UI_HOST` and
the receiver both bind 0.0.0.0 in the Dockerfile / OpenShift ConfigMap), the
fix is free, and leaving the weaker primitive on the more exposed value is the
kind of inconsistency that reads as a decision when it was an oversight.

TWO DESIGN CHOICES, both pinned:

  * An EMPTY expected token never matches. "No token configured means auth is
    off" is a decision each server makes explicitly and loudly (both warn at
    startup); folding it in here would mean a caller that forgets its guard
    silently authenticates everyone, which is the failure mode this module
    should make impossible rather than convenient.
  * Comparison is on BYTES. `hmac.compare_digest` raises TypeError on `str`
    arguments containing non-ASCII, so a header carrying one character outside
    ASCII would raise out of the auth check - turning a bad token into a 500,
    or worse, an unhandled path. Encoding first makes a wrong token simply
    wrong.
"""
import hmac


def matches(presented, expected):
    """True when `presented` equals `expected`, in constant time.

    False for an empty/None `expected` by construction - see the module
    docstring. Never raises: any input that cannot be encoded is not the token.
    """
    if not expected or not isinstance(expected, str):
        return False
    if not isinstance(presented, str):
        return False
    try:
        a = presented.encode("utf-8")
        b = expected.encode("utf-8")
    except (UnicodeError, AttributeError):      # pragma: no cover - defensive
        return False
    return hmac.compare_digest(a, b)


def bearer_matches(auth_header, expected):
    """True when an `Authorization: Bearer <token>` header carries the token.

    The scheme prefix is compared with a plain `==` deliberately: it is not a
    secret, and requiring constant time on a public constant would only make
    the code look confused about which part is sensitive.
    """
    if not isinstance(auth_header, str) or not auth_header.startswith("Bearer "):
        return False
    return matches(auth_header[len("Bearer "):], expected)

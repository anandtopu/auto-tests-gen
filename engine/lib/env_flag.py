"""One place that decides what a boolean environment knob MEANS.

Every toggle in this platform was resolved inline as `os.environ.get(X, d) == "1"`,
which has one virtue — it is obvious — and one defect that turned out to matter:
**only the literal `1` counts.** Every other value silently takes the other
branch.

For most knobs that is merely annoying (`AIQE_CRITIC=false` leaves the critic
running). For `AIQE_MOCK` it is dangerous, because the other branch is REAL
adapters and REAL model spend:

    AIQE_MOCK=1        mock          (as documented)
    AIQE_MOCK=true     REAL          <- someone turning mock ON
    AIQE_MOCK=yes      REAL
    AIQE_MOCK=         REAL          <- a bare key in a .env file
    AIQE_MOCK=mock     REAL

Somebody trying to enable mock mode by writing `true` gets real pushes to real
repositories and a real bill. Every other knob in this codebase fails safe; this
one failed toward spending money.

**What this module changes, and what it deliberately does not.**

It accepts the spellings people actually type (`true/yes/on`, `false/no/off`)
alongside `1`/`0`, and it WARNS — once per process per variable — when a value
is set but unrecognized, resolving it to `default` rather than to whichever
branch the string comparison happened to fall through to.

It does NOT change what an UNSET variable means. `engine/pipeline.sh` treats an
unset `AIQE_MOCK` as REAL (`${AIQE_MOCK:-0}`) and `make run-pr` depends on that;
the Python libraries treat unset as MOCK because a library that cannot know
should not assume it may spend money. Those two defaults disagree on purpose and
changing either would break a documented path, so each caller still passes the
default it needs. The fix here is only for values that were being MIS-honoured.
"""
import os
import sys

TRUEISH = ("1", "true", "yes", "on")
FALSEISH = ("0", "false", "no", "off")

# Warn once per (variable, value): these are read on nearly every call in some
# modules, and a warning printed forty times is a warning nobody reads.
_warned = set()


def flag(name, default, warn=None):
    """Resolve `name` to a bool, or `default` when it is unset or unusable.

    `default` is required rather than assumed — see the module docstring on why
    the shell and the libraries disagree about an unset `AIQE_MOCK`.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    v = raw.strip().lower()
    if v in TRUEISH:
        return True
    if v in FALSEISH:
        return False
    say = warn if warn is not None else (lambda m: print(m, file=sys.stderr))
    if (name, v) not in _warned:
        _warned.add((name, v))
        # Name the resolved behaviour, not just the rejection: "ignored" leaves
        # the reader to work out which way it went, and for AIQE_MOCK the two
        # directions differ by a real bill.
        say(f"[config] {name}={raw!r} is not a recognized boolean "
            f"({'/'.join(TRUEISH)} or {'/'.join(FALSEISH)}) — using "
            f"{'ON' if default else 'OFF'}.")
    return default


def mock(default=True, warn=None):
    """Are we in mock mode? The libraries' default is MOCK.

    Deliberately the safe side: a library that cannot tell must not be the
    reason something pushes a commit or bills an account. `AIQE_MOCK=0` still
    means real, exactly as documented — only unusable values changed, and they
    now land on mock instead of on real-by-accident.
    """
    return flag("AIQE_MOCK", default, warn)

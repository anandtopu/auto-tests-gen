#!/usr/bin/env python3
"""Parse a pull-request URL into (repo_slug, pr_number, project) for any SCM we speak.

Asking a user for "repo name" and "PR number" as separate fields assumes they know
which registry name a repository was filed under, and — on Stash — which project key
addresses it. Neither is visible from the thing they actually have in front of them:
the pull-request URL they were just looking at. That gap is what makes a PR run fail
with an error about a project the user never knew they had to configure.

The URL already carries every part:

    Stash / Bitbucket Server   .../projects/ENG/repos/orders-api/pull-requests/42
    Bitbucket Cloud            .../my-workspace/orders-api/pull-requests/42
    GitHub / GHE               .../my-org/orders-api/pull/42

so we take it and derive the rest. `project` is the Stash project key (or the
Bitbucket workspace / GitHub owner) — enough to tell the user exactly what to put in
the repo's `stash_project` field when the repository is not registered yet.

Deliberately does NOT touch the registry or guess a repo name: resolution stays the
registry's job (engine/lib/stash_target.py). This only reads a string.
"""
import re
import sys
from urllib.parse import urlsplit

# /projects/<KEY>/repos/<slug>/pull-requests/<n>   (Stash / Bitbucket Server & DC)
_STASH = re.compile(
    r"/projects/(?P<project>[^/]+)/repos/(?P<slug>[^/]+)/pull-requests/(?P<num>\d+)",
    re.I)
# /<workspace>/<slug>/pull-requests/<n>            (Bitbucket Cloud)
_BB_CLOUD = re.compile(
    r"^/(?P<project>[^/]+)/(?P<slug>[^/]+)/pull-requests/(?P<num>\d+)", re.I)
# /<owner>/<slug>/pull/<n>                          (GitHub / GHE)
_GITHUB = re.compile(
    r"^/(?P<project>[^/]+)/(?P<slug>[^/]+)/pull/(?P<num>\d+)", re.I)

_KINDS = (("stash", _STASH), ("bitbucket", _BB_CLOUD), ("github", _GITHUB))


def parse(url):
    """{kind, project, slug, pr, host} for a PR URL, or None if it isn't one.

    Total by construction — a malformed or unrelated string returns None rather than
    raising, because this runs on whatever a user pastes into a text box.
    """
    if not isinstance(url, str) or not url.strip():
        return None
    raw = url.strip()
    # Tolerate a bare path or a scheme-less host ("stash.corp/projects/...").
    if "://" not in raw:
        raw = "https://" + raw.lstrip("/") if not raw.startswith("/") else raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    path = parts.path or raw

    # Stash first: its path also contains segments that would match the looser
    # two-segment patterns, so a generic match would mis-read the project key.
    for kind, rx in _KINDS:
        m = rx.search(path) if kind == "stash" else rx.match(path)
        if m:
            return {"kind": kind, "project": m.group("project"),
                    "slug": m.group("slug"), "pr": m.group("num"),
                    "host": parts.netloc or ""}
    return None


def describe(url):
    """One human line about a parsed URL — used in error hints and the CLI."""
    p = parse(url)
    if not p:
        return ""
    where = "project" if p["kind"] == "stash" else (
        "workspace" if p["kind"] == "bitbucket" else "owner")
    return (f"{p['kind']}: {where} {p['project']}, repo {p['slug']}, PR #{p['pr']}")


def main(argv):
    if len(argv) < 2:
        print("usage: pr_url.py <pull-request-url>", file=sys.stderr)
        return 64
    p = parse(argv[1])
    if not p:
        print("not a recognised pull-request URL (Stash, Bitbucket Cloud or GitHub)",
              file=sys.stderr)
        return 3
    import json
    print(json.dumps(p, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

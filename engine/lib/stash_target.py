#!/usr/bin/env python3
"""Resolve a repo name to its Stash (Bitbucket Server/DC) project key and slug.

A real estate spreads repositories across several Stash projects — application repos
under one, E2E test repos under another — so a single global STASH_PROJECT cannot
address them all. Each registry entry already carries a `url` of the form
`PROJECT/slug`; this resolves the project and slug for one repo so adapters/scm/stash.sh
can build project-correct REST and clone URLs per repo.

Resolution order for the PROJECT:
  1. the entry's explicit `stash_project` field, if set;
  2. the first path segment of its `url` (`ENG/orders-api` -> `ENG`);
  3. the STASH_PROJECT environment variable — the backward-compatible default for an
     estate whose repos all live under one project.

The SLUG is the last path segment of `url`, else the repo name. Unknown repos fall
back to (STASH_PROJECT, name) so a probe for a not-yet-registered repo still forms a
URL rather than crashing.

Output: one line, `PROJECT<TAB>SLUG`. Exit 3 when no project can be determined (no
field, no url segment, and STASH_PROJECT unset) — the adapter turns that into a clear
error instead of building `/projects//repos/...`.
"""
import os, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def _entry(name):
    try:
        from registry import load_registry
        reg = load_registry()
    except Exception:
        return None
    for sect in ("source_repositories", "test_repositories"):
        for r in reg.get(sect, []):
            if r.get("name") == name:
                return r
    return None


def resolve(name, env=None):
    """(project, slug) for a repo name. Never raises; returns ("", name) when no
    project can be determined so the caller decides how to report it."""
    env = os.environ if env is None else env
    entry = _entry(name) or {}
    url = str(entry.get("url") or "")
    parts = [p for p in url.split("/") if p]          # tolerate leading/empty segments

    slug = parts[-1] if parts else name
    project = (str(entry.get("stash_project") or "").strip()
               or (parts[0] if len(parts) >= 2 else "")
               or env.get("STASH_PROJECT", "").strip())
    return project, slug


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: stash_target.py <repo-name>")
    project, slug = resolve(sys.argv[1])
    if not project:
        sys.stderr.write(
            f"NO_STASH_PROJECT for {sys.argv[1]}: set its url to 'PROJECT/slug', add a "
            f"stash_project field, or set STASH_PROJECT as the default.\n")
        sys.exit(3)
    # slug carries no whitespace (repo slugs can't), so a plain tab-join is safe
    print(f"{project}\t{slug}")

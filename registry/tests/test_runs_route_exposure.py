"""The /runs/ route serves run records — not the state files sharing that directory.

Found by exploratory probing of the RUNNING server, not by any test: a plain
`GET /runs/reviews.json` returned 200 with the entire team-review board — every
key's status, reviewer, notes and critic findings — from a route whose job is
serving a run record or its diff.

The rule it broke is one this codebase states everywhere: `reports/runs/` holds
run records AND three NAMED state files (the review board, the work queue, the
webhook dedupe log), so every consumer must skip those three by name. Seventeen
sites do exactly that. This route was the eighteenth, and it was the one that
did not — the same shape as the catalog-paths defect (twelve readers, one
honoured the knob), and the same reason it survived: nothing drove the route
with those filenames.

The route is same-origin and gated by the UI token/SSO when configured, so this
is disclosure of internal review state to an authenticated caller rather than an
open leak — low severity, real defect. Pinned in both directions: the named
state files 404, and ordinary run records still serve, because a fix that
blanket-404s the route would "pass" a one-sided test while breaking the feature.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import run_progress  # noqa: E402

SRC = (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")


def _runs_route():
    """The body of the `/runs/` branch, up to the next elif."""
    start = SRC.index('elif url.path.startswith("/runs/")')
    end = SRC.index("elif ", start + 10)
    return SRC[start:end]


def test_the_runs_route_refuses_the_named_state_files():
    body = _runs_route()
    assert "STATE_FILES" in body, (
        "the /runs/ route no longer excludes the named state files — "
        "GET /runs/reviews.json would serve the whole team-review board")
    # It must consult the shared definition, not a fresh literal: an 18th
    # hand-rolled copy of the same tuple is how this drifted in the first place.
    assert not re.search(r'"reviews\.json"\s*,\s*"queue\.json"', body), \
        "the route hard-codes its own copy of the state-file list; use the shared one"


def test_the_shared_definition_still_names_all_three():
    assert set(run_progress.STATE_FILES) == {
        "reviews.json", "queue.json", "hooks-seen.json"}, \
        "the canonical state-file list changed — every consumer's exclusion moved with it"


def test_ordinary_run_records_are_still_served():
    """The other direction. A guard that 404s everything would satisfy the test
    above while removing the feature — run diffs are how a reviewer reads
    generated code after the workspace is gone."""
    body = _runs_route()
    assert 'ROOT / "reports/runs" / name' in body, "the route no longer resolves run files"
    assert "f.read_bytes()" in body, "the route no longer serves file content"
    assert "404" in body, "the route lost its not-found handling"


def test_the_traversal_guard_survived_the_fix():
    """The strict-charset + `..` check predates this change and must outlive it —
    re-adding a state-file check is no reason to drop the Windows-backslash
    traversal guard the comment above it explains."""
    body = _runs_route()
    assert re.search(r'fullmatch\(r?"\[\\w\.-\]\+"', body) or 'fullmatch(r"[\\w.-]+"' in body, \
        "the strict-charset basename guard is gone"
    assert '".." in name' in body, "the parent-directory guard is gone"

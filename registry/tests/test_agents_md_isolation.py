"""The suite must not rewrite the estate's tracked AGENTS.md.

Every pytest run left AGENTS.md modified in git status. MEASURED: the only
difference was the "> Regenerated <timestamp>." header — content otherwise
byte-identical — so unlike the spec-of-record leak (CLAUDE.md, sixth instance)
nothing was LOST here, only churn. Filed and fixed as its own item rather than
folded into that one, because the severities are genuinely different and
saying so plainly is more useful than treating every leak as equally bad.

Six test files read `ROOT / "AGENTS.md"` directly; five regenerate it
themselves via a subprocess (which inherits AIQE_AGENTS_FILE automatically)
and one only reads the estate's existing content
(test_agents_md_annotates_gaps). AIQE_AGENTS_FILE is redirected and SEEDED —
app_paths.agents_file()'s own docstring calls the file "purely derived...
needs no seeding" because a missing one self-heals on the next `make agents`,
which is true for the five that regenerate but not for the one that only
reads, so this seeds anyway rather than special-case that caller.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import app_paths  # noqa: E402


def test_agents_file_does_not_point_at_the_estate():
    assert app_paths.agents_file().resolve() != (ROOT / "AGENTS.md").resolve(), \
        "tests write the estate's tracked AGENTS.md"


def test_the_redirect_is_seeded_so_the_one_read_only_caller_still_works():
    """test_agents_md_annotates_gaps reads the estate's existing gap markers
    without regenerating — an empty redirect would leave it reading nothing."""
    assert app_paths.agents_file().is_file(), \
        "the AGENTS.md redirect was not seeded — a read-only fixture use sees " \
        "nothing"


def test_an_explicit_value_from_the_caller_still_wins():
    """Same contract as every other redirect in conftest."""
    conftest = (ROOT / "registry/tests/conftest.py").read_text(encoding="utf-8")
    assert '_redirect_file_seeded("AIQE_AGENTS_FILE"' in conftest, \
        "the AGENTS.md redirect is no longer wired in conftest"
    import os
    assert os.environ.get("AIQE_AGENTS_FILE"), \
        "AIQE_AGENTS_FILE is not set at all — nothing is redirected"


def test_no_writable_state_store_pin_also_covers_agents_file():
    """The class pin (test_review_isolation.py) enumerates modules that write
    via fs_lock and checks their module-level paths don't resolve into the
    estate. bin/gen_agents_md.py is a SCRIPT, not an engine/lib module, so that
    enumeration cannot see it — this is the targeted pin filling the gap the
    class pin structurally cannot reach."""
    src = (ROOT / "bin/gen_agents_md.py").read_text(encoding="utf-8")
    assert "app_paths.agents_file(" in src, \
        "gen_agents_md.py stopped resolving its output through app_paths — " \
        "it will not follow AIQE_AGENTS_FILE"

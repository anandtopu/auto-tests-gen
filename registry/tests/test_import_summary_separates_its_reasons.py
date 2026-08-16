"""A restore said "kept existing" about a file it had refused.

FOUND WHILE DRIVING use case 10's import half - the disaster-recovery path you
find out about on the day you need it. TWO HYPOTHESES WERE RAISED AND BOTH
DISPROVEN BY MEASUREMENT, which is most of what this investigation produced and
is recorded so nobody repeats it:

  * `import_bundle` resolves targets with `resolve_rel(rel, ROOT)` while the
    export side uses `resolve_rel(rel)`, which LOOKS like the relocation defect
    already recorded for the export. Measured: the two agree for every mutable
    path, because `root` only steers FROZEN (code) paths and the mutable
    branches consult `state_root()` regardless.
  * A dry-run into an EMPTY state root reported "kept 405 existing", which
    looks like the import ignoring the destination. Measured: `reports/` is
    deliberately not relocated (it is the PVC mount), so those members
    correctly resolve to the checkout and are correctly kept.

The restore itself was then driven end to end into a fresh root: 32 files
written, the registry loads (5 app repos, 3 test repos), catalog files resolve,
and the working estate is untouched. That behaviour was ALREADY pinned by
`test_import_restores_into_the_receiving_state_volume`.

WHAT WAS ACTUALLY WRONG is small and is the only thing changed: `skipped`
counted two different decisions. A member declined because it is CODE
(`_frozen_import`) is not a member kept because the destination already had it,
and the summary called both "existing" - on the single line a DR restore
prints. One of them means "your data was already there"; the other means "this
was never yours to restore".
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import state_bundle                                       # noqa: E402


def test_a_refused_code_path_is_not_counted_as_existing(tmp_path, monkeypatch):
    """THE DEFECT. Driven through a real bundle so the two counters are
    exercised by the same import that produces the summary."""
    monkeypatch.setenv("AIQE_STATE_DIR", str(tmp_path / "state"))
    bundles = sorted((ROOT / "reports/exports").glob("*state.tar.gz"))
    if not bundles:
        pytest.skip("no bundle in reports/exports to import")
    r = state_bundle.import_bundle(bundles[-1], dry_run=True)
    assert "refused" in r, "the import no longer reports refusals separately"
    # Every refused member must genuinely be one the importer declines by rule.
    for rel in r["refused"]:
        assert state_bundle._frozen_import(rel), rel
    # ...and none of them may also be counted as an existing-file skip.
    assert not (set(r["refused"]) & set(r["skipped"]))


def test_the_two_counters_do_not_overlap_or_lose_members(tmp_path, monkeypatch):
    """The arithmetic a reader does: written + kept + refused should account for
    every state member, or the summary is quietly short."""
    monkeypatch.setenv("AIQE_STATE_DIR", str(tmp_path / "state"))
    bundles = sorted((ROOT / "reports/exports").glob("*state.tar.gz"))
    if not bundles:
        pytest.skip("no bundle in reports/exports to import")
    import tarfile
    r = state_bundle.import_bundle(bundles[-1], dry_run=True)
    with tarfile.open(bundles[-1], "r:gz") as t:
        members = [m.name[len("state/"):] for m in t.getmembers()
                   if m.isfile() and m.name.startswith("state/")]
    assert len(r["written"]) + len(r["skipped"]) + len(r["refused"]) == len(members)


def test_a_clean_import_says_nothing_about_refusals(capsys, tmp_path,
                                                     monkeypatch):
    """OVER-FIX GUARD: the clause must not appear when there is nothing to
    refuse, or it becomes noise on every restore."""
    line = _summary({"mode": "merge", "dry_run": False, "written": ["a"],
                     "skipped": ["b"], "refused": [], "mismatched": []})
    assert "refused" not in line, line
    assert "kept 1 existing" in line, line


def test_the_clause_names_what_was_refused_and_why_it_differs():
    line = _summary({"mode": "merge", "dry_run": False, "written": [],
                     "skipped": ["b"], "refused": ["engine/lib/x.py"],
                     "mismatched": []})
    assert "refused 1 code path(s)" in line, line
    assert "kept 1 existing" in line, line


def _summary(r):
    """Re-render the CLI's summary line from a result dict.

    Extracted the same way `_repair_loop_cell` and `commit_rate_line` were, so
    the rule is checkable against a fabricated result instead of only against
    whatever bundles this estate happens to hold.
    """
    verb = "would write" if r["dry_run"] else "wrote"
    line = (f"{r['mode']} import: {verb} {len(r['written'])} file(s), "
            f"kept {len(r['skipped'])} existing")
    if r.get("refused"):
        line += f", refused {len(r['refused'])} code path(s)"
    return line


def test_the_cli_renders_the_same_shape_this_test_asserts():
    """The extraction above is only honest if it matches the CLI. Pinned
    against the source, because a helper that has drifted from the code it
    stands in for is a test of nothing."""
    src = (ROOT / "engine/lib/state_bundle.py").read_text(encoding="utf-8")
    i = src.index('f"{r[\'mode\']} import: {verb} ')
    block = src[i:i + 600]
    assert 'kept {len(r[\'skipped\'])} existing' in block
    assert 'refused {len(r[\'refused\'])} code path(s)' in block
    assert 'if r.get("refused"):' in block

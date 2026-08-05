"""The dashboard's geometry comes from tokens, not from literals.

Implemented from the Claude Design project "QA Dashboard UI redesign"
(`QA Dashboard.dc.html` + `tokens.css`). The colour half of that token set was
already here; the GEOMETRY half was not. Every radius, control height and the
sidebar/topbar size was written as a literal at each use site, so the design
could not be adjusted in one place — and `--sr-ring` was defined by neither
side, which is why keyboard focus fell back to the UA default and was invisible
against the dark primary.

Measured on the served page after the change: body is a grid of
`240px 1025px` from `--sr-sidebar-w`, the sidebar and topbar both resolve to
their tokens, the breadcrumb renders `ai-qe / <view>` in the mono family, and at
375px the grid collapses to one column with the sidebar stacked above main and
no horizontal overflow.

That last one is why this file exists. Converting the shell from flex to grid
silently killed the responsive rule — `body { flex-direction:column }` does
nothing to a grid, so the sidebar would have stayed a 240px column on a phone.
Nothing would have failed; it would just have been wrong.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
DASH = ROOT / "bin/dashboard.py"


# Read the SOURCE, never render. bin/dashboard.py writes reports/dashboard.html
# both when run and when imported (it has no __main__ guard), and this file
# learned that the expensive way: a version that rendered per test made
# test_repo_admin/test_stash_multiproject flaky with `OSError: [Errno 22]` on
# their AGENTS.md write. Cutting four renders to one halved it; only removing
# the render entirely made it stop. Measured: with this file present and
# rendering, `make review` failed 3 runs out of 3 (in three DIFFERENT tests);
# with the file removed, 1231 passed clean; with the file present and
# source-only, see the run recorded in the commit.
#
# What is lost is that this no longer proves the constants reach the page. That
# is covered instead by driving the SERVED page: body computed to a
# `240px 1025px` grid from --sr-sidebar-w, sidebar 240 / topbar 56 from their
# tokens, the crumb in the mono family, and at 375px a single column with the
# sidebar stacked above main and no horizontal overflow.
SRC = DASH.read_text(encoding="utf-8")


def test_no_token_is_used_without_being_defined():
    """A `var(--sr-x)` with no definition silently falls back to nothing, which
    is how an invisible focus ring happens."""
    html = SRC
    defined = set(re.findall(r"(--sr-[\w-]+)\s*:", html))
    used = set(re.findall(r"var\((--sr-[\w-]+)", html))
    assert not (used - defined), f"used but never defined: {sorted(used - defined)}"


def test_the_shell_geometry_comes_from_tokens():
    """The point of the token layer. If these become literals again the design
    can only be changed by find-and-replace."""
    html = SRC
    assert "grid-template-columns:var(--sr-sidebar-w) 1fr" in html, \
        "the shell no longer sizes itself from --sr-sidebar-w"
    assert "height:var(--sr-topbar-h)" in html, "the topbar height is a literal again"
    assert "height:var(--sr-control-h)" in html, "controls no longer use the height token"
    assert "outline:2px solid var(--sr-ring)" in html, "the focus ring is gone"


def test_the_narrow_layout_still_collapses():
    """The trap this change walked into. `flex-direction:column` is a no-op on a
    grid, so the media query has to restate the columns — otherwise the sidebar
    stays 240px wide on a phone and nothing reports a problem."""
    html = SRC
    media = html.split("@media (max-width: 900px)", 1)
    assert len(media) == 2, "the narrow-screen rule vanished"
    block = media[1][:400]
    assert "grid-template-columns:1fr" in block, \
        "the narrow layout does not restate the grid — it will not collapse"
    assert "flex-direction:column" not in block, \
        "a flex rule is back in the grid layout; it does nothing"


def test_the_breadcrumb_tracks_the_view():
    """It is rendered once server-side and updated in go(); both have to agree,
    or the topbar names a view you are not on."""
    html = SRC
    assert 'id="view-crumb"' in html
    assert "'ai-qe / ' + view" in html, "go() no longer updates the breadcrumb"
    src = DASH.read_text(encoding="utf-8")
    fn = src.split("function go(view)", 1)[1][:600]
    assert "if (crumb)" in fn, \
        "the breadcrumb update is unguarded — a missing node would throw before " \
        "runViewLoaders and strand the page on the previous view"

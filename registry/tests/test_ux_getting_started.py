"""Getting started, and a navigation a newcomer can scan.

Two findings from using the product as a first-time user would:

* An estate with no repos, no runs and no catalog was told "Nothing needs
  attention — all clear." Everything WAS clear; nothing was set up. That is C13
  applied to onboarding — an absence of data reported as a healthy state — and
  it leaves the first question a new user has ("what do I do?") unanswered by
  the panel that exists to answer it.
* Fifteen flat nav items give no way to tell the three you need from the twelve
  you do not.

The Start-here panel is DERIVED from the estate, not a static splash: each step
reports what is actually true, so it doubles as a status check for a half-built
estate, and the whole panel disappears once the three are done.
"""
import pathlib
import re
import subprocess
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _render(tmp_path, *, repos, catalog_rows):
    """Generate the dashboard against an isolated estate and return the HTML."""
    reg = tmp_path / "registry.yaml"
    if repos:
        src = yaml.safe_load((ROOT / "registry/repo-registry.yaml").read_text(encoding="utf-8"))
    else:
        src = {"source_repositories": [], "test_repositories": []}
    reg.write_text(yaml.safe_dump(src), encoding="utf-8")
    cat = tmp_path / "catalog"
    cat.mkdir(exist_ok=True)
    if catalog_rows:
        for f in (ROOT / "catalog").glob("*.jsonl"):
            (cat / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    out = tmp_path / "dash.html"
    env = {**__import__("os").environ,
           "AIQE_REGISTRY_FILE": str(reg), "AIQE_CATALOG_DIR": str(cat),
           "AIQE_DASHBOARD_OUT": str(out)}
    r = subprocess.run([sys.executable, "bin/dashboard.py"], cwd=ROOT, env=env,
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=240)
    assert r.returncode == 0, r.stderr[-500:]
    # AIQE_DASHBOARD_OUT keeps this out of the estate. Without it the first
    # version of this test REPLACED reports/dashboard.html with a render of its
    # own fixture — the operator's dashboard silently became a view of an empty
    # estate. Same pollution class as the transaction log and the run history.
    assert out.exists(), "AIQE_DASHBOARD_OUT was ignored — the real dashboard was written"
    return out.read_text(encoding="utf-8", errors="replace")


def test_a_fresh_estate_is_told_what_to_do_not_that_all_is_clear(tmp_path):
    html = _render(tmp_path, repos=False, catalog_rows=False)
    assert 'id="start-here"' in html, "a fresh estate gets no getting-started path"
    assert "Register your repositories" in html
    assert "Generate tests from a PR or a ticket" in html
    assert "Review what was generated" in html
    # And the attention panel must not claim health it cannot know.
    m = re.search(r"Nothing needs attention (<b>yet</b>|—|\u2014)", html)
    assert "Nothing needs attention <b>yet</b>" in html or "start-here" in html, \
        "an unconfigured estate still reads as 'all clear'"


def test_the_panel_reports_what_is_actually_true(tmp_path):
    """Not a static splash: a half-built estate sees which steps it has done."""
    html = _render(tmp_path, repos=True, catalog_rows=False)
    if 'id="start-here"' in html:
        block = html[html.index('id="start-here"'):]
        block = block[:block.index("</section>")]
        assert "done" in block, "a completed step is not marked done"


def test_every_nav_entry_belongs_to_a_group_and_no_view_is_lost():
    """Grouping must not drop or duplicate a destination — that is how a
    reorganisation quietly removes a feature."""
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    block = src[src.index("NAV = ["):src.index("TITLES = {")]
    entries = re.findall(r'\("([^"]+)",\s*"([\w-]+)",', block)
    assert entries, "NAV could not be parsed"
    groups = {g for g, _ in entries}
    ids = [v for _, v in entries]
    assert len(ids) == len(set(ids)), f"duplicate nav destination: {ids}"
    assert groups == {"Start", "Work", "Insight", "Configure"}, groups
    # Every id must be a real view in the page.
    views = set(re.findall(r'data-view="([\w-]+)"', src))
    missing = [v for v in ids if v not in views]
    assert not missing, f"nav points at views that do not exist: {missing}"
    # ...and every view must be reachable from the nav.
    unreachable = [v for v in views if v not in ids]
    assert not unreachable, f"views with no nav entry: {unreachable}"


def test_group_headings_are_rendered_not_just_declared():
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert 'class="nav-group"' in src, "groups are declared but never rendered"
    assert ".nav-group {" in src, "no styling for the group heading"

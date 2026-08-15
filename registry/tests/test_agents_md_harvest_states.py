"""AGENTS.md must name the fix that would actually work.

`bin/gen_agents_md.py` carried its own copy of `coverage_gaps.harvest` that
collapsed all three non-harvested outcomes into `[]`, and the caller printed
ONE sentence for every one of them:

    - **brand-new-api**: contract `?` not available locally
      (clone appears at workspace/src/ during runs)

MEASURED by registering a repo the way `upsert_app` leaves one -- `kind` and
`url` are its only required fields, so a fresh onboard declares no artifact --
and rendering AGENTS.md: that sentence tells the reader to wait for a clone
that can NEVER produce the file, because nothing is registered to look for.
`coverage_gaps` said about the SAME repo: "no contract is registered ...
register one with bin/repos.py". Two surfaces over one fact with OPPOSITE
fixes (C13), and this is the state a repo is in immediately after onboarding,
which is when AGENTS.md is first read -- and AGENTS.md is injected as context
into every authoring phase, so the model was told the same wrong thing.

THE ALLOW-LIST ENTRY WAS THE DEEPER FINDING. `test_coverage_gaps_observability`
exempts this module from the observed() invariant on the grounds that it "has
its own harvest and its own honest not-available-locally branch", and cites
AGENTS.md lines 27 and 32 as the evidence. Those two lines are the UNREADABLE
case. The claim was never true of the UNDECLARED case, which shares the branch
-- an allow-list entry granted on half-verified evidence, hiding a live defect,
exactly as that file's own docstring warns. So this file pins the rendering
BEHAVIOURALLY: the exemption now has to be earned per state rather than
asserted in a comment.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import coverage_gaps                                            # noqa: E402

# One repo per state harvest() can be in. `orders-api` ships a contract under
# demo/ and is the harvested control.
UNDECLARED = "zz-states-undeclared"
UNREADABLE = "zz-states-unreadable"
HARVESTED = "orders-api"


@pytest.fixture(scope="module")
def rendered():
    """(AGENTS.md text, {repo: coverage_gaps detail}) from a real run of the
    generator against an isolated registry. Driving the entry point is the
    point: the library was never wrong here, only the renderer.
    """
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="agents-states-"))
    try:
        reg = tmp / "repo-registry.yaml"
        shutil.copy(ROOT / "registry/repo-registry.yaml", reg)
        d = yaml.safe_load(reg.read_text(encoding="utf-8"))
        d["source_repositories"] += [
            {"name": UNDECLARED, "type": "backend", "scm": "bitbucket",
             "url": "PROJ/" + UNDECLARED, "domains": ["billing"],
             "testable_paths": ["src/**"]},
            {"name": UNREADABLE, "type": "backend", "scm": "bitbucket",
             "url": "PROJ/" + UNREADABLE, "domains": ["billing"],
             "testable_paths": ["src/**"], "contract": "openapi/nope.yaml"},
        ]
        reg.write_text(yaml.safe_dump(d, sort_keys=False), encoding="utf-8")

        agents = tmp / "AGENTS.md"
        env = dict(os.environ,
                   AIQE_REGISTRY_FILE=str(reg),
                   AIQE_AGENTS_FILE=str(agents),
                   AIQE_GENERATED_GUIDANCE_DIR=str(tmp / "generated"))
        r = subprocess.run([sys.executable, str(ROOT / "bin/gen_agents_md.py")],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, env=env)
        assert r.returncode == 0, r.stderr[-2000:]
        text = agents.read_text(encoding="utf-8")

        os.environ["AIQE_REGISTRY_FILE"] = str(reg)
        try:
            import importlib
            import registry as reg_mod
            importlib.reload(reg_mod)
            importlib.reload(coverage_gaps)
            gaps = coverage_gaps.compute()
            details = {n: g["detail"] for n, g in gaps.items()}
            states = {n: g["status"] for n, g in gaps.items()}
        finally:
            os.environ.pop("AIQE_REGISTRY_FILE", None)
            import importlib
            import registry as reg_mod
            importlib.reload(reg_mod)
            importlib.reload(coverage_gaps)
        yield text, details, states
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _line(text, repo):
    for line in text.splitlines():
        if line.startswith(f"- **{repo}**"):
            return line
    return ""


def test_the_fixture_really_exercises_three_distinct_states(rendered):
    """A probe that proves nothing is the failure mode this repo keeps
    hitting. Without this the assertions below could all pass vacuously."""
    _text, _details, states = rendered
    assert states[UNDECLARED] == "undeclared"
    assert states[UNREADABLE] == "unreadable"
    assert states[HARVESTED] == "harvested"


def test_an_undeclared_repo_is_told_to_register_a_contract(rendered):
    """THE DEFECT. Nothing is registered, so no clone can ever produce it."""
    text, _details, _states = rendered
    line = _line(text, UNDECLARED)
    assert line, f"{UNDECLARED} is missing from the surface section entirely"
    assert "is registered" in line and "bin/repos.py" in line, \
        f"undeclared repo does not name the fix that works: {line}"
    assert "not available locally" not in line, \
        f"undeclared repo is still told to wait for a clone: {line}"


def test_an_unreadable_repo_still_says_the_clone_is_missing(rendered):
    """The state the old allow-list entry WAS verified against. Fixing the
    sibling must not cost the case that already worked."""
    text, _details, _states = rendered
    line = _line(text, UNREADABLE)
    assert "not available locally" in line, \
        f"a declared-but-absent artifact lost its message: {line}"
    assert "NOT checked" in line, \
        f"an unread surface must not read as an examined one: {line}"
    # The old copy said "clone appears at workspace/src/ during runs" for every
    # repo; naming THIS repo's path is what makes it actionable.
    assert UNREADABLE in line.split(":", 1)[1], \
        f"the message does not name the repo's own clone path: {line}"


def test_the_two_absences_do_not_render_alike(rendered):
    """The property that was violated, stated directly."""
    text, _details, _states = rendered
    assert _line(text, UNDECLARED) != _line(text, UNREADABLE)


def test_a_harvested_repo_still_lists_its_surface(rendered):
    """The over-fix guard: most repos are fine and must not gain a caveat."""
    text, _details, _states = rendered
    line = _line(text, HARVESTED)
    assert "/v1/orders" in line, f"harvested surface disappeared: {line}"
    assert "not available locally" not in line and "is registered" not in line


def test_every_rendered_absence_is_the_library_s_own_words(rendered):
    """One definition. The renderer must not paraphrase -- a second wording is
    how the two surfaces came to disagree in the first place."""
    text, details, states = rendered
    checked = 0
    for repo, status in states.items():
        if status == "harvested":
            continue
        line = _line(text, repo)
        if not line:
            continue
        checked += 1
        assert line.endswith(details[repo]), \
            f"{repo}: AGENTS.md wording has drifted from coverage_gaps\n" \
            f"  rendered: {line}\n  library:  {details[repo]}"
    assert checked >= 2, "no non-harvested repo was rendered -- nothing checked"


def test_the_generator_does_not_re_implement_the_harvest():
    """The invariant, not today's wording. A second copy of the extraction is
    what let the two surfaces drift apart, and a future edit that reinstates
    one would silently reintroduce the whole class.
    """
    src = (ROOT / "bin/gen_agents_md.py").read_text(encoding="utf-8")
    assert "coverage_gaps.harvest(" in src, \
        "gen_agents_md no longer delegates to the one definition"
    for pattern in ("^\\\\s{2}(/[^:\\\\s]+):", "path:\\\\s*['\\\"]"):
        assert pattern not in src, \
            f"gen_agents_md has its own surface extraction again: {pattern}"

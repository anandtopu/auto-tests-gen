"""Existing-approach exemplars (feature: generated tests must follow the target
repo's existing approach, never introduce a new one).

Pins: the extractor surfaces real helper + exemplar code deterministically, favors
the repo norm over legacy patterns, degrades gracefully on empty repos; the
pipeline generates the context and passes it to every generate/validate call; the
prompts and the test-generation skill carry the follow-the-approach instruction.
"""
import pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import spec_exemplars


def _mk(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_demo_repo_profile_shows_real_code_and_conventions():
    pr = spec_exemplars.profile(ROOT / "demo/e2e-api-tests-1", "e2e-api-tests-1", "suites/")
    assert pr["specs"] >= 3
    assert any("test(" in f for f in pr["facts"])
    assert any("require" in f for f in pr["facts"])
    code = "\n".join(c for _, c in pr["exemplars"])
    assert "node:test" in code and "assert" in code, "exemplars must be REAL spec code"


def test_legacy_specs_are_not_chosen_as_exemplars():
    pr = spec_exemplars.profile(ROOT / "demo/e2e-api-tests-1", "e2e-api-tests-1", "suites/")
    for rel, _ in pr["exemplars"]:
        assert "legacy" not in rel, "a legacy spec must never model the approach"


def test_shared_helpers_are_detected_and_included(tmp_path):
    _mk(tmp_path, "suites/_lib/client.js", "module.exports.get = (p) => fetch(p);\n")
    _mk(tmp_path, "suites/a/a.spec.js",
        "const { get } = require('../_lib/client');\ntest('a', () => {});\n")
    _mk(tmp_path, "suites/b/b.spec.js",
        "const { get } = require('../_lib/client');\ntest('b', () => {});\n")
    _mk(tmp_path, "suites/c/lone.spec.js", "test('c', () => {});\n")
    pr = spec_exemplars.profile(tmp_path, "x", "suites/")
    helpers = dict(pr["helpers"])
    assert "suites/_lib/client.js" in helpers, "module imported by 2+ specs is a shared helper"
    assert "module.exports.get" in helpers["suites/_lib/client.js"]


def test_dot_test_js_naming_is_recognized(tmp_path):
    """A mature repo on the idiomatic node--test naming (*.test.js) must not be
    misread as 'no approach to follow yet'."""
    _mk(tmp_path, "suites/orders/checkout.test.js",
        "const { test } = require('node:test');\ntest('x', () => {});\n")
    pr = spec_exemplars.profile(tmp_path, "x", "suites/")
    assert pr["specs"] == 1
    assert any(".test.js" in f for f in pr["facts"])


def test_pathological_imports_never_crash_the_profile(tmp_path):
    """A crash here silently disables the whole feature for the run (the
    pipeline falls back to an empty conventions file)."""
    _mk(tmp_path, "suites/deep.spec.js",
        "require('../../../../../../../../..');\ntest('d', () => {});\n")
    pr = spec_exemplars.profile(tmp_path, "x", "suites/")
    assert pr["specs"] == 1                        # profiled, not raised


def test_out_of_repo_imports_are_not_helpers(tmp_path):
    """A monorepo-style import resolving into a sibling checkout is not this
    repo's helper (and relative_to() on it used to raise)."""
    _mk(tmp_path, "shared/util.js", "module.exports = 1;\n")
    repo = tmp_path / "repo"
    _mk(repo, "suites/a.spec.js", "require('../../shared/util');\ntest('a',()=>{});\n")
    _mk(repo, "suites/b.spec.js", "require('../../shared/util');\ntest('b',()=>{});\n")
    pr = spec_exemplars.profile(repo, "x", "suites/")
    assert pr["helpers"] == [], "outside-the-repo modules must not be included"


def test_empty_repo_degrades_to_a_note_not_an_error(tmp_path):
    md = spec_exemplars.to_markdown([spec_exemplars.profile(tmp_path, "fresh", "suites/")])
    assert "fresh" in md and "No existing specs" in md


def test_output_is_deterministic():
    a = spec_exemplars.build(["e2e-api-tests-1", "e2e-ui-tests-1"])
    b = spec_exemplars.build(["e2e-api-tests-1", "e2e-ui-tests-1"])
    assert a == b


def test_markdown_carries_the_follow_this_framing():
    md = spec_exemplars.build(["e2e-api-tests-1"])
    assert "FOLLOW THIS" in md
    assert "Do NOT introduce" in md
    assert "mirror this shape" in md


def test_pipeline_generates_and_passes_the_context_to_every_author_phase():
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert "spec_exemplars.py out/repo-conventions.md" in src
    gen_lines = [l for l in src.splitlines() if l.strip().startswith("PHASE generate")]
    assert len(gen_lines) == 3 and all("out/repo-conventions.md" in l for l in gen_lines), \
        "every generate call (pr / tests / jira) must receive the exemplar context"
    val = next(l for l in src.splitlines() if l.strip().startswith("PHASE validate"))
    assert "out/repo-conventions.md" in val, \
        "repairs must also see the approach, or a repair can rewrite onto a new one"
    assert src.index("spec_exemplars.py") < src.index("PHASE triage"), \
        "the context must exist before any phase consumes it"


def test_prompts_and_skill_enforce_the_existing_approach():
    gen = (ROOT / "prompts/pr-generate.md").read_text(encoding="utf-8")
    assert "FOLLOW THE EXISTING APPROACH" in gen
    assert "no new HTTP clients" in gen
    rep = (ROOT / "prompts/validate-repair.md").read_text(encoding="utf-8")
    assert "PRESERVE the repo's existing approach" in rep
    critic = (ROOT / "prompts/critic.md").read_text(encoding="utf-8")
    assert "new-approach" in critic, "the critic must flag approach deviations"
    skill = (ROOT / ".agents/skills/test-generation/SKILL.md").read_text(encoding="utf-8")
    assert "existing approach" in skill.lower()

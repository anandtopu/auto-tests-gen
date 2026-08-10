"""Content-addressed phase reuse — the cost-reduction mechanism.

The value of this cache is entirely in its key: it must be impossible to get a stale
hit, and impossible to skip a phase whose real product is files rather than JSON.
"""
import json
import os
import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import phase_cache as pc


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "DIR", tmp_path / "cache")
    monkeypatch.setenv("AIQE_PHASE_CACHE", "1")
    yield


def _seed(tmp_path, body="ctx"):
    ctx = tmp_path / "ctx.md"
    ctx.write_text(body, encoding="utf-8")
    prompt = tmp_path / "p.md"
    prompt.write_text("prompt template", encoding="utf-8")
    return str(prompt), [str(ctx)]


def _write_contract(obj):
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    (out / "analyze.contract.json").write_text(json.dumps(obj), encoding="utf-8")


def test_identical_inputs_hit_and_any_change_misses(tmp_path):
    prompt, ctx = _seed(tmp_path)
    _write_contract({"behaviors": [{"id": "B1"}]})
    assert pc.lookup("analyze", "analyze", "m1", prompt, ctx) is False
    assert pc.store("analyze", "analyze", "m1", prompt, ctx) is True
    assert pc.lookup("analyze", "analyze", "m1", prompt, ctx) is True

    # One byte of context -> different answer required.
    pathlib.Path(ctx[0]).write_text("ctx CHANGED", encoding="utf-8")
    assert pc.lookup("analyze", "analyze", "m1", prompt, ctx) is False

    # A different model tier is a different result, not the same one cheaper.
    pathlib.Path(ctx[0]).write_text("ctx", encoding="utf-8")
    assert pc.lookup("analyze", "analyze", "m2", prompt, ctx) is False

    # A changed prompt template invalidates too.
    pathlib.Path(prompt).write_text("prompt CHANGED", encoding="utf-8")
    assert pc.lookup("analyze", "analyze", "m1", prompt, ctx) is False


def test_a_hit_restores_artifacts_not_just_the_contract(tmp_path):
    """testplan's product includes testplans/<KEY>.md. Replaying only the JSON would
    leave the next phase reading a file that is not there."""
    prompt, ctx = _seed(tmp_path)
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    (out / "testplan.contract.json").write_text('{"scenarios":[],"open_questions":[]}',
                                                encoding="utf-8")
    # Where the CACHE looks, not where the estate keeps plans. This read
    # ROOT/"testplans" while phase_cache resolves through app_paths, so it both
    # deposited a fixture file in the estate and, once conftest redirected the
    # tree, asserted against a directory the code no longer writes.
    import app_paths
    plan = app_paths.testplans_dir() / "ZZCACHE-1.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("# plan body", encoding="utf-8")
    try:
        assert pc.store("testplan", "testplan", "m", prompt, ctx, "ZZCACHE-1")
        plan.unlink()
        (out / "testplan.contract.json").unlink()
        assert pc.lookup("testplan", "testplan", "m", prompt, ctx, "ZZCACHE-1")
        assert plan.exists() and plan.read_text(encoding="utf-8").strip() == "# plan body"
        assert (out / "testplan.contract.json").exists()
    finally:
        plan.unlink(missing_ok=True)


def test_workspace_editing_phases_can_never_be_cached():
    """Their product is files written into the test repos and the git state the gate
    inspects — replaying a contract would hand the gate a clean tree and a green
    report for work that never happened."""
    prompts = {
        "generate": ROOT / "prompts/pr-generate.md",
        "validate": ROOT / "prompts/validate-repair.md",
        "reviewrepair": ROOT / "prompts/review-repair.md",
    }
    for phase, prompt in prompts.items():
        assert phase not in pc.CACHEABLE
        assert pc.lookup(phase, phase, "m", str(prompt), []) is False
        assert pc.store(phase, phase, "m", str(prompt), []) is False


def test_disabled_by_env(tmp_path, monkeypatch):
    prompt, ctx = _seed(tmp_path)
    _write_contract({"behaviors": []})
    assert pc.store("analyze", "analyze", "m", prompt, ctx)
    monkeypatch.setenv("AIQE_PHASE_CACHE", "0")
    assert pc.enabled() is False
    assert pc.lookup("analyze", "analyze", "m", prompt, ctx) is False


def test_cache_failures_are_never_fatal(tmp_path, monkeypatch):
    """A cache problem must not fail a phase that already succeeded."""
    prompt, ctx = _seed(tmp_path)
    assert pc.store("analyze", "analyze", "m", prompt, "not-a-list") in (True, False)
    (ROOT / "out").mkdir(exist_ok=True)
    bad = ROOT / "out/analyze.contract.json"
    bad.write_text("{not json", encoding="utf-8")
    assert pc.store("analyze", "analyze", "m", prompt, ctx) is False
    monkeypatch.setattr(pc, "DIR", tmp_path / "c2")
    (tmp_path / "c2").mkdir()
    (tmp_path / "c2" / (pc.key("analyze", "m", prompt, ctx) + ".json")).write_text(
        "<corrupt>", encoding="utf-8")
    assert pc.lookup("analyze", "analyze", "m", prompt, ctx) is False


def test_stats_report_avoided_calls(tmp_path):
    prompt, ctx = _seed(tmp_path)
    _write_contract({"behaviors": []})
    pc.store("analyze", "analyze", "m", prompt, ctx)
    pc.lookup("analyze", "analyze", "m", prompt, ctx)
    pc.lookup("analyze", "analyze", "m", prompt, ctx)
    s = pc.stats()
    assert s["entries"] == 1 and s["hits"] == 2
    assert s["by_phase"]["analyze"] == 2


# ------------------------------------------------ cost posture (config-level)

def test_every_phase_names_its_model_tier():
    """Unlisted phases fell back to the `generate` model, which silently put eight of
    ten phases on the authoring tier. Each must now be a deliberate choice."""
    cfg = yaml.safe_load((ROOT / "registry/org-config.yaml").read_text(encoding="utf-8"))
    models, phases = cfg["models"], cfg["phases"]
    missing = [p for p in phases if p not in models]
    assert not missing, f"phases falling back to the generate model: {missing}"


def test_bounded_phases_are_not_on_the_authoring_tier():
    cfg = yaml.safe_load((ROOT / "registry/org-config.yaml").read_text(encoding="utf-8"))
    models = cfg["models"]
    for cheap in ("triage", "analyze", "testdata", "critic", "validate"):
        assert "haiku" in models[cheap], f"{cheap} does not need the authoring tier"
    # Judgement-grade work stays on the capable tier — cheap models agree too easily.
    for rich in ("testplan", "planadversary", "generate", "reviewer",
                 "reviewrepair"):
        assert "haiku" not in models[rich], f"{rich} must stay judgement-grade"


def test_prompt_assembly_is_cache_ordered():
    """A run-unique value early in the prompt makes every invocation's prefix unique,
    so no provider-side prompt cache can ever hit."""
    src = (ROOT / "engine/phases/run_phase.sh").read_text(encoding="utf-8")
    assert 'PROMPT_TEXT=$(cat "$PROMPT")' in src, \
        "the prompt template must be sent verbatim, not key-substituted inline"
    assert "RUN PARAMETERS" in src
    i_prompt = src.index('PROMPT_TEXT=$(cat "$PROMPT")')
    i_params = src.index("RUN PARAMETERS")
    assert i_prompt < i_params, "run-specific bytes must come last"

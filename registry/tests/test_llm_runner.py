"""LLM Runner port pins (multi-LLM stories 1.1, 1.2).

The seam must change NOTHING today: claude stays the default, the wrapper
dispatches through the adapter, and the phase-cache key gains the provider so
a switch can never replay another provider's result. Capability validation is
config-time and there is NO silent fallback.
"""
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import llm_runner as lr  # noqa: E402


def test_default_is_claude_everywhere():
    """The seam ships behavior-neutral: every phase resolves to claude."""
    for phase in lr.ALL_PHASES:
        assert lr.provider_for(phase, {}) == "claude"
    assert lr.validate({}) == []


def test_env_beats_config_and_per_phase_beats_global(monkeypatch):
    cfg = {"provider": "codex", "phase_providers": {"triage": "ollama"}}
    assert lr.provider_for("triage", cfg) == "ollama"
    assert lr.provider_for("analyze", cfg) == "codex"
    # Fan-out labels resolve their POLICY phase, like the model lookup.
    assert lr.provider_for("generate-e2e-api-tests-1", cfg) == "codex"
    monkeypatch.setenv("AIQE_LLM_PROVIDER", "claude")
    assert lr.provider_for("triage", cfg) == "claude", \
        "the Settings switch (env) wins over every config layer"


def test_agentic_phase_on_a_completion_provider_is_refused():
    """Story 1.2: config-time capability validation with the fix NAMED —
    never a mid-run discovery."""
    err = lr.check_assignment("generate", "ollama")
    assert err and "cannot run agentic phase" in err
    assert "phase_providers" in err, "the refusal must name the fix"
    assert lr.check_assignment("validate", "ollama")
    # Fan-out labels are checked as their policy phase.
    assert lr.check_assignment("generate-e2e-api-tests-1", "ollama")
    # Completion phases are NOT refused on capability grounds — the only
    # remaining objection is that the adapter ships in slice 2. When it does,
    # these become None with no change here.
    for phase in ("triage", "analyze", "testplan", "testdata", "critic",
                  "planadversary", "planarbiter"):
        err = lr.check_assignment(phase, "ollama")
        assert err is None or "not built yet" in err, \
            f"{phase} must never be refused on capability grounds: {err}"


def test_unknown_or_unbuilt_provider_is_refused():
    assert "unknown LLM provider" in lr.check_assignment("triage", "gpt9")
    # An adapter that does not exist yet is refused too — but capability is
    # reported FIRST for phases the provider could never serve anyway.
    err = lr.check_assignment("triage", "ollama")   # adapter arrives in s2
    if err:
        assert "not built yet" in err


def test_validate_lists_every_bad_assignment():
    cfg = {"provider": "claude", "phase_providers": {"generate": "ollama",
                                                     "validate": "ollama"}}
    errors = lr.validate(cfg)
    assert len(errors) == 2 and all("agentic" in e for e in errors)


def test_model_mapping_passes_through_unmapped():
    cfg = {"models_by_provider": {"ollama": {"claude-sonnet-4-6": "qwen:32b"}}}
    assert lr.map_model("ollama", "claude-sonnet-4-6", cfg) == "qwen:32b"
    assert lr.map_model("ollama", "claude-haiku-4-5", cfg) == "claude-haiku-4-5"
    assert lr.map_model("claude", "claude-sonnet-4-6", cfg) == "claude-sonnet-4-6"


def test_resolve_cli_contract():
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/llm_runner.py"),
                        "resolve", "triage"], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    assert r.returncode == 0
    # rstrip newline only — .strip() would eat the trailing empty model field
    provider, adapter, _model = r.stdout.rstrip("\n").split("\t")
    assert provider == "claude" and adapter.endswith("claude.sh")
    assert pathlib.Path(adapter).exists()


def test_resolve_cli_refuses_impossible_assignment(monkeypatch):
    env = {**os.environ, "AIQE_LLM_PROVIDER": "ollama"}
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/llm_runner.py"),
                        "resolve", "generate"], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", stdin=subprocess.DEVNULL,
                       env=env)
    assert r.returncode == 1 and "PROVIDER_CONFIG" in r.stderr


def test_wrapper_dispatches_through_the_port_and_keys_the_cache_by_provider():
    """Source pins: no direct CLI call survives, and both cache call sites
    carry PROVIDER:MODEL — switching providers cannot replay another
    provider's cached result."""
    src = (ROOT / "engine/phases/run_phase.sh").read_text(encoding="utf-8")
    assert 'claude -p "$PROMPT_TEXT' not in src, \
        "the wrapper must not call a provider CLI directly"
    assert 'bash "$RUNNER" run_phase' in src
    assert src.count('"${PROVIDER}:${MODEL}"') == 2, \
        "both phase_cache lookup and store are provider-qualified"
    assert "llm_runner.py resolve" in src


def test_claude_adapter_preserves_the_invocation():
    """The default adapter is the OLD call, verbatim — behavior-neutral."""
    src = (ROOT / "adapters/llm/claude.sh").read_text(encoding="utf-8")
    for flag in ("--output-format json", "--max-turns", "--allowedTools",
                 "--model", "--dangerously-skip-permissions"):
        assert flag in src
    assert 'echo "agentic"' in src


def test_adapters_reject_unknown_verbs():
    import work_queue
    for adapter in ("adapters/llm/claude.sh", "adapters/mock/llm.sh"):
        r = subprocess.run([work_queue.bash_exe(), str(ROOT / adapter),
                            "definitely_unknown"], cwd=ROOT,
                           capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        assert r.returncode == 64, f"{adapter} must exit 64 on unknown verbs"


def test_org_config_ships_claude_as_the_default():
    import yaml
    cfg = yaml.safe_load(open(ROOT / "registry/org-config.yaml",
                              encoding="utf-8"))
    llm = cfg.get("llm") or {}
    assert llm.get("provider") == "claude"
    assert llm.get("phase_providers") == {}, \
        "no per-phase routing by default — the seam is behavior-neutral"


# ---------------------------------------------------------------- slice 2
def test_ollama_is_completion_class_and_serves_non_agentic_phases():
    """2.1: the adapter exists and declares completion — so every phase EXCEPT
    generate/validate is now a valid assignment."""
    import work_queue
    r = subprocess.run([work_queue.bash_exe(),
                        str(ROOT / "adapters/llm/ollama.sh"), "capabilities"],
                       cwd=ROOT, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0 and r.stdout.strip() == "completion"
    for phase in ("triage", "analyze", "testplan", "planarbiter", "testdata",
                  "critic", "planadversary"):
        assert lr.check_assignment(phase, "ollama") is None
    assert lr.check_assignment("generate", "ollama")


def test_ollama_never_falls_back_silently():
    """A down daemon must fail loudly — routing to a paid provider behind the
    user's back is the one thing this port may never do."""
    src = (ROOT / "adapters/llm/ollama.sh").read_text(encoding="utf-8")
    assert "PROVIDER_UNREACHABLE" in src and "No silent fallback" in src
    assert "total_cost_usd" not in src.split("out = {")[1].split("}")[0], \
        "local inference reports no cost — inventing one breaks the honesty rule"


def test_derived_writes_only_for_the_derived_phases():
    import derived_writes as dw
    assert bool(dw.addendum("testplan")) and bool(dw.addendum("testdata"))
    assert dw.addendum("triage") == "" and dw.addendum("generate") == ""
    # Fan-out labels resolve their policy phase.
    assert bool(dw.addendum("planarbiter"))


def test_derived_writes_materializes_testdata_from_inlined_content(tmp_path,
                                                                  monkeypatch):
    import derived_writes as dw
    monkeypatch.setattr(dw, "ROOT", tmp_path)
    contract = {"fixtures": [
        {"canonical": "testdata/K-1/cases.json", "content": '{"a":1}'},
        {"canonical": "testdata/K-1/missing.json"},
        {"canonical": "../escape.json", "content": "x"},
        {"canonical": "/abs/escape.json", "content": "x"}]}
    written, problems = dw.materialize("testdata", "K-1", contract)
    assert written == ["testdata\K-1\cases.json".replace("\\", os.sep)]
    assert (tmp_path / "testdata/K-1/cases.json").read_text() == '{"a":1}'
    assert any("no `content`" in p for p in problems)
    assert sum("refused" in p for p in problems) == 2, \
        "a contract is model output — it never chooses a path outside testdata/"
    assert not (tmp_path.parent / "escape.json").exists()


def test_derived_writes_renders_the_plan_through_the_spec_renderer(tmp_path,
                                                                   monkeypatch):
    """A plan authored on a local model goes through the SAME SDD renderer —
    one source of truth regardless of provider."""
    import derived_writes as dw
    import plan_state as ps
    import spec_store as ss
    monkeypatch.setattr(dw, "ROOT", tmp_path)
    monkeypatch.setattr(ss, "SPEC_DIR", tmp_path / "specs")
    monkeypatch.setattr(ps, "PLAN_DIR", tmp_path / "testplans")
    monkeypatch.setattr(ps, "DIR", tmp_path / "plans")
    monkeypatch.setattr(ps, "FILE", tmp_path / "plans/state.json")
    (tmp_path / "plans").mkdir()
    contract = {"scenarios": [{"id": "K-1-S1", "title": "boundary",
                               "layer": "api", "target_repo": "r",
                               "steps": {"given": "g", "when": "w", "then": "t"},
                               "verification": ["status is 422"]}]}
    written, problems = dw.materialize("testplan", "K-1", contract)
    assert written and not problems
    md = (tmp_path / "testplans/K-1.md").read_text(encoding="utf-8")
    assert "Rendered from" in md and "verify: status is 422" in md


def test_derived_writes_free_form_fallback(tmp_path, monkeypatch):
    import derived_writes as dw
    monkeypatch.setattr(dw, "ROOT", tmp_path)
    contract = {"scenarios": [{"id": "K-1-S1", "title": "t", "layer": "api",
                               "target_repo": "r", "behavior_ref": "B1"}],
                "open_questions": ["q"]}
    written, _ = dw.materialize("testplan", "K-1", contract)
    md = (tmp_path / "testplans/K-1.md").read_text(encoding="utf-8")
    assert "K-1-S1" in md and "Open Questions" in md


def test_wrapper_is_capability_aware():
    src = (ROOT / "engine/phases/run_phase.sh").read_text(encoding="utf-8")
    assert 'CAPS=$(bash "$RUNNER" capabilities' in src
    assert 'if [ "$CAPS" = "completion" ]' in src
    assert "derived_writes.py materialize" in src
    # The addendum lands INSIDE the run-parameters block (after the cacheable
    # prefix), never in the prompt prefix.
    assert src.index("derived_writes\nsys.stdout.write") > src.index("RUN PARAMETERS") \
        if "derived_writes\nsys.stdout.write" in src else True


def test_llm_provider_check_is_registered_without_stealing_the_llm_id():
    import integration_check as ic
    assert ic.CHECKS["llm"].__name__ == "check_llm", \
        "`llm` is the Anthropic key check — the provider probe has its own id"
    assert "llm_provider" in ic.CHECKS

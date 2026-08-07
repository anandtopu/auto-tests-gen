"""LLM Runner port pins (multi-LLM stories 1.1, 1.2).

The seam must change NOTHING today: claude stays the default, the wrapper
dispatches through the adapter, and the phase-cache key gains the provider so
a switch can never replay another provider's result. Capability validation is
config-time and there is NO silent fallback.
"""
import json
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
    assert src.count('"${PROVIDER}:${FINAL_MODEL}"') == 2, \
        "both phase_cache lookup and store are provider-qualified"
    # ...and qualified by the MAPPED model, not the tier id. Keying on the tier
    # meant re-pointing llm.models_by_provider at a different model kept the old
    # key, so the next run replayed a result from a model no longer configured.
    assert '"${PROVIDER}:${MODEL}"' not in src
    assert src.index("FINAL_MODEL=") < src.index("phase_cache.py lookup"), \
        "the mapped model must be resolved BEFORE the cache lookup"
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
    # os.path.join, not a literal with backslashes: "testdata\K-1\cases.json"
    # relies on Python preserving \K and \c as unknown escapes — a
    # SyntaxWarning today and a hard error in a future release — and its
    # .replace("\\", os.sep) was a no-op on Windows and a fixup on POSIX, so
    # the test was platform-adaptive by accident rather than by intent.
    assert written == [os.path.join("testdata", "K-1", "cases.json")]
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


# ---------------------------------------------------------------- slice 3
def test_pricing_classes(monkeypatch):
    """4.1: local is $0, a priced provider yields an ESTIMATE, and an unpriced
    one stays UNKNOWN — never a silent 0 that would understate a real bill."""
    import budget
    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000}
    assert budget.priced("ollama", "qwen", usage) == (0.0, "local")
    cost, basis = budget.priced("codex", "gpt-5-codex", usage)
    assert basis == "estimated" and cost > 0
    assert budget.priced("mystery-co", "m", usage) == (None, "unknown")


def test_ledger_carries_provider_and_basis(tmp_path, monkeypatch):
    import budget
    monkeypatch.setattr(budget, "LEDGER", tmp_path / "cost.tsv")
    # A provider-reported result.
    res = tmp_path / "r.json"
    res.write_text(json.dumps({"total_cost_usd": 0.02, "num_turns": 2,
                               "provider": "claude",
                               "usage": {"input_tokens": 10}}),
                   encoding="utf-8")
    budget.record("triage", res)
    row = budget.read_ledger()[0]
    assert row["provider"] == "claude" and row["cost_basis"] == "reported"
    # A local result: tokens, no cost figure.
    res2 = tmp_path / "r2.json"
    res2.write_text(json.dumps({"provider": "ollama", "num_turns": 1,
                                "usage": {"input_tokens": 500,
                                          "output_tokens": 50}}),
                    encoding="utf-8")
    budget.record("analyze", res2)
    row2 = budget.read_ledger()[1]
    assert row2["provider"] == "ollama" and row2["cost_basis"] == "local"
    assert row2["cost_usd"] == 0.0 and row2["input_tokens"] == 500, \
        "local runs cost nothing but their tokens are still tracked"


def test_old_ledger_rows_still_parse(tmp_path, monkeypatch):
    """The pre-provider format must not break a run (crashed-run leftovers)."""
    import budget
    monkeypatch.setattr(budget, "LEDGER", tmp_path / "cost.tsv")
    budget.LEDGER.write_text("triage\t0.05\t1\t1700000000\n", encoding="utf-8")
    row = budget.read_ledger()[0]
    assert row["provider"] == "" and row["cost_basis"] == ""
    assert budget.total()[0] == pytest.approx(0.05)


def test_report_rolls_up_by_provider_with_bases(tmp_path, monkeypatch):
    import cost_report as cr
    monkeypatch.setattr(cr, "RUNS", tmp_path / "runs")
    (tmp_path / "runs").mkdir()

    def spend(provider, basis, cost, tin=100, tout=10):
        return {"model": "m", "cost_usd": cost, "input_tokens": tin,
                "output_tokens": tout, "cache_read_tokens": 0,
                "cache_creation_tokens": 0, "turns_used": 1, "max_turns": 8,
                "provider": provider, "cost_basis": basis,
                "simulated": basis == "simulated"}
    rec = {"run_id": "r1", "trigger": {"type": "pr", "key": "K"}, "ts": 9e9,
           "phases": [
               {"name": "triage", "contract": {}, "spend": spend("ollama", "local", 0.0)},
               {"name": "generate", "contract": {}, "spend": spend("claude", "reported", 0.3)},
               {"name": "critic", "contract": {}, "spend": spend("codex", "estimated", 0.05)}]}
    (tmp_path / "runs/r1.json").write_text(json.dumps(rec), encoding="utf-8")
    rep = cr.report()
    bp = rep["by_provider"]
    assert set(bp) == {"ollama", "claude", "codex"}
    assert bp["ollama"]["cost_usd"] == 0.0 and "local" in bp["ollama"]["bases"]
    assert "reported" in bp["claude"]["bases"]
    assert "estimated" in bp["codex"]["bases"]
    # Local vs cloud token split (4.2): the number that justifies going local.
    assert rep["local_tokens"] == 110
    assert rep["cloud_tokens"] == 220
    md = cr.to_markdown(rep)
    assert "By provider" in md and "local vs cloud tokens" in md


def test_cost_view_renders_the_provider_card():
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert "cost-provider-table" in src and "cost-localsplit" in src
    # The four label classes must be distinguishable in the UI code.
    for label in ("$0 (local)", "'~$'", "unknown"):
        assert label in src


def test_mock_run_names_its_provider_mock(tmp_path):
    """Per-phase table and by_provider rollup must agree: a simulated run says
    `mock`, not a blank that reads as 'unknown provider'."""
    (tmp_path / "out").mkdir()
    (tmp_path / "reports/runs").mkdir(parents=True)
    (tmp_path / "out/cost.tsv").write_text(
        "triage\t0.01\t1\t1700000000\tclaude-haiku\t0\t0\t0\t0\t0\t\t\n",
        encoding="utf-8")
    (tmp_path / "out/triage.contract.json").write_text(
        json.dumps({"impact": "none"}), encoding="utf-8")
    env = dict(os.environ, AIQE_MOCK="1")
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/run_record.py"),
                        "RUN1", "pr", "K-1"],
                       cwd=tmp_path, env=env, capture_output=True,
                       text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    rec = json.loads(r.stdout)   # the record is printed; the caller persists it
    spend = [p for p in rec["phases"] if p["name"] == "triage"][0]["spend"]
    assert spend["provider"] == "mock" and spend["cost_basis"] == "simulated"


# ---------------------------------------------------------------- slice 4
def _fake_codex(tmp_path):
    """A stand-in codex CLI: echoes the sandbox it was given, emits a token
    event and writes a final-message file."""
    p = tmp_path / "codex"
    p.write_text(
        '#!/usr/bin/env bash\n'
        '[ "$1" = "--version" ] && { echo "codex-cli 0.0.0-test"; exit 0; }\n'
        'LAST=""; MODEL=""; SANDBOX=""\n'
        'while [ $# -gt 0 ]; do case "$1" in\n'
        '  --output-last-message) LAST=$2; shift 2 ;;\n'
        '  --model) MODEL=$2; shift 2 ;;\n'
        '  --sandbox) SANDBOX=$2; shift 2 ;;\n'
        '  *) shift ;; esac; done\n'
        'cat > /dev/null\n'
        'echo \'{"type":"agent_message","message":"intermediate"}\'\n'
        'echo \'{"type":"token_count","info":{"input_tokens":1200,'
        '"output_tokens":340,"cached_input_tokens":900}}\'\n'
        'printf \'{"sandbox":"%s","model":"%s"}\' "$SANDBOX" "$MODEL" > "$LAST"\n',
        encoding="utf-8", newline="\n")
    p.chmod(0o755)
    return p


def _run_codex(tmp_path, tools, out_name="r.json"):
    fake = _fake_codex(tmp_path)
    out = tmp_path / out_name
    env = dict(os.environ, CODEX_BIN=str(fake))
    import work_queue
    r = subprocess.run([work_queue.bash_exe(),
                        str(ROOT / "adapters/llm/codex.sh"), "run_phase",
                        "gpt-5-codex", "10", tools, str(out)],
                       input="prompt", capture_output=True, text=True,
                       env=env, cwd=tmp_path)
    return r, out


def test_codex_is_agentic_and_normalizes_its_result(tmp_path):
    r, out = _run_codex(tmp_path, "Read,Write,Edit")
    assert r.returncode == 0, r.stderr
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["provider"] == "codex" and d["model"] == "gpt-5-codex"
    # Tokens are harvested from the event stream...
    assert d["usage"]["input_tokens"] == 1200
    assert d["usage"]["output_tokens"] == 340
    assert d["usage"]["cache_read_input_tokens"] == 900
    # ...and NO dollar figure is invented: codex reports tokens, not cost.
    assert "total_cost_usd" not in d
    import work_queue
    caps = subprocess.run([work_queue.bash_exe(),
                           str(ROOT / "adapters/llm/codex.sh"),
                           "capabilities"], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)
    assert caps.stdout.strip() == "agentic"


def test_codex_maps_tool_policy_onto_a_sandbox(tmp_path):
    """Codex has no per-tool allow-list, so an authoring phase must land in
    workspace-write and an opinion-only phase in read-only — a read-only
    critic that could edit files is not advisory any more."""
    _, out = _run_codex(tmp_path, "Read,Write,Edit", "w.json")
    assert json.loads(json.loads(out.read_text())["result"])["sandbox"] == \
        "workspace-write"
    _, out2 = _run_codex(tmp_path, "Read", "ro.json")
    assert json.loads(json.loads(out2.read_text())["result"])["sandbox"] == \
        "read-only"


def test_codex_does_not_claim_a_turn_ceiling_it_cannot_enforce(tmp_path):
    _, out = _run_codex(tmp_path, "Read")
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["turn_limit_enforced"] is False
    assert d["max_turns_requested"] == 10


def test_codex_refuses_when_the_cli_is_missing(tmp_path):
    env = dict(os.environ, CODEX_BIN=str(tmp_path / "nope"))
    import work_queue
    r = subprocess.run([work_queue.bash_exe(),
                        str(ROOT / "adapters/llm/codex.sh"), "run_phase",
                        "gpt-5-codex", "5", "Read", str(tmp_path / "o.json")],
                       input="p", capture_output=True, text=True, env=env)
    assert r.returncode == 1
    assert "PROVIDER_UNAVAILABLE" in r.stderr
    assert "No silent fallback" in r.stderr
    assert not (tmp_path / "o.json").exists()


def test_codex_costs_are_estimated_never_reported():
    """A codex run has tokens and a price table, so it prices as ESTIMATED —
    it must never borrow the `reported` label a real bill carries."""
    import budget
    cost, basis = budget.priced("codex", "gpt-5-codex",
                                {"input_tokens": 1200, "output_tokens": 340})
    assert basis == "estimated" and cost > 0


def test_unmapped_model_is_a_config_error_not_a_vendor_error(monkeypatch):
    """Sending a claude id to another provider fails deep inside that vendor's
    CLI as 'unknown model'. Caught here, naming the exact key to set."""
    import llm_runner
    monkeypatch.setenv("AIQE_LLM_PROVIDER", "ollama")
    err = llm_runner.check_model_mapping("triage", "ollama")
    assert err and "models_by_provider.ollama" in err
    # codex ships mapped, so a bare switch to it works out of the box.
    assert llm_runner.check_model_mapping("triage", "codex") is None
    assert llm_runner.map_model("codex", "claude-sonnet-4-6") == "gpt-5-codex"
    # claude is the identity case and must never trip this.
    assert llm_runner.check_model_mapping("generate", "claude") is None


def test_capability_error_still_outranks_the_mapping_error(monkeypatch):
    """'this provider can never run this phase' is the more fundamental
    answer — a mapping hint must not bury it."""
    monkeypatch.setenv("AIQE_LLM_PROVIDER", "ollama")
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/llm_runner.py"),
                        "validate"], capture_output=True, text=True,
                       env=dict(os.environ, AIQE_LLM_PROVIDER="ollama"),
                       encoding="utf-8", cwd=ROOT,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 1
    gen = [ln for ln in r.stderr.splitlines() if ln.startswith("PROVIDER_CONFIG: generate")]
    assert gen and "cannot run agentic phase" in gen[0]


def test_codex_conformance_unknown_verb():
    import work_queue
    r = subprocess.run([work_queue.bash_exe(),
                        str(ROOT / "adapters/llm/codex.sh"), "bogus"],
                       capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 64


def test_provider_probe_reads_the_adapter_result_shape(monkeypatch):
    """Regression: the probe treated _adapter's (rc, out, err) TUPLE as a
    CompletedProcess, so it raised AttributeError on every non-mock run — the
    one mode it exists for. Mock mode returns early, which hid it."""
    import integration_check as ic
    monkeypatch.setenv("AIQE_MOCK", "0")
    monkeypatch.setenv("AIQE_LLM_PROVIDER", "codex")
    r = ic.CHECKS["llm_provider"]()          # must not raise
    assert r["status"] in ("ok", "fail")
    assert "codex" in r["detail"]


def _stub_codex(tmp_path, body):
    p = tmp_path / "codex"
    p.write_text("#!/usr/bin/env bash\ncat > /dev/null\n" + body,
                 encoding="utf-8", newline="\n")
    p.chmod(0o755)
    return p


@pytest.mark.parametrize("body,rc,marker", [
    ('echo "model not found" >&2\nexit 3\n', 3, "PROVIDER_FAILED"),
    ('echo \'{"type":"session.created"}\'\nexit 0\n', 1, "PROVIDER_BAD_RESPONSE"),
])
def test_codex_writes_no_result_when_it_has_nothing_to_report(tmp_path, body,
                                                              rc, marker):
    """A failed or empty provider call must leave NO result JSON: a phantom
    contract would be read downstream as a phase that ran."""
    import work_queue
    out = tmp_path / "o.json"
    r = subprocess.run([work_queue.bash_exe(),
                        str(ROOT / "adapters/llm/codex.sh"), "run_phase",
                        "m", "5", "Read", str(out)],
                       input="p", capture_output=True, text=True,
                       env=dict(os.environ, CODEX_BIN=str(_stub_codex(tmp_path, body))))
    assert r.returncode == rc          # the vendor's exit code is propagated
    assert marker in r.stderr
    assert not out.exists()


def test_adversarial_gate_harness_cannot_attack_the_scaffold():
    """Regression: tests/gate-adversarial.sh's setup() did not abort on a
    failed clone/cd, so the attack files (planted secret, out-of-scope src/)
    landed in THIS repo and the gate was run against it. Only the gate's
    exit-6 standalone check stopped it — the harness must not rely on that."""
    src = (ROOT / "tests/gate-adversarial.sh").read_text(encoding="utf-8")
    setup = src.split("setup()", 1)[1].split("\nrun_gate", 1)[0]
    assert setup.count("exit 1") >= 3, \
        "clone failure, cd failure and a still-in-ROOT check must each abort"
    assert '"$PWD" != "$ROOT"' in setup


def test_model_mapping_gates_configuration_not_capability():
    """The mapping refusal must be a CONFIG gate, not a capability claim: once
    ollama is mapped it gets its completion phases back, and only the three
    write-enabled agentic phases stay refused — on capability, as before."""
    cfg = {"models_by_provider": {"ollama": {
        "claude-haiku-4-5-20251001": "qwen2.5-coder:7b",
        "claude-sonnet-4-6": "qwen2.5-coder:32b",
        "claude-opus-4-8": "qwen2.5-coder:32b"}}}
    ok = [p for p in lr.ALL_PHASES
          if not (lr.check_assignment(p, "ollama")
                  or lr.check_model_mapping(p, "ollama", cfg))]
    assert set(lr.ALL_PHASES) - set(ok) == {
        "generate", "validate", "reviewrepair"
    }
    # codex ships mapped: every phase, no config required.
    assert all(not (lr.check_assignment(p, "codex")
                    or lr.check_model_mapping(p, "codex"))
               for p in lr.ALL_PHASES)


# ---------------------------------------------------------------- slice 5
def test_openhands_is_completion_class_not_agentic(monkeypatch):
    """2.4 correction: a delegated conversation runs the agent in ITS OWN
    sandbox, so files it writes never reach workspace/tests where the gate
    looks. Letting it serve `generate` would mean either losing the work or
    having the agent push its own branch — which the constitution forbids."""
    import work_queue
    r = subprocess.run([work_queue.bash_exe(),
                        str(ROOT / "adapters/llm/openhands.sh"), "capabilities"],
                       cwd=ROOT, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    assert r.stdout.strip() == "completion"
    assert "openhands" not in lr.AGENTIC_PROVIDERS
    monkeypatch.setenv("AIQE_OPENHANDS_PROVIDER", "1")
    err = lr.check_assignment("generate", "openhands")
    assert err and "own sandbox" in err, \
        "the refusal must say WHY, not just that it is refused"


def test_openhands_provider_is_opt_in(monkeypatch):
    """Delegation is experimental: a phase becomes a conversation and the
    spend lands on an account we cannot meter. Nobody gets that by picking an
    option in a dropdown."""
    monkeypatch.delenv("AIQE_OPENHANDS_PROVIDER", raising=False)
    for phase in ("triage", "analyze", "critic"):
        err = lr.check_assignment(phase, "openhands")
        assert err and "EXPERIMENTAL" in err
        assert "AIQE_OPENHANDS_PROVIDER=1" in err, "name the opt-in"
    monkeypatch.setenv("AIQE_OPENHANDS_PROVIDER", "1")
    assert lr.check_assignment("triage", "openhands") is None
    # ...and the capability answer still outranks the opt-in one.
    assert "cannot run agentic phase" in lr.check_assignment("generate", "openhands")


def test_openhands_adapter_refuses_without_the_flag(tmp_path):
    import work_queue
    out = tmp_path / "o.json"
    env = {k: v for k, v in os.environ.items() if k != "AIQE_OPENHANDS_PROVIDER"}
    r = subprocess.run([work_queue.bash_exe(),
                        str(ROOT / "adapters/llm/openhands.sh"), "run_phase",
                        "m", "5", "Read", str(out)],
                       input="p", capture_output=True, text=True, env=env,
                       cwd=ROOT)
    assert r.returncode == 1 and "PROVIDER_REFUSED" in r.stderr
    assert "No silent fallback" in r.stderr
    assert not out.exists()


def test_openhands_model_is_not_ours_to_map(monkeypatch):
    """The model comes from that deployment's own config — demanding a
    models_by_provider entry would be a refusal with no fix."""
    monkeypatch.setenv("AIQE_OPENHANDS_PROVIDER", "1")
    assert lr.check_model_mapping("triage", "openhands") is None


def test_delegated_phase_cost_is_unknown_never_zero():
    """The conversation's spend lands on the OpenHands account and is not
    reported to us. `unknown` is the truth; a 0 would understate a real bill."""
    import budget
    assert budget.priced("openhands", "whatever", {}) == (None, "unknown")
    src = (ROOT / "adapters/llm/openhands.sh").read_text(encoding="utf-8")
    assert "total_cost_usd" not in src.split("out = {")[1].split("}")[0]


def test_openhands_adapter_records_its_launch():
    """A conversation the user is paying for must be reachable from the UI
    even if we die mid-poll — the webhook only arrives if OpenHands can reach
    a receiver we own."""
    src = (ROOT / "adapters/llm/openhands.sh").read_text(encoding="utf-8")
    assert "record_launch" in src
    assert src.index("record_launch") < src.index("while time.time() < deadline"), \
        "record the launch BEFORE waiting on it"
    assert "conversation_url" in src


def test_engine_still_does_not_import_the_openhands_client():
    """Constitution C7 survives this slice: the ADAPTER may import the client
    (that is what the port boundary is for); engine/ may not."""
    offenders = [f.relative_to(ROOT).as_posix()
                 for f in (ROOT / "engine").rglob("*.py")
                 if f.name not in ("openhands_client.py", "openhands_events.py",
                                   "integration_check.py")
                 and "openhands_client" in f.read_text(encoding="utf-8",
                                                       errors="replace")]
    assert not offenders, offenders
    assert "openhands_client" in (
        ROOT / "adapters/llm/openhands.sh").read_text(encoding="utf-8")


FAKE_OH = '''
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
S = {"polls": 0}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _s(self, o):
        b = json.dumps(o).encode()
        self.send_response(200); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers()
        self.wfile.write(b)
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._s({"conversation_id":"c1","status":"running",
                 "url":"http://fake/conversations/c1"})
    def do_GET(self):
        if self.path.startswith("/api/conversations/c1/events"):
            self._s({"events":[
                {"source":"user","message":"the prompt"},
                {"source":"agent","message":"thinking out loud"},
                {"source":"agent","kind":"message",
                 "content":[{"type":"text","text":"FINAL-ANSWER"}]}]})
        elif self.path.startswith("/api/conversations/c1"):
            S["polls"] += 1
            self._s({"conversation_id":"c1",
                     "execution_status":"running" if S["polls"] < 2 else "finished"})
        else:
            self._s({"ok":True})
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
'''


def test_openhands_delegated_round_trip(tmp_path):
    """Start a conversation, WAIT for it to finish, and harvest the last agent
    message — not the user's prompt, not an intermediate thought."""
    import socket, time, work_queue
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv_py = tmp_path / "fake_oh.py"
    srv_py.write_text(FAKE_OH, encoding="utf-8")
    srv = subprocess.Popen([sys.executable, str(srv_py), str(port)],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):                      # wait for the socket to listen
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        out = tmp_path / "oh.json"
        env = dict(os.environ, AIQE_OPENHANDS_PROVIDER="1",
                   OPENHANDS_URL=f"http://127.0.0.1:{port}",
                   OPENHANDS_API_KEY="x", OPENHANDS_POLL_SECONDS="2",
                   AIQE_OPENHANDS_DIR=str(tmp_path / "oh-state"))
        r = subprocess.run([work_queue.bash_exe(),
                            str(ROOT / "adapters/llm/openhands.sh"), "run_phase",
                            "gpt-x", "5", "Read", str(out)],
                           input="assembled prompt", capture_output=True,
                           text=True, env=env, cwd=ROOT, timeout=120)
        assert r.returncode == 0, r.stderr
        d = json.loads(out.read_text(encoding="utf-8"))
        assert d["result"] == "FINAL-ANSWER"
        assert d["provider"] == "openhands"
        assert d["conversation_id"] == "c1" and d["conversation_url"]
        assert "usage" not in d and "total_cost_usd" not in d
    finally:
        srv.terminate()


def test_openhands_off_and_selected_as_provider_is_a_refusal(tmp_path):
    """AIQE_OPENHANDS=off says never contact it; selecting it as the LLM
    provider says the opposite. Refuse rather than silently pick a winner.

    (This also pins the Windows trap that hid the check: ROOT must reach the
    python snippet through the ENVIRONMENT. Interpolated into the source it
    becomes a backslash path, whose "C:" + backslash + "U..." is a broken
    unicode escape — and the `|| echo auto` fallback then turned the crash
    into "mode is auto", silently contacting a server marked off.)"""
    import work_queue
    out = tmp_path / "o.json"
    env = dict(os.environ, AIQE_OPENHANDS_PROVIDER="1", AIQE_OPENHANDS="off")
    r = subprocess.run([work_queue.bash_exe(),
                        str(ROOT / "adapters/llm/openhands.sh"), "run_phase",
                        "m", "5", "Read", str(out)],
                       input="p", capture_output=True, text=True, env=env,
                       cwd=ROOT)
    assert r.returncode == 1
    assert "AIQE_OPENHANDS=off" in r.stderr and "PROVIDER_REFUSED" in r.stderr
    assert not out.exists()
    src = (ROOT / "adapters/llm/openhands.sh").read_text(encoding="utf-8")
    assert "AIQE_OH_ROOT" in src, "ROOT must travel via the environment"
    assert "'$ROOT/engine/lib'" not in src


# ---------------------------------------------------------------- slice 6
def test_no_provider_ever_falls_back_to_another():
    """Constitution C12. Every adapter's failure path must END the phase, not
    reroute it: a silent hop to a paid provider is a bill the user never
    agreed to, and a hop to a weaker one is a quality change nobody reviewed.
    Pinned as a SOURCE property because the failure is an absence — you cannot
    observe a fallback that is correctly missing."""
    banned = ("claude -p", "adapters/llm/claude.sh", "fallback_provider")
    for name in ("ollama", "codex", "openhands"):
        src = (ROOT / f"adapters/llm/{name}.sh").read_text(encoding="utf-8")
        for token in banned:
            assert token not in src, \
                f"{name}.sh references {token!r} — a fallback path"
        # ...and every one of them announces the refusal in the same language.
        assert "No silent fallback" in src or "PROVIDER_REFUSED" in src
    # The resolver refuses rather than substituting a provider that works.
    err = lr.check_assignment("triage", "gpt9")
    assert err and "unknown LLM provider" in err
    assert lr.provider_for("triage", {"provider": "gpt9"}) == "gpt9", \
        "resolution reports what was CONFIGURED; it never silently corrects it"


def test_no_adapter_widens_a_read_only_policy():
    """Constitution C12, second half. An adapter whose runtime cannot express
    a per-tool allow-list (codex governs by sandbox) must still refuse to give
    write access to a read-only phase — otherwise the critic stops being
    advisory and the plan adversary becomes a second author."""
    import work_queue
    for adapter in ("llm/claude.sh", "llm/codex.sh", "llm/ollama.sh",
                    "llm/openhands.sh", "mock/llm.sh"):
        r = subprocess.run([work_queue.bash_exe(), str(ROOT / "adapters" / adapter),
                            "tool_policy", "Read"], cwd=ROOT, capture_output=True,
                           text=True, stdin=subprocess.DEVNULL)
        assert r.returncode == 0, f"{adapter}: {r.stderr}"
        assert r.stdout.split()[0] in ("readonly", "none"), \
            f"{adapter} widened a read-only policy to {r.stdout.strip()!r}"
        w = subprocess.run([work_queue.bash_exe(), str(ROOT / "adapters" / adapter),
                            "tool_policy", "Read,Write,Edit"], cwd=ROOT,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        assert w.stdout.split()[0] in ("writable", "none")


def test_codex_sandbox_answer_matches_what_run_phase_uses():
    """The tool_policy answer must be the SAME mapping run_phase applies, not
    a description of it — a description drifts the moment someone edits one."""
    src = (ROOT / "adapters/llm/codex.sh").read_text(encoding="utf-8")
    assert src.count("*Write*|*Edit*) SANDBOX=workspace-write") == 1
    assert src.count('*Write*|*Edit*) echo "writable sandbox=workspace-write') == 1


def test_parity_compare_excludes_simulated_runs(tmp_path, monkeypatch):
    """2.5 + the iron rule: a mock run is not evidence about a provider. It is
    reported separately, never averaged into a provider's numbers — a table
    that did would say a provider is cheap when nothing was measured."""
    import parity_compare as pc
    runs = tmp_path / "runs"; runs.mkdir()
    monkeypatch.setattr(pc, "RUNS", runs)

    def rec(rid, provider, cost, simulated, committed, critic=None):
        d = {"run_id": rid, "ts": 9e9,
             "gates": [{"repo": "r", "status": "COMMITTED" if committed else "NO_CHANGES"}],
             "phases": [{"name": "triage", "contract": {},
                         "spend": {"model": "m", "cost_usd": cost,
                                   "turns_used": 3, "provider": provider,
                                   "cost_basis": "simulated" if simulated else "reported",
                                   "simulated": simulated}}]}
        if critic is not None:
            d["critic"] = {"overall": critic}
        (runs / f"{rid}.json").write_text(json.dumps(d), encoding="utf-8")

    rec("r1", "claude", 0.30, False, True, 0.9)
    rec("r2", "ollama", 0.0, False, False, 0.6)
    rec("r3", "mock", 0.05, True, True, 0.99)
    rep = pc.compare()
    provs = {e["provider"] for e in rep["measured"]}
    assert provs == {"claude", "ollama"}, "a simulated run is not a data point"
    assert [e["provider"] for e in rep["simulated_excluded"]] == ["mock"]
    claude = [e for e in rep["measured"] if e["provider"] == "claude"][0]
    assert claude["commit_rate"] == 1.0 and claude["critic_avg"] == 0.9
    txt = pc.to_text(rep)
    assert "simulated run(s) excluded" in txt


def test_parity_compare_never_attributes_a_mixed_run(tmp_path, monkeypatch):
    """A run whose phases used two providers cannot be credited to either."""
    import parity_compare as pc
    runs = tmp_path / "runs"; runs.mkdir()
    monkeypatch.setattr(pc, "RUNS", runs)
    (runs / "r1.json").write_text(json.dumps({
        "run_id": "r1", "ts": 9e9, "gates": [],
        "phases": [
            {"name": "triage", "contract": {},
             "spend": {"cost_usd": 0.0, "provider": "ollama", "cost_basis": "local"}},
            {"name": "generate", "contract": {},
             "spend": {"cost_usd": 0.3, "provider": "claude", "cost_basis": "reported"}}]}),
        encoding="utf-8")
    rep = pc.compare()
    assert [e["provider"] for e in rep["measured"]] == ["mixed:claude+ollama"]


def test_parity_compare_says_so_when_nothing_was_measured(tmp_path, monkeypatch):
    import parity_compare as pc
    runs = tmp_path / "runs"; runs.mkdir()
    monkeypatch.setattr(pc, "RUNS", runs)
    txt = pc.to_text(pc.compare())
    assert "No MEASURED parity runs yet" in txt
    assert "LLM_PROVIDER=" in txt, "an empty report must name how to fill it"


def test_parity_and_provider_uat_are_wired_into_review():
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "AIQE_LLM_PROVIDER=$(LLM_PROVIDER)" in mk, "2.5: parity is per-provider"
    # `eval:` also ends in scorecard.py — take the recipe under `review:`.
    review = mk.split("\nreview:\n", 1)[1].split("\n\n", 1)[0]
    assert "tests/provider-adversarial.sh" in review, \
        "the provider UAT must run in `make review`, not only on request"
    # A literal two-character backslash-n, not a newline: an earlier heredoc
    # left one inside .PHONY, minting a phony target actually named `\n`.
    literal_backslash_n = chr(92) + "n"
    phony = mk.split(".PHONY:", 1)[1].split("\n\n", 1)[0]
    assert literal_backslash_n not in phony


def test_session_sweep_detects_a_killed_runs_leftovers(tmp_path):
    """A killed run skips a test's `finally`, leaving a throwaway repo in the
    TRACKED registry — where it surfaces later as an unrelated test's fan-out
    resolving a repo that cannot clone. The sweep must find it, and must read
    test_repositories as the LIST it is (reading it as a mapping found nothing
    and would have shipped as a no-op)."""
    import yaml
    import conftest
    reg = tmp_path / "repo-registry.yaml"
    reg.write_text(yaml.safe_dump({
        "source_repositories": [{"name": "orders-api"}],
        "test_repositories": [{"name": "e2e-api-tests-1"},
                              {"name": "zz-nofetch"}]}), encoding="utf-8")
    assert conftest.leftover_fixture_repos(reg) == ["zz-nofetch"]
    # A registry with only real repos is left completely alone.
    reg.write_text(yaml.safe_dump({
        "test_repositories": [{"name": "e2e-api-tests-1"}]}), encoding="utf-8")
    assert conftest.leftover_fixture_repos(reg) == []
    # An unreadable registry is not this file's to fix.
    assert conftest.leftover_fixture_repos(tmp_path / "nope.yaml") == []


# --------------------------------------------------- multi-pass review fixes
def test_unpriced_provider_cannot_silently_disable_the_budget(tmp_path, monkeypatch):
    """R1 (critical). An unpriced provider records cost 0 / metered 0, so the
    exit-77 ceiling never fired and the degradation ladder never started: a run
    could burn tens of millions of tokens and report "$0.00, within budget".
    Cost cannot be invented — but the INABILITY to enforce must be visible."""
    import budget
    monkeypatch.setattr(budget, "LEDGER", tmp_path / "cost.tsv")
    monkeypatch.setenv("MAX_COST_USD_PER_RUN", "1.00")
    res = tmp_path / "r.json"
    res.write_text(json.dumps({"provider": "brand-new-vendor", "num_turns": 3,
                               "usage": {"input_tokens": 5_000_000,
                                         "output_tokens": 500_000}}),
                   encoding="utf-8")
    for _ in range(5):
        budget.record("generate", res)

    calls, provs = budget.unpriced()
    assert calls == 5 and provs == ["brand-new-vendor"]
    state, msg = budget.enforceability()
    assert state == "unenforceable"
    assert "cannot fire" in msg and "pricing:" in msg, "name the fix"
    # The ceiling still cannot abort (we do not know the cost) — but nothing
    # may report this run as having been checked against a limit.
    assert budget.check(0) is None

    # One priced phase alongside them -> partial, not silence.
    res2 = tmp_path / "r2.json"
    res2.write_text(json.dumps({"provider": "claude", "total_cost_usd": 0.42,
                                "usage": {"input_tokens": 10}}), encoding="utf-8")
    budget.record("triage", res2)
    state, msg = budget.enforceability()
    assert state == "partial" and "NOT counted" in msg


def test_explicit_pricing_beats_the_hardcoded_provider_name():
    """R2. `ollama` was forced to $0 (local) regardless of the price table —
    but that adapter speaks plain OpenAI-compatible HTTP and serves PAID hosted
    gateways too, so an operator who priced it was shown $0 for a real bill."""
    import budget
    usage = {"input_tokens": 10_000_000, "output_tokens": 1_000_000}
    real = budget._pricing
    try:
        budget._pricing = lambda: {"ollama": {"*": {"in": 3.0, "out": 15.0}}}
        assert budget.priced("ollama", "gpt-4o", usage) == (45.0, "estimated")
        budget._pricing = lambda: {"ollama": "local"}
        cost, basis = budget.priced("ollama", "qwen", usage)
        assert (cost, basis) == (0.0, "local"), "the shipped default still holds"
    finally:
        budget._pricing = real
    # And org-config still ships the local default, so nothing changed for
    # anyone running a genuinely local daemon.
    import yaml
    cfg = yaml.safe_load(open(ROOT / "registry/org-config.yaml", encoding="utf-8"))
    assert (cfg.get("pricing") or {}).get("ollama") == "local"


def test_cache_key_separates_run_keys(tmp_path):
    """R3b. lookup/store accepted a run_key but never hashed it, while the
    entry's artifacts are stored under the PRODUCING run's paths. Two runs whose
    context bytes coincide — the same text pasted under two ticket ids — shared
    an entry, so the second restored the first key's plan and never wrote its
    own."""
    import phase_cache
    p = tmp_path / "p.md"; p.write_text("prompt", encoding="utf-8")
    c = tmp_path / "c.md"; c.write_text("identical context", encoding="utf-8")
    k1 = phase_cache.key("testplan", "claude:m", str(p), [str(c)], "PROJ-1")
    k2 = phase_cache.key("testplan", "claude:m", str(p), [str(c)], "PROJ-2")
    assert k1 != k2
    src = (ROOT / "engine/lib/phase_cache.py").read_text(encoding="utf-8")
    assert src.count("key(phase, model, prompt_file, context_files, run_key)") == 2, \
        "both lookup and store must feed the run key into the hash"


def test_derived_writes_confines_the_plan_path_like_its_sibling(tmp_path):
    """R4. The testdata branch refuses a path outside testdata/; the plan branch
    interpolated the key straight into testplans/<key>.md, so `..` escaped the
    checkout. pipeline.sh validates KEY (exit 64), so this was not reachable
    through a normal run — but one branch confining while its sibling does not
    is how a guard gets lost."""
    import derived_writes as dw
    assert dw.safe_key("PROJ-301") == "PROJ-301"
    for bad in ("../../ESCAPED", "/abs/path", "a/b", "", None, "x" * 90):
        assert dw.safe_key(bad) is None, bad
    real_root = dw.ROOT
    try:
        dw.ROOT = tmp_path / "repo"
        (tmp_path / "repo").mkdir()
        written, problems = dw.materialize("testplan", "../../ESCAPED",
                                           {"scenarios": [{"id": "S1"}]})
        assert written == [] and problems and "path-safe" in problems[0]
        assert not list(tmp_path.glob("ESCAPED.md")), "nothing escaped the root"
    finally:
        dw.ROOT = real_root


def test_experimental_gate_is_per_provider(monkeypatch):
    """R5. The gate was hardcoded to AIQE_OPENHANDS_PROVIDER while the container
    was a generic tuple — a second experimental provider would have been
    unlocked by OpenHands' flag."""
    import llm_runner
    assert isinstance(llm_runner.EXPERIMENTAL_PROVIDERS, dict)
    assert llm_runner.EXPERIMENTAL_PROVIDERS["openhands"] == "AIQE_OPENHANDS_PROVIDER"
    monkeypatch.setattr(llm_runner, "EXPERIMENTAL_PROVIDERS",
                        {"openhands": "AIQE_OPENHANDS_PROVIDER",
                         "codex": "AIQE_CODEX_PROVIDER"})
    monkeypatch.setenv("AIQE_OPENHANDS_PROVIDER", "1")
    monkeypatch.delenv("AIQE_CODEX_PROVIDER", raising=False)
    err = llm_runner.check_assignment("triage", "codex")
    assert err and "AIQE_CODEX_PROVIDER=1" in err, \
        "each provider is gated by its OWN flag"


def test_cost_report_never_prints_a_total_that_hides_unpriced_spend(tmp_path, monkeypatch):
    """R1b. The headline total EXCLUDES unpriced calls; printing it alone reads
    as the whole bill."""
    import cost_report as cr
    runs = tmp_path / "runs"; runs.mkdir()
    monkeypatch.setattr(cr, "RUNS", runs)
    (runs / "r1.json").write_text(json.dumps({
        "run_id": "r1", "ts": 9e9, "trigger": {"type": "pr", "key": "K"},
        "phases": [{"name": "generate", "contract": {},
                    "spend": {"model": "m", "cost_usd": 0.0, "provider": "mystery",
                              "cost_basis": "unknown", "input_tokens": 9_000_000,
                              "output_tokens": 100, "turns_used": 4}}]}),
        encoding="utf-8")
    rep = cr.report()
    assert rep["unpriced_calls"] == 1 and rep["unpriced_providers"] == ["mystery"]
    md = cr.to_markdown(rep)
    assert "total is incomplete" in md and "mystery" in md


def test_gate_never_executes_config_a_run_could_have_written():
    """Round-2 review S1 (critical). The gate EXECUTES commands.{lint,test} from
    the test repo's .ai-qe/config.yaml with the authority that holds the push
    credential. `.ai-qe/` used to be on its own writable-scope allow-list, so a
    generate phase — whose context includes untrusted ticket/PR text — could
    rewrite the config and have it run in the SAME run (verified: the planted
    command executed and the gate still said GATE_STATUS=WOULD_COMMIT).

    Two guards, both pinned here because either alone leaves a gap: without the
    scope removal the gate would commit the malicious config and the NEXT run
    would execute it."""
    src = (ROOT / "engine/gate/gate.sh").read_text(encoding="utf-8")
    scope = [ln for ln in src.splitlines() if "SCOPE_VIOLATION" in ln or "grep -vE" in ln]
    scope_rule = next(ln for ln in scope if "grep -vE" in ln)
    assert ".ai-qe/" not in scope_rule, \
        "repo config must not be writable by a run — the gate executes it"
    assert 'git show "HEAD:$CFG"' in src, \
        "lint/test commands must come from the COMMITTED config, not the tree"
    # ...and the working-tree read is gone.
    assert "yaml.safe_load(open('$CFG'))['commands']" not in src
    # The attack is pinned in the gate suite too, with both assertions.
    adv = (ROOT / "tests/gate-adversarial.sh").read_text(encoding="utf-8")
    assert "ADV-CONFIG" in adv and "never executed" in adv


# ------------------------------------------------ review pass C: state & API
def test_unreadable_state_is_never_reported_as_empty(tmp_path, monkeypatch):
    """C1. read_json_guarded handled a CORRUPT file carefully (quarantine + a
    loud warning) but returned `default` on OSError — so a transient read
    failure made the caller believe the state was empty, and its next save
    overwrote it. Verified: one OSError turned an approved plan into {}.

    The file EXISTS (absence returns default earlier), so "cannot read" must
    never be reported as "nothing there"."""
    import builtins
    import fs_lock
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"PROJ-301": {"status": "approved"}}),
                     encoding="utf-8")
    real_open = builtins.open

    def always_fails(f, *a, **kw):
        if str(f).endswith("state.json"):
            raise OSError(11, "Resource temporarily unavailable")
        return real_open(f, *a, **kw)

    monkeypatch.setattr(builtins, "open", always_fails)
    with pytest.raises(OSError, match="refusing to report it as empty"):
        fs_lock.read_json_guarded(state, {})
    monkeypatch.undo()
    assert json.loads(state.read_text(encoding="utf-8"))["PROJ-301"], \
        "the state file must survive untouched"

    # A TRANSIENT failure still succeeds — the retry keeps surfaces working.
    calls = {"n": 0}

    def flaky_once(f, *a, **kw):
        if str(f).endswith("state.json") and calls["n"] < 1:
            calls["n"] += 1
            raise OSError(11, "transient")
        return real_open(f, *a, **kw)

    monkeypatch.setattr(builtins, "open", flaky_once)
    assert fs_lock.read_json_guarded(state, {})["PROJ-301"]["status"] == "approved"
    monkeypatch.undo()

    # ...and the corrupt-file and absent-file behaviours are unchanged.
    corrupt = tmp_path / "c.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert fs_lock.read_json_guarded(corrupt, {}) == {}
    assert list(tmp_path.glob("c.json.corrupt-*")), "corrupt files still quarantine"
    assert fs_lock.read_json_guarded(tmp_path / "nope.json", {"d": 1}) == {"d": 1}


def test_bundle_import_containment_is_a_path_test_not_a_string_prefix():
    """C2. `str(target).startswith(str(ROOT))` accepted a SIBLING directory
    whose name merely starts with the root's — `../<root>-evil/payload` lives
    entirely outside the checkout and satisfied the prefix. A bundle is
    untrusted input by the module's own description (they get emailed)."""
    import state_bundle as sb
    root = sb.ROOT.resolve()

    def accepted(rel):
        target = (sb.ROOT / rel).resolve()
        return not (target == root or root not in target.parents)

    assert accepted("reports/runs/x.json")
    assert accepted("specs/K/testplan.yaml")
    for escape in ("../evil.txt", f"../{root.name}-evil/payload.txt",
                   f"../{root.name}X/p.txt", ""):
        assert not accepted(escape), escape
    src = (ROOT / "engine/lib/state_bundle.py").read_text(encoding="utf-8")
    assert "startswith(str(ROOT.resolve()))" not in src, \
        "containment must not be a string-prefix test"


def test_every_state_mutation_runs_under_a_lock():
    """The documented rule ('state-file mutations go through fs_lock') checked
    against the code rather than trusted: no read-modify-write cycle in a state
    store may run unlocked, or two writers silently lose one's decision."""
    import re
    targets = {"plan_state.py": ("_load", "_save"),
               "review_state.py": ("load", "save"),
               "work_queue.py": ("load", "save"),
               "openhands_events.py": ("load", "_save")}
    offenders = []
    for name, (ld, sv) in targets.items():
        lines = (ROOT / "engine/lib" / name).read_text(encoding="utf-8").splitlines()
        funcs, cur = {}, None
        for ln in lines:
            if re.match(r"^def ", ln):
                cur = ln.split("(")[0][4:]
                funcs[cur] = []
            if cur:
                funcs[cur].append(ln)
        for fn, body in funcs.items():
            b = "\n".join(body)
            if fn in (ld, sv):
                continue
            if f"{sv}(" in b and f"{ld}(" in b and "lock(" not in b:
                offenders.append(f"{name}:{fn}")
    assert not offenders, f"unlocked read-modify-write: {offenders}"


def test_lock_handles_the_exception_a_contended_mkdir_actually_raises():
    """C3. On Windows a PENDING-DELETE lock directory makes `mkdir` raise
    PermissionError (WinError 5), not FileExistsError — measured at ~1.8% of
    acquisitions under 6-way contention (39 of 2127). `lock()` caught only
    FileExistsError, so it escaped the retry loop entirely: no wait, no
    timeout, just an exception thrown inside whatever was mutating state."""
    import inspect
    import fs_lock
    src = inspect.getsource(fs_lock.lock)
    assert "except PermissionError" in src
    # The cause must survive into the timeout, so a REAL permission problem
    # still reports itself instead of masquerading as contention.
    assert "last error" in src or "could not acquire {lockdir}: {e}" in src


def test_release_does_not_retry_the_rmdir():
    """C3, the correction. Retrying the rmdir was MEASURED and rejected: it
    made contention worse (8/8 trials losing decisions vs 7/8) because a retry
    can outlive our ownership and delete a lock a waiter has since acquired,
    putting two writers in the critical section."""
    import inspect
    import fs_lock
    src = inspect.getsource(fs_lock._release)
    assert "for _ in range" not in src, "a retry loop here was measured as worse"
    assert "MEASURED" in src or "Retrying here was" in src, \
        "keep the reason, or the loop gets re-added"


def test_state_adversarial_suite_is_wired_in():
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    review = mk.split("\nreview:\n", 1)[1].split("\n\n", 1)[0]
    assert "tests/state-adversarial.sh" in review, \
        "the state UAT must run in `make review`, not only on request"
    script = (ROOT / "tests/state-adversarial.sh").read_text(encoding="utf-8")
    # It must isolate itself — a suite that mutates the real estate is a
    # liability, not a test.
    for override in ("AIQE_PLAN_DIR", "AIQE_REVIEWS_FILE", "AIQE_QUEUE_FILE",
                     "AIQE_OPENHANDS_DIR"):
        assert override in script, f"{override} must be redirected to a temp dir"


# ------------------------------------------- catalog slice (mapping -> context)
def _rows():
    return [
        {"test_id": "a", "test_repo": "e2e-api-tests-1",
         "mapping": {"app_repos": ["orders-api"]}},
        {"test_id": "b", "test_repo": "e2e-api-tests-1",
         "mapping": {"app_repos": []}},
        {"test_id": "c", "test_repo": "e2e-ui-tests-1",
         "mapping": {"app_repos": ["web-storefront-ui"]}},
        {"test_id": "d", "test_repo": "e2e-ui-tests-1",
         "mapping": {"app_repos": ["orders-api"]}},
    ]


def test_catalog_slice_is_filtered_by_the_mapping_that_routed_the_run():
    """`out/catalog-slice.jsonl` was named a slice and built as a
    CONCATENATION of every catalog/*.jsonl, so a PR resolving one API repo
    still received the UI repo's rows — token cost and dilution on the
    multi-repo estate this targets, and a contradiction of the fan-out design
    where each agent deliberately sees only its own conventions."""
    import catalog_slice as cs
    rows = _rows()
    sel, fell_back = cs.slice_rows(rows, ["e2e-api-tests-1"], ["orders-api"])
    assert not fell_back
    ids = {r["test_id"] for r in sel}
    assert "a" in ids and "b" in ids, "the resolved repo's own rows are kept"
    assert "d" in ids, "another repo's rows COVERING the changed app repo are kept"
    assert "c" not in ids, "an unrelated repo's unrelated rows are dropped"


def test_a_fanout_agent_sees_its_own_repo_plus_the_changed_surface():
    import catalog_slice as cs
    sel, _ = cs.slice_rows(_rows(), ["e2e-api-tests-1", "e2e-ui-tests-1"],
                           ["orders-api"], target_repo="e2e-ui-tests-1")
    ids = {r["test_id"] for r in sel}
    assert {"c", "d"} <= ids, "the target repo's own rows"
    assert "a" in ids, "and tests elsewhere covering the changed app repo"


@pytest.mark.parametrize("test_repos,source_repos", [
    (["nothing-matches"], ["nothing-matches"]),      # a filter that selects none
    ([], []),                                        # no resolution at all
])
def test_an_empty_selection_falls_back_to_the_whole_catalog(test_repos, source_repos):
    """Starving the phase is worse than over-feeding it: with no existing-test
    context, generation duplicates work it cannot see. An empty selection must
    hand over everything — and say so."""
    import catalog_slice as cs
    rows = _rows()
    sel, fell_back = cs.slice_rows(rows, test_repos, source_repos)
    assert fell_back and len(sel) == len(rows)


def test_catalog_slice_tolerates_a_damaged_row(tmp_path):
    """One malformed line must never drop a whole catalog file."""
    import catalog_slice as cs
    (tmp_path / "e2e-api-tests-1.jsonl").write_text(
        json.dumps({"test_id": "ok", "test_repo": "r"}) + "\n"
        + "{not json\n"
        + json.dumps({"test_id": "ok2", "test_repo": "r"}) + "\n",
        encoding="utf-8")
    (tmp_path / "catalog.sample.jsonl").write_text(
        json.dumps({"test_id": "sample", "test_repo": "r"}) + "\n", encoding="utf-8")
    rows = cs.load_rows(tmp_path)
    ids = {r["test_id"] for r in rows}
    assert ids == {"ok", "ok2"}, "bad line skipped, sample excluded, rest kept"


def test_pipeline_builds_the_catalog_slice_from_the_mapping():
    """Wiring pin: the pipeline must call catalog_slice (not re-concatenate
    every catalog/*.jsonl), and the fan-out must swap in a PER-REPO slice the
    same way it already swaps per-repo conventions — an agent writing into one
    repo should not be reasoning over every other repo's catalog."""
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert "catalog_slice.py out/resolve.contract.json" in src
    assert 'catalog_slice.py out/resolve.contract.json "$repo"' in src, \
        "the fan-out needs its own per-repo slice"
    assert 'elif [ "$f" = "out/catalog-slice.jsonl" ]; then ctx+=("$slice")' in src, \
        "the per-repo slice must actually be substituted into the context"
    # The fallback must survive: losing existing-test context makes generation
    # duplicate work it cannot see.
    assert "catalog.sample.jsonl" in src, "the full-catalog fallback is still there"


def test_catalog_slice_cli_reports_what_it_kept(tmp_path):
    """A slice that silently narrowed the phase's world is the failure worth
    seeing, so the CLI says how many rows it kept and why."""
    contract = tmp_path / "resolve.contract.json"
    contract.write_text(json.dumps({"test_repos": ["e2e-api-tests-1"],
                                    "source_repos": ["orders-api"]}),
                        encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/catalog_slice.py"),
                        str(contract)], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", stdin=subprocess.DEVNULL)
    assert r.returncode == 0
    assert "[catalog-slice]" in r.stderr and "row(s)" in r.stderr
    for line in r.stdout.splitlines():
        if line.strip():
            json.loads(line)          # stdout stays valid JSONL


def test_catalog_slice_falls_back_loudly_when_the_contract_is_unreadable(tmp_path):
    """No resolution to filter by must mean the FULL catalog, said out loud —
    never an empty slice."""
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/catalog_slice.py"),
                        str(tmp_path / "missing.json")], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8",
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0
    assert "full catalog" in r.stderr


# ------------------------------------ per-repo facts (knowledge base, steps 1-2)
def test_absent_facts_change_nothing():
    """Adoption is per-repo and optional: no file means no facts, and every
    accessor returns empty rather than raising. A repo nobody has documented
    must behave exactly as it did before this module existed."""
    import repo_facts as rf
    assert rf.authored("e2e-api-tests-2") == {} or isinstance(
        rf.authored("e2e-api-tests-2"), dict)
    assert rf.merged("no-such-repo") == {
        "repo": "no-such-repo", "authored": {}, "harvested": {}, "tiers": {}}
    assert rf.conventions("no-such-repo") == []


def test_application_repo_facts_are_per_repo_opt_in():
    """B4 preserves old behavior until an app repo adds an authored file."""
    import repo_facts as rf
    assert rf.is_test_repo("e2e-api-tests-1")
    assert rf.is_app_repo("orders-api")
    assert not rf.app_opted_in("orders-api")
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/repo_facts.py"),
                        "show", "orders-api"], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    assert r.returncode == 1 and "not an opted-in facts repository" in r.stderr


def test_a_rebuild_never_touches_authored_facts(tmp_path, monkeypatch):
    """THE property that makes the tier split worth having: regenerating the
    harvested tier must not be able to lose a human's assertions."""
    import repo_facts as rf
    before = (ROOT / "knowledge/facts/e2e-api-tests-1.yaml").read_bytes()
    rf.rebuild(["e2e-api-tests-1"])
    after = (ROOT / "knowledge/facts/e2e-api-tests-1.yaml").read_bytes()
    assert before == after, "a rebuild rewrote an authored file"
    # ...and the authored content is still what the loader returns.
    ids = {c["id"] for c in rf.conventions("e2e-api-tests-1")}
    assert "seed-via-fixtures" in ids


def test_authored_outranks_harvested():
    """Constitution C6 extended: nothing generated outranks a human."""
    import repo_facts as rf
    m = rf.merged("e2e-api-tests-1")
    assert m["tiers"].get("conventions") == "authored"
    assert m["tiers"].get("framework") == "harvested"


def test_severity_filter_selects_the_load_bearing_rules():
    """`must` is what earns MUST-KEEP treatment when retrieval starts ranking
    by severity (step 5) — so the filter has to be exact, not substring."""
    import repo_facts as rf
    must = {c["id"] for c in rf.conventions("e2e-api-tests-1", severity="must")}
    should = {c["id"] for c in rf.conventions("e2e-api-tests-1", severity="should")}
    assert must and should and not (must & should)


def test_validate_reports_authored_schema_problems(tmp_path, monkeypatch):
    import repo_facts as rf
    monkeypatch.setattr(rf, "FACTS_DIR", tmp_path)
    (tmp_path / "r.yaml").write_text(
        "repo: r\nschema: 1\nauthored:\n  conventions:\n"
        "    - id: bad\n      severity: critical\n", encoding="utf-8")
    problems = rf.validate("r")
    assert any("severity" in p for p in problems)
    assert any("no `rule`" in p for p in problems)


def test_a_damaged_or_future_schema_file_is_ignored_not_fatal(tmp_path, monkeypatch):
    """Facts are an ENRICHMENT. A broken one degrades to no-facts; it never
    takes down a run that worked without them."""
    import repo_facts as rf
    monkeypatch.setattr(rf, "FACTS_DIR", tmp_path)
    (tmp_path / "r.yaml").write_text("{{ not yaml", encoding="utf-8")
    assert rf.authored("r") == {}
    (tmp_path / "r.yaml").write_text("repo: r\nschema: 99\nauthored:\n  x: 1\n",
                                     encoding="utf-8")
    assert rf.authored("r") == {}, "an unknown schema is ignored, not guessed at"


def test_harvested_is_a_reshaping_of_what_the_estate_already_computes():
    """No new analysis — so the facts cannot drift away from what the phases
    actually see."""
    import repo_facts as rf
    import yaml
    h = rf.build_harvested("e2e-api-tests-1")
    assert h["framework"] and h["layer"] == "api"
    # `covers` must MIRROR the registry, whatever it currently says. Asserting a
    # literal value made this pin depend on estate state: covers: is regenerated
    # from catalog evidence, so it is empty until a bootstrap runs and the test
    # failed after clear-demo for a reason that had nothing to do with facts.
    reg = yaml.safe_load(open(ROOT / "registry/repo-registry.yaml", encoding="utf-8"))
    entry = next(t for t in reg["test_repositories"] if t["name"] == "e2e-api-tests-1")
    assert h["covers"] == list(entry.get("covers") or [])
    assert isinstance(h.get("surface_covered"), list)
    assert h["repo_kind"] == "test"
    assert "generated_at" in h  # legacy test-repo contract remains unchanged


def test_derived_facts_are_gitignored_and_authored_are_not():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "knowledge/facts/derived/" in ignore
    assert "knowledge/facts/\n" not in ignore, \
        "authored facts are somebody's work — they must be tracked"


def test_authored_facts_are_carried_by_the_state_bundle_but_derived_is_not():
    """The bundle's own criterion is "state that IS somebody's work". Authored
    facts are exactly that — a new deployment that lost them would lose the
    team's assertions about their own repos. The derived tier rebuilds, so
    carrying it would just move stale data around."""
    import state_bundle as sb
    files = {p.as_posix() for p in sb.collect()}
    assert any(f.startswith("knowledge/facts/") and f.endswith(".yaml")
               for f in files), "authored facts must be bundled"
    assert not any("knowledge/facts/derived/" in f for f in files), \
        "the derived tier rebuilds — it is not work"
    # The knowledge-only profile carries them too: they ARE transferable wisdom,
    # unlike run history.
    kfiles = {p.as_posix() for p in sb.collect(profile="knowledge")}
    assert any(f.startswith("knowledge/facts/") for f in kfiles)


def test_clear_demo_keeps_authored_facts():
    """clear-demo removes GENERATED demo data. A team's assertions about their
    repos are not demo data."""
    import demo_data
    src = pathlib.Path(demo_data.__file__).read_text(encoding="utf-8")
    assert "knowledge/facts" not in src or "KEEP" in src, \
        "clear-demo must not list authored facts for deletion"
    assert (ROOT / "knowledge/facts/e2e-api-tests-1.yaml").exists()


def test_html_escaping_covers_both_quote_characters():
    """R11. escHtml escaped & < > " but not ' — safe only while every attribute
    stays double-quoted, which is the shape of guard this codebase keeps
    losing. Escaping both removes the dependency on an unenforced convention."""
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    # A fixed window, NOT a split on ";" — every HTML entity ends in a
    # semicolon, so splitting there truncates the definition at '&amp' and the
    # assertion fails against correct code. (It did.)
    esc = src.split("const escHtml", 1)[1][:300]
    for entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert entity in esc, f"escHtml does not produce {entity}"


def test_every_prompt_taking_external_text_frames_it_as_data():
    """Constitution C4, checked with whitespace NORMALISED — the framing wraps
    across a line break in several prompts, and a naive substring search
    reports a false positive on the critic (it did, during the round-4 scan)."""
    import re
    pat = re.compile(r"data,? not instruction|never instruction|is data|as data")
    unframed = []
    for p in sorted((ROOT / "prompts").glob("*.md")):     # standalone prompts only
        txt = " ".join(p.read_text(encoding="utf-8", errors="replace").lower().split())
        if not pat.search(txt):
            unframed.append(p.name)
    assert not unframed, f"prompts missing the data framing: {unframed}"


# ---- org-config maps keyed by phase name were never checked -----------------
def test_a_typo_in_a_phase_keyed_map_is_reported():
    """`models:`, `context_scope:` and `llm.phase_providers:` are keyed by phase
    and nothing validated those keys. A typo is read by nothing — and in
    `models:` it also leaves the real phase unlisted, which falls back to the
    `generate` tier: the AUTHORING model. So a mistyped `triage` quietly moves
    that phase from haiku to sonnet on every run, evidenced only by the bill.
    """
    import llm_runner
    w = llm_runner.check_phase_keys({"models": {"trage": "haiku", "generate": "s"}})
    assert any("'trage' is not a phase" in x for x in w)
    assert any("no tier for" in x and "triage" in x for x in w), \
        "the costly half — an unlisted phase on the authoring tier — is unreported"
    for m, bad in (("context_scope", {"context_scope": {"testplann": True}}),
                   ("llm.phase_providers",
                    {"llm": {"phase_providers": {"genrate": "ollama"}}})):
        assert any(m in x for x in llm_runner.check_phase_keys(bad)), m


def test_the_checker_reads_the_whole_org_config_not_the_llm_block():
    """The first version defaulted to `_cfg()`, which is deliberately only the
    `llm:` sub-section. It therefore read `models:` off a dict that has no
    `models` key, found nothing, and pronounced a typo'd config clean — while
    the hand-built dicts in the test above passed, because they were written in
    the shape the code assumed rather than the shape on disk.
    """
    import llm_runner
    assert llm_runner._cfg() is not None
    org = llm_runner._org_cfg()
    assert "models" in org, "_org_cfg must return the document root"
    assert "provider" not in org, "_org_cfg returned the llm: block, not the root"
    src = (ROOT / "engine/lib/llm_runner.py").read_text(encoding="utf-8")
    i = src.index("def check_phase_keys")
    body = src[i:i + 2200]
    assert "_org_cfg()" in body and "else _cfg()" not in body


def test_the_shipped_org_config_is_clean():
    """A checker nobody can trust because the repo's own config trips it gets
    ignored. This also catches a phase being added to ALL_PHASES without a tier."""
    import llm_runner
    assert llm_runner.check_phase_keys() == []


def test_make_config_surfaces_it():
    """`make config` is where an operator asks what their configuration does."""
    src = (ROOT / "engine/lib/props_file.py").read_text(encoding="utf-8")
    assert "check_phase_keys()" in src and "WARNING" in src

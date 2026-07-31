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
    ollama is mapped it gets its eight completion phases back, and only
    generate/validate stay refused — on capability, as before."""
    cfg = {"models_by_provider": {"ollama": {
        "claude-haiku-4-5-20251001": "qwen2.5-coder:7b",
        "claude-sonnet-4-6": "qwen2.5-coder:32b",
        "claude-opus-4-8": "qwen2.5-coder:32b"}}}
    ok = [p for p in lr.ALL_PHASES
          if not (lr.check_assignment(p, "ollama")
                  or lr.check_model_mapping(p, "ollama", cfg))]
    assert set(lr.ALL_PHASES) - set(ok) == {"generate", "validate"}
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

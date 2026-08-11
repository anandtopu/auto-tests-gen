"""Every `{{PLACEHOLDER}}` a prompt uses must be one the runner resolves.

`prompts/validate-repair.md` says "At most {{REPAIR_LOOPS}} repair iterations."
`run_phase.sh` resolved only {{KEY}} and {{TARGET_REPO}}, so on EVERY real run
the model received that literal token instead of the configured number. The
ceiling org-config declares was never actually stated to the model it governs.

Worse, nothing enforced it either: every `repair_loops` reference in the tree is
a READER -- bin/dashboard.py, bin/qa.py, export_plan, pr_comment, run_progress,
team_report -- each displaying the number the MODEL self-reported in its
contract. docs/architecture.md described "<=3 validate-repair cycles" as a
bound. The hard stops are `--max-turns` and the exit-77 budget ceiling; the loop
count is an instruction, and the docs now say so.

The class pin below is the one that matters: a placeholder nothing resolves is
invisible (the run succeeds, the model just gets worse instructions), so it has
to be the build that notices, not a person reading two files side by side.
"""
import pathlib
import re
import subprocess
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNNER = ROOT / "engine/phases/run_phase.sh"
PROMPTS = ROOT / "prompts"
CFG = ROOT / "registry/org-config.yaml"


def _used_in_prompts():
    used = {}
    for p in sorted(PROMPTS.rglob("*.md")):
        for name in set(re.findall(r"\{\{([A-Z_]+)\}\}", p.read_text(encoding="utf-8"))):
            used.setdefault(name, []).append(p.relative_to(ROOT).as_posix())
    return used


def _runner_code():
    """run_phase.sh with shell comments stripped.

    Mutation-tested and it mattered: deleting the mapping clause from the
    instruction line SURVIVED, because `{{REPAIR_LOOPS}}` still appeared in a
    COMMENT a few lines above -- one I had written explaining the bug. A pin
    that matches its author's prose proves nothing, and this repo has been
    caught by that exact shape before.
    """
    out = []
    for line in RUNNER.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def _resolved_by_runner():
    return set(re.findall(r"\{\{([A-Z_]+)\}\}", _runner_code()))


def test_every_prompt_placeholder_is_resolved_by_the_runner():
    """THE CLASS. An unresolved placeholder does not fail a run -- it silently
    degrades the instruction, which is why nothing caught {{REPAIR_LOOPS}}."""
    used, resolved = _used_in_prompts(), _resolved_by_runner()
    orphans = {n: f for n, f in used.items() if n not in resolved}
    assert not orphans, (
        "these placeholders reach the model as literal text because "
        f"run_phase.sh never resolves them: {orphans}")


def test_the_runner_does_not_resolve_placeholders_no_prompt_uses():
    """The other direction, so the mapping line cannot rot into a description of
    variables that no longer exist."""
    used, resolved = _used_in_prompts(), _resolved_by_runner()
    stale = sorted(resolved - set(used))
    assert not stale, f"run_phase.sh maps placeholders no prompt uses: {stale}"


def test_the_prompt_template_is_still_sent_verbatim():
    """The fix must NOT substitute inline. {{KEY}} was deliberately moved out of
    the template because a run-unique value in the first few hundred tokens
    makes every invocation's prefix unique and no provider cache can hit it.
    Resolution belongs in the appended RUN PARAMETERS block."""
    src = RUNNER.read_text(encoding="utf-8")
    assert 'PROMPT_TEXT=$(cat "$PROMPT")' in src, \
        "the prompt is no longer sent verbatim; prefix caching is broken"
    assert "RUN PARAMETERS" in src
    # The value is appended, not sed-substituted into the template.
    assert not re.search(r"sed[^\n]*\{\{REPAIR_LOOPS\}\}", src)


def test_validate_declares_a_repair_loop_count_to_pass_on():
    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    assert cfg["phases"]["validate"].get("repair_loops"), \
        "validate stopped declaring repair_loops; the prompt has nothing to state"


@pytest.mark.parametrize("phase,expected", [("validate", "3"), ("triage", "")])
def test_the_runner_reads_repair_loops_only_where_it_is_declared(phase, expected):
    """Optional by design: only `validate` declares it, and a phase without one
    must emit no REPAIR_LOOPS line rather than an empty or invented value."""
    out = subprocess.run(
        [sys.executable, "-c",
         "import yaml;p=yaml.safe_load(open(r'%s'))['phases']['%s'];"
         "print(p.get('repair_loops',''))" % (CFG, phase)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    assert out.stdout.strip() == expected


def test_the_runner_emits_the_value_only_when_present():
    src = RUNNER.read_text(encoding="utf-8")
    assert 'if [ -n "${REPAIR_LOOPS:-}" ]; then' in src, \
        "an absent repair_loops would emit an empty REPAIR_LOOPS= line"
    assert 'CONTEXT+=$\'\\n\'"REPAIR_LOOPS=${REPAIR_LOOPS}"' in src


def test_the_per_phase_policy_values_come_from_one_interpreter():
    """efficiency-review §7 is about interpreter-start tax, so adding a THIRD
    `python3 -c` to read repair_loops would have been a regression. This pins
    only what this change guarantees: max_turns, allowed_tools and repair_loops
    are read together.

    Deliberately NOT a claim about the whole file. `MODEL` (and the conditional
    `CHEAP` on the degradation rung) are separate pre-existing reads that run
    BEFORE this block and are needed earlier; folding those in is a different
    change, and asserting `<= 1` here would fail the build over code this
    commit does not touch.
    """
    src = RUNNER.read_text(encoding="utf-8")
    assert src.count("_PHASE_CFG=$(python3 -c") == 1
    for var in ("TURNS", "TOOLS", "REPAIR_LOOPS"):
        assert re.search(rf'^{var}=\$\(printf .*_PHASE_CFG', src, re.M), \
            f"{var} no longer comes from the shared read"


def test_the_docs_do_not_call_the_loop_count_an_enforced_bound():
    """It is an instruction to the model. Calling it a bound in the same breath
    as --max-turns (which IS enforced) is the misreading to avoid."""
    arch = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
    line = next((l for l in arch.splitlines()
                 if "validate-repair cycles" in l), "")
    assert line, "the bounded-loops line vanished; re-point this pin"
    assert "instructed" in line or "not enforced" in line, \
        f"still described as an enforced bound: {line.strip()[:120]}"


# --------------------------------------------------- what the model receives

def _capture_prompt(phase, prompt, tmp_path):
    """Run the REAL runner with a fake `claude` on PATH and return the argv it
    was handed -- i.e. exactly what the provider would receive.

    Everything above this line reads source text. Two mutations survived that:
    deleting the mapping clause (a COMMENT still carried the token) and gutting
    the config read. Only driving the runner catches those, so the manual check
    that verified this fix is the test.
    """
    import os
    shim = tmp_path / "bin"
    shim.mkdir(exist_ok=True)
    fake = shim / "claude"
    fake.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\n" "$a"; done '
                    '> "$AIQE_PROMPT_DUMP"\n'
                    'printf \'{"result":"{}","total_cost_usd":0,"num_turns":1}\n\'\n',
                    encoding="utf-8")
    fake.chmod(0o755)
    dump = tmp_path / f"{phase}-prompt.txt"
    ctx = tmp_path / "ctx.json"
    ctx.write_text("{}", encoding="utf-8")
    sys.path.insert(0, str(ROOT / "engine" / "lib"))
    import work_queue
    subprocess.run([work_queue.bash_exe(), "engine/phases/run_phase.sh",
                    phase, prompt, str(ctx)],
                   capture_output=True, text=True, cwd=str(ROOT),
                   stdin=subprocess.DEVNULL, timeout=300,
                   env={**os.environ, "PATH": f"{shim}{os.pathsep}{os.environ['PATH']}",
                        "AIQE_PROMPT_DUMP": str(dump), "AIQE_MOCK": "0",
                        "KEY": "ZZ-PROBE-1", "AIQE_PHASE_CACHE": "0"})
    return dump.read_text(encoding="utf-8", errors="replace") if dump.exists() else ""


def test_the_configured_repair_loop_count_reaches_the_model(tmp_path):
    """The defect in one assertion: org-config says 3, and the model must be
    told 3 rather than the literal `{{REPAIR_LOOPS}}`."""
    text = _capture_prompt("validate", "prompts/validate-repair.md", tmp_path)
    assert text, "captured no prompt; the probe did not exercise the runner"
    want = str(yaml.safe_load(CFG.read_text(encoding="utf-8"))
               ["phases"]["validate"]["repair_loops"])
    assert re.search(rf"^REPAIR_LOOPS={want}$", text, re.M), \
        "the configured repair-loop count never reached the model"
    assert "{{REPAIR_LOOPS}}" in text, \
        "the template was substituted inline; that breaks the cacheable prefix"
    assert re.search(r"\{\{REPAIR_LOOPS\}\}[^\n]*use REPAIR_LOOPS", text) or \
        "use REPAIR_LOOPS" in text, "nothing tells the model how to map it"


def test_a_phase_without_repair_loops_says_nothing_about_them(tmp_path):
    """An empty `REPAIR_LOOPS=` line would instruct a phase with a blank."""
    text = _capture_prompt("triage", "prompts/pr-triage.md", tmp_path)
    assert text, "captured no prompt; the probe did not exercise the runner"
    assert not re.search(r"^REPAIR_LOOPS=", text, re.M)

"""One inventory of LLM phases, pinned across all three places that declare one.

R14 (docs/requirements-hardening.md) was `resolve_llm`: a phase with a model
tier, a written prompt and an entry in `ALL_PHASES`, but no `phases:` policy —
so `validate()` reported the config clean while a dispatch would have died on
`KeyError: 'resolve_llm'` inside run_phase.sh. Digging turned up two more of the
same shape: `resolve` (a second name for the same unbuilt fallback, this one
WITH a policy) and `escalate` (a tier for a retry escalation nothing
implements). All three looked like live configuration. Tuning any of them
changed nothing, and the only way to find that out was to read the dispatch
sites.

So the fix is not "delete three keys" — it is this invariant:

    org-config models:  ==  org-config phases:  ==  ALL_PHASES  ==  dispatched

Any future phase that is half-wired breaks the build here, with a message
naming which side is missing.
"""
import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
PIPELINE = ROOT / "engine" / "pipeline.sh"


def _org():
    return yaml.safe_load((ROOT / "registry" / "org-config.yaml").read_text(encoding="utf-8"))


def _all_phases():
    import sys
    sys.path.insert(0, str(ROOT / "engine" / "lib"))
    import llm_runner
    return set(llm_runner.ALL_PHASES)


def _dispatched():
    """Phase names pipeline.sh actually runs.

    Two traps, both hit while writing this:

    * `PHASE` appears in PROSE — "PHASE captures the phase's own exit", "PHASE
      would return the metering". Anchoring to command position (start of line)
      drops those without needing a comment parser.
    * `critic` is dispatched via `_PHASE_IMPL`, NOT `PHASE`, deliberately: the
      budget guard must not abort a fully-paid-for run over an advisory signal.
      A pin matching only `PHASE` would have called `critic` a phantom and
      "proved" the very bug it exists to catch. Both spellings are matched.
    """
    src = PIPELINE.read_text(encoding="utf-8")
    return set(re.findall(r"^[ \t]*(?:PHASE|_PHASE_IMPL)[ \t]+([a-z_]+)", src, re.M))


def test_models_and_phases_declare_the_same_set():
    org = _org()
    models, phases = set(org["models"]), set(org["phases"])
    assert models == phases, (
        f"org-config models: and phases: disagree — "
        f"tier without policy (run_phase.sh KeyErrors): {sorted(models - phases)}; "
        f"policy without tier (silently falls back to the generate tier): "
        f"{sorted(phases - models)}"
    )


def test_all_phases_matches_org_config():
    org, known = _org(), _all_phases()
    models = set(org["models"])
    assert known == models, (
        f"llm_runner.ALL_PHASES and org-config models: disagree — "
        f"in ALL_PHASES only: {sorted(known - models)}; "
        f"in org-config only: {sorted(models - known)}"
    )


def test_every_declared_phase_is_actually_dispatched():
    """The direction that caught R14. A declared phase nothing runs is config
    that looks live and is not."""
    known, live = _all_phases(), _dispatched()
    assert not (known - live), (
        f"declared but never dispatched by pipeline.sh: {sorted(known - live)} — "
        f"either wire it up or remove it; config nothing reads is a trap"
    )


def test_every_dispatched_phase_is_declared():
    """The opposite direction: a dispatched phase with no policy dies at run
    time, and with no tier silently runs on the expensive authoring model."""
    known, live = _all_phases(), _dispatched()
    assert not (live - known), (
        f"pipeline.sh dispatches undeclared phases: {sorted(live - known)} — "
        f"add them to ALL_PHASES and to org-config models:/phases:"
    )


def test_every_phase_has_a_prompt_and_no_prompt_is_orphaned():
    """`prompts/resolve-llm.md` outlived its phase. A prompt with no phase reads
    as a supported capability to anyone browsing the directory."""
    prompts = {p.name for p in (ROOT / "prompts").glob("*.md")}
    src = PIPELINE.read_text(encoding="utf-8")
    used = set(re.findall(r"([a-z0-9-]+\.md)", src))
    orphans = {p for p in prompts - used if p != "critic.md"}
    # critic.md is referenced from the _PHASE_IMPL line, which the regex above
    # does catch — assert that rather than carve out a blind exemption.
    assert "critic.md" in used, "critic.md is no longer referenced by pipeline.sh"
    assert not orphans, (
        f"prompt files no phase dispatches: {sorted(orphans)} — "
        f"delete them or wire up the phase; git history keeps the text"
    )


@pytest.mark.parametrize("gone", ["resolve_llm", "resolve", "escalate"])
def test_the_three_phantoms_stay_gone(gone):
    """Named regression guard. These three are the specific keys R14 removed;
    re-adding one without a dispatch site should fail loudly and by name."""
    org = _org()
    assert gone not in org["models"], (
        f"'{gone}' is back in org-config models: — it was removed as a phantom "
        f"(architecture SS5.8.2 / ADR-5). If it is now real, dispatch it in "
        f"pipeline.sh and add it to phases: and ALL_PHASES."
    )
    assert gone not in org["phases"], f"'{gone}' is back in org-config phases:"
    assert gone not in _all_phases(), f"'{gone}' is back in llm_runner.ALL_PHASES"

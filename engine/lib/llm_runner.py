#!/usr/bin/env python3
"""LLM Runner port resolution (multi-LLM design, stories 1.1/1.2).

The engine calls LLM providers only through `adapters/llm/<provider>.sh`,
dispatched by run_phase.sh via this resolver. Selection layering matches
every other setting: AIQE_LLM_PROVIDER env > org-config `llm.provider`
(default claude), with per-phase overrides in `llm.phase_providers`.

Capability validation is CONFIG-TIME, not mid-run: agentic phases (generate,
validate, reviewrepair — workspace edits and/or test execution loops) refuse a completion-only
provider with the fix named. There is NO silent fallback to another provider
— an impossible or unreachable assignment fails loudly (constitution-bound
in slice 6).

CLI (run_phase.sh):
  llm_runner.py resolve <phase>   -> "provider<TAB>adapter_path<TAB>model"
                                     (model = the phase's configured id mapped
                                     through llm.models_by_provider; $AIQE_PHASE_MODEL
                                     overrides the configured id — the
                                     degradation ladder uses it)
  llm_runner.py validate          -> exit 0, or exit 1 listing every bad
                                     phase->provider assignment
"""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

PROVIDERS = ("claude", "ollama", "codex", "openhands", "mock")
# Providers that can run an agentic tool loop IN OUR WORKSPACE. Completion-only
# providers may serve every other phase (context is pre-injected; SDD artifacts
# derive from contracts — see docs/multi-llm-providers.md capability matrix).
#
# openhands is NOT here, and that is a correction to the original design (2.4):
# a delegated conversation runs the agent in ITS OWN sandbox, so files it
# writes never reach workspace/tests/<repo> where the gate looks. The only ways
# to close that gap are for the agent to push its own branch — which the
# constitution forbids, the gate is the sole push path — or to invent a
# fetch-back channel. So openhands serves the COMPLETION class: we harvest its
# final message as the contract and the harness materializes artifacts, exactly
# as for a local model. (OpenHands running the pipeline as a TRIGGER is
# unaffected and still the supported way to have it author tests: there the
# gate still commits.)
AGENTIC_PROVIDERS = ("claude", "codex", "mock")
AGENTIC_PHASES = ("generate", "validate", "reviewrepair")

# Delegating a phase to another agent platform is experimental: latency is a
# conversation, not a call, and the spend lands on an account we cannot meter.
# Opt in per run/deployment rather than by picking it in a dropdown.
# provider -> the env var that opts IN to it. A dict, not a bare tuple: the
# gate used to be hardcoded to AIQE_OPENHANDS_PROVIDER, so a second
# experimental provider would silently have been unlocked by OpenHands' flag.
EXPERIMENTAL_PROVIDERS = {"openhands": "AIQE_OPENHANDS_PROVIDER"}

# Exactly the phases pipeline.sh dispatches — no more. `resolve_llm` used to sit
# at the front of this tuple: a phase with a model tier, a written prompt and an
# entry here, but no `phases:` policy, so a dispatch would have died on a
# KeyError in run_phase.sh. It was the LLM routing fallback from ADR-5 step 2,
# which is now deliberately NOT built (architecture §5.8.2) — below the
# confidence threshold the platform asks a human, and that is the terminal
# answer. registry/tests/test_phase_inventory.py pins this tuple against
# org-config and the dispatch sites so a phantom phase cannot come back.
ALL_PHASES = ("triage", "analyze", "testplan", "planadversary",
              "planarbiter", "testdata", "generate", "validate", "reviewer",
              "reviewrepair", "critic")


def _cfg():
    try:
        import yaml
        return (yaml.safe_load(open(ROOT / "registry/org-config.yaml",
                                    encoding="utf-8")) or {}).get("llm") or {}
    except Exception:
        return {}


def provider_for(phase, cfg=None):
    """The provider serving `phase`. Precedence: AIQE_LLM_PROVIDER env >
    llm.phase_providers[phase] > llm.provider > claude."""
    env = os.environ.get("AIQE_LLM_PROVIDER", "").strip().lower()
    if env:
        return env
    cfg = cfg if cfg is not None else _cfg()
    per = cfg.get("phase_providers") or {}
    base = phase.split("-", 1)[0]                  # fan-out labels -> policy phase
    return str(per.get(base) or cfg.get("provider") or "claude").lower()


def default_provider(cfg=None):
    """Estate default without arbitrarily applying a phase override."""
    env = os.environ.get("AIQE_LLM_PROVIDER", "").strip().lower()
    if env:
        return env
    cfg = cfg if cfg is not None else _cfg()
    return str(cfg.get("provider") or "claude").lower()


def adapter_path(provider):
    if provider == "mock":
        return ROOT / "adapters/mock/llm.sh"
    return ROOT / f"adapters/llm/{provider}.sh"


def map_model(provider, model, cfg=None):
    """The provider's model id for a configured (claude-namespace) id, via
    llm.models_by_provider. Unmapped -> the id passes through unchanged."""
    cfg = cfg if cfg is not None else _cfg()
    m = (cfg.get("models_by_provider") or {}).get(provider) or {}
    return str(m.get(model, model))


def check_assignment(phase, provider):
    """Error string, or None when the assignment is possible."""
    base = phase.split("-", 1)[0]
    if provider not in PROVIDERS:
        return (f"unknown LLM provider '{provider}' — one of: "
                f"{', '.join(PROVIDERS)}")
    # Capability BEFORE availability: "this provider can never run this phase"
    # is the more fundamental answer, and stays true once the adapter ships.
    # Reporting "not built yet" first would send an operator to build an
    # adapter that still could not serve the phase.
    if base in AGENTIC_PHASES and provider not in AGENTIC_PROVIDERS:
        extra = ""
        if provider == "openhands":
            extra = (" (a delegated conversation writes in its own sandbox, "
                     "not in workspace/tests where the gate looks)")
        return (f"'{provider}' cannot run agentic phase '{base}' (workspace "
                f"edits or test execution){extra} — assign claude or codex "
                f"for it in llm.phase_providers")
    gate = EXPERIMENTAL_PROVIDERS.get(provider)
    if gate and os.environ.get(gate, "").strip() != "1":
        return (f"'{provider}' as an LLM provider is EXPERIMENTAL: a phase "
                f"becomes a conversation (minutes, not seconds) and its spend "
                f"lands on an account this platform cannot meter — set "
                f"{gate}=1 to opt in")
    if not adapter_path(provider).exists():
        return (f"provider '{provider}' has no adapter at "
                f"{adapter_path(provider).relative_to(ROOT).as_posix()} — "
                f"not built yet")
    return None


def _phase_model(phase):
    """The org-config tier id for a phase (same fallback run_phase.sh uses)."""
    try:
        import yaml
        models = (yaml.safe_load(open(ROOT / "registry/org-config.yaml",
                                      encoding="utf-8")) or {}).get("models") or {}
    except Exception:
        return ""
    base = phase.split("-", 1)[0]
    return str(models.get(base) or models.get("generate") or "")


def check_model_mapping(phase, provider, cfg=None):
    """Error string when `provider` would receive a model id from another
    provider's namespace. Model ids are CONFIGURED, never guessed: sending
    `claude-sonnet-4-6` to codex or a local daemon fails deep inside the CLI
    with a message about an unknown model, long after the operator could
    connect it to a provider switch. Caught here, at config time, instead."""
    # claude/mock: the tier ids ARE claude ids, so this is the identity case.
    # openhands: the model is chosen by that deployment, not by us — demanding
    # a mapping for an id we never send would be a refusal with no fix.
    if provider in ("claude", "mock", "openhands"):
        return None
    tier = _phase_model(phase)
    if not tier:
        return None
    if map_model(provider, tier, cfg) != tier:
        return None                       # explicitly mapped — nothing to say
    if tier.startswith("claude-"):
        return (f"no model mapping for '{provider}': phase '{phase}' would "
                f"send the claude id '{tier}' to it — set "
                f"llm.models_by_provider.{provider}['{tier}'] in "
                f"registry/org-config.yaml")
    return None


def _org_cfg():
    """The WHOLE org-config document.

    Deliberately separate from `_cfg()`, which returns only the `llm:` block —
    conflating them is how the first version of `check_phase_keys` read
    `models:` off the llm sub-section, found nothing, and reported a clean
    config while a typo sat in the file.
    """
    try:
        import yaml
        return yaml.safe_load(open(ROOT / "registry/org-config.yaml",
                                   encoding="utf-8")) or {}
    except Exception:                          # noqa: BLE001
        return {}


def check_phase_keys(cfg=None):
    """Warnings about org-config maps keyed by PHASE NAME.

    `models:`, `context_scope:` and `llm.phase_providers:` are all keyed by
    phase, and nothing checked those keys. Both failure modes are silent and
    both cost money in the same direction:

    * a TYPO (`trage:`) creates a key nothing reads, and leaves the real phase
      unlisted;
    * an UNLISTED phase in `models:` falls back to the `generate` tier — the
      AUTHORING model. So a mistyped `triage` quietly moves that phase from
      haiku to sonnet on every run, and the only evidence is the bill.

    That fallback already bit this platform once (8 of 10 phases silently on the
    authoring tier). It was fixed by listing every phase, which fixes the
    symptom; this reports the cause, so the next typo is visible immediately.

    WARNINGS, not errors. An org-config may legitimately carry a key for a phase
    that does not exist yet (or no longer does), and refusing to run over a
    stale config line would be worse than the cost it is guarding.
    """
    cfg = cfg if cfg is not None else _org_cfg()   # the whole doc, not llm:
    warnings = []
    known = set(ALL_PHASES)
    maps = {
        "models": cfg.get("models") or {},
        "context_scope": cfg.get("context_scope") or {},
        "llm.phase_providers": (cfg.get("llm") or {}).get("phase_providers") or {},
    }
    for name, m in maps.items():
        if not isinstance(m, dict):
            continue
        for k in sorted(m):
            if str(k) not in known:
                warnings.append(
                    f"{name}: '{k}' is not a phase — this line is read by "
                    f"nothing. Phases are: {', '.join(sorted(known))}")
    # Only `models:` has a costly fallback, so only it is checked for gaps.
    models = maps["models"]
    if isinstance(models, dict) and models:
        missing = sorted(known - set(map(str, models)))
        if missing:
            warnings.append(
                "models: no tier for " + ", ".join(missing) +
                " — each falls back to the `generate` tier, which is the "
                "AUTHORING model. Name a tier for each, or accept the cost.")
    return warnings


def validate(cfg=None):
    """Every phase's assignment checked. Returns [errors]."""
    cfg = cfg if cfg is not None else _cfg()
    errors = []
    for phase in ALL_PHASES:
        p = provider_for(phase, cfg)
        err = check_assignment(phase, p) or check_model_mapping(phase, p, cfg)
        if err:
            errors.append(f"{phase}: {err}")
    return errors


def main(argv):
    # BOTH streams: the refusals below go to STDERR and carry non-cp1252
    # characters. Reconfiguring only stdout left a Windows consumer reading
    # utf-8 with a decode error and an EMPTY stderr — the fix hint vanished
    # exactly when it was needed.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    if argv and argv[0] == "resolve" and len(argv) > 1:
        phase = argv[1]
        provider = provider_for(phase)
        # Same two checks validate() runs, so a run that skipped config-time
        # validation still fails with the fix named rather than with a vendor
        # CLI's "unknown model" three layers down.
        err = check_assignment(phase, provider) or \
            check_model_mapping(phase, provider)
        if err:
            print(f"PROVIDER_CONFIG: {err}", file=sys.stderr)
            return 1
        model = os.environ.get("AIQE_PHASE_MODEL", "").strip()
        model = map_model(provider, model) if model else ""
        print(f"{provider}\t{adapter_path(provider)}\t{model}")
        return 0
    if argv and argv[0] == "validate":
        errors = validate()
        for e in errors:
            print(f"PROVIDER_CONFIG: {e}", file=sys.stderr)
        return 1 if errors else 0
    print("usage: llm_runner.py resolve <phase> | validate", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""LLM Runner port resolution (multi-LLM design, stories 1.1/1.2).

The engine calls LLM providers only through `adapters/llm/<provider>.sh`,
dispatched by run_phase.sh via this resolver. Selection layering matches
every other setting: AIQE_LLM_PROVIDER env > org-config `llm.provider`
(default claude), with per-phase overrides in `llm.phase_providers`.

Capability validation is CONFIG-TIME, not mid-run: agentic phases (generate,
validate — multi-file edits + test execution loops) refuse a completion-only
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
AGENTIC_PHASES = ("generate", "validate")

# Delegating a phase to another agent platform is experimental: latency is a
# conversation, not a call, and the spend lands on an account we cannot meter.
# Opt in per run/deployment rather than by picking it in a dropdown.
EXPERIMENTAL_PROVIDERS = ("openhands",)

ALL_PHASES = ("resolve_llm", "triage", "analyze", "testplan", "planadversary",
              "planarbiter", "testdata", "generate", "validate", "critic")


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
        return (f"'{provider}' cannot run agentic phase '{base}' (multi-file "
                f"edits + test execution){extra} — assign claude or codex "
                f"for it in llm.phase_providers")
    if provider in EXPERIMENTAL_PROVIDERS and \
            os.environ.get("AIQE_OPENHANDS_PROVIDER", "").strip() != "1":
        return (f"'{provider}' as an LLM provider is EXPERIMENTAL: a phase "
                f"becomes a conversation (minutes, not seconds) and its spend "
                f"lands on an account this platform cannot meter — set "
                f"AIQE_OPENHANDS_PROVIDER=1 to opt in")
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

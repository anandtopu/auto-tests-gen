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
# Providers that can run an agentic tool loop. Completion-only providers may
# serve every other phase (context is pre-injected; SDD artifacts derive from
# contracts — see docs/multi-llm-providers.md capability matrix).
AGENTIC_PROVIDERS = ("claude", "codex", "openhands", "mock")
AGENTIC_PHASES = ("generate", "validate")

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
        return (f"'{provider}' cannot run agentic phase '{base}' (multi-file "
                f"edits + test execution) — assign claude, codex or openhands "
                f"for it in llm.phase_providers")
    if not adapter_path(provider).exists():
        return (f"provider '{provider}' has no adapter at "
                f"{adapter_path(provider).relative_to(ROOT).as_posix()} — "
                f"not built yet")
    return None


def validate(cfg=None):
    """Every phase's assignment checked. Returns [errors]."""
    cfg = cfg if cfg is not None else _cfg()
    errors = []
    for phase in ALL_PHASES:
        p = provider_for(phase, cfg)
        err = check_assignment(phase, p)
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
        err = check_assignment(phase, provider)
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

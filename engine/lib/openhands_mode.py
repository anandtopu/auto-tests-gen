#!/usr/bin/env python3
"""How much this platform depends on OpenHands — the hybrid switch
(docs/integrations/standalone-operation.md).

The platform can run with OpenHands, without it, or either way. Which of those you
are in is a deployment decision, not something to infer at each call site, so it is
stated once here:

  off       Never contact OpenHands. Connectivity checks report `skipped` even when a
            URL is configured, and the dashboard refuses to start conversations.
            Use when you have no installation, or want the estate provably standalone.

  auto      (default) Use OpenHands when it is reachable; fall back to the CI /
            TaskEvent / work-queue trigger paths when it is not. An outage is
            reported as `degraded` — visible, never fatal. This is the hybrid posture:
            you get the Stop hook and path-triggered skills when the enterprise
            install is up, and runs keep working when it is not.

  required  You depend on it. An outage is a hard `fail` and exits non-zero, so a CI
            gate goes red. Only choose this if a broken OpenHands genuinely means the
            estate is broken.

`auto` is the default deliberately: it is the only setting under which an OpenHands
outage cannot stop a team from shipping tests.

Precedence is AIQE_OPENHANDS > registry/org-config.yaml > "auto", matching how
AIQE_MOCK and AIQE_CRITIC behave — an operator overriding for one run must not have
to edit committed config.

CLI:
  openhands_mode.py            print the mode
  openhands_mode.py enabled    exit 0 unless mode is `off`
"""
import os, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

MODES = ("off", "auto", "required")
DEFAULT = "auto"

# Spellings people actually type, mapped onto the three real modes.
_ALIASES = {"0": "off", "false": "off", "no": "off", "none": "off", "disabled": "off",
            "1": "auto", "true": "auto", "yes": "auto", "on": "auto", "optional": "auto",
            "hybrid": "auto", "fallback": "auto",
            "require": "required", "strict": "required", "hard": "required"}


def _normalize(raw):
    if raw is None:
        return None
    v = str(raw).strip().lower()
    if not v:
        return None
    v = _ALIASES.get(v, v)
    return v if v in MODES else None


def _from_config():
    try:
        import yaml
        loaded = yaml.safe_load(open(ROOT / "registry/org-config.yaml", encoding="utf-8"))
        return _normalize(((loaded or {}).get("openhands") or {}).get("mode"))
    except Exception:
        return None                       # unreadable config must never break a run


def mode():
    """Resolve the effective mode. Never raises; unrecognised values fall back to
    the default rather than failing a run over a typo in config."""
    return _normalize(os.environ.get("AIQE_OPENHANDS")) or _from_config() or DEFAULT


def enabled(m=None):
    """True unless OpenHands is switched off entirely."""
    return (m or mode()) != "off"


def required(m=None):
    """True when an OpenHands outage should be treated as a hard failure."""
    return (m or mode()) == "required"


def describe(m=None):
    m = m or mode()
    return {
        "off": "disabled — the platform runs standalone (CI / TaskEvent / queue triggers)",
        "auto": "hybrid — used when reachable, falls back to standalone when not",
        "required": "required — an outage fails the estate's connectivity check",
    }[m]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")   # Windows consoles default to cp1252
    m = mode()
    if len(sys.argv) > 1 and sys.argv[1] == "enabled":
        print(f"openhands: {m}")
        sys.exit(0 if enabled(m) else 1)
    print(f"openhands mode: {m} — {describe(m)}")

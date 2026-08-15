#!/usr/bin/env python3
"""Unrecognised keys in registry/org-config.yaml, named rather than ignored.

C12 says configuration is explicit and there is no silent fallback. The value
side of that is already defended in places — `spec_check.mode()` complains
about an unusable `spec.enforce`, `resolve.py` reports an unknown label-rule
key. The KEY side was not defended anywhere.

MEASURED: renaming `budgets:` to `budget:` — one character — zeroes every
workflow envelope:

    pr 1.5 -> 0.0 · jira 4.0 -> 0.0 · plan 1.0 -> 0.0 · tests 3.0 -> 0.0

which means no queue warning, no degradation ladder and no config-driven
spend ceiling, silently, with the key sitting in the file the operator just
edited. `models:` misspelt is the same shape and CLAUDE.md already records its
consequence: unlisted phases fall back to the `generate` tier, "which silently
put 8/10 phases on the authoring tier".

THERE IS NO SINGLE LOAD POINT TO VALIDATE AT: 30 modules open
`registry/org-config.yaml` themselves rather than going through
`registry.load_org_config`, so a check inside that loader would miss most
readers. This is therefore a CHECK an operator runs (`make config`) and the
pipeline reports at startup, not a gate — a config complaint must never break
every command, the same reasoning `spec_check.mode()` states for falling back
to `off` rather than refusing.

SCOPE IS TOP-LEVEL ONLY, and that is a measured decision rather than
laziness. The first version of this module also checked the sub-keys of
`budgets` and `review` from a hand-written list, and its FIRST run against the
CORRECT shipped config reported seven keys — `budgets.max_cost_usd_cross_repo`,
`review.on_unavailable` and five more — every one of which production code
actually reads. A warning that fires on a good configuration is one operators
learn to ignore, which would cost more than the defect it was added for. The
sub-key ambition needed a real schema; half a schema that looks like a whole
one is its own lie, so it was removed rather than patched with a longer list
that would rot the same way.

WHAT THAT LEAVES UNCAUGHT, said plainly: a misspelt SUB-key
(`budgets.envelope` for `budgets.envelopes`) still passes silently. The
top-level check catches the case measured above and nothing more.
"""

# Every top-level section the code reads. Pinned against the SHIPPED config in
# both directions, so adding a section without declaring it breaks the build
# rather than producing a spurious warning for everyone who uses it.
KNOWN_TOP = {
    "adapters", "budgets", "catalog", "comments", "context_budget",
    "context_scope", "critic", "generate_fanout", "knowledge", "llm",
    "models", "observability", "openhands", "phases", "plan_adversary",
    "pricing", "resolution", "retrieval_eval", "retry", "reuse", "review",
    "spec",
}


def unknown_keys(cfg):
    """['budget', 'budgets.envelope', ...] — sorted, [] when the config is
    clean or unreadable.

    A non-mapping config is NOT reported as a pile of unknown keys: that is a
    different failure with a different fix, and the loaders already treat it
    as absent.
    """
    if not isinstance(cfg, dict):
        return []
    return sorted(k for k in cfg if k not in KNOWN_TOP)


def report(cfg, warn=None):
    """Say what will be ignored, and what it means. "" when clean.

    Silent on a good config by construction — a warning that fires on a
    correct setup is one operators learn to scroll past, which is the whole
    reason the existing value-side complaints are conditional too.
    """
    unknown = unknown_keys(cfg)
    if not unknown:
        return ""
    msg = (f"[org-config] {len(unknown)} key(s) nothing reads: "
           f"{', '.join(unknown)} — these are IGNORED, so whatever they were "
           f"meant to configure is running on its default. Check the spelling "
           f"against registry/org-config.yaml's shipped sections.")
    if warn is not None:
        warn(msg)
    return msg


if __name__ == "__main__":
    import os
    import pathlib
    import sys
    import yaml
    sys.stdout.reconfigure(encoding="utf-8")
    # AIQE_ROOT so the unreadable-config branch can be DRIVEN rather than
    # reasoned about; engine/gate/spec_check.py already resolves its root the
    # same way.
    root = pathlib.Path(os.environ.get("AIQE_ROOT") or
                        pathlib.Path(__file__).resolve().parents[2])
    try:
        cfg = yaml.safe_load(
            (root / "registry/org-config.yaml").read_text(encoding="utf-8"))
    except Exception as exc:                     # unreadable is not "unknown"
        print(f"[org-config] could not be read: {exc}")
        sys.exit(0)
    line = report(cfg)
    print(line if line else
          "[org-config] every configured section is one the code reads.")

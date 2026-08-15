#!/usr/bin/env python3
"""Unrecognised keys on a repo-registry entry, named rather than ignored.

The sibling of `org_config_check`, aimed at the file whose typos change
ROUTING — the one failure this platform cannot see from the inside, because
nothing downstream can notice work that was never routed.

MEASURED against the shipped registry:

  * `covers:` mistyped as `cover:` -> `test_repos_for(reg, "orders-api")`
    returns `[]`. Every run for that repo resolves NO test repo and generates
    nothing, silently.
  * `testable_paths:` mistyped -> resolve falls back to `["**"]`, so EVERY
    changed file counts as testable. The opposite direction, equally silent.

`repo_admin` validates on WRITE (name charset, kind, scm), but the registry is
a tracked YAML file that people edit by hand and that merges bring together,
and NOTHING validated it on read.

THE KNOWN SET COMES FROM THE WRITER, NOT FROM THE SHIPPED FILE, and that is a
deliberate difference from `org_config_check`. There, every declared section
had to appear in the shipped config, because a name left behind after a
section is removed is an allow-list excusing a ghost. Here the same rule would
be wrong: `stash_project` is a legitimate optional field that this demo estate
does not use, and deriving the set from one sample would warn every Stash
deployment about a key the platform itself writes. So the schema is taken from
`repo_admin.upsert_app` / `upsert_test` — the functions that CREATE entries —
plus the fields the engine maintains, and the pin runs one direction only:
every key in the shipped registry must be known.

Not a gate, for the reason `spec_check.mode()` states: a configuration
complaint must never break the commands an operator runs to diagnose it.
"""

# What `repo_admin` can write, plus the fields the engine maintains itself.
# `covers` is GENERATED from catalog evidence by regen_coverage.py and is never
# hand-edited; it is listed because it is legitimately present, not because a
# human should type it.
COMMON = {"name", "scm", "url", "stash_project"}
APP_KEYS = COMMON | {
    "type",                 # written as `type` from upsert_app's `kind`
    "domains", "testable_paths", "contract", "route_table",
    "consumes_services", "consumed_by",
}
TEST_KEYS = COMMON | {
    "layer", "framework", "layout", "scope", "covers", "jenkins_job",
}

SECTIONS = {"source_repositories": APP_KEYS, "test_repositories": TEST_KEYS}


def unknown_keys(reg):
    """['e2e-api-tests-1.cover', ...] — sorted, [] when clean or unreadable.

    Named per entry: "an unknown key" is not actionable when a registry holds
    forty repos, and the whole point is telling someone which line to fix.
    """
    if not isinstance(reg, dict):
        return []
    out = []
    for section, known in SECTIONS.items():
        entries = reg.get(section)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            who = entry.get("name") or "<unnamed>"
            out += [f"{who}.{k}" for k in entry if k not in known]
    return sorted(out)


def report(reg, warn=None):
    """What will be ignored and what it costs. "" when clean."""
    unknown = unknown_keys(reg)
    if not unknown:
        return ""
    msg = (f"[registry] {len(unknown)} key(s) nothing reads: "
           f"{', '.join(unknown)} — these are IGNORED. A misspelt routing key "
           f"does not fail: `covers` resolves no test repo (runs generate "
           f"nothing) and `testable_paths` falls back to `**` (everything "
           f"looks testable). Fix the spelling in registry/repo-registry.yaml.")
    if warn is not None:
        warn(msg)
    return msg


if __name__ == "__main__":
    import os
    import pathlib
    import sys
    import yaml
    sys.stdout.reconfigure(encoding="utf-8")
    root = pathlib.Path(os.environ.get("AIQE_ROOT") or
                        pathlib.Path(__file__).resolve().parents[2])
    path = pathlib.Path(os.environ.get("AIQE_REGISTRY_FILE") or
                        root / "registry/repo-registry.yaml")
    try:
        reg = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[registry] could not be read: {exc}")
        sys.exit(0)
    line = report(reg)
    print(line if line else
          "[registry] every configured repo key is one the code reads.")

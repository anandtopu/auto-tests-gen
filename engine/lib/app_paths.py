"""Where MUTABLE state lives, as opposed to where the code lives.

`/app` is a git checkout that mutates itself: it regenerates `AGENTS.md`, edits
`registry/repo-registry.yaml` from the Settings UI, bootstraps `catalog/*.jsonl`,
writes `testplans/`, `specs/<KEY>/`, `testdata/` and four subtrees of
`knowledge/`. That is deliberate and works fine for development, where the
checkout IS the deployment. It is also exactly what `readOnlyRootFilesystem`
forbids, which is why R12 was never a one-line manifest change.

The obvious fix — mount a volume over each writable directory — is wrong here,
and the audit is what showed it. Every mutable directory except `testplans/`
and `testdata/` MIXES data with code or config:

    catalog/    bootstrap/*.py (code) + schema.json  |  *.jsonl, review/, health.json
    registry/   tests/ (code) + org-config.yaml      |  repo-registry.yaml
    specs/      platform/constitution.yaml           |  <KEY>/
    knowledge/  repos/, curated/, facts/ (tracked)   |  generated/, synced/, facts/derived/
    .agents/    7 hand-authored skills               |  2 generated skills

Mounting a volume over `catalog/` hides `catalog/bootstrap/*.py`. Seeding the
volume from the image instead freezes that code and `org-config.yaml` at
first-boot forever — an image upgrade would ship new logic that never runs,
which is a worse failure than the one being fixed because it is silent.

So state is RELOCATED rather than mounted over, through this module. Every
mutable path resolves as:

    the path's own env knob   (AIQE_SPEC_DIR, AIQE_TESTPLAN_DIR, ...)
      else AIQE_STATE_DIR/<relative path>
      else ROOT/<relative path>        <- unchanged for every existing caller

The per-path knobs already existed as the test-isolation mechanism (the state
adversarial suite drives them); this keeps them authoritative so a test that
redirects one directory is unaffected by a container that redirects all of them.
With neither set, every function below returns exactly what the callers
hard-coded before, so development and the demo estate are byte-identical —
pinned by test_app_paths.py.
"""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def state_root():
    """The mutable-state root. ROOT unless AIQE_STATE_DIR redirects it."""
    v = (os.environ.get("AIQE_STATE_DIR") or "").strip()
    return pathlib.Path(v) if v else ROOT


def _p(env_key, rel):
    """A specific knob wins, then the state root, then the checkout."""
    v = (os.environ.get(env_key) or "").strip() if env_key else ""
    return pathlib.Path(v) if v else state_root() / rel


# --- mutable leaves -------------------------------------------------------
# Each names the knob that already existed for it, or None where this module
# introduces the first one.

def catalog_dir():
    """`*.jsonl`, `review/`, `health.json` — NOT `bootstrap/` or `schema.json`,
    which are code and travel with the image."""
    return _p("AIQE_CATALOG_DIR", "catalog")


def registry_file():
    """The estate. Edited by the Settings UI; `org-config.yaml` is NOT here
    because it is configuration that must upgrade with the image."""
    return _p("AIQE_REGISTRY_FILE", "registry/repo-registry.yaml")


def testplans_dir():
    return _p("AIQE_TESTPLAN_DIR", "testplans")


def testdata_dir():
    return _p("AIQE_TESTDATA_DIR", "testdata")


def specs_dir():
    return _p("AIQE_SPEC_DIR", "specs")


def agents_file():
    """Generated estate knowledge. Purely derived — a missing one self-heals on
    the next regeneration, so it needs no seeding."""
    return _p("AIQE_AGENTS_FILE", "AGENTS.md")


def skills_dir():
    """Only the GENERATED path-triggered skills land here. The hand-authored
    task skills ship in the image and are read from ROOT."""
    return _p("AIQE_SKILLS_DIR", ".agents/skills")


def knowledge_dir(sub=""):
    """`generated/`, `synced/`, `curated/`, `facts/` all mutate."""
    base = _p("AIQE_KNOWLEDGE_DIR", "knowledge")
    return base / sub if sub else base


# Relative paths whose content ships in the image and must therefore be copied
# into an empty state root on first boot. `AGENTS.md`, `testplans/`, `testdata/`
# and `specs/<KEY>/` are absent: they are generated, and an empty directory is
# the correct starting state for them.
SEEDED = ("catalog", "registry/repo-registry.yaml", "knowledge")


def describe():
    """What every mutable path resolves to — for `make config` and the
    container entrypoint, so an operator can see the mapping without reading
    this file."""
    return {
        "state_root": str(state_root()),
        "catalog": str(catalog_dir()),
        "registry_file": str(registry_file()),
        "testplans": str(testplans_dir()),
        "testdata": str(testdata_dir()),
        "specs": str(specs_dir()),
        "agents_file": str(agents_file()),
        "skills": str(skills_dir()),
        "knowledge": str(knowledge_dir()),
    }


if __name__ == "__main__":
    import json
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(describe(), indent=2))

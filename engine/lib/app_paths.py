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


def _p(env_key, rel, root=None):
    """A specific knob wins, then the state root, then the caller's checkout.

    `root` exists because several modules are unit-tested by patching their own
    module-level ROOT at a tmp_path, and a resolver that ignored that would
    silently read the LIVE estate during those tests — which is how the first
    rewiring attempt broke four plan-similarity tests. When nothing is
    configured the caller's root wins, so that isolation contract is preserved
    exactly; when a knob or AIQE_STATE_DIR IS set, configuration wins, because
    a container that relocated state means it.
    """
    v = (os.environ.get(env_key) or "").strip() if env_key else ""
    if v:
        return pathlib.Path(v)
    sr = (os.environ.get("AIQE_STATE_DIR") or "").strip()
    if sr:
        return pathlib.Path(sr) / rel
    return (pathlib.Path(root) if root else ROOT) / rel


# --- mutable leaves -------------------------------------------------------
# Each names the knob that already existed for it, or None where this module
# introduces the first one.

def catalog_dir(root=None):
    """`*.jsonl`, `review/`, `health.json` — NOT `bootstrap/` or `schema.json`,
    which are code and travel with the image."""
    return _p("AIQE_CATALOG_DIR", "catalog", root)


# The catalog is READ by twelve modules and each had copy-pasted the same three
# lines against a hardcoded `catalog/`; exactly one (regen_coverage) resolved it
# through catalog_dir(). So under AIQE_CATALOG_DIR the bootstrap wrote to the
# relocated directory and routing regenerated from it, while AGENTS.md, the
# query index and every report still read the image path — the estate knowledge
# injected into every LLM phase silently described a catalog nobody was writing
# to. `catalog/*.jsonl` was in SEEDED all along, and the pin asserted the string
# was listed, not that any reader honoured it.
SAMPLE_CATALOG = "catalog.sample.jsonl"


def catalog_files(root=None):
    """Every catalog mapping file, sorted, documentation fixture excluded.

    The one place that decides what "the catalog" means. Returns [] when the
    directory is absent — a fresh checkout before bootstrap, which every caller
    already handled as "no evidence yet".
    """
    import glob as _glob
    return [pathlib.Path(f)
            for f in sorted(_glob.glob(str(catalog_dir(root) / "*.jsonl")))
            if pathlib.Path(f).name != SAMPLE_CATALOG]


def catalog_health(root=None):
    """CI-derived flake/health data. Follows the catalog when it is relocated —
    an explicit AIQE_HEALTH_FILE still wins, but leaving health.json behind in
    the image path while the mappings moved would split one dataset in two."""
    v = (os.environ.get("AIQE_HEALTH_FILE") or "").strip()
    return pathlib.Path(v) if v else catalog_dir(root) / "health.json"


def catalog_review_dir(root=None):
    return catalog_dir(root) / "review"


def registry_file(root=None):
    """The estate. Edited by the Settings UI; `org-config.yaml` is NOT here
    because it is configuration that must upgrade with the image."""
    return _p("AIQE_REGISTRY_FILE", "registry/repo-registry.yaml", root)


def testplans_dir(root=None):
    return _p("AIQE_TESTPLAN_DIR", "testplans", root)


def testdata_dir(root=None):
    return _p("AIQE_TESTDATA_DIR", "testdata", root)


def specs_dir(root=None):
    return _p("AIQE_SPEC_DIR", "specs", root)


def agents_file(root=None):
    """Generated estate knowledge. Purely derived — a missing one self-heals on
    the next regeneration, so it needs no seeding."""
    return _p("AIQE_AGENTS_FILE", "AGENTS.md", root)


def skills_dir(root=None):
    """Only the GENERATED path-triggered skills land here. The hand-authored
    task skills ship in the image and are read from ROOT."""
    return _p("AIQE_SKILLS_DIR", ".agents/skills", root)


def knowledge_dir(sub="", root=None):
    """`generated/`, `synced/`, `curated/`, `facts/` all mutate."""
    base = _p("AIQE_KNOWLEDGE_DIR", "knowledge", root)
    return base / sub if sub else base


def artifacts_dir(root=None):
    """Durable agent artifacts on the reports volume, independently isolatable."""
    return _p("AIQE_ARTIFACTS_DIR", "reports/agent-artifacts", root)


# Paths whose content ships in the image and must be copied into an empty state
# root on first boot (bin/container-entrypoint.sh reads this via `--seeded`).
#
# DATA ONLY, and deliberately narrower than the directories that contain it.
# `catalog/` as a whole would drag `bootstrap/*.py` and `schema.json` into the
# volume, and `registry/` would drag `org-config.yaml` — at which point the
# volume owns the code and configuration, an image upgrade ships logic that
# never runs, and we have rebuilt the exact failure this design exists to avoid.
# Globs are expanded by the entrypoint.
#
# `AGENTS.md`, `testplans/`, `testdata/` and `specs/<KEY>/` are absent because
# they are GENERATED: an empty directory is their correct initial state, and
# seeding one would restore a stale plan over an empty volume and call it state.
SEEDED = (
    "catalog/*.jsonl",           # the mappings — what each test covers
    "catalog/review",            # pending review queues
    "registry/repo-registry.yaml",   # the estate; NOT registry/org-config.yaml
    "knowledge/repos",           # team notes (tracked)
    "knowledge/curated",         # curated per-repo guidance (tracked)
    "knowledge/facts",           # authored per-repo facts (tracked)
)


# Repo-relative -> resolver, for callers that carry a relative path rather than
# a named directory (`phase_cache`'s artifact list, `derived_writes`' writer).
# Only the first segment is consulted, so `testplans/PROJ-1.md` follows
# `testplans_dir()` while `engine/lib/x.py` stays in the checkout.
_MUTABLE_TOP = {
    "testplans": testplans_dir,
    "testdata": testdata_dir,
    "specs": specs_dir,
    "catalog": catalog_dir,
    "knowledge": knowledge_dir,
}


# Subpaths that live INSIDE a mutable directory but are code or configuration,
# so they must stay in the image. Without these, `catalog/` being mutable drags
# `catalog/bootstrap/*.py` along with it and `specs/` drags the constitution —
# which is the freeze this module exists to prevent, reintroduced one level
# down. Caught by test_code_and_config_are_never_relocated.
_FROZEN_SUBPATHS = (
    "catalog/bootstrap", "catalog/schema.json", "catalog/templates",
    "registry/tests", "registry/org-config.yaml",
    "specs/platform",
)


# Content that lives INSIDE a seeded directory but must never be copied into a
# state volume. SEEDED names directories (`catalog/review`, `knowledge/facts`)
# whose contents are not uniformly data:
#
#   * `export_review_queue.py` is CODE. The design rule is "seed data only";
#     `state_bundle` already learned this exact lesson — excluding by directory
#     missed this same file — and a `.pyc` beside it is worse.
#   * `knowledge/facts/derived/` is the HARVESTED tier: gitignored, rebuilt by
#     `make repo-facts`, and deliberately excluded from the state bundle
#     because it regenerates. Seeded, it plants stale harvested facts in a
#     fresh volume where they are indistinguishable from a team's assertions.
#
# The old pin asserted the SEEDED strings looked right; nothing checked what a
# boot actually copied, and a boot copied all three.
SEED_EXCLUDE_SEGMENTS = ("__pycache__", "derived")
SEED_EXCLUDE_SUFFIXES = (".py", ".pyc", ".pyo", ".sh")


def _seed_excluded(rel):
    parts = rel.split("/")
    if _is_frozen(parts):
        return True
    if any(p in SEED_EXCLUDE_SEGMENTS for p in parts):
        return True
    return rel.endswith(SEED_EXCLUDE_SUFFIXES)


def seed_plan(root=None):
    """Repo-relative FILES a first boot should copy into an empty state root.

    Expansion and exclusion happen HERE, not in the entrypoint: bash deciding
    policy is how `catalog/review` came to seed a `.py` and a `__pycache__`
    while a Python-side pin asserted no code was seeded. The entrypoint copies
    what this returns and decides nothing.
    """
    import glob as _glob
    base = pathlib.Path(root) if root else ROOT
    out = []
    for pat in SEEDED:
        for m in sorted(_glob.glob(str(base / pat))):
            p = pathlib.Path(m)
            if p.is_dir():
                files = sorted(q for q in p.rglob("*") if q.is_file())
            else:
                files = [p]
            for q in files:
                rel = q.relative_to(base).as_posix()
                if not _seed_excluded(rel):
                    out.append(rel)
    return sorted(set(out))


def _is_frozen(parts):
    """Segment comparison, never a string prefix — `startswith` would treat
    `catalog/bootstrap-old/` as frozen and, worse, is the same defect class as
    R5 (a sibling sharing a name prefix)."""
    for f in _FROZEN_SUBPATHS:
        fp = f.split("/")
        if parts[:len(fp)] == fp:
            return True
    return False


def resolve_rel(rel, root=None):
    """Map a repo-relative path onto wherever that path actually lives.

    Anything not named above resolves under the checkout unchanged — `out/`,
    `reports/` and `workspace/` are volume-mounted already, and code paths must
    never move. Callers keep passing the same relative strings they always did.
    """
    s = str(rel).replace("\\", "/").lstrip("/")
    if _is_frozen(s.split("/")):
        return (pathlib.Path(root) if root else ROOT) / s
    head, _, tail = s.partition("/")
    if head == "AGENTS.md" and not tail:
        return agents_file(root)
    # Two single-file leaves whose PARENT is not mutable. Without these,
    # resolve_rel and registry_file() would disagree about the same path — no
    # live caller passes it today, but a resolver that contradicts its sibling
    # is a guard waiting to be lost.
    if s == "registry/repo-registry.yaml":
        return registry_file(root)
    fn = _MUTABLE_TOP.get(head)
    if fn is None:
        return (pathlib.Path(root) if root else ROOT) / s
    return (fn(root) / tail) if tail else fn(root)


def ensure_dirs():
    """Create the generated directories under the state root.

    In a checkout these already exist (they are tracked, or a previous run made
    them), so this is a no-op — which is why it went unnoticed until the first
    run against a fresh AIQE_STATE_DIR failed with
    `.../statedir/testplans/PROJ-301.md: No such file or directory`. A relocated
    state root starts EMPTY, and `cat > "$dir/file"` does not create `$dir`.

    Only generated directories are created. The SEEDED paths are deliberately
    not touched here: an empty `catalog/` is a different thing from a seeded
    one, and silently creating it would let a first boot look healthy while the
    estate's mappings were missing.
    """
    made = []
    for d in (testplans_dir(), testdata_dir(), specs_dir(), skills_dir()):
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            made.append(str(d))
    return made


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
        "artifacts": str(artifacts_dir()),
    }


def sh_exports():
    """Shell assignments for pipeline.sh to `eval`.

    bash must NOT re-implement the precedence rules — one source of truth, or
    the two drift and a container half-relocates. Values are shell-quoted:
    they are ours today, but `eval` of unquoted output is the shortcut that
    becomes an injection the day a path comes from configuration.
    """
    import shlex
    pairs = [("AIQE_P_TESTPLANS", testplans_dir()), ("AIQE_P_TESTDATA", testdata_dir()),
             ("AIQE_P_SPECS", specs_dir()), ("AIQE_P_CATALOG", catalog_dir()),
             ("AIQE_P_KNOWLEDGE", knowledge_dir()), ("AIQE_P_AGENTS", agents_file()),
             ("AIQE_P_REGISTRY", registry_file()), ("AIQE_P_SKILLS", skills_dir()),
             ("AIQE_P_ARTIFACTS", artifacts_dir())]
    return chr(10).join(f"{k}={shlex.quote(str(v))}" for k, v in pairs)


if __name__ == "__main__":
    import json
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    if "--sh" in sys.argv:
        ensure_dirs()
        print(sh_exports())
    elif "--seed-plan" in sys.argv:
        for rel in seed_plan():
            print(rel)
    elif "--seeded" in sys.argv:
        for rel in SEEDED:
            print(rel)
    elif "--ensure-dirs" in sys.argv:
        for d in ensure_dirs():
            print(f"created {d}")
    else:
        print(json.dumps(describe(), indent=2))

#!/usr/bin/env python3
"""Existing-approach exemplars: real code from each target E2E repo, handed to the
generate/validate phases so new tests MIRROR the repo's own approach instead of
inventing a new one (feature: "new tests should not introduce a new approach").

Deterministic, no LLM. For each resolved test repo it profiles the clone:

  - shared helpers  — relative modules imported by 2+ specs (the repo's sanctioned
    client/util layer), included as full code so the agent reuses instead of
    hand-rolling;
  - exemplar specs  — up to EXEMPLARS existing specs whose imports best match the
    repo norm (paths containing legacy/deprecated are penalized: they demonstrate
    the OLD approach, which is exactly what new tests must not copy either);
  - convention facts — observed test-fn style (test/it/describe), module style
    (require vs import), assertion library, spec filename suffix.

Output is one markdown context file (out/repo-conventions.md), framed imperatively:
FOLLOW THIS — the same instruction the prompts and the test-generation skill carry.
Empty/missing repos degrade to an explicit "no existing specs" note, never an error:
a brand-new test repo genuinely has no approach to follow yet.

CLI: spec_exemplars.py <out_md> <repo_name>...   (repo located at workspace/tests/,
     demo/ as the fixture fallback; layout from the registry)
"""
import os, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

EXEMPLARS = 2                # full spec files shown per repo
MAX_HELPERS = 3
SPEC_CAP = 3000              # chars per included file — context budget, not truth
HELPER_CAP = 2000
_IMPORT_RE = re.compile(
    r"""(?:require\(\s*['"](\.[^'"]+)['"]\s*\)|from\s+['"](\.[^'"]+)['"]|import\s+['"](\.[^'"]+)['"])""")
_PENALIZED = ("legacy", "deprecated", "old", "archive")


def _read(p):
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _rel_imports(text):
    return [next(g for g in m.groups() if g) for m in _IMPORT_RE.finditer(text)]


def _resolve(spec_path, rel):
    """Resolve a relative import to a repo file (tries as-is, .js, .ts, /index.js).
    Total: a pathological import (e.g. one that walks to the filesystem root)
    returns None rather than raising — a crash here would silently disable the
    whole existing-approach feature for the run."""
    try:
        base = (spec_path.parent / rel).resolve()
        for cand in (base, pathlib.Path(str(base) + ".js"),
                     pathlib.Path(str(base) + ".ts"),
                     base / "index.js", base / "index.ts"):
            if cand.is_file():
                return cand
    except (OSError, ValueError):
        pass
    return None


def _semantic_ranks(repo):
    """rel-path -> rank for this repo's spec chunks, by semantic relevance to
    the run's signals (diff/ticket/plan). {} when embeddings are unconfigured,
    signals are absent, the index has no rows for the repo, or anything at all
    fails — the heuristic ordering is ALWAYS the fallback (pinned)."""
    try:
        import embeddings
        if not embeddings.configured():
            return {}
        import context_scope
        import vector_index
        sig = context_scope.gather_signals(os.environ.get("KEY", ""))
        if not sig.strip():
            return {}
        hits = vector_index.query(sig[:2000], k=20, kind="spec", repo=repo)
        return {h["chunk_id"].split(":", 2)[2]: i for i, h in enumerate(hits)}
    except Exception:
        return {}


def profile(repo_root, name, specs_dir):
    repo_root = pathlib.Path(repo_root).resolve()
    spec_root = repo_root / specs_dir if specs_dir else repo_root
    # Both idiomatic namings: *.spec.* and *.test.* (node --test convention) —
    # a mature .test.js estate must not be misread as "no approach to follow".
    specs = sorted(p for pat in ("**/*.spec.js", "**/*.spec.ts",
                                 "**/*.test.js", "**/*.test.ts")
                   for p in spec_root.glob(pat))
    if not specs:
        return {"name": name, "specs": 0}

    texts = {p: _read(p) for p in specs}
    # Shared helpers: relative modules 2+ specs import. Only files INSIDE the
    # repo qualify (a monorepo import that resolves into a sibling clone is not
    # this repo's helper — and relative_to() on it would crash the run).
    helper_users = {}
    for p, t in texts.items():
        for rel in set(_rel_imports(t)):
            target = _resolve(p, rel)
            if target is not None and str(target).startswith(str(repo_root)):
                helper_users.setdefault(target, set()).add(p)
    helpers = sorted((h for h, users in helper_users.items() if len(users) >= 2),
                     key=lambda h: (-len(helper_users[h]), str(h)))[:MAX_HELPERS]

    # Convention facts (counted over all specs — the repo norm, not one file's habit)
    joined = "\n".join(texts.values())
    facts = []
    fn = max((("test(", joined.count("test(")), ("it(", joined.count("it(")),
              ("describe(", joined.count("describe("))), key=lambda x: x[1])
    if fn[1]:
        facts.append(f"test functions use `{fn[0]}...)`")
    mod = "require(...)" if joined.count("require(") >= joined.count("import ") else "import"
    facts.append(f"module style is `{mod}`")
    for lib, label in (("node:assert", "`node:assert` assertions"),
                       ("expect(", "`expect()` assertions"), ("chai", "chai assertions")):
        if lib in joined:
            facts.append(label)
            break
    # Derive the naming fact from what actually matched (.spec.js vs .test.ts …)
    endings = sorted({"".join(p.suffixes[-2:]) for p in specs})
    facts.append(f"spec files end in `{'` / `'.join(endings)}` under `{specs_dir or './'}`")

    # Exemplars: most-representative specs — imports closest to the repo norm,
    # legacy-ish paths penalized, recency as tie-break, then name (determinism).
    # With embeddings configured (cost-reduction 3.4), semantic relevance to the
    # RUN'S CHANGE becomes the primary key AFTER the legacy penalty — a similar
    # legacy spec must still lose — and the heuristic order is the tiebreak.
    # Unconfigured -> today's ordering, byte for byte (pinned).
    semantic_rank = _semantic_ranks(name)
    common = {h for h in helper_users if len(helper_users[h]) >= 2}
    def score(p):
        own = {_resolve(p, r) for r in _rel_imports(texts[p])}
        penal = any(seg in _PENALIZED for seg in p.parts[:-1] or ()) or \
                any(k in p.stem.lower() for k in _PENALIZED)
        heuristic = (-len(own & common), -p.stat().st_mtime, p.name)
        if semantic_rank:
            rel = p.relative_to(repo_root).as_posix()
            return (penal, semantic_rank.get(rel, 10**6)) + heuristic
        return (heuristic[0], penal) + heuristic[1:]
    exemplars = sorted(specs, key=score)[:EXEMPLARS]

    return {"name": name, "specs": len(specs), "facts": facts,
            "helpers": [(h.relative_to(repo_root).as_posix(), _read(h)[:HELPER_CAP])
                        for h in helpers],
            "exemplars": [(p.relative_to(repo_root).as_posix(), texts[p][:SPEC_CAP])
                          for p in exemplars]}


def to_markdown(profiles):
    out = ["# Existing approach per test repo — FOLLOW THIS",
           "",
           "New or updated specs must use the SAME approach as the code below: the",
           "same helpers/clients, assertion style, layout and naming. Do NOT introduce",
           "a new approach — no new HTTP clients, wrappers, assertion helpers or",
           "frameworks. If a needed helper does not exist, use the closest existing",
           "pattern and raise an open question instead of inventing an abstraction.",
           ""]
    for pr in profiles:
        out.append(f"## {pr['name']}")
        if not pr.get("specs"):
            out.append("_No existing specs — a new repo has no approach to follow yet; "
                       "use its CLAUDE.md/AGENTS.md conventions._\n")
            continue
        out.append(f"{pr['specs']} existing spec(s). Observed conventions: "
                   + "; ".join(pr["facts"]) + ".")
        for rel, code in pr.get("helpers", []):
            out += ["", f"### Shared helper `{rel}` — REUSE this, never re-implement it",
                    "```", code.rstrip(), "```"]
        for rel, code in pr.get("exemplars", []):
            out += ["", f"### Exemplar spec `{rel}` — mirror this shape",
                    "```", code.rstrip(), "```"]
        out.append("")
    return "\n".join(out) + "\n"


def build(names, root=ROOT):
    try:
        from registry import load_registry
        layouts = {t["name"]: (t.get("layout") or {}).get("specs", "")
                   for t in load_registry()["test_repositories"]}
    except Exception:
        layouts = {}
    profiles = []
    for n in names:
        repo = next((root / base / n for base in ("workspace/tests", "demo")
                     if (root / base / n).is_dir()), None)
        profiles.append(profile(repo, n, layouts.get(n, "")) if repo
                        else {"name": n, "specs": 0})
    return to_markdown(profiles)


def build_all(out_path, names, root=ROOT):
    """Write the combined file AND one per repo, profiling each repo ONCE.

    The pipeline needs both: `out/repo-conventions.md` (every resolved repo,
    read by validate and by a single-repo generate) and
    `out/repo-conventions-<repo>.md` (the fan-out, where each agent must see
    only its own repo). They were produced by separate processes — 1 + N
    interpreter starts, each re-scanning repos the previous call had already
    scanned. Measured on a two-repo run: 4.16s across three invocations, of
    which two repo scans were duplicates.

    Returns the same markdown the old per-file calls produced, byte for byte —
    pinned by test_spec_exemplars.py, because a context file that changes shape
    silently changes what every authoring phase is told.
    """
    try:
        from registry import load_registry
        layouts = {t["name"]: (t.get("layout") or {}).get("specs", "")
                   for t in load_registry()["test_repositories"]}
    except Exception:
        layouts = {}
    profiles = []
    for n in names:
        repo = next((root / base / n for base in ("workspace/tests", "demo")
                     if (root / base / n).is_dir()), None)
        profiles.append(profile(repo, n, layouts.get(n, "")) if repo
                        else {"name": n, "specs": 0})
    out_path = pathlib.Path(out_path)
    written = {}
    combined = (to_markdown(profiles) if profiles
                else "# Existing approach\n\n_No test repos resolved._\n")
    out_path.write_text(combined, encoding="utf-8", newline="\n")
    written[str(out_path)] = len(combined)
    for p in profiles:
        # `out/repo-conventions.md` -> `out/repo-conventions-<repo>.md`, the
        # exact names the fan-out already reads.
        per = out_path.with_name(f"{out_path.stem}-{p['name']}{out_path.suffix}")
        md = to_markdown([p])
        per.write_text(md, encoding="utf-8", newline="\n")
        written[str(per)] = len(md)
    return written


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: spec_exemplars.py [all] <out_md> [repo_name ...]")
    if sys.argv[1] == "all":
        w = build_all(sys.argv[2], sys.argv[3:])
        print(f"wrote {len(w)} conventions file(s) from {len(sys.argv) - 3} repo(s)")
    else:
        out_path = pathlib.Path(sys.argv[1])
        md = (build(sys.argv[2:]) if sys.argv[2:]
              else "# Existing approach\n\n_No test repos resolved._\n")
        out_path.write_text(md, encoding="utf-8", newline="\n")
        print(f"wrote {out_path} ({len(md)} chars, {len(sys.argv) - 2} repo(s))")

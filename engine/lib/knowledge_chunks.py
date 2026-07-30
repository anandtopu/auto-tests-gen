#!/usr/bin/env python3
"""Knowledge chunk store (cost-reduction story 2.1) — the substrate for
retrieval-scoped context (2.2) and the vector index (3.2).

Today every authoring phase receives the WHOLE estate (AGENTS.md + full catalog
+ all guidance). Retrieval needs something better to work with than whole
files, so this module chunks the same sources `bin/gen_agents_md.py` reads into
addressed units with stable ids and provenance:

  kind         one chunk per...                       source
  repo-surface app/test repo: registry facts +        registry + workspace/demo
               harvested endpoints/routes             contract or route table
  guidance     repo with any guidance text            repo_admin.guidance_for
               (team notes + repo-owned + curated
               + generated, already ranked)
  exemplar     test repo: shared-helper + exemplar    spec_exemplars
               spec profile
  catalog      app repo: its mapped tests             catalog/*.jsonl
  scenario     test plan: its scenario section        testplans/<KEY>.md
  testdata     ticket with generated data sets        testdata/<KEY>/

Chunks are DERIVED data, like `covers:` — regenerated, never hand-edited, and
gitignored (`reports/knowledge-index/`). `chunk_id` derives from kind + repo +
a stable slug of the source, NOT from content: an edited file keeps its id and
the vector index sees it as changed-in-place via the sha256.

Determinism is a contract: same inputs -> byte-identical chunks.jsonl (no
timestamps, sorted ordering, sorted JSON keys). Pinned by
registry/tests/test_knowledge_chunks.py.

Rebuilt wherever AGENTS.md is regenerated (gen_agents_md.py calls rebuild()
best-effort) and by `make maintain`; removed by clear-demo.
"""
import glob
import hashlib
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "reports/knowledge-index/chunks.jsonl"

# Caps keep a chunk a retrieval unit, not a whole-file smuggling route.
MAX_CHUNK_CHARS = 6000
MAX_TESTDATA_FILE_CHARS = 2000


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chunk(kind, repo, slug, source_path, text):
    text = text[:MAX_CHUNK_CHARS]
    return {"chunk_id": f"{kind}:{repo}:{slug}", "kind": kind, "repo": repo,
            "source_path": str(source_path), "text": text, "sha256": _sha(text)}


def _harvest_surface(r):
    """Endpoints (backend) or routes (frontend) — same logic as gen_agents_md."""
    art = r.get("contract") if r.get("type") == "backend" else r.get("route_table")
    if not art:
        return None, []
    for base in (ROOT / "workspace/src" / r["name"], ROOT / "demo" / r["name"]):
        p = base / art
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="ignore")
            if r.get("type") == "backend":
                return p, re.findall(r"^\s{2}(/[^:\s]+):", text, re.M)
            return p, re.findall(r"path:\s*['\"]([^'\"]+)", text)
    return None, []


def build():
    """Every chunk, deterministic order. Read-only over the estate."""
    from registry import load_registry
    import repo_admin
    reg = load_registry()
    chunks = []

    # --- repo-surface: one per app repo (facts + harvested surface) ----------
    for r in reg.get("source_repositories", []):
        src, items = _harvest_surface(r)
        lines = [f"repo: {r['name']} ({r.get('type', '?')})",
                 f"domains: {', '.join(r.get('domains', [])) or '-'}",
                 f"consumes: {', '.join(r.get('consumes_services', [])) or '-'}",
                 f"consumed_by: {', '.join(r.get('consumed_by', [])) or '-'}"]
        if items:
            label = "endpoints" if r.get("type") == "backend" else "routes"
            lines.append(f"{label}:")
            lines += [f"  {i}" for i in items]
        chunks.append(_chunk("repo-surface", r["name"], "app",
                             src or "registry/repo-registry.yaml",
                             "\n".join(lines)))

    # --- repo-surface: one per test repo (layer, layout, covers) -------------
    for t in reg.get("test_repositories", []):
        lay = t.get("layout") or {}
        lines = [f"test repo: {t['name']} (layer {t.get('layer', '?')}, "
                 f"framework {t.get('framework', '?')})",
                 f"specs dir: {lay.get('specs', '-')}  fixtures: {lay.get('fixtures', '-')}",
                 f"covers: {', '.join(t.get('covers', [])) or '-'}",
                 f"scope: {', '.join(t.get('scope', [])) or '-'}"]
        chunks.append(_chunk("repo-surface", t["name"], "test",
                             "registry/repo-registry.yaml", "\n".join(lines)))

    # --- guidance: one per repo that has any ---------------------------------
    all_names = [r["name"] for r in reg.get("source_repositories", [])] + \
                [t["name"] for t in reg.get("test_repositories", [])]
    for name in all_names:
        try:
            pairs = repo_admin.guidance_for(name)   # [(source_label, text)]
        except Exception:
            pairs = []
        merged = "\n\n".join(f"### {src}\n{t.strip()}" for src, t in pairs
                             if t and t.strip())
        if merged:
            chunks.append(_chunk("guidance", name, "merged",
                                 f"knowledge (merged sources for {name})",
                                 merged))

    # --- exemplar: one per test repo with a spec profile ---------------------
    try:
        import spec_exemplars
        names = [t["name"] for t in reg.get("test_repositories", [])]
        for name in names:
            try:
                md = spec_exemplars.build([name])   # returns markdown directly
            except Exception:
                continue
            if md and md.strip():
                chunks.append(_chunk("exemplar", name, "profile",
                                     f"spec_exemplars({name})", md.strip()))
    except Exception:
        pass

    # --- catalog: one per app repo with mapped tests -------------------------
    catalog = []
    for f in sorted(glob.glob(str(ROOT / "catalog/*.jsonl"))):
        if pathlib.Path(f).name == "catalog.sample.jsonl":
            continue
        for line in open(f, encoding="utf-8"):
            if line.strip():
                try:
                    catalog.append((f, json.loads(line)))
                except ValueError:
                    continue
    by_app = {}
    for f, e in catalog:
        for app in (e.get("mapping") or {}).get("app_repos", []):
            by_app.setdefault(app, []).append((f, e))
    for app in sorted(by_app):
        lines = []
        for f, e in sorted(by_app[app], key=lambda fe: fe[1].get("test_id", "")):
            ev = e.get("evidence") or {}
            lines.append(f"- {e.get('test_id', '?')}"
                         + (f"  endpoints: {', '.join(ev['endpoints'])}"
                            if ev.get("endpoints") else "")
                         + (f"  routes: {', '.join(ev['ui_routes'])}"
                            if ev.get("ui_routes") else ""))
        chunks.append(_chunk("catalog", app, "mapped",
                             "catalog/*.jsonl", "\n".join(lines)))

    # --- scenario: one per test plan -----------------------------------------
    for p in sorted((ROOT / "testplans").glob("*.md")):
        text = p.read_text(encoding="utf-8", errors="ignore").strip()
        if text:
            chunks.append(_chunk("scenario", p.stem, "plan",
                                 f"testplans/{p.name}", text))

    # --- testdata: one per ticket with generated data ------------------------
    td_root = ROOT / "testdata"
    if td_root.is_dir():
        for d in sorted(td_root.iterdir()):
            if not d.is_dir():
                continue
            parts, budget = [], MAX_CHUNK_CHARS
            for f in sorted(d.rglob("*")):
                if not f.is_file() or budget <= 0:
                    continue
                body = f.read_text(encoding="utf-8",
                                   errors="ignore")[:MAX_TESTDATA_FILE_CHARS]
                parts.append(f"## {f.relative_to(d).as_posix()}\n{body}")
                budget -= len(body)
            if parts:
                chunks.append(_chunk("testdata", d.name, "sets",
                                     f"testdata/{d.name}/", "\n\n".join(parts)))

    return sorted(chunks, key=lambda c: c["chunk_id"])


def rebuild():
    """Write chunks.jsonl. Returns the chunk count. Deterministic: same estate
    -> byte-identical file."""
    chunks = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(c, sort_keys=True) + "\n" for c in chunks)
    OUT.write_text(body, encoding="utf-8", newline="\n")
    return len(chunks)


def load():
    """Chunks from disk; a bad line is skipped, never fatal."""
    out = []
    try:
        for line in open(OUT, encoding="utf-8"):
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "rebuild"
    if cmd == "rebuild":
        n = rebuild()
        print(f"knowledge chunks rebuilt: {n} chunk(s) -> {OUT}")
        return 0
    if cmd == "stats":
        by_kind = {}
        for c in load():
            by_kind[c["kind"]] = by_kind.get(c["kind"], 0) + 1
        print(f"{len(load())} chunk(s): " +
              ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items())))
        return 0
    print("usage: knowledge_chunks.py rebuild|stats", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv))

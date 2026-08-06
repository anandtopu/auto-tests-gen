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
import hashlib
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app_paths                      # R12: mutable paths resolve here
import env_flag

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "reports/knowledge-index/chunks.jsonl"

# Caps keep a chunk a retrieval unit, not a whole-file smuggling route.
MAX_CHUNK_CHARS = 6000
MAX_TESTDATA_FILE_CHARS = 2000
DEFAULT_TESTCASE_CHARS = 2000
KINDS = frozenset({"repo-surface", "guidance", "exemplar", "spec",
                   "catalog", "scenario", "testdata", "testcase"})


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chunk(kind, repo, slug, source_path, text, **metadata):
    text = text[:MAX_CHUNK_CHARS]
    chunk = {"chunk_id": f"{kind}:{repo}:{slug}", "kind": kind, "repo": repo,
             "source_path": str(source_path), "text": text, "sha256": _sha(text)}
    chunk.update(metadata)
    return chunk


def testcase_enabled():
    return env_flag.flag("AIQE_TESTCASE_INDEX", False)


def testcase_chunk_chars():
    try:
        value = int(os.environ.get("AIQE_TESTCASE_CHUNK_CHARS", "") or
                    DEFAULT_TESTCASE_CHARS)
    except ValueError:
        value = DEFAULT_TESTCASE_CHARS
    return max(512, min(value, MAX_CHUNK_CHARS))


def _case_chunks(repo, rel, source_path, case, occurrence=1):
    """Physical chunks for one logical case, all sharing ``case_id``."""
    suite = [_compact(v) for v in case.get("suite", []) if _compact(v)]
    title = _compact(case.get("title", ""), 300) or "untitled"
    path = "/".join([*suite, title])
    slug = f"{rel}#{path}" + (f"~{occurrence}" if occurrence > 1 else "")
    case_id = f"testcase:{repo}:{slug}"
    fields = {
        "suite": suite,
        "title": title,
        "tags": sorted(set(case.get("tags") or [])),
        "exercises": sorted(set(case.get("exercises") or [])),
        "fixtures": sorted(set(case.get("fixtures") or [])),
        "assertions": list(dict.fromkeys(case.get("assertions") or [])),
    }
    header = "\n".join([
        f"suite: {' > '.join(fields['suite']) or '-'}",
        f"title: {fields['title']}",
        f"tags: {', '.join(fields['tags']) or '-'}",
        f"exercises: {', '.join(fields['exercises']) or '-'}",
        f"fixtures: {', '.join(fields['fixtures']) or '-'}",
        f"assertions: {'; '.join(fields['assertions']) or '-'}",
        "body:",
    ])
    limit = testcase_chunk_chars()
    # Metadata is bounded independently so even a pathological identifier list
    # cannot consume the entire retrieval unit.
    if len(header) >= limit - 64:
        header = header[:limit - 64].rstrip() + "\nbody:"
    body_budget = max(1, limit - len(header) - 1)
    body = case.get("body") or ""
    parts = [body[i:i + body_budget] for i in range(0, len(body), body_budget)] or [""]
    out = []
    for index, part in enumerate(parts, 1):
        part_slug = slug if len(parts) == 1 else f"{slug}:part-{index}"
        text = f"{header}\n{part}"
        out.append(_chunk(
            "testcase", repo, part_slug, source_path, text,
            case_id=case_id, part=index, parts=len(parts), parse_status="parsed",
            **fields,
        ))
    return out


def _compact(value, limit=160):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


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


def build(test_roots=None, index_outcomes=None):
    """Every chunk, deterministic order. Read-only over the estate."""
    from registry import load_registry
    import repo_admin
    reg = load_registry()
    chunks = []
    use_testcases = testcase_enabled()
    resolved_roots = dict(test_roots or {})
    outcomes = dict(index_outcomes or {})
    if test_roots is None:
        for t in reg.get("test_repositories", []):
            name = t["name"]
            base = next((ROOT / b / name for b in ("workspace/tests", "demo")
                         if (ROOT / b / name).is_dir()), None)
            if base is not None:
                resolved_roots[name] = base
    if use_testcases:
        for t in reg.get("test_repositories", []):
            name = t["name"]
            if name in outcomes:
                continue
            if name in resolved_roots:
                outcomes[name] = {
                    "status": "indexed", "source": "local", "scm": "not_called",
                    "exit_class": "not_called", "reason": "",
                }
            else:
                outcomes[name] = {
                    "status": "not_indexed", "source": "local",
                    "scm": str(t.get("scm") or "unknown"),
                    "exit_class": "not_resolved",
                    "reason": "no resolved checkout was provided or found locally",
                }

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
        surface = _chunk("repo-surface", t["name"], "test",
                         "registry/repo-registry.yaml", "\n".join(lines))
        if use_testcases:
            outcome = outcomes[t["name"]]
            surface.update(index_status=outcome["status"],
                           index_source=outcome["source"],
                           index_scm=outcome["scm"],
                           index_exit_class=outcome["exit_class"],
                           index_reason=outcome["reason"])
        chunks.append(surface)

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

    # --- spec/testcase: file-level today, case-level behind the S1 flag -------
    # Parsed files replace their whole-file semantic chunk with bounded testcase
    # chunks. An unsupported/malformed file retains the old spec chunk and says
    # why, so "could not parse" can never masquerade as "contains no tests".
    if use_testcases:
        import testcase_parser
    for t in reg.get("test_repositories", []):
        base = resolved_roots.get(t["name"])
        if base is None:
            continue
        base = pathlib.Path(base)
        spec_root = base / ((t.get("layout") or {}).get("specs") or "")
        specs = sorted(p for pat in ("**/*.spec.js", "**/*.spec.ts",
                                     "**/*.test.js", "**/*.test.ts")
                       for p in spec_root.glob(pat))
        for p in specs:
            rel = p.relative_to(base).as_posix()
            source = p.read_text(encoding="utf-8", errors="replace")
            if not use_testcases:
                chunks.append(_chunk("spec", t["name"], rel, str(p), source))
                continue
            parsed = testcase_parser.parse(source)
            if parsed["unparsed_reason"]:
                chunks.append(_chunk(
                    "spec", t["name"], rel, str(p), source,
                    parse_status="unparsed", parse_reason=parsed["unparsed_reason"],
                ))
                continue
            seen = {}
            for case in parsed["cases"]:
                identity = (tuple(case.get("suite") or []), case.get("title") or "")
                seen[identity] = seen.get(identity, 0) + 1
                chunks.extend(_case_chunks(t["name"], rel, str(p), case,
                                           occurrence=seen[identity]))

    # --- catalog: one per app repo with mapped tests -------------------------
    catalog = []
    for f in app_paths.catalog_files(ROOT):
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
                             "catalog/*.jsonl",  # not-a-path: provenance LABEL, not a directory read; chunk bytes are pinned byte-deterministic across machines so this cannot carry a resolved directory
                             "\n".join(lines)))

    # --- scenario: per SCENARIO when a structured spec exists (SDD 6.3 —
    # sharper retrieval/reuse granularity), else one per plan (legacy).
    try:
        import spec_store
    except Exception:
        spec_store = None
    for p in sorted(app_paths.testplans_dir(ROOT).glob("*.md")):
        key = p.stem
        spec = spec_store.load(key) if spec_store else None
        if spec:
            for s in spec.get("scenarios", []):
                body = "\n".join(filter(None, [
                    f"{s.get('id')}: {s.get('title')}",
                    f"layer {s.get('layer')} -> {s.get('target_repo')}",
                    str(s.get("steps") or ""),
                    *(f"verify: {v}" for v in s.get("verification") or [])]))
                chunks.append(_chunk("scenario", key, s.get("id", "?"),
                                     f"specs/{key}/testplan.yaml", body))
            continue
        text = p.read_text(encoding="utf-8", errors="ignore").strip()
        if text:
            chunks.append(_chunk("scenario", key, "plan",
                                 f"testplans/{p.name}", text))

    # --- testdata: one per ticket with generated data ------------------------
    td_root = app_paths.testdata_dir(ROOT)
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
    roots = outcomes = None
    if testcase_enabled():
        from registry import load_registry
        import index_checkouts
        roots, outcomes = index_checkouts.resolve(
            load_registry().get("test_repositories", []), root=ROOT)
        for repo, outcome in sorted(outcomes.items()):
            if outcome.get("status") == "not_indexed":
                detail = "/".join(filter(None, [outcome.get("scm", ""),
                                                 outcome.get("exit_class", "")]))
                print(f"[knowledge-index] NOT INDEXED {repo}"
                      f"{f' ({detail})' if detail else ''}: "
                      f"{outcome.get('reason') or 'reason not recorded'}",
                      file=sys.stderr)
    chunks = build(test_roots=roots, index_outcomes=outcomes)
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


def index_stats(chunks=None):
    """Parse-coverage report derived from chunks, with no second state file."""
    chunks = load() if chunks is None else chunks
    repos = {}
    try:
        from registry import load_registry
        for entry in load_registry().get("test_repositories", []):
            repos[entry["name"]] = {"cases": set(), "chunks": 0,
                                    "parsed_files": set(), "unparsed": {},
                                    "index_status": "unknown",
                                    "index_source": "", "index_scm": "",
                                    "index_exit_class": "", "index_reason": ""}
    except Exception:
        pass
    for chunk in chunks:
        if chunk.get("kind") == "repo-surface" and chunk.get("index_status"):
            repo = chunk.get("repo", "?")
            row = repos.setdefault(repo, {"cases": set(), "chunks": 0,
                                          "parsed_files": set(), "unparsed": {}})
            for field in ("status", "source", "scm", "exit_class", "reason"):
                row[f"index_{field}"] = chunk.get(f"index_{field}", "")
        if chunk.get("kind") not in ("testcase", "spec"):
            continue
        repo = chunk.get("repo", "?")
        row = repos.setdefault(repo, {"cases": set(), "chunks": 0,
                                      "parsed_files": set(), "unparsed": {},
                                      "index_status": "unknown",
                                      "index_source": "", "index_scm": "",
                                      "index_exit_class": "", "index_reason": ""})
        if chunk.get("kind") == "testcase":
            row["cases"].add(chunk.get("case_id") or chunk["chunk_id"])
            row["chunks"] += 1
            row["parsed_files"].add(chunk.get("source_path", "?"))
        elif chunk.get("parse_status") == "unparsed":
            row["unparsed"][chunk.get("source_path", "?")] = \
                chunk.get("parse_reason", "reason not recorded")
    clean = {}
    for repo, row in sorted(repos.items()):
        clean[repo] = {"cases_indexed": len(row["cases"]),
                       "chunks_emitted": row["chunks"],
                       "files_parsed": len(row["parsed_files"]),
                       "files_unparsed": len(row["unparsed"]),
                       "index_status": row.get("index_status", "unknown"),
                       "index_source": row.get("index_source", ""),
                       "index_scm": row.get("index_scm", ""),
                       "index_exit_class": row.get("index_exit_class", ""),
                       "not_indexed_reason": (
                           row.get("index_reason", "")
                           if row.get("index_status") == "not_indexed" else
                           ("index outcome was not recorded"
                            if testcase_enabled()
                            and row.get("index_status", "unknown") == "unknown"
                            and not row["parsed_files"] and not row["unparsed"]
                            else "")),
                       "unparsed": [{"file": f, "reason": reason}
                                    for f, reason in sorted(row["unparsed"].items())]}
    return {"enabled": testcase_enabled(), "repos": clean,
            "cases_indexed": sum(r["cases_indexed"] for r in clean.values()),
            "chunks_emitted": sum(r["chunks_emitted"] for r in clean.values()),
            "files_unparsed": sum(r["files_unparsed"] for r in clean.values())}


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
    if cmd == "index-stats":
        stats = index_stats()
        state = "enabled" if stats["enabled"] else "disabled"
        print(f"testcase index ({state}): {stats['cases_indexed']} case(s), "
              f"{stats['chunks_emitted']} chunk(s), "
              f"{stats['files_unparsed']} unparsed file(s)")
        for repo, row in stats["repos"].items():
            print(f"  {repo}: {row['cases_indexed']} case(s), "
                  f"{row['files_parsed']} parsed file(s), "
                  f"{row['files_unparsed']} unparsed file(s)")
            if row["not_indexed_reason"]:
                detail = "/".join(filter(None, [row["index_scm"],
                                                 row["index_exit_class"]]))
                print(f"    NOT INDEXED{f' ({detail})' if detail else ''}: "
                      f"{row['not_indexed_reason']}")
            for item in row["unparsed"]:
                print(f"    UNPARSED {item['file']}: {item['reason']}")
        return 0
    print("usage: knowledge_chunks.py rebuild|stats|index-stats", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
"""Curated per-repo agent guidance — the DURABLE, user-editable layer.

The generated AGENTS.md under knowledge/generated/ is gitignored scratch: it is
rebuilt on demand and lost on redeploy by design. When a user wants to OWN the
guidance for a repo from the platform UI — edit it, keep it across deployments,
export it — it graduates here: knowledge/curated/<repo>/{AGENTS.md,CLAUDE.md},
a TRACKED directory committed with the control repo (that is what makes it
durable across container deployments: the control repo is the deployment).

Ranking (repo_admin.repo_local_files): a file the repo itself ships ALWAYS wins
(non-negotiable), then curated, then the demo fixture, then generated scratch.
So curating never overrides a team's own committed guidance — it replaces only
what the platform would otherwise have generated.

Plain "Clear demo data" KEEPS curated files (they are user content, part of
what the estate *is*); factory reset deletes them along with the registry.
"""
import pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

CURATED_DIR = ROOT / "knowledge/curated"
FILENAMES = ("AGENTS.md", "CLAUDE.md")


def _known(name):
    from registry import load_registry
    reg = load_registry()
    names = {r["name"] for s in ("source_repositories", "test_repositories")
             for r in reg[s]}
    if name not in names:
        raise SystemExit(f"unknown repo '{name}' — register it first")


def _check_filename(filename):
    if filename not in FILENAMES:
        raise SystemExit(f"filename must be one of {FILENAMES}")


def path_for(name, filename="AGENTS.md"):
    return CURATED_DIR / name / filename


def get(name):
    """{filename: content} for every curated file this repo has."""
    _known(name)
    out = {}
    for fn in FILENAMES:
        p = path_for(name, fn)
        if p.exists():
            out[fn] = p.read_text(encoding="utf-8", errors="replace")
    return out


def save(name, filename, content):
    """Persist (or overwrite) a curated guidance file. Empty content deletes."""
    _known(name)
    _check_filename(filename)
    p = path_for(name, filename)
    if not (content or "").strip():
        p.unlink(missing_ok=True)
        try:
            p.parent.rmdir()                       # only if now empty
        except OSError:
            pass
        return {"repo": name, "file": filename, "deleted": True}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8", newline="\n")
    return {"repo": name, "file": filename,
            "path": p.relative_to(ROOT).as_posix(), "bytes": len(content)}


def curated_files(name):
    """repo_admin-shaped [{path, text}] rows, AGENTS.md first."""
    out = []
    for fn in FILENAMES:
        p = path_for(name, fn)
        if p.exists():
            out.append({"path": p.relative_to(ROOT).as_posix(),
                        "text": p.read_text(encoding="utf-8", errors="replace")})
    return out


def drop(name):
    """Remove every curated file for a repo (repo removal / factory reset)."""
    import shutil
    d = CURATED_DIR / name
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit("usage: curated_guidance.py get <repo> | "
                         "save <repo> <AGENTS.md|CLAUDE.md> < content-on-stdin")
    cmd, name = sys.argv[1], sys.argv[2]
    if cmd == "get":
        import json
        print(json.dumps(get(name), indent=1))
    elif cmd == "save":
        r = save(name, sys.argv[3], sys.stdin.read())
        print(r)
    else:
        raise SystemExit(f"unknown command {cmd}")

#!/usr/bin/env python3
"""Sync per-repo agent guidance (AGENTS.md / CLAUDE.md) from the SCM.

Repo-local guidance used to be discoverable only from a checkout — the workspace
clone that exists during a run, or the demo estate. That meant a user could not
refresh guidance on demand: there was no path to the real Bitbucket/GitHub/Stash
content outside a pipeline run.

This module pulls those files straight from the remote through the Scm port's
`fetch_file` verb (no clone), for BOTH application repos (ui + api/service) and E2E
test repos, and caches them under knowledge/synced/<repo>/<FILE>. `repo_admin`
then merges them into the estate AGENTS.md, so the freshly-synced guidance reaches
every LLM phase — PR triage/generation and JIRA story/bug plan+generation alike.

Precedence when the same filename exists in several places (see repo_admin):
    workspace clone (exact revision under test)  >  synced from SCM  >  demo fixture

CLI:  guidance_sync.py sync <repo> | sync-all | status
"""
import json, os, pathlib, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fs_lock, work_queue
from registry import load_registry

SYNC_DIR = pathlib.Path(os.environ.get("AIQE_SYNC_DIR") or ROOT / "knowledge/synced")
STATE = SYNC_DIR / "state.json"
# AGENTS.md/CLAUDE.md are the always-on convention; .agents/skills/qa-guide.md is
# OpenHands' per-repo QA customization point, so a team already using OpenHands keeps
# ONE guidance file that both systems honour (see openhands-review.md §2.3).
GUIDANCE_FILES = ("AGENTS.md", "CLAUDE.md", ".agents/skills/qa-guide.md")
MAX_BYTES = 100_000                       # a guidance file, not a data dump


def _scm_adapter():
    """The Scm adapter for the current mode — mock unless AIQE_MOCK=0."""
    try:
        import settings_store
        settings_store.load_env_into()
    except Exception:
        pass
    if os.environ.get("AIQE_MOCK", "1") == "1":
        return ROOT / "adapters/mock/scm.sh"
    kind = os.environ.get("SCM_KIND", "github")
    import yaml
    cfg = yaml.safe_load(open(ROOT / "registry/org-config.yaml", encoding="utf-8"))
    return ROOT / cfg["adapters"]["scm"][kind]


def load_state():
    if STATE.exists():
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(s):
    SYNC_DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2, sort_keys=True) + "\n",
                     encoding="utf-8", newline="\n")


def known_repos():
    """Every registered repo: application (ui + service) and E2E test repos."""
    reg = load_registry()
    return ([{"name": r["name"], "kind": "ui" if r["type"] == "frontend" else "service"}
             for r in reg["source_repositories"]]
            + [{"name": t["name"], "kind": "test"} for t in reg["test_repositories"]])


def fetch_one(repo, filename, ref=None):
    """Fetch a single guidance file via the Scm port. Returns text, or None when the
    repo has no such file (adapter exit 3)."""
    cmd = [work_queue.bash_exe(), str(_scm_adapter()), "fetch_file", repo, filename]
    if ref:
        cmd.append(ref)
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    if r.returncode == 3:
        return None                        # absent — normal, not an error
    if r.returncode != 0:
        raise SystemExit(f"fetch_file failed for {repo}:{filename}: "
                         f"{(r.stderr or r.stdout).strip()[:200]}")
    return r.stdout


def sync_repo(repo, ref=None):
    """Pull every guidance file for one repo from the SCM into the local cache.
    Returns {'repo', 'files': [...], 'missing': [...]}"""
    names = {r["name"] for r in known_repos()}
    if repo not in names:
        raise SystemExit(f"not a registered repo: {repo}")
    found, missing = [], []
    dest = SYNC_DIR / repo
    for fname in GUIDANCE_FILES:
        text = fetch_one(repo, fname, ref)
        if text is None:
            missing.append(fname)
            (dest / fname).unlink(missing_ok=True)      # remote deleted it -> drop cache
            continue
        if len(text.encode("utf-8")) > MAX_BYTES:
            text = text[:MAX_BYTES] + "\n… (truncated at sync)\n"
        target = dest / fname                     # fname may be nested (.agents/skills/…)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        found.append(fname)
    with fs_lock.lock(STATE):
        s = load_state()
        s[repo] = {"files": found, "missing": missing, "ref": ref or "default",
                   "synced_at": time.time(),
                   "source": "mock" if os.environ.get("AIQE_MOCK", "1") == "1" else "scm"}
        _save_state(s)
    return {"repo": repo, "files": found, "missing": missing}


def sync_all(ref=None):
    results = [sync_repo(r["name"], ref) for r in known_repos()]
    return {"repos": len(results), "with_guidance": sum(1 for r in results if r["files"]),
            "results": results}


def synced_files(repo):
    """Cached guidance for a repo: [{path, text}] — consumed by repo_admin."""
    out = []
    d = SYNC_DIR / repo
    for fname in GUIDANCE_FILES:
        p = d / fname
        if p.exists():
            try:
                rel = p.relative_to(ROOT).as_posix()
            except ValueError:
                rel = p.as_posix()
            out.append({"path": rel, "text": p.read_text(encoding="utf-8",
                                                         errors="ignore")})
    return out


def regenerate_agents_md():
    subprocess.run([sys.executable, str(ROOT / "bin/gen_agents_md.py")], cwd=ROOT,
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)


def status():
    """Per-repo sync status for the UI/CLI."""
    s = load_state()
    out = []
    for r in known_repos():
        e = s.get(r["name"], {})
        out.append({**r, "files": e.get("files", []), "missing": e.get("missing", []),
                    "synced_at": e.get("synced_at"), "source": e.get("source", ""),
                    "cached": [f["path"] for f in synced_files(r["name"])]})
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    if a[0] == "sync" and len(a) > 1:
        r = sync_repo(a[1], a[2] if len(a) > 2 else None)
        regenerate_agents_md()
        print(f"{r['repo']}: synced {', '.join(r['files']) or '(none)'}"
              + (f"; absent: {', '.join(r['missing'])}" if r["missing"] else "")
              + " — AGENTS.md regenerated")
    elif a[0] == "sync-all":
        r = sync_all(a[1] if len(a) > 1 else None)
        regenerate_agents_md()
        print(f"synced {r['repos']} repo(s); {r['with_guidance']} carry guidance "
              f"— AGENTS.md regenerated")
        for x in r["results"]:
            if x["files"]:
                print(f"  {x['repo']}: {', '.join(x['files'])}")
    elif a[0] == "status":
        for x in status():
            when = (time.strftime("%Y-%m-%d %H:%M", time.localtime(x["synced_at"]))
                    if x["synced_at"] else "never")
            print(f"{x['name']:<22} {x['kind']:<8} {when:<17} "
                  f"{', '.join(x['files']) or '-'}")
    else:
        sys.exit(f"unknown command: {a[0]}")

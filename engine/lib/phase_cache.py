#!/usr/bin/env python3
"""Content-addressed reuse of LLM phase results.

Re-authoring a test plan for a ticket that has not changed, against repos that have
not changed, with a prompt that has not changed, buys nothing and costs a full phase.
The same is true of analyze, testdata and the plan adversary. This makes that work
happen once.

## The key is the whole input, so a stale hit is impossible

    sha256( phase · model · prompt template · every context file's content · artifacts )

If any byte of any input moves — the ticket text, AGENTS.md, the catalog slice, the
prompt, the model tier — the key changes and the phase runs for real. There is no TTL
to tune and no invalidation to forget, which is the only kind of cache worth having in
a pipeline whose output gets committed to real repositories.

## What may NOT be cached, and why

`generate` and `validate` are excluded by construction. Their product is not the
contract — it is files written into `workspace/tests/<repo>` and the git state the
gate then inspects. Replaying a contract without re-writing those files would hand the
gate a clean tree and a green report for work that never happened. `CACHEABLE` is an
allow-list for exactly this reason; adding a phase to it means asserting that its
contract plus its declared artifacts ARE its entire product.

Cached phases restore their artifacts too (testplan's markdown, testdata's fixtures),
so a hit reproduces the phase's full effect, not just its JSON.

Storage: reports/phase-cache/<key>.json (gitignored, prunable). Disable per run with
AIQE_PHASE_CACHE=0.
"""
import hashlib
import json
import os
import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
DIR = pathlib.Path(os.environ.get("AIQE_PHASE_CACHE_DIR") or ROOT / "reports/phase-cache")

# phase -> artifact paths it produces, as {KEY}-templated globs. A phase is cacheable
# only if its contract + these paths are its ENTIRE product.
CACHEABLE = {
    "analyze": [],
    "testplan": ["testplans/{KEY}.md"],
    "planadversary": [],
    "planarbiter": ["testplans/{KEY}.md"],
    "testdata": ["testdata/{KEY}"],
    "critic": [],
    "triage": [],
}


def enabled():
    return os.environ.get("AIQE_PHASE_CACHE", "1").strip() not in ("0", "false", "no", "off")


def _digest(path):
    p = pathlib.Path(path)
    if not p.exists():
        return f"absent:{p.name}"
    if p.is_dir():
        parts = []
        for f in sorted(p.rglob("*")):
            if f.is_file():
                parts.append(f.relative_to(p).as_posix())
                parts.append(hashlib.sha256(f.read_bytes()).hexdigest())
        return hashlib.sha256("|".join(parts).encode()).hexdigest()
    return hashlib.sha256(p.read_bytes()).hexdigest()


def key(phase, model, prompt_file, context_files, extra=""):
    """The cache key. Uses the prompt TEMPLATE (pre-substitution) plus the context
    files, which already carry the run's specifics — so two runs of the same ticket
    hash the same, while a changed ticket hashes differently."""
    h = hashlib.sha256()
    h.update(f"v1|{phase}|{model}|{extra}".encode())
    h.update(_digest(prompt_file).encode())
    for f in context_files:
        h.update(f"|{pathlib.Path(f).name}:".encode())
        h.update(_digest(f).encode())
    return h.hexdigest()[:32]


def _artifacts_for(phase, run_key):
    return [ROOT / p.format(KEY=run_key) for p in CACHEABLE.get(phase, [])]


def lookup(phase, out_label, model, prompt_file, context_files, run_key=""):
    """Restore a previous result for an identical input set. True on a hit."""
    if not enabled() or phase not in CACHEABLE:
        return False
    entry_path = DIR / f"{key(phase, model, prompt_file, context_files)}.json"
    if not entry_path.exists():
        return False
    try:
        entry = json.loads(entry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False

    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    (out / f"{out_label}.contract.json").write_text(
        json.dumps(entry["contract"], indent=1), encoding="utf-8", newline="\n")
    # A phase's artifacts are part of its product; a hit must reproduce them or the
    # next phase reads a file that is not there.
    for rel, body in (entry.get("artifacts") or {}).items():
        dest = ROOT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8", newline="\n")
    entry["hits"] = entry.get("hits", 0) + 1
    entry["last_hit"] = time.time()
    try:
        entry_path.write_text(json.dumps(entry, indent=1), encoding="utf-8", newline="\n")
    except OSError:
        pass
    return True


def store(phase, out_label, model, prompt_file, context_files, run_key=""):
    """Record a fresh result. Never fatal — a cache write failure is not a run failure."""
    if not enabled() or phase not in CACHEABLE:
        return False
    contract_path = ROOT / "out" / f"{out_label}.contract.json"
    if not contract_path.exists():
        return False
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False

    artifacts = {}
    for path in _artifacts_for(phase, run_key):
        if path.is_dir():
            for f in sorted(path.rglob("*")):
                if f.is_file():
                    artifacts[f.relative_to(ROOT).as_posix()] = f.read_text(
                        encoding="utf-8", errors="replace")
        elif path.exists():
            artifacts[path.relative_to(ROOT).as_posix()] = path.read_text(
                encoding="utf-8", errors="replace")

    DIR.mkdir(parents=True, exist_ok=True)
    entry = {"phase": phase, "model": model, "key_of": run_key,
             "stored": time.time(), "hits": 0,
             "contract": contract, "artifacts": artifacts}
    try:
        (DIR / f"{key(phase, model, prompt_file, context_files)}.json").write_text(
            json.dumps(entry, indent=1), encoding="utf-8", newline="\n")
    except OSError:
        return False
    return True


def stats():
    """What the cache has actually saved — hits are avoided phase calls."""
    entries, hits, by_phase = 0, 0, {}
    if DIR.exists():
        for f in DIR.glob("*.json"):
            try:
                e = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            entries += 1
            hits += e.get("hits", 0)
            p = e.get("phase", "?")
            by_phase[p] = by_phase.get(p, 0) + e.get("hits", 0)
    return {"entries": entries, "hits": hits, "by_phase": by_phase,
            "enabled": enabled(), "dir": str(DIR)}


def clear():
    if DIR.exists():
        shutil.rmtree(DIR, ignore_errors=True)
    return True


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "stats"
    if cmd == "stats":
        s = stats()
        print(f"phase cache: {s['entries']} entr(ies), {s['hits']} hit(s) "
              f"[{'enabled' if s['enabled'] else 'DISABLED'}]")
        for p, n in sorted(s["by_phase"].items(), key=lambda kv: -kv[1]):
            if n:
                print(f"  {p:<15} {n} phase call(s) avoided")
        return 0
    if cmd == "clear":
        clear()
        print("phase cache cleared")
        return 0
    if cmd in ("lookup", "store"):
        # lookup|store <phase> <out_label> <model> <prompt_file> <run_key> [ctx...]
        if len(argv) < 7:
            print("usage: phase_cache.py lookup|store <phase> <out_label> <model> "
                  "<prompt_file> <run_key> [context...]", file=sys.stderr)
            return 64
        phase, out_label, model, prompt_file, run_key = argv[2:7]
        ctx = argv[7:]
        fn = lookup if cmd == "lookup" else store
        ok = fn(phase, out_label, model, prompt_file, ctx, run_key)
        return 0 if ok else 1
    print(f"unknown command {cmd}", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv))

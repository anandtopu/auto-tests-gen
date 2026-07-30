#!/usr/bin/env python3
"""Export and import the platform's durable state as one portable bundle.

## The problem

Platform state is spread across the filesystem by purpose, which is right for the
running system and wrong for moving it. Some of it is committed (registry, curated
guidance, catalog), some lives only on a volume (`reports/` — run records, plans,
review board, OpenHands trace), and some is deliberately disposable (`out/`,
`workspace/`, the phase cache).

That means a deployment's *history* is only as durable as one PVC. Restarts are fine —
the volume outlives the pod. But a new namespace, a rebuilt cluster, a laptop-to-server
move or a second environment starts blank, and there was no way to carry the work over.

## What a bundle contains

One `.tar.gz` holding exactly the state that constitutes work somebody did:

  registry/repo-registry.yaml     the estate: which repos exist and how they map
  registry/org-config.yaml        phase policy, budgets, model tiers
  knowledge/repos/, curated/      per-repo guidance a human wrote
  catalog/*.jsonl                 the test catalog (evidence, mappings)
  catalog/review/                 mapping review queues
  reports/runs/*.json, *.diff     run history + archived generated code
  reports/runs/reviews.json       team-review decisions and release assignments
  reports/plans/                  test plans, their contracts and lifecycle state
  reports/openhands/state.json    the OpenHands request/conversation trace
  testplans/, testdata/           the plan markdown and canonical data

Plus a `manifest.json` recording the schema version, when and where it came from, and a
sha256 per file — so an import can verify it got what was exported.

## What it deliberately excludes

`out/`, `workspace/`, `reports/phase-cache/`, `reports/exports/`, `reports/*.log`,
`reports/catalog.db`, `knowledge/generated/`, `.env` and `aiqe.properties`.

The first group is regenerable scratch; carrying it would move stale derived data
around. The last two are **credentials** — a bundle is a thing people email and commit,
so secrets must never be in one. `catalog.db` is rebuilt by `make catalog-db` from the
JSONL that IS included.

## Import modes

  merge (default)  add what is missing, keep what the target already has. Safe on a
                   populated deployment: nothing local is destroyed.
  replace          overwrite the target's copy of every path in the bundle.

Both refuse to run while a pipeline holds the lock, because rewriting state under a
live run is how you get a half-imported estate.

CLI:
  state_bundle.py export [path]            -> reports/exports/<stamp>-state.tar.gz
  state_bundle.py inspect <bundle>         manifest + file count, no writes
  state_bundle.py import <bundle> [--replace] [--dry-run]
"""
import hashlib
import json
import os
import pathlib
import sys
import tarfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

SCHEMA = 1

# Directories copied wholesale, and single files. Order is irrelevant; both are
# filtered through EXCLUDE so a new disposable path cannot leak in by accident.
INCLUDE_DIRS = [
    "knowledge/repos", "knowledge/curated", "knowledge/synced",
    "catalog", "reports/runs", "reports/plans", "reports/openhands",
    "testplans", "testdata",
]
INCLUDE_FILES = [
    "registry/repo-registry.yaml", "registry/org-config.yaml", "AGENTS.md",
]

# Never bundled. Scratch is regenerable; the last two are CREDENTIALS and a bundle is
# something people copy between machines and attach to tickets.
EXCLUDE_PARTS = (
    "out/", "workspace/", "reports/phase-cache/", "reports/exports/",
    "knowledge/generated/", "__pycache__/", ".git/",
    # Bootstrap is CODE (extract/correlate/index), not state — it ships in the image
    # and the repo. Bundling it moved a copy of the source around for no reason and
    # would let an import overwrite live tooling with an older revision.
    "catalog/bootstrap/", "catalog/templates/",
)
EXCLUDE_NAMES = (".env", "aiqe.properties", "catalog.db", "dashboard.html",
                 "queue.json")
# `.py` by suffix, not by directory: a bundle carries STATE, and source lives in the
# image. Excluding paths one at a time missed catalog/review/export_review_queue.py,
# and would miss the next script somebody drops next to a data file.
EXCLUDE_SUFFIX = (".log", ".py", ".pyc", ".lock", ".sh")
# Quarantined state files (fs_lock renames a corrupt store to <name>.corrupt-<ts>)
# are local forensic artifacts — carrying them would plant one deployment's damage
# in another.
EXCLUDE_PARTS = EXCLUDE_PARTS + (".corrupt-",)


def _excluded(rel):
    posix = rel.as_posix()
    if any(part in posix + "/" for part in EXCLUDE_PARTS):
        return True
    if rel.name in EXCLUDE_NAMES or rel.name.startswith("."):
        return rel.name not in (".gitkeep",)
    return rel.suffix in EXCLUDE_SUFFIX


def collect():
    """Every bundled path, relative to the repo root. Deterministic order."""
    out = []
    for f in INCLUDE_FILES:
        p = ROOT / f
        if p.is_file() and not _excluded(pathlib.Path(f)):
            out.append(pathlib.Path(f))
    for d in INCLUDE_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(ROOT)
            if not _excluded(rel):
                out.append(rel)
    return sorted(set(out), key=lambda r: r.as_posix())


def _sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def export(dest=None):
    """Write a bundle. Returns its path."""
    files = collect()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if dest:
        out = pathlib.Path(dest)
    else:
        out = ROOT / "reports/exports" / f"{stamp}-state.tar.gz"
    out.parent.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema": SCHEMA, "created": time.time(), "created_h": stamp,
        "source_host": os.environ.get("HOSTNAME") or os.environ.get("COMPUTERNAME", ""),
        "file_count": len(files),
        "files": {r.as_posix(): _sha(ROOT / r) for r in files},
    }
    man_path = ROOT / "reports/exports" / f".manifest-{stamp}.json"
    man_path.parent.mkdir(parents=True, exist_ok=True)
    man_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8", newline="\n")
    try:
        with tarfile.open(out, "w:gz") as tar:
            tar.add(man_path, arcname="manifest.json")
            for rel in files:
                tar.add(ROOT / rel, arcname=f"state/{rel.as_posix()}")
    finally:
        man_path.unlink(missing_ok=True)
    return out


def inspect(bundle):
    """Manifest + a verification of what the archive actually carries. No writes."""
    with tarfile.open(bundle, "r:gz") as tar:
        try:
            man = json.loads(tar.extractfile("manifest.json").read().decode("utf-8"))
        except (KeyError, AttributeError, ValueError):
            raise SystemExit(f"{bundle}: not an AI-QE state bundle (no manifest.json)")
        members = [m.name[len("state/"):] for m in tar.getmembers()
                   if m.isfile() and m.name.startswith("state/")]
    declared = set(man.get("files") or {})
    present = set(members)
    return {"schema": man.get("schema"), "created_h": man.get("created_h", ""),
            "source_host": man.get("source_host", ""),
            "declared": len(declared), "present": len(present),
            "missing": sorted(declared - present)[:20],
            "extra": sorted(present - declared)[:20]}


def _pipeline_busy():
    lock = ROOT / "out/.pipeline.lock"
    return lock.exists()


def import_bundle(bundle, replace=False, dry_run=False, force=False):
    """Restore a bundle into this deployment.

    merge (default): only paths absent locally are written — a populated deployment
    keeps everything it already has. replace: every bundled path overwrites its local
    copy. Refuses while a run holds the pipeline lock either way.
    """
    if _pipeline_busy() and not force:
        raise SystemExit("a pipeline run holds out/.pipeline.lock — rewriting state "
                         "under a live run risks a half-imported estate. Wait for it "
                         "to finish, or pass --force if the lock is stale.")
    info = inspect(bundle)
    if info["schema"] != SCHEMA:
        raise SystemExit(f"bundle schema {info['schema']} != supported {SCHEMA}")

    written, skipped, mismatched = [], [], []
    with tarfile.open(bundle, "r:gz") as tar:
        man = json.loads(tar.extractfile("manifest.json").read().decode("utf-8"))
        shas = man.get("files") or {}
        for m in tar.getmembers():
            if not m.isfile() or not m.name.startswith("state/"):
                continue
            rel = m.name[len("state/"):]
            # Refuse anything that escapes the root: a bundle is untrusted input.
            target = (ROOT / rel).resolve()
            if not str(target).startswith(str(ROOT.resolve())):
                raise SystemExit(f"bundle contains an unsafe path: {rel}")
            data = tar.extractfile(m).read()
            if shas.get(rel) and hashlib.sha256(data).hexdigest() != shas[rel]:
                mismatched.append(rel)
                continue
            exists = target.exists()
            if exists and not replace:
                skipped.append(rel)
                continue
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            written.append(rel)
    return {"written": written, "skipped": skipped, "mismatched": mismatched,
            "mode": "replace" if replace else "merge", "dry_run": dry_run}


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "export"
    if cmd == "export":
        out = export(argv[2] if len(argv) > 2 else None)
        n = len(collect())
        print(f"exported {n} file(s) -> {out}")
        print("Import elsewhere with: python3 engine/lib/state_bundle.py import "
              f"{out.name}   (add --replace to overwrite the target's copies)")
        return 0
    if cmd == "inspect":
        if len(argv) < 3:
            print("usage: state_bundle.py inspect <bundle>", file=sys.stderr)
            return 64
        for k, v in inspect(argv[2]).items():
            print(f"{k:<12} {v}")
        return 0
    if cmd == "import":
        if len(argv) < 3:
            print("usage: state_bundle.py import <bundle> [--replace] [--dry-run]",
                  file=sys.stderr)
            return 64
        r = import_bundle(argv[2], replace="--replace" in argv,
                          dry_run="--dry-run" in argv, force="--force" in argv)
        verb = "would write" if r["dry_run"] else "wrote"
        print(f"{r['mode']} import: {verb} {len(r['written'])} file(s), "
              f"kept {len(r['skipped'])} existing")
        if r["mismatched"]:
            print(f"REJECTED {len(r['mismatched'])} file(s) whose checksum did not "
                  f"match the manifest: {', '.join(r['mismatched'][:5])}")
        if not r["dry_run"] and r["written"]:
            print("Next: make agents && make catalog-db   (regenerate derived data)")
        return 0
    print(f"unknown command {cmd} (export | inspect | import)", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv))

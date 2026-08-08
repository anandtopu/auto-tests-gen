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
  reports/agent-artifacts/        content-addressed run evidence (full profile only)
  reports/plans/                  test plans, their contracts and lifecycle state
  reports/approved/               finalized selective-review artifacts (who approved what)
  reports/openhands/state.json    the OpenHands request/conversation trace
  testplans/, testdata/           the plan markdown and canonical data

Plus a `manifest.json` recording the schema version, when and where it came from, and a
sha256 per file — so an import can verify it got what was exported.

## What it deliberately excludes

`out/`, `workspace/`, `reports/phase-cache/`, `reports/exports/`, `reports/*.log`,
`reports/catalog.db`, `knowledge/generated/`, `.env` and `aiqe.properties`.
The knowledge-only profile also excludes `reports/agent-artifacts/` because it is
run-scoped audit history, not curated reusable knowledge.

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
import collections
import contextlib
import hashlib
import json
import os
import pathlib
import sys
import tarfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import app_paths
import fs_lock

SCHEMA = 1

# Directories copied wholesale, and single files. Order is irrelevant; both are
# filtered through EXCLUDE so a new disposable path cannot leak in by accident.
INCLUDE_DIRS = [
    "knowledge/repos", "knowledge/curated", "knowledge/synced",
    # Authored per-repo facts (docs/knowledge-base-proposal.md) are a
    # human's assertions about a repo — exactly the "state that IS
    # somebody's work" this bundle exists to carry. The derived tier
    # under knowledge/facts/derived/ is EXCLUDED below: it rebuilds.
    "knowledge/facts",
    "catalog", "reports/runs", "reports/plans", "reports/openhands",
    "reports/agent-artifacts",
    # The finalized product of a selective review: which scenarios and tests a
    # named reviewer approved, which they excluded and why, and the
    # needs_follow_up list for exclusions the gate had already committed. That
    # is a human decision, not a derived file — dropping it on migration loses
    # the answer to "who approved this, and what did they turn down?", which is
    # the question an audit opens with.
    "reports/approved",
    "testplans", "testdata", "specs",
]
# DELIBERATELY not bundled, so the absence is a decision and not an oversight:
# reports/retries.json holds rate-limit COUNTERS. They are operational state that
# rebuilds on first use, and carrying them would import one environment's
# cooldowns into another where nothing has failed yet.
INCLUDE_FILES = [
    "registry/repo-registry.yaml", "registry/org-config.yaml", "AGENTS.md",
]

# Profile: knowledge (roadmap 6.4). A new team bootstrapping from an experienced
# team's estate wants the compounding knowledge — guidance, catalog, conventions —
# WITHOUT inheriting the donor's run history, review decisions or plan lifecycle,
# which are that team's records, not transferable wisdom.
KNOWLEDGE_DIRS = ["knowledge/repos", "knowledge/curated", "knowledge/synced",
                  "knowledge/facts",
                  "catalog", "testplans", "specs"]
KNOWLEDGE_FILES = ["registry/org-config.yaml", "AGENTS.md"]

# Bundles carry org-config as transferable policy evidence, and older schema-1
# exports also carried the other image-owned paths below. Import never restores any
# of them: doing so would freeze an old policy/schema over a newer image (and fails
# under readOnlyRootFilesystem). The receiving image remains authoritative.
FROZEN_IMPORT_FILES = {"registry/org-config.yaml", "catalog/schema.json"}
FROZEN_IMPORT_PREFIXES = ("specs/platform/",)


def _frozen_import(rel):
    return rel in FROZEN_IMPORT_FILES or rel.startswith(FROZEN_IMPORT_PREFIXES)

# Never bundled. Scratch is regenerable; the last two are CREDENTIALS and a bundle is
# something people copy between machines and attach to tickets.
EXCLUDE_PARTS = (
    "out/", "workspace/", "reports/phase-cache/", "reports/exports/",
    "knowledge/generated/", "knowledge/facts/derived/",
    "__pycache__/", ".git/",
    # Retrieval substrate (cost-reduction 8.1): chunks + vectors are DERIVED
    # data — a bundle carries work, not caches; `make index-rebuild` restores
    # them on the receiving deployment.
    "reports/knowledge-index/",
    ".lock/",
    # Bootstrap is CODE (extract/correlate/index), not state — it ships in the image
    # and the repo. Bundling it moved a copy of the source around for no reason and
    # would let an import overwrite live tooling with an older revision.
    "catalog/bootstrap/", "catalog/schema.json", "catalog/templates/",
    "specs/platform/",
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
EXCLUDE_PARTS = EXCLUDE_PARTS + (".corrupt-", ".superseded-")


def _excluded(rel):
    posix = rel.as_posix()
    if any(part in posix + "/" for part in EXCLUDE_PARTS):
        return True
    if rel.name in EXCLUDE_NAMES or rel.name.startswith("."):
        return rel.name not in (".gitkeep",)
    return rel.suffix in EXCLUDE_SUFFIX


def source_of(rel):
    """WHERE a bundled path actually lives.

    This is the whole reason the backup was wrong. `collect()` resolved every
    include against ROOT, so under R12 relocation (AIQE_STATE_DIR, or a per-path
    knob) it read the IMAGE's factory copies at /app instead of the operator's
    state on the volume. Measured in a container with AIQE_STATE_DIR=/state: an
    edit to /state/registry/repo-registry.yaml was absent from every member of
    the resulting bundle, while the bundle still reported "exported 29 file(s)"
    and the nightly summary still said `ok`.

    A backup that silently contains somebody else's data is worse than no backup:
    the operator stops worrying, and finds out on the day they restore.

    Reading moves; the ARCHIVE NAME does not. Members and manifest keys stay
    repo-relative so a bundle taken from a relocated deployment still imports
    into one laid out differently — which is the entire point of a portable
    bundle."""
    return app_paths.resolve_rel(rel)


def collect(profile="full"):
    """Every bundled path, as a repo-relative NAME. Deterministic order.
    profile="knowledge" carries guidance/catalog/conventions only (roadmap 6.4).
    Use source_of() to turn a name into the file to read."""
    files = KNOWLEDGE_FILES if profile == "knowledge" else INCLUDE_FILES
    dirs = KNOWLEDGE_DIRS if profile == "knowledge" else INCLUDE_DIRS
    out = []
    for f in files:
        if source_of(f).is_file() and not _excluded(pathlib.Path(f)):
            out.append(pathlib.Path(f))
    for d in dirs:
        base = source_of(d)
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            # Name it relative to the INCLUDE, then re-root under the include's
            # repo-relative path — so a relocated directory still lands in the
            # archive under `catalog/...`, not `state/catalog/...`.
            rel = pathlib.Path(d) / p.relative_to(base)
            if not _excluded(rel):
                out.append(rel)
    return sorted(set(out), key=lambda r: r.as_posix())


def _sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def export(dest=None, profile="full"):
    """Write a bundle. Returns its path."""
    artifact_root = app_paths.artifacts_dir(ROOT)
    artifact_mutex = artifact_root / ".mutation"
    artifact_lock = (fs_lock.lock(artifact_mutex, timeout=30)
                     if profile == "full" and artifact_root.exists()
                     else contextlib.nullcontext())
    with artifact_lock:
        return _export_locked(dest, profile)


def _export_locked(dest=None, profile="full"):
    """Export implementation while the B1 store is stable."""
    files = collect(profile)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    suffix = "knowledge" if profile == "knowledge" else "state"
    if dest:
        out = pathlib.Path(dest)
    else:
        out = ROOT / "reports/exports" / f"{stamp}-{suffix}.tar.gz"
    out.parent.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema": SCHEMA, "profile": profile,
        "created": time.time(), "created_h": stamp,
        "source_host": os.environ.get("HOSTNAME") or os.environ.get("COMPUTERNAME", ""),
        "file_count": len(files),
        "files": {r.as_posix(): _sha(source_of(r)) for r in files},
    }
    man_path = ROOT / "reports/exports" / f".manifest-{stamp}.json"
    man_path.parent.mkdir(parents=True, exist_ok=True)
    man_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8", newline="\n")
    try:
        with tarfile.open(out, "w:gz") as tar:
            tar.add(man_path, arcname="manifest.json")
            for rel in files:
                tar.add(source_of(rel), arcname=f"state/{rel.as_posix()}")
    finally:
        man_path.unlink(missing_ok=True)
    return out


def _unsafe_archive_path(rel):
    """Return why a member name is unsafe/non-canonical, or an empty string.

    Tar names are POSIX paths, even when the importer runs on Windows. A backslash
    therefore has to be rejected explicitly: PurePosixPath treats it as a normal
    character, while WindowsPath later treats it as a separator. Without this check,
    ``state/..\\sibling`` passed the POSIX traversal guard and escaped ROOT on Windows.
    """
    if not rel or "\\" in rel:
        return "empty or backslash-separated path"
    rel_path = pathlib.PurePosixPath(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        return "absolute or parent-traversing path"
    if rel_path.as_posix() != rel or any(":" in part for part in rel_path.parts):
        return "non-canonical or drive-qualified path"
    return ""


def _allowed_archive_path(rel, profile):
    """A bundle may restore state, never an arbitrary file under the checkout.

    Checksums prove that bytes match the manifest supplied with the bundle; they do
    not make that manifest trusted. Apply the same include/exclude policy on import
    that export uses so a self-consistent hostile bundle cannot overwrite code.
    """
    if _frozen_import(rel):
        return True
    files = KNOWLEDGE_FILES if profile == "knowledge" else INCLUDE_FILES
    dirs = KNOWLEDGE_DIRS if profile == "knowledge" else INCLUDE_DIRS
    path = pathlib.Path(rel)
    included = rel in files or any(rel.startswith(d + "/") for d in dirs)
    return included and not _excluded(path)


def _inspect_tar(tar, bundle):
    """Verify one already-open archive without changing deployment state."""
    manifests = [m for m in tar.getmembers() if m.name == "manifest.json"]
    if len(manifests) != 1 or not manifests[0].isfile():
        raise SystemExit(
            f"{bundle}: not an AI-QE state bundle (need exactly one manifest.json)")
    try:
        man = json.loads(tar.extractfile(manifests[0]).read().decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, ValueError):
        raise SystemExit(
            f"{bundle}: not an AI-QE state bundle (invalid manifest.json)") from None
    shas = man.get("files")
    if not isinstance(shas, dict):
        raise SystemExit(
            f"{bundle}: not an AI-QE state bundle (manifest files is not an object)")

    profile = man.get("profile", "full")
    if profile not in ("full", "knowledge"):
        raise SystemExit(f"{bundle}: unsupported bundle profile: {profile}")

    names, mismatched, unsafe, invalid, disallowed = [], [], [], [], []
    for member in tar.getmembers():
        if member.name == "manifest.json":
            continue
        if not member.name.startswith("state/"):
            invalid.append(member.name)
            continue
        rel = member.name[len("state/"):]
        if not member.isfile():
            invalid.append(rel or member.name)
            continue
        names.append(rel)
        if _unsafe_archive_path(rel):
            unsafe.append(rel)
        if not _allowed_archive_path(rel, profile):
            disallowed.append(rel)
        stream = tar.extractfile(member)
        digest = hashlib.sha256(stream.read()).hexdigest() if stream else ""
        # Equality is intentional: an undeclared member has no trusted checksum
        # and must be reported as both extra and mismatched, never imported.
        if shas.get(rel) != digest:
            mismatched.append(rel)

    counts = collections.Counter(names)
    declared, present = set(shas), set(names)
    duplicates = sorted(name for name, count in counts.items() if count != 1)
    manifest_count = man.get("file_count")
    count_mismatch = not isinstance(manifest_count, int) or manifest_count != len(shas)
    return {
        "schema": man.get("schema"), "created_h": man.get("created_h", ""),
        "source_host": man.get("source_host", ""),
        "declared": len(declared), "present": len(names),
        "missing": sorted(declared - present)[:20],
        "extra": sorted(present - declared)[:20],
        "duplicates": duplicates[:20], "mismatched": sorted(set(mismatched))[:20],
        "unsafe": sorted(set(unsafe))[:20], "invalid": sorted(set(invalid))[:20],
        "disallowed": sorted(set(disallowed))[:20], "count_mismatch": count_mismatch,
    }


def _integrity_failures(info):
    failures = [name for name in
                ("missing", "extra", "duplicates", "mismatched", "unsafe", "invalid",
                 "disallowed")
                if info.get(name)]
    if info.get("count_mismatch"):
        failures.append("file_count")
    return failures


def inspect(bundle):
    """Manifest, membership and checksum verification. Never writes state."""
    with tarfile.open(bundle, "r:gz") as tar:
        return _inspect_tar(tar, bundle)


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
    written, skipped = [], []
    artifact_mutex = app_paths.artifacts_dir(ROOT) / ".mutation"
    # Preflight the SAME open archive before acquiring a state lock or creating a
    # destination directory. A corrupt member used to be skipped after earlier
    # members had already been written, and the CLI still exited 0: a failed restore
    # could therefore leave a plausible-looking half-estate that was not rollbackable.
    with tarfile.open(bundle, "r:gz") as tar:
        info = _inspect_tar(tar, bundle)
        if info["schema"] != SCHEMA:
            raise SystemExit(f"bundle schema {info['schema']} != supported {SCHEMA}")
        failures = _integrity_failures(info)
        if failures:
            labels = {"unsafe": "unsafe path", "invalid": "invalid member"}
            raise SystemExit("bundle integrity check failed before import: " +
                             ", ".join(labels.get(f, f) for f in failures))
        mutation_lock = (contextlib.nullcontext() if dry_run
                         else fs_lock.lock(artifact_mutex, timeout=30))
        with mutation_lock:
            for m in tar.getmembers():
                if not m.isfile() or not m.name.startswith("state/"):
                    continue
                rel = m.name[len("state/"):]
                if _frozen_import(rel):
                    skipped.append(rel)
                    continue
            # Refuse anything that escapes the root: a bundle is untrusted input
            # (they get emailed and attached to tickets — see the module header).
            #
            # Containment is a PATH relationship, never a string prefix. The old
            # str.startswith check accepted `../<root-name>-evil/payload`: a
            # SIBLING directory whose name merely starts with the root's does
            # satisfy the prefix while living entirely outside the checkout.
                target = app_paths.resolve_rel(rel, ROOT).resolve()
                # Reject a symlink inside an allowed top-level state directory that
                # resolves beyond its configured root. A configured state root or
                # per-path knob may itself live outside ROOT and is intentionally the
                # authority, so containment is relative to that resolver, not cwd.
                head = rel.partition("/")[0]
                anchor = app_paths.resolve_rel(head, ROOT).resolve()
                if rel.startswith("reports/agent-artifacts/"):
                    anchor = app_paths.artifacts_dir(ROOT).resolve()
                if rel in INCLUDE_FILES or rel in KNOWLEDGE_FILES:
                    anchor = target
                if target != anchor and anchor not in target.parents:
                    raise SystemExit(f"bundle contains an unsafe resolved path: {rel}")
                data = tar.extractfile(m).read()
                exists = target.exists()
                if exists and not replace:
                    skipped.append(rel)
                    continue
                if not dry_run:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                written.append(rel)
    return {"written": written, "skipped": skipped, "mismatched": [],
            "mode": "replace" if replace else "merge", "dry_run": dry_run}


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "export"
    if cmd == "export":
        profile = "knowledge" if "--knowledge" in argv else "full"
        dest = next((a for a in argv[2:] if not a.startswith("--")), None)
        out = export(dest, profile=profile)
        n = len(collect(profile))
        print(f"exported {n} file(s) -> {out}")
        print("Import elsewhere with: python3 engine/lib/state_bundle.py import "
              f"{out.name}   (add --replace to overwrite the target's copies)")
        return 0
    if cmd == "inspect":
        if len(argv) < 3:
            print("usage: state_bundle.py inspect <bundle>", file=sys.stderr)
            return 64
        info = inspect(argv[2])
        for k, v in info.items():
            print(f"{k:<12} {v}")
        return 1 if _integrity_failures(info) else 0
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
            print("Next: make agents && make catalog-db && make index-rebuild"
                  "   (regenerate derived data)")
        return 0
    print(f"unknown command {cmd} (export | inspect | import)", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv))

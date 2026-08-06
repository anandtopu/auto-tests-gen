#!/usr/bin/env python3
"""Durable content-addressed agent artifacts (PRD B1).

Blobs contain only immutable bytes.  Run-specific provenance lives in separate,
append-only reference records so identical content can be shared without losing
who produced it.  Every mutation is serialized by ``fs_lock`` and every read
recomputes the advertised digest.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app_paths  # noqa: E402
import fs_lock  # noqa: E402

SCHEMA = 1
STORE = app_paths.artifacts_dir(ROOT)
ALLOWED_KINDS = frozenset({
    "estate-guidance", "repo-guidance", "conventions", "exemplar",
    "phase-context", "context-manifest", "extend-candidates",
    "requirements", "plan", "generated-skill",
    "task-bundle",
})
DEFAULT_MAX_BYTES = 1_048_576
DEFAULT_KEEP_RUNS = 200
_SHA = re.compile(r"^[0-9a-f]{64}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_REF = re.compile(r"^[0-9a-f]{32}$")
_SECRET_HINTS = (
    "token", "secret", "password", "passwd", "api_key", "apikey",
    "credential", "auth", "cookie", "session", "private", "webhook",
    "signature", "bearer", "pat",
)
_SECRET_ASSIGNMENT = re.compile(
    rb"(?i)(api[_-]?key|password|passwd|secret|token|credential|webhook)"
    rb"\s*[:=]\s*[\"'][^\"'\r\n]+"
)
_PRIVATE_KEY = re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_BEARER = re.compile(rb"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}")
_URL_CREDENTIAL = re.compile(rb"(?i)https?://[^/@\s:]+:[^/@\s]+@")


class ArtifactStoreError(RuntimeError):
    """Base class for artifact-store failures."""


class ArtifactRejected(ArtifactStoreError):
    """Content or metadata is unsafe or outside the store contract."""


class ArtifactCorrupt(ArtifactStoreError):
    """Stored bytes do not match their immutable address or record schema."""


def enabled() -> bool:
    return (os.environ.get("AIQE_ARTIFACT_STORE") or "0").strip() == "1"


def store_dir(root=None) -> pathlib.Path:
    return app_paths.artifacts_dir(ROOT if root is None else root)


def _positive_env(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ArtifactRejected(f"{name} must be a positive integer") from exc
    if value < 1:
        raise ArtifactRejected(f"{name} must be a positive integer")
    return value


def _bytes(content: bytes | str) -> bytes:
    if isinstance(content, str):
        return content.encode("utf-8")
    if isinstance(content, bytes):
        return content
    raise ArtifactRejected("artifact content must be str or bytes")


def _validate_content(content: bytes) -> None:
    ceiling = _positive_env("AIQE_ARTIFACT_MAX_BYTES", DEFAULT_MAX_BYTES)
    if len(content) > ceiling:
        raise ArtifactRejected(
            f"artifact is {len(content)} bytes; ceiling is {ceiling}")
    if any(p.search(content) for p in (
            _SECRET_ASSIGNMENT, _PRIVATE_KEY, _BEARER, _URL_CREDENTIAL)):
        raise ArtifactRejected("artifact contains secret-shaped content")
    # Reuse the platform's denylist against configured values too.  This catches
    # a raw credential even when the surrounding field name was stripped.
    for name, value in os.environ.items():
        if (len(value) >= 8 and any(h in name.lower() for h in _SECRET_HINTS)
                and value.encode("utf-8", errors="ignore") in content):
            raise ArtifactRejected(f"artifact contains configured secret {name}")


def _validate_name(label: str, value: str) -> str:
    value = str(value or "")
    if not _NAME.fullmatch(value):
        raise ArtifactRejected(f"invalid {label}")
    return value


def _validate_sha(label: str, value: str) -> str:
    value = str(value or "").lower()
    if not _SHA.fullmatch(value):
        raise ArtifactRejected(f"{label} must be a sha256")
    return value


def _paths(root=None):
    base = store_dir(root)
    return base, base / "blobs", base / "refs", base / "quarantine", base / ".mutation"


def _quarantine(path: pathlib.Path, quarantine: pathlib.Path) -> pathlib.Path:
    quarantine.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = quarantine / f"{path.name}.corrupt-{stamp}-{uuid.uuid4().hex[:8]}"
    fs_lock.replace_atomic(path, target)
    return target


def _atomic_bytes(path: pathlib.Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        fs_lock.replace_atomic(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _read_blob(path: pathlib.Path, sha: str, quarantine: pathlib.Path) -> bytes:
    try:
        content = path.read_bytes()
    except FileNotFoundError as exc:
        raise ArtifactCorrupt(f"artifact blob {sha} is missing") from exc
    if hashlib.sha256(content).hexdigest() != sha:
        moved = _quarantine(path, quarantine)
        raise ArtifactCorrupt(
            f"artifact blob {sha} failed hash validation; quarantined as {moved.name}")
    return content


def _read_ref(path: pathlib.Path, quarantine: pathlib.Path) -> dict:
    if not path.exists():
        raise ArtifactCorrupt(f"artifact reference {path.stem} is missing")
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        required = {"schema", "reference_id", "kind", "produced_by_run",
                    "produced_at", "inputs_sha", "blob_sha256", "size",
                    "record_sha256"}
        if (not isinstance(row, dict) or not required.issubset(row)
                or row["schema"] != SCHEMA
                or row["reference_id"] != path.stem
                or not _REF.fullmatch(str(row["reference_id"]))
                or row["kind"] not in ALLOWED_KINDS
                or not _SHA.fullmatch(str(row["inputs_sha"]))
                or not _SHA.fullmatch(str(row["blob_sha256"]))
                or not isinstance(row["size"], int) or row["size"] < 0
                or ("repo" in row) == ("key" in row)
                or row["record_sha256"] != _record_sha(row)):
            raise ValueError("invalid reference schema")
        _validate_name("produced_by_run", row["produced_by_run"])
        _validate_name("scope", row.get("repo") or row.get("key"))
        parsed = dt.datetime.fromisoformat(row["produced_at"].replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("reference timestamp lacks timezone")
        return row
    except (ArtifactRejected, AttributeError, OSError, OverflowError, TypeError,
            UnicodeError, json.JSONDecodeError, ValueError) as exc:
        moved = _quarantine(path, quarantine)
        raise ArtifactCorrupt(
            f"artifact reference {path.name} is corrupt; quarantined as {moved.name}") from exc


def _record_sha(row: dict) -> str:
    unsigned = {key: value for key, value in row.items() if key != "record_sha256"}
    raw = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def put(content: bytes | str, *, kind: str, produced_by_run: str,
        inputs_sha: str, repo: str | None = None, key: str | None = None,
        produced_at: str | None = None, source_tier: str | None = None,
        root=None) -> dict | None:
    """Store content and append one provenance reference; disabled is a no-op."""
    if not enabled():
        return None
    raw = _bytes(content)
    _validate_content(raw)
    if kind not in ALLOWED_KINDS:
        raise ArtifactRejected(f"unsupported artifact kind {kind!r}")
    if kind == "repo-guidance" and source_tier != "generated":
        raise ArtifactRejected(
            "repo guidance is storable only when source_tier='generated'")
    if (repo is None) == (key is None):
        raise ArtifactRejected("exactly one of repo or key is required")
    scope_name, scope_value = ("repo", repo) if repo is not None else ("key", key)
    scope_value = _validate_name(scope_name, scope_value)
    run = _validate_name("produced_by_run", produced_by_run)
    inputs = _validate_sha("inputs_sha", inputs_sha)
    when = produced_at or dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        parsed_at = dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
        if parsed_at.tzinfo is None:
            raise ValueError("timezone required")
    except ValueError as exc:
        raise ArtifactRejected("produced_at must be ISO-8601") from exc

    when = parsed_at.astimezone(dt.timezone.utc).isoformat()
    sha = hashlib.sha256(raw).hexdigest()
    ref_id = uuid.uuid4().hex
    row = {"schema": SCHEMA, "reference_id": ref_id, "kind": kind,
           scope_name: scope_value, "produced_by_run": run,
           "produced_at": when, "inputs_sha": inputs, "blob_sha256": sha,
           "size": len(raw)}
    if source_tier is not None:
        row["source_tier"] = _validate_name("source_tier", source_tier)
    row["record_sha256"] = _record_sha(row)
    base, blobs, refs, quarantine, mutex = _paths(root)
    with fs_lock.lock(mutex, timeout=30):
        blobs.mkdir(parents=True, exist_ok=True)
        refs.mkdir(parents=True, exist_ok=True)
        blob = blobs / sha
        if blob.exists():
            try:
                _read_blob(blob, sha, quarantine)
            except ArtifactCorrupt:
                _atomic_bytes(blob, raw)  # the caller supplied the addressed bytes
        else:
            _atomic_bytes(blob, raw)
        # A collision (or a deterministic UUID monkeypatch in a test) must not
        # turn append into replace.  Generate a free name while holding the lock.
        target = refs / f"{ref_id}.json"
        attempts = 0
        while target.exists() and attempts < 8:
            ref_id = uuid.uuid4().hex
            row["reference_id"] = ref_id
            row["record_sha256"] = _record_sha(row)
            target = refs / f"{ref_id}.json"
            attempts += 1
        if target.exists():
            raise ArtifactStoreError("could not allocate a unique artifact reference id")
        fs_lock.write_json_atomic(target, row, sort_keys=True)
    return row


def get(reference_id: str, *, root=None) -> tuple[dict, bytes]:
    if not _REF.fullmatch(str(reference_id or "")):
        raise ArtifactRejected("invalid reference id")
    _, blobs, refs, quarantine, mutex = _paths(root)
    with fs_lock.lock(mutex, timeout=30):
        row = _read_ref(refs / f"{reference_id}.json", quarantine)
        content = _read_blob(blobs / row["blob_sha256"], row["blob_sha256"], quarantine)
        if len(content) != row["size"]:
            moved = _quarantine(refs / f"{reference_id}.json", quarantine)
            raise ArtifactCorrupt(
                f"artifact reference {reference_id} has the wrong size; "
                f"quarantined as {moved.name}")
    return row, content


def references(*, root=None) -> list[dict]:
    _, _, refs, quarantine, mutex = _paths(root)
    if not refs.exists():
        return []
    with fs_lock.lock(mutex, timeout=30):
        rows = [_read_ref(path, quarantine) for path in sorted(refs.glob("*.json"))]
    return sorted(rows, key=lambda row: (row["produced_at"], row["reference_id"]))


def prune(*, keep_runs: int | None = None, root=None) -> dict:
    """Keep references for newest N runs, then sweep unreferenced blobs."""
    keep = keep_runs if keep_runs is not None else _positive_env(
        "AIQE_ARTIFACT_KEEP_RUNS", DEFAULT_KEEP_RUNS)
    if keep < 1:
        raise ArtifactRejected("keep_runs must be positive")
    base, blobs, refs, quarantine, mutex = _paths(root)
    if not base.exists():
        return {"kept_runs": 0, "removed_references": 0, "removed_blobs": 0,
                "sweep_skipped": False}
    with fs_lock.lock(mutex, timeout=30):
        rows = []
        corrupt = bool(quarantine.exists() and any(
            ".json.corrupt-" in path.name for path in quarantine.iterdir()))
        for path in sorted(refs.glob("*.json")) if refs.exists() else []:
            try:
                rows.append((path, _read_ref(path, quarantine)))
            except ArtifactCorrupt:
                corrupt = True
        newest = {}
        for _, row in rows:
            newest[row["produced_by_run"]] = max(
                newest.get(row["produced_by_run"], ""), row["produced_at"])
        kept_runs = {run for run, _ in sorted(
            newest.items(), key=lambda item: (item[1], item[0]), reverse=True)[:keep]}
        removed_refs = 0
        for path, row in rows:
            if row["produced_by_run"] not in kept_runs:
                path.unlink()
                removed_refs += 1
        live = {row["blob_sha256"] for path, row in rows if path.exists()}
        removed_blobs = 0
        # A quarantined reference may be the only pointer to a blob.  Preserve
        # blobs until an operator resolves it rather than turning corruption
        # recovery into data loss.
        if not corrupt and blobs.exists():
            for path in blobs.iterdir():
                if path.is_file() and path.name not in live:
                    path.unlink()
                    removed_blobs += 1
    return {"kept_runs": len(kept_runs), "removed_references": removed_refs,
            "removed_blobs": removed_blobs, "sweep_skipped": corrupt}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prune_p = sub.add_parser("prune")
    prune_p.add_argument("--keep", type=int)
    args = parser.parse_args(argv)
    if args.command == "prune":
        print(json.dumps(prune(keep_runs=args.keep), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Durable, content-addressed reuse of pure phase products (PRD B3).

The local phase cache remains first owner.  This module is a second-level cache
backed by B1, useful after a cache clear or portable-state move.  Its key covers
the complete prompt/context bytes, run parameters, provider model, policy and
generator implementation.  Only phases whose contract plus declared artifacts
are their entire product are allowed; workspace/git producers are denied.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import pathlib
import re
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app_paths  # noqa: E402
import artifact_store  # noqa: E402
import budget  # noqa: E402
import env_flag  # noqa: E402
import fs_lock  # noqa: E402
import phase_cache  # noqa: E402

SCHEMA = 1
GENERATOR_VERSION = "artifact-reuse-v1"
PURE_PHASES = frozenset({
    "analyze", "testplan", "planadversary", "planarbiter", "testdata",
    "critic", "triage",
})
DENIED_PHASES = frozenset({"generate", "validate", "reviewrepair"})
PRODUCTS = {phase: tuple(phase_cache.CACHEABLE[phase]) for phase in PURE_PHASES}
DIRECTORY_PRODUCTS = frozenset({"testdata"})
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ReuseError(RuntimeError):
    """Reusable evidence is invalid or unsafe to restore."""


def flag_enabled() -> bool:
    return env_flag.flag("AIQE_ARTIFACT_REUSE", False)


def enabled() -> bool:
    return flag_enabled() and artifact_store.enabled()


def _event_path(root=ROOT) -> pathlib.Path:
    return pathlib.Path(root) / "out/artifact-reuse.json"


def _logical(path: pathlib.Path, root=ROOT) -> str:
    try:
        return path.resolve(strict=False).relative_to(
            pathlib.Path(root).resolve()).as_posix()
    except ValueError:
        return f"external/{path.name}"


def _describe(value, root=ROOT) -> dict:
    path = pathlib.Path(value)
    if not path.is_absolute():
        path = pathlib.Path(root) / path
    logical = _logical(path, root)
    if not path.exists():
        return {"path": logical, "state": "absent"}
    if path.is_dir():
        files = []
        for item in sorted(path.rglob("*")):
            if item.is_file():
                files.append({"path": item.relative_to(path).as_posix(),
                              "sha256": hashlib.sha256(item.read_bytes()).hexdigest()})
        raw = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        return {"path": logical, "state": "directory", "files": files,
                "sha256": hashlib.sha256(raw).hexdigest()}
    body = path.read_bytes()
    return {"path": logical, "state": "file", "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest()}


def _generator_fingerprint(root=ROOT) -> str:
    root = pathlib.Path(root)
    sources = [root / "engine/phases", root / "adapters/llm",
               root / "engine/lib/extract_contract.py",
               root / "engine/lib/derived_writes.py",
               root / "registry/org-config.yaml"]
    rows = [_describe(path, root) for path in sources]
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def input_manifest(phase: str, model: str, prompt_file: str,
                   context_files: list[str], run_key: str, *, root=ROOT,
                   generator_version: str = GENERATOR_VERSION) -> dict:
    return {"schema": SCHEMA, "generator_version": generator_version,
            "generator_fingerprint": _generator_fingerprint(root),
            "phase": phase, "model": model, "run_key": run_key,
            "target_repo": os.environ.get("AIQE_TARGET_REPO", ""),
            "prompt": _describe(prompt_file, root),
            "context": [_describe(path, root) for path in context_files]}


def inputs_sha(phase: str, model: str, prompt_file: str,
               context_files: list[str], run_key: str, *, root=ROOT,
               generator_version: str = GENERATOR_VERSION) -> str:
    raw = json.dumps(input_manifest(
        phase, model, prompt_file, context_files, run_key, root=root,
        generator_version=generator_version), sort_keys=True,
        separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _record_event(event: dict, root=ROOT) -> bool:
    if not flag_enabled():
        return False
    path = _event_path(root)
    try:
        with fs_lock.lock(path, timeout=30):
            doc = fs_lock.read_json_guarded(path, {"schema": SCHEMA, "events": []})
            if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
                doc = {"schema": SCHEMA, "events": []}
            if not isinstance(doc.get("events"), list):
                doc["events"] = []
            doc["events"].append({"schema": SCHEMA, **event})
            fs_lock.write_json_atomic(path, doc, sort_keys=True)
        return True
    except (OSError, TimeoutError, TypeError, ValueError):
        return False


def _reason(outcome: str, phase: str, reason: str, **extra) -> dict:
    return {"outcome": outcome, "phase": phase, "reason": reason[:300], **extra}


def phase_cache_claim(phase: str, root=ROOT) -> None:
    _record_event(_reason(
        "phase_cache", phase,
        "identical work was already claimed by the phase cache; artifact reuse "
        "is not counted"), root)


def _product_roots(phase: str, run_key: str, root=ROOT) -> list[pathlib.Path]:
    return [app_paths.resolve_rel(pattern.format(KEY=run_key), root)
            for pattern in PRODUCTS.get(phase, ())]


def _safe_label(value: str) -> str:
    if not _LABEL.fullmatch(str(value or "")):
        raise ReuseError("invalid phase output label")
    return value


def _collect_artifacts(phase: str, run_key: str, root=ROOT) -> dict[str, str]:
    out = {}
    patterns = [pattern.format(KEY=run_key) for pattern in PRODUCTS.get(phase, ())]
    for logical, path in zip(patterns, _product_roots(phase, run_key, root)):
        if path.is_dir():
            candidates = [item for item in sorted(path.rglob("*")) if item.is_file()]
        else:
            candidates = [path] if path.is_file() else []
        for item in candidates:
            rel = (pathlib.PurePosixPath(logical) / item.relative_to(path).as_posix()
                   if path.is_dir() else pathlib.PurePosixPath(logical))
            if item.resolve() != _allowed_target(rel.as_posix(), phase, run_key, root):
                raise ReuseError("phase product resolved through an unsafe link")
            out[rel.as_posix()] = item.read_text(encoding="utf-8", errors="replace")
    return out


def _allowed_target(rel: str, phase: str, run_key: str, root=ROOT) -> pathlib.Path:
    pure = pathlib.PurePosixPath(rel)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise ReuseError("reusable artifact contains an unsafe path")
    target = app_paths.resolve_rel(pure, root).resolve()
    product_base = app_paths.resolve_rel(pure.parts[0], root).resolve()
    if target != product_base and product_base not in target.parents:
        raise ReuseError("reusable artifact escaped its configured product directory")
    for product in _product_roots(phase, run_key, root):
        allowed = product.resolve(strict=False)
        if target == allowed or (phase in DIRECTORY_PRODUCTS and allowed in target.parents):
            return target
    raise ReuseError("reusable artifact is outside the phase product allowlist")


def _write_text(path: pathlib.Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(body, encoding="utf-8", newline="\n")
        fs_lock.replace_atomic(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _estimated_tokens(prompt_file: str, context_files: list[str], package: dict,
                      root=ROOT) -> int:
    chars = len(json.dumps(package, sort_keys=True))
    for value in [prompt_file, *context_files]:
        path = pathlib.Path(value)
        if not path.is_absolute():
            path = pathlib.Path(root) / path
        try:
            chars += path.stat().st_size if path.is_file() else 0
        except OSError:
            pass
    return max(1, math.ceil(chars / 4))


def store(phase: str, out_label: str, model: str, prompt_file: str,
          context_files: list[str], run_id: str, run_key: str, *, root=ROOT,
          generator_version: str = GENERATOR_VERSION) -> bool:
    if not enabled() or phase not in PURE_PHASES or phase in DENIED_PHASES:
        return False
    try:
        out_label = _safe_label(out_label)
        contract_path = pathlib.Path(root) / f"out/{out_label}.contract.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        if not isinstance(contract, dict):
            return False
        manifest = input_manifest(
            phase, model, prompt_file, context_files, run_key, root=root,
            generator_version=generator_version)
        digest = hashlib.sha256(json.dumps(
            manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        package = {"schema": SCHEMA, "artifact": "reusable-phase",
                   "phase": phase, "model": model, "inputs_sha": digest,
                   "input_manifest": manifest, "contract": contract,
                   "artifacts": _collect_artifacts(phase, run_key, root)}
        usage = budget.phase_usage(pathlib.Path(root) / f"out/{out_label}.json")
        supplied = sum(int(usage.get(name) or 0) for name in (
            "input_tokens", "output_tokens", "cache_read_tokens",
            "cache_creation_tokens"))
        package["tokens_avoided"] = supplied or _estimated_tokens(
            prompt_file, context_files, package, root)
        package["token_basis"] = "reported" if supplied else "estimated"
        body = json.dumps(package, sort_keys=True, separators=(",", ":"))
        row = artifact_store.put(
            body, kind="reusable-phase", key=run_key,
            produced_by_run=run_id, inputs_sha=digest, root=root)
        if row is not None:
            _record_event({"outcome": "stored", "phase": phase,
                           "reason": "fresh pure-phase product stored for reuse",
                           "reference_id": row["reference_id"],
                           "inputs_sha": digest}, root)
            return True
        return False
    except artifact_store.ArtifactRejected as exc:
        _record_event(_reason(
            "rejected", phase,
            f"artifact store refused fresh product: {exc.__class__.__name__}"), root)
        return False
    except (artifact_store.ArtifactStoreError, KeyError, OSError, ReuseError,
            TypeError, ValueError) as exc:
        _record_event(_reason(
            "unavailable", phase,
            f"fresh product could not be stored: {exc.__class__.__name__}"), root)
        return False


def restore(phase: str, out_label: str, model: str, prompt_file: str,
            context_files: list[str], run_key: str, *, root=ROOT,
            generator_version: str = GENERATOR_VERSION) -> bool:
    if not flag_enabled():
        return False
    if not artifact_store.enabled():
        _record_event(_reason("unavailable", phase,
                             "AIQE_ARTIFACT_STORE is disabled"), root)
        return False
    if phase in DENIED_PHASES or phase not in PURE_PHASES:
        _record_event(_reason(
            "rejected", phase,
            "phase product includes workspace/git state or is not explicitly pure"), root)
        return False
    try:
        out_label = _safe_label(out_label)
        expected_manifest = input_manifest(
            phase, model, prompt_file, context_files, run_key, root=root,
            generator_version=generator_version)
        digest = hashlib.sha256(json.dumps(
            expected_manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        found = artifact_store.find(
            kind="reusable-phase", inputs_sha=digest, key=run_key, root=root)
        if found is None:
            _record_event(_reason("miss", phase, "no artifact has identical inputs",
                                  inputs_sha=digest), root)
            return False
        row, body = found
        package = json.loads(body.decode("utf-8"))
        if (not isinstance(package, dict) or package.get("schema") != SCHEMA
                or package.get("artifact") != "reusable-phase"
                or package.get("phase") != phase or package.get("model") != model
                or package.get("inputs_sha") != digest
                or package.get("input_manifest") != expected_manifest
                or not isinstance(package.get("contract"), dict)
                or not isinstance(package.get("artifacts"), dict)):
            raise ReuseError("reusable phase manifest does not match the request")
        token_count = package.get("tokens_avoided")
        token_basis = package.get("token_basis")
        if (not isinstance(token_count, int) or token_count < 0
                or token_basis not in ("reported", "estimated")):
            raise ReuseError("reusable phase token attribution is invalid")
        targets = [(_allowed_target(rel, phase, run_key, root), text)
                   for rel, text in package["artifacts"].items()
                   if isinstance(rel, str) and isinstance(text, str)]
        if len(targets) != len(package["artifacts"]):
            raise ReuseError("reusable artifact body has an invalid type")
        contract_path = pathlib.Path(root) / f"out/{out_label}.contract.json"
        fs_lock.write_json_atomic(contract_path, package["contract"], sort_keys=True)
        for target, text in targets:
            _write_text(target, text)
        _record_event({"outcome": "hit", "phase": phase,
                       "reason": "identical canonical inputs and generator version",
                       "reference_id": row["reference_id"],
                       "inputs_sha": digest,
                       "tokens_avoided": token_count,
                       "token_basis": token_basis}, root)
        return True
    except (artifact_store.ArtifactStoreError, OSError, ReuseError, TypeError,
            UnicodeError, ValueError) as exc:
        _record_event(_reason("unavailable", phase,
                             f"reusable artifact refused: {exc.__class__.__name__}"), root)
        return False


def summary(root=ROOT) -> dict:
    doc = fs_lock.read_json_guarded(_event_path(root), {"schema": SCHEMA, "events": []})
    events = doc.get("events") if isinstance(doc, dict) else []
    events = events if isinstance(events, list) else []
    hits = [event for event in events if event.get("outcome") == "hit"]
    basis = {}
    for event in hits:
        name = event.get("token_basis") or "estimated"
        basis[name] = basis.get(name, 0) + int(event.get("tokens_avoided") or 0)
    return {"schema": SCHEMA, "artifacts_reused": len(hits),
            "tokens_avoided": sum(basis.values()), "tokens_by_basis": basis,
            "events": events}


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "phase-cache":
        phase_cache_claim(argv[2])
        return 0
    if len(argv) < 8 or argv[1] not in ("restore", "store"):
        print("usage: artifact_reuse.py restore|store PHASE OUT MODEL PROMPT RUN KEY "
              "[CONTEXT ...]", file=sys.stderr)
        return 64
    command, phase, out_label, model, prompt, run_id, key = argv[1:8]
    context = argv[8:]
    if command == "restore":
        return 0 if restore(phase, out_label, model, prompt, context, key) else 1
    return 0 if store(phase, out_label, model, prompt, context, run_id, key) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

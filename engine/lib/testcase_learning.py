#!/usr/bin/env python3
"""A6 same-run testcase indexing and append-only review provenance.

The gate remains the only component that writes or commits test repositories.
This module reads an already-committed SHA, upserts only the changed spec files
into the derived chunk store, and records durable provenance beside run records.
Human outcomes are separate events: they never rewrite chunk text or sha256.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import env_flag  # noqa: E402
import fs_lock  # noqa: E402
import knowledge_chunks  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA_VERSION = 1
SPEC_SUFFIXES = (".spec.js", ".spec.ts", ".test.js", ".test.ts")
REVIEW_OUTCOMES = frozenset({"approved", "changes_requested"})


def _provenance_path(root=ROOT) -> pathlib.Path:
    configured = (os.environ.get("AIQE_TESTCASE_PROVENANCE_FILE") or "").strip()
    if configured and pathlib.Path(root) == ROOT:
        return pathlib.Path(configured)
    return pathlib.Path(root) / "reports/runs/testcase-provenance.jsonl"


def _chunks_path(root=ROOT) -> pathlib.Path:
    return knowledge_chunks.OUT if pathlib.Path(root) == ROOT else \
        pathlib.Path(root) / "reports/knowledge-index/chunks.jsonl"


def _read_jsonl(path: pathlib.Path, *, strict=False) -> list[dict]:
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            if strict:
                raise RuntimeError(
                    f"{path.name} contains malformed JSON on line {number}; "
                    "refusing to overwrite provenance")
            continue
        if not isinstance(row, dict):
            if strict:
                raise RuntimeError(
                    f"{path.name} line {number} is not an object; refusing to overwrite")
            continue
        rows.append(row)
    return rows


def _write_jsonl(path: pathlib.Path, rows: list[dict], *, sort_rows=False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=lambda row: row.get("chunk_id", "")) \
        if sort_rows else rows
    body = "".join(json.dumps(row, sort_keys=True) + "\n" for row in ordered)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(body, encoding="utf-8", newline="\n")
        fs_lock.replace_atomic(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _event_id(kind: str, *identity) -> str:
    raw = json.dumps([kind, *identity], separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def append_event(event: dict, root=ROOT) -> dict:
    """Append one idempotent event under a cross-process lock."""
    path = _provenance_path(root)
    clean = dict(event)
    clean["schema_version"] = SCHEMA_VERSION
    event_id = str(clean.get("event_id") or "")
    if not event_id:
        raise ValueError("provenance event requires event_id")
    with fs_lock.lock(path, timeout=30):
        rows = _read_jsonl(path, strict=True)
        existing = next((row for row in rows if row.get("event_id") == event_id), None)
        if existing is not None:
            return existing
        rows.append(clean)
        _write_jsonl(path, rows)
    return clean


def events(root=ROOT, *, strict=False) -> list[dict]:
    return _read_jsonl(_provenance_path(root), strict=strict)


def _git(repo_dir: pathlib.Path, *args: str, check=True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
        timeout=30,
    )
    if check and result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "git failed")[:500])
    return result.stdout


def _changed_files(repo_dir: pathlib.Path, commit: str) -> tuple[set[str], set[str]]:
    """Return (remove paths, add/modify paths) for an established commit."""
    _git(repo_dir, "cat-file", "-e", f"{commit}^{{commit}}")
    lines = _git(repo_dir, "diff-tree", "--root", "--no-commit-id",
                 "--name-status", "-r", "-M", commit).splitlines()
    remove, upsert = set(), set()
    for line in lines:
        parts = line.split("\t")
        status = parts[0] if parts else ""
        if status.startswith("R") and len(parts) >= 3:
            remove.add(parts[1])
            upsert.add(parts[2])
        elif status.startswith("D") and len(parts) >= 2:
            remove.add(parts[1])
        elif status[:1] in ("A", "M", "C", "T") and len(parts) >= 2:
            upsert.add(parts[-1])
    return remove | upsert, upsert


def _show(repo_dir: pathlib.Path, commit: str, repo_file: str) -> str:
    return _git(repo_dir, "show", f"{commit}:{repo_file}")


def _registered_test_repos() -> dict[str, str]:
    from registry import load_registry
    out = {}
    for repo in load_registry().get("test_repositories", []):
        out[str(repo["name"])] = str((repo.get("layout") or {}).get("specs") or "")
    return out


def _gate_rows(path: pathlib.Path) -> list[dict]:
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        parts = (line.split("\t") + ["", "", "", ""])[:4]
        repo, status, exit_code, commit = parts
        if repo and status:
            rows.append({"repo": repo, "status": status,
                         "exit_code": exit_code, "commit": commit})
    return rows


def _is_spec(path: str, specs_dir: str) -> bool:
    clean = pathlib.PurePosixPath(path.replace("\\", "/")).as_posix()
    prefix = pathlib.PurePosixPath(specs_dir.replace("\\", "/")).as_posix().strip("./")
    under = not prefix or clean == prefix or clean.startswith(prefix.rstrip("/") + "/")
    return under and clean.endswith(SPEC_SUFFIXES)


def _chunk_repo_file(chunk: dict) -> str:
    explicit = str(chunk.get("repo_file") or "").replace("\\", "/")
    if explicit:
        return explicit
    source = str(chunk.get("source_path") or "").replace("\\", "/")
    marker = f"/{chunk.get('repo', '')}/"
    return source.split(marker, 1)[1] if marker in source else source


def _parse_file(repo: str, repo_file: str, source: str) -> list[dict]:
    import testcase_parser
    parsed = testcase_parser.parse(source)
    source_path = f"workspace/tests/{repo}/{repo_file}"
    if parsed["unparsed_reason"]:
        return [knowledge_chunks._chunk(
            "spec", repo, repo_file, source_path, source,
            parse_status="unparsed", parse_reason=parsed["unparsed_reason"],
            repo_file=repo_file,
        )]
    chunks, seen = [], {}
    for case in parsed["cases"]:
        identity = (tuple(case.get("suite") or []), case.get("title") or "")
        seen[identity] = seen.get(identity, 0) + 1
        rows = knowledge_chunks._case_chunks(
            repo, repo_file, source_path, case, occurrence=seen[identity])
        for row in rows:
            row["repo_file"] = repo_file
        chunks.extend(rows)
    return chunks


def _upsert_repo(repo: str, commit: str, repo_dir: pathlib.Path,
                 specs_dir: str, chunks_path: pathlib.Path) -> dict:
    remove, upsert = _changed_files(repo_dir, commit)
    remove = {path for path in remove if _is_spec(path, specs_dir)}
    upsert = {path for path in upsert if _is_spec(path, specs_dir)}
    added = []
    for repo_file in sorted(upsert):
        added.extend(_parse_file(repo, repo_file, _show(repo_dir, commit, repo_file)))
    with fs_lock.lock(chunks_path, timeout=30):
        current = _read_jsonl(chunks_path, strict=True)
        kept = [row for row in current
                if not (row.get("repo") == repo
                        and _chunk_repo_file(row) in remove
                        and row.get("kind") in ("testcase", "spec"))]
        _write_jsonl(chunks_path, [*kept, *added], sort_rows=True)
    return {
        "repo": repo, "commit": commit, "files": sorted(upsert),
        "removed_files": sorted(remove - upsert),
        "case_ids": sorted({row.get("case_id") for row in added if row.get("case_id")}),
        "chunk_ids": sorted({row["chunk_id"] for row in added}),
        "unparsed_files": sorted({row.get("repo_file") for row in added
                                  if row.get("parse_status") == "unparsed"}),
    }


def index_commits(run_id: str, key: str, root=ROOT, *,
                  gate_results: pathlib.Path | None = None,
                  test_repos: dict[str, str] | None = None,
                  refresh_vectors=True) -> dict:
    """Index committed gate outputs before the run record is finalized."""
    root = pathlib.Path(root)
    if not knowledge_chunks.testcase_enabled():
        return {"schema_version": SCHEMA_VERSION, "artifact": "testcase-learning",
                "state": "disabled", "run_id": run_id, "key": key, "repos": []}
    gate_results = pathlib.Path(gate_results or root / "out/gate_results.tsv")
    registered = test_repos or _registered_test_repos()
    chunks_path = _chunks_path(root)
    indexed = []
    for row in _gate_rows(gate_results):
        if row["status"] != "committed" or not row["commit"]:
            continue
        repo = row["repo"]
        if repo not in registered:
            raise RuntimeError(f"gate result names unregistered test repo {repo!r}")
        repo_dir = root / "workspace/tests" / repo
        full_commit = _git(repo_dir, "rev-parse", f"{row['commit']}^{{commit}}").strip()
        detail = _upsert_repo(repo, full_commit, repo_dir,
                              registered[repo], chunks_path)
        indexed.append(detail)
        append_event({
            "event_id": _event_id("gate_commit", run_id, repo, full_commit),
            "event_type": "gate_commit", "recorded_at": time.time(),
            "run_id": run_id, "key": key, "test_repo": repo,
            "commit": full_commit, "gate_result": "committed",
            "files": detail["files"], "case_ids": detail["case_ids"],
            "chunk_ids": detail["chunk_ids"],
        }, root)

    vector = {"state": "not_run", "reason": "no committed testcase chunks"}
    if indexed and refresh_vectors and root == ROOT:
        try:
            import vector_index
            vector = {"state": "refreshed", **vector_index.refresh()}
        except Exception as exc:  # chunk indexing remains available lexically
            vector = {"state": "unavailable", "reason": str(exc)[:300]}
    return {"schema_version": SCHEMA_VERSION, "artifact": "testcase-learning",
            "state": "indexed" if indexed else "no_commits", "run_id": run_id,
            "key": key, "repos": indexed, "vector_index": vector}


def produced_for_key(key: str, root=ROOT) -> dict:
    commits = [row for row in events(root)
               if row.get("event_type") == "gate_commit" and row.get("key") == key]
    if not commits:
        return {"run_id": None, "case_ids": [], "chunk_ids": []}
    latest = max(commits, key=lambda row: float(row.get("recorded_at") or 0))["run_id"]
    selected = [row for row in commits if row.get("run_id") == latest]
    return {"run_id": latest,
            "case_ids": sorted({cid for row in selected for cid in row.get("case_ids") or []}),
            "chunk_ids": sorted({cid for row in selected for cid in row.get("chunk_ids") or []})}


def record_review(key: str, status: str, actor="", note="", ts=None, root=ROOT):
    if status not in REVIEW_OUTCOMES:
        return None
    produced = produced_for_key(key, root)
    stamp = time.time() if ts is None else float(ts)
    return append_event({
        "event_id": _event_id("review_decision", key, status, actor, stamp),
        "event_type": "review_decision", "recorded_at": stamp, "key": key,
        "status": status, "actor": str(actor)[:200], "note": str(note)[:1000],
        "produced_run": produced["run_id"], "case_ids": produced["case_ids"],
        "chunk_ids": produced["chunk_ids"],
    }, root)


def record_duplicate(key: str, kind: str, item_id: str, duplicate_case_id: str,
                     actor="", reason="", ts=None, root=ROOT):
    produced = produced_for_key(key, root)
    stamp = time.time() if ts is None else float(ts)
    return append_event({
        "event_id": _event_id("duplicate_exclusion", key, kind, item_id,
                              duplicate_case_id, actor, stamp),
        "event_type": "duplicate_exclusion", "recorded_at": stamp, "key": key,
        "kind": kind, "item_id": str(item_id)[:500],
        "duplicate_case_id": str(duplicate_case_id)[:500],
        "actor": str(actor)[:200], "reason": str(reason)[:1000],
        "produced_run": produced["run_id"], "case_ids": produced["case_ids"],
        "chunk_ids": produced["chunk_ids"],
    }, root)


def _ranking_scores(rows: list[dict]) -> dict[str, float]:
    scores, latest_reviews, duplicates = {}, {}, []
    for row in rows:
        kind = row.get("event_type")
        if kind == "review_decision" and row.get("status") in REVIEW_OUTCOMES:
            identity = (row.get("key"), row.get("produced_run"))
            prior = latest_reviews.get(identity)
            try:
                stamp = float(row.get("recorded_at") or 0)
                prior_stamp = float((prior or {}).get("recorded_at") or 0)
            except (TypeError, ValueError, OverflowError):
                stamp, prior_stamp = 0.0, 0.0
            if prior is None or stamp >= prior_stamp:
                latest_reviews[identity] = row
        elif kind == "duplicate_exclusion":
            duplicates.append(row)
    # A review history is append-only, but ranking follows the latest human
    # decision for a produced run. Repeated approvals must not manufacture
    # weight, and an approval after requested changes must be able to supersede.
    for row in latest_reviews.values():
        delta = 1.0 if row.get("status") == "approved" else -1.0
        for case_id in row.get("case_ids") or []:
            scores[case_id] = scores.get(case_id, 0.0) + delta
    for row in duplicates:
        for case_id in row.get("case_ids") or []:
            scores[case_id] = scores.get(case_id, 0.0) - 1.0
        duplicate = row.get("duplicate_case_id")
        if duplicate:
            scores[duplicate] = scores.get(duplicate, 0.0) + 0.25
    return {case_id: max(-2.0, min(2.0, score))
            for case_id, score in scores.items()}


def ranking_signal_result(root=ROOT) -> dict:
    """Outcome tie-breaker state without silent corruption fallback."""
    if not env_flag.flag("AIQE_ARTIFACT_REUSE", False):
        return {"state": "disabled", "scores": {},
                "reason": "AIQE_ARTIFACT_REUSE is disabled"}
    try:
        rows = events(root, strict=True)
    except RuntimeError as exc:
        return {"state": "unavailable", "scores": {}, "reason": str(exc)[:300]}
    return {"state": "measured", "scores": _ranking_scores(rows),
            "events_considered": len(rows)}


def ranking_signals(root=ROOT) -> dict[str, float]:
    """Bounded outcome tie-breakers. They never create or rescore a match."""
    return ranking_signal_result(root)["scores"]


def _write_result(result: dict, root=ROOT) -> pathlib.Path:
    target = pathlib.Path(root) / "out/learning-loop.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    fs_lock.write_json_atomic(target, result, sort_keys=True)
    return target


def persist_result(result: dict, root=ROOT) -> pathlib.Path | None:
    """Feature-off parity: disabled runs leave no new/stale run artifact."""
    target = pathlib.Path(root) / "out/learning-loop.json"
    if result.get("state") == "disabled":
        target.unlink(missing_ok=True)
        return None
    return _write_result(result, root)


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] != "index":
        print("usage: testcase_learning.py index <RUN_ID> <KEY>", file=sys.stderr)
        return 64
    try:
        result = index_commits(argv[2], argv[3])
    except Exception as exc:
        result = {"schema_version": SCHEMA_VERSION,
                  "artifact": "testcase-learning", "state": "unavailable",
                  "run_id": argv[2], "key": argv[3], "repos": [],
                  "reason": str(exc)[:500]}
    persist_result(result)
    print(f"testcase learning: {result['state']}"
          f" ({len(result.get('repos') or [])} repo(s))")
    return 1 if result["state"] == "unavailable" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

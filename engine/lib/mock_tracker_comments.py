#!/usr/bin/env python3
"""Deterministic, credential-free mock Tracker comment state.

The append-only JSONL store survives pipeline scratch cleanup so retry journeys
exercise the same id/update behavior as Jira without contacting a real ticket.
Bodies live only in this synthetic mock store; platform receipts remain hashes.
"""
import hashlib
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app_paths
import fs_lock

ROOT = app_paths.ROOT
STATE = pathlib.Path(os.environ.get("AIQE_MOCK_TRACKER_COMMENTS")
                     or ROOT / "out/mock-tracker-comments.jsonl")
AUTHOR = "mock-platform"


def _rows(path=STATE):
    try:
        lines = pathlib.Path(path).read_text(
            encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines[-1000:]:
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except ValueError:
            continue
    return rows


def _append(row, path=STATE):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with fs_lock.lock(path), path.open(
            "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False,
                                separators=(",", ":")) + "\n")


def post(key, body, author=AUTHOR, path=STATE):
    seed = f"{key}\0{body}\0{time.time_ns()}".encode("utf-8", errors="replace")
    comment_id = "mock-" + hashlib.sha256(seed).hexdigest()[:20]
    _append({"op": "post", "id": comment_id, "key": key,
             "author": author, "body": body, "ts": time.time()}, path)
    return comment_id


def latest(comment_id, path=STATE):
    matches = [r for r in _rows(path) if r.get("id") == comment_id]
    return matches[-1] if matches else None


def update(key, comment_id, body, expected_author, path=STATE):
    prior = latest(comment_id, path)
    if prior is None or prior.get("key") != key:
        raise LookupError("comment_missing")
    author = str(prior.get("author") or "")
    if not author:
        raise RuntimeError("authorship_unverified")
    if author != expected_author:
        raise PermissionError("author_mismatch")
    _append({"op": "update", "id": comment_id, "key": key,
             "author": author, "body": body, "ts": time.time()}, path)
    return comment_id


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 3 and args[0] == "post":
        print(f"comment_id={post(args[1], args[2])}")
    elif len(args) == 5 and args[0] == "update":
        try:
            print(f"comment_id={update(args[1], args[2], args[3], args[4])}")
        except PermissionError:
            print("author_mismatch", file=sys.stderr)
            raise SystemExit(68)
        except RuntimeError:
            print("authorship_unverified", file=sys.stderr)
            raise SystemExit(69)
        except LookupError:
            print("comment_missing", file=sys.stderr)
            raise SystemExit(70)
    else:
        raise SystemExit(
            "usage: mock_tracker_comments.py post <key> <body> | "
            "update <key> <id> <body> <expected-author>")

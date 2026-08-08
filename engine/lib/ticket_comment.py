#!/usr/bin/env python3
"""Best-effort Tracker comment delivery with durable, payload-free receipts.

The comment body crosses the Tracker port but is never copied into a receipt or
event.  A failed adapter cannot fail the caller: ``post`` always returns a
receipt whose outcome is an explicit fact.
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app_paths
import env_flag
import event_log
import fs_lock
import settings_store
import work_queue

ROOT = app_paths.ROOT
ATTEMPTS = pathlib.Path(os.environ.get("AIQE_COMMENT_ATTEMPTS")
                        or ROOT / "out/comment-attempts.jsonl")
OUTCOMES = frozenset({"posted", "updated", "skipped_unchanged", "failed"})
MAX_FAILURE_CHARS = 240
MAX_ATTEMPTS = 200
_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_TARGET_RE = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,198}[A-Za-z0-9_])?")


def receipt(kind, target, outcome, *, comment_id=None, failure_detail=None,
            run_id=None, ts=None):
    """Build the pure public result model; comment content is not an input."""
    if not isinstance(kind, str) or not _NAME_RE.fullmatch(kind):
        raise ValueError("invalid comment kind")
    if not isinstance(target, str) or not _TARGET_RE.fullmatch(target):
        raise ValueError("invalid comment target")
    if outcome not in OUTCOMES:
        raise ValueError("invalid comment outcome")
    if comment_id is not None and not isinstance(comment_id, str):
        raise ValueError("comment id must be a string")
    comment_id = (comment_id or "").strip()[:200] or None
    failure = str(failure_detail or "").strip()[:MAX_FAILURE_CHARS]
    return {
        "kind": kind,
        "target": target,
        "comment_id": comment_id,
        "outcome": outcome,
        "failure_detail": failure if outcome == "failed" else None,
        "run_id": str(run_id or "").strip()[:200] or None,
        "ts": float(time.time() if ts is None else ts),
    }


def _comment_id(output):
    output = (output or "").strip()
    try:
        parsed = json.loads(output.splitlines()[-1])
        if isinstance(parsed, dict) and parsed.get("id") is not None:
            return str(parsed["id"])
    except (IndexError, json.JSONDecodeError):
        pass
    match = re.search(r"(?:^|\s)comment_id=([^\s]+)", output)
    return match.group(1) if match else None


def _failure(result=None, exc=None):
    """Return bounded operational metadata, never raw response bodies/tokens."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return "tracker comment timed out"
    if exc is not None:
        return f"tracker comment failed ({exc.__class__.__name__})"
    raw = " ".join((getattr(result, "stdout", "") or "",
                    getattr(result, "stderr", "") or ""))
    status = re.search(r"(?:HTTP(?:/\S+)?\s*)?\b([45][0-9]{2})\b", raw)
    http = f", HTTP {status.group(1)}" if status else ""
    return f"tracker comment failed (exit {getattr(result, 'returncode', '?')}{http})"


def _append(item, path=None):
    path = pathlib.Path(path or ATTEMPTS)
    path.parent.mkdir(parents=True, exist_ok=True)
    with fs_lock.lock(path):
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(item, ensure_ascii=False,
                                    separators=(",", ":")) + "\n")


def read_attempts(path=None, run_id=None):
    """Defensively read receipts, returning (valid rows, corrupt row count)."""
    path = pathlib.Path(path or ATTEMPTS)
    rows, corrupt = [], 0
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], 0
    for line in lines[-MAX_ATTEMPTS:]:
        try:
            row = json.loads(line)
            valid = (isinstance(row, dict)
                     and isinstance(row.get("kind"), str)
                     and isinstance(row.get("target"), str)
                     and row.get("outcome") in OUTCOMES
                     and (row.get("comment_id") is None
                          or isinstance(row.get("comment_id"), str))
                     and (row.get("failure_detail") is None
                          or isinstance(row.get("failure_detail"), str)))
            if not valid:
                raise ValueError("invalid receipt")
        except (json.JSONDecodeError, ValueError):
            corrupt += 1
            continue
        if run_id is None or row.get("run_id") == run_id:
            rows.append(row)
    return rows, corrupt


def post(kind, target, body, *, plan_key=None, adapter=None, timeout=60,
         source="pipeline"):
    """Attempt one post, record it everywhere applicable, and never raise."""
    run_id = os.environ.get("RUN_ID") or None
    try:
        if not isinstance(body, str):
            raise ValueError("comment body must be text")
        # Validate identifiers before an external call.
        receipt(kind, target, "failed", run_id=run_id)
        settings_store.load_env_into()
        chosen = pathlib.Path(adapter) if adapter else ROOT / (
            "adapters/mock/tracker.sh" if env_flag.mock()
            else "adapters/tracker/jira.sh")
        cmd, env = work_queue.git_bash_command(
            chosen, "comment", target, body,
            prepend=(pathlib.Path(sys.executable).parent,))
        result = subprocess.run(
            cmd, cwd=ROOT, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
            timeout=timeout, check=False)
        if result.returncode == 0:
            item = receipt(kind, target, "posted",
                           comment_id=_comment_id(result.stdout), run_id=run_id)
        else:
            item = receipt(kind, target, "failed",
                           failure_detail=_failure(result), run_id=run_id)
    except Exception as exc:  # noqa: BLE001 - delivery must remain best-effort
        try:
            item = receipt(kind, target, "failed",
                           failure_detail=_failure(exc=exc), run_id=run_id)
        except ValueError:
            # Caller programming errors remain non-fatal and credential-free.
            item = {"kind": "invalid", "target": "invalid", "comment_id": None,
                    "outcome": "failed", "failure_detail": "invalid comment request",
                    "run_id": str(run_id or "")[:200] or None, "ts": time.time()}

    try:
        _append(item)
    except Exception as exc:  # noqa: BLE001 - observability cannot abort delivery
        print(f"[ticket-comment] receipt persistence degraded "
              f"({exc.__class__.__name__})", file=sys.stderr)
    event_log.emit(
        "ticket.comment", source=source, target=item["target"],
        run_id=item.get("run_id"),
        outcome="failed" if item["outcome"] == "failed" else "ok",
        detail={"kind": item["kind"], "comment_id": item.get("comment_id"),
                "comment_outcome": item["outcome"],
                "failure_detail": item.get("failure_detail")})
    if plan_key:
        try:
            import plan_state
            plan_state.record_comment_attempt(plan_key, item)
        except Exception as exc:  # noqa: BLE001 - never make comments fatal
            print(f"[ticket-comment] plan provenance degraded "
                  f"({exc.__class__.__name__})", file=sys.stderr)
    return item


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) >= 4 and args[0] == "post":
        plan_key = args[args.index("--plan-key") + 1] if "--plan-key" in args else None
        print(json.dumps(post(args[1], args[2], args[3], plan_key=plan_key),
                         ensure_ascii=False))
    else:
        raise SystemExit("usage: ticket_comment.py post <kind> <target> <body> "
                         "[--plan-key <state-key>]")

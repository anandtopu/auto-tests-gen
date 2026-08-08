#!/usr/bin/env python3
"""Best-effort Tracker comment delivery with durable, payload-free receipts.

The comment body crosses the Tracker port but is never copied into a receipt or
event.  A failed adapter cannot fail the caller: ``post`` always returns a
receipt whose outcome is an explicit fact.
"""
import hashlib
import json
import math
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
BODY_HASH_RE = re.compile(r"[0-9a-f]{64}")
COMMENT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")
APPEND_ONLY_KINDS = frozenset({
    "routing_clarification", "requirements", "clarification",
})
FALLBACK_REASONS = frozenset({
    "capability_unavailable", "authorship_unverified", "author_mismatch",
    "permission_denied", "comment_missing",
})
_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_TARGET_RE = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,78}[A-Za-z0-9_])?")


def receipt(kind, target, outcome, *, comment_id=None, failure_detail=None,
            run_id=None, ts=None, body_sha256=None, marker=None,
            supersedes_comment_id=None, fallback_reason=None):
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
    if comment_id is not None and not COMMENT_ID_RE.fullmatch(comment_id):
        raise ValueError("invalid comment id")
    failure = str(failure_detail or "").strip()[:MAX_FAILURE_CHARS]
    timestamp = float(time.time() if ts is None else ts)
    if not math.isfinite(timestamp):
        raise ValueError("invalid comment timestamp")
    item = {
        "kind": kind,
        "target": target,
        "comment_id": comment_id,
        "outcome": outcome,
        "failure_detail": failure if outcome == "failed" else None,
        "run_id": str(run_id or "").strip()[:200] or None,
        "ts": timestamp,
    }
    if body_sha256 is not None:
        value = str(body_sha256).strip().lower()
        if not BODY_HASH_RE.fullmatch(value):
            raise ValueError("invalid comment body hash")
        item["body_sha256"] = value
    if marker is not None:
        expected = comment_marker(kind, target)
        if marker != expected:
            raise ValueError("invalid comment marker")
        item["marker"] = marker
    if supersedes_comment_id is not None:
        value = str(supersedes_comment_id).strip()[:200]
        if not value:
            raise ValueError("invalid superseded comment id")
        item["supersedes_comment_id"] = value
    if fallback_reason is not None:
        if fallback_reason not in FALLBACK_REASONS:
            raise ValueError("invalid comment fallback reason")
        item["fallback_reason"] = fallback_reason
    return item


def comment_marker(kind, target):
    """Stable visible identity for one platform comment subject."""
    if not isinstance(kind, str) or not _NAME_RE.fullmatch(kind):
        raise ValueError("invalid comment kind")
    if not isinstance(target, str) or not _TARGET_RE.fullmatch(target):
        raise ValueError("invalid comment target")
    return f"aiqe:{kind}:{target}"


def decorate_body(kind, target, body, run_id=None, max_chars=32767):
    """Add the visible attribution footer and return its payload-free hash.

    The run id is attribution, not delivery content.  It is normalized out of
    the digest so an otherwise byte-identical retry can be skipped while the
    ticket keeps the attribution of the run that actually wrote the comment.
    """
    if not isinstance(body, str):
        raise TypeError("comment body must be text")
    marker = comment_marker(kind, target)
    clean = body.replace("\r\n", "\n").replace("\r", "\n").rstrip()
    run = re.sub(r"[\x00-\x1f\x7f]+", " ", str(run_id or "")).strip()[:200]
    run_display = run if len(run) <= 40 else run[:39] + "…"
    footer = f"⚙ {marker} · run {run_display or 'unattributed'}"
    try:
        limit = max(256, min(32767, int(max_chars)))
    except (TypeError, ValueError):
        limit = 32767
    available = limit - len(footer) - 2
    if len(clean) > available:
        notice = "... comment content truncated - full report in AI-QE Run progress."
        if available < len(notice):
            raise ValueError("comment marker leaves no room for truncation notice")
        kept = []
        for line in clean.splitlines():
            candidate = "\n".join(kept + [line, notice])
            if len(candidate) > available:
                break
            kept.append(line)
        clean = "\n".join(kept + [notice])
    canonical = clean
    if run:
        escaped = re.escape(run)
        canonical = re.sub(
            rf"(?m)^Run:\s*{escaped}\s*$", "Run: <run>", canonical)
        canonical = canonical.replace(
            f"AI-QE run {run} ", "AI-QE run <run> ")
    digest = hashlib.sha256(
        (canonical + "\n" + marker).encode("utf-8", errors="replace")
    ).hexdigest()
    return f"{clean}\n\n{footer}", marker, digest


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
    with fs_lock.lock(path), path.open(
            "a", encoding="utf-8", newline="\n") as handle:
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
                          or isinstance(row.get("failure_detail"), str))
                     and (row.get("body_sha256") is None
                          or (isinstance(row.get("body_sha256"), str)
                              and BODY_HASH_RE.fullmatch(row["body_sha256"])))
                     and (row.get("fallback_reason") is None
                          or row.get("fallback_reason") in FALLBACK_REASONS)
                     and (row.get("marker") is None
                          or (isinstance(row.get("marker"), str)
                              and row.get("marker") == comment_marker(
                                  row.get("kind"), row.get("target"))))
                     and (row.get("supersedes_comment_id") is None
                          or (isinstance(row.get("supersedes_comment_id"), str)
                              and COMMENT_ID_RE.fullmatch(
                                  row["supersedes_comment_id"])))
                     and isinstance(row.get("ts"), (int, float))
                     and math.isfinite(float(row["ts"])))
            if not valid:
                raise ValueError("invalid receipt")
        except (json.JSONDecodeError, ValueError):
            corrupt += 1
            continue
        if run_id is None or row.get("run_id") == run_id:
            rows.append(row)
    return rows, corrupt


def _prior_attempt(kind, target, plan_key=None, reports_dir=None):
    """Newest locally recorded successful comment for this stable marker.

    The platform deliberately reads only its own receipts.  It never searches
    a Jira thread for markers, which would make a spoofed marker a lookup key.
    """
    candidates, _ = read_attempts()
    if plan_key:
        try:
            import plan_state
            candidates.extend((plan_state.get(plan_key) or {}).get("comments") or [])
        except Exception:  # noqa: BLE001, S110 - optional prior provenance
            pass
    directory = pathlib.Path(reports_dir or ROOT / "reports/runs")
    try:
        paths = list(directory.glob("*.json"))
    except OSError:
        paths = []
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(record, dict):
                candidates.extend(record.get("comments") or [])
        except (OSError, ValueError):
            continue
    eligible = [
        row for row in candidates
        if isinstance(row, dict)
        and row.get("kind") == kind and row.get("target") == target
        and row.get("outcome") in {"posted", "updated", "skipped_unchanged"}
        and (row.get("comment_id") or row.get("body_sha256"))
    ]
    def safe_ts(row):
        try:
            value = float(row.get("ts") or 0)
            return value if math.isfinite(value) else 0
        except (TypeError, ValueError):
            return 0

    return max(eligible, key=safe_ts, default=None)


def _prior_id(prior):
    value = str((prior or {}).get("comment_id") or "").strip()
    return value if COMMENT_ID_RE.fullmatch(value) else None


def _adapter_run(chosen, verb, *args, timeout=60):
    cmd, env = work_queue.git_bash_command(
        chosen, verb, *args, prepend=(pathlib.Path(sys.executable).parent,))
    return subprocess.run(
        cmd, cwd=ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
        timeout=timeout, check=False)


def _update_capability(result):
    if result.returncode != 0:
        return False
    text = (result.stdout or "").strip()
    if "update_comment=available" in text:
        return True
    try:
        value = json.loads(text.splitlines()[-1])
        return isinstance(value, dict) and value.get("update_comment") == "available"
    except (IndexError, json.JSONDecodeError):
        return False


def _update_failure_reason(result):
    """Map adapter signals onto closed, credential-free fallback reasons."""
    text = " ".join((result.stdout or "", result.stderr or "")).lower()
    if result.returncode == 68 or "author_mismatch" in text:
        return "author_mismatch"
    if result.returncode == 69 or "authorship_unverified" in text:
        return "authorship_unverified"
    if result.returncode == 70 or "comment_missing" in text:
        return "comment_missing"
    if result.returncode == 77 or "permission_denied" in text or "http 403" in text:
        return "permission_denied"
    if result.returncode == 78 or "capability_unavailable" in text:
        return "capability_unavailable"
    return None


def _superseding_body(body, prior_id, reason):
    labels = {
        "capability_unavailable": "comment updates are unavailable",
        "authorship_unverified": "the prior comment's authorship could not be verified",
        "author_mismatch": "the prior comment was not authored by this platform account",
        "permission_denied": "the platform account cannot update the prior comment",
        "comment_missing": "the prior recorded comment no longer exists",
    }
    note = f"Supersedes prior AI-QE comment {prior_id}; {labels[reason]}."
    if "\n\n⚙ aiqe:" in body:
        content, footer = body.rsplit("\n\n", 1)
        return f"{content}\n\n{note}\n\n{footer}"
    return f"{body}\n\n{note}"


def post(kind, target, body, *, plan_key=None, adapter=None, timeout=60,
         source="pipeline"):
    """Deliver one idempotent platform comment and never raise.

    Questions/progress remain append-only.  Other kinds use only locally
    persisted ids, skip unchanged content, and ask the adapter to verify the
    configured platform author before an update.  An ambiguous update failure
    is recorded as failed rather than appending a possible duplicate.
    """
    run_id = os.environ.get("RUN_ID") or None
    try:
        try:
            import ticket_comment_render
            limit = ticket_comment_render.max_chars()
        except Exception:  # noqa: BLE001 - hard ceiling remains the fallback
            limit = 32767
        decorated, marker, body_hash = decorate_body(
            kind, target, body, run_id=run_id, max_chars=limit)
        prior = None if kind in APPEND_ONLY_KINDS else _prior_attempt(
            kind, target, plan_key=plan_key)
        prior_id = _prior_id(prior)
        if prior and prior.get("body_sha256") == body_hash:
            item = receipt(
                kind, target, "skipped_unchanged",
                comment_id=prior_id, run_id=run_id,
                body_sha256=body_hash, marker=marker)
        else:
            settings_store.load_env_into()
            chosen = pathlib.Path(adapter) if adapter else ROOT / (
                "adapters/mock/tracker.sh" if env_flag.mock()
                else "adapters/tracker/jira.sh")
            if prior_id:
                capability = _adapter_run(
                    chosen, "comment_capabilities", timeout=timeout)
                fallback_reason = None
                if not _update_capability(capability):
                    fallback_reason = "capability_unavailable"
                else:
                    expected_author = os.environ.get("AIQE_JIRA_PLATFORM_ACCOUNT", "").strip()
                    if env_flag.mock() and not expected_author:
                        expected_author = "mock-platform"
                    if not expected_author:
                        fallback_reason = "authorship_unverified"
                    else:
                        result = _adapter_run(
                            chosen, "update_comment", target,
                            prior_id, decorated, expected_author,
                            timeout=timeout)
                        if result.returncode == 0:
                            item = receipt(
                                kind, target, "updated",
                                comment_id=_comment_id(result.stdout)
                                or prior_id, run_id=run_id,
                                body_sha256=body_hash, marker=marker)
                        else:
                            fallback_reason = _update_failure_reason(result)
                            if fallback_reason is None:
                                item = receipt(
                                    kind, target, "failed",
                                    comment_id=prior_id,
                                    failure_detail=_failure(result), run_id=run_id,
                                    body_sha256=body_hash, marker=marker)
                if fallback_reason:
                    superseding = _superseding_body(
                        decorated, prior_id, fallback_reason)
                    result = _adapter_run(
                        chosen, "comment", target, superseding, timeout=timeout)
                    item = receipt(
                        kind, target,
                        "posted" if result.returncode == 0 else "failed",
                        comment_id=(_comment_id(result.stdout)
                                    if result.returncode == 0 else None),
                        failure_detail=(_failure(result)
                                        if result.returncode != 0 else None),
                        run_id=run_id, body_sha256=body_hash, marker=marker,
                        supersedes_comment_id=prior_id,
                        fallback_reason=fallback_reason)
            else:
                result = _adapter_run(
                    chosen, "comment", target, decorated, timeout=timeout)
                item = receipt(
                    kind, target,
                    "posted" if result.returncode == 0 else "failed",
                    comment_id=(_comment_id(result.stdout)
                                if result.returncode == 0 else None),
                    failure_detail=(_failure(result)
                                    if result.returncode != 0 else None),
                    run_id=run_id, body_sha256=body_hash, marker=marker)
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
                "failure_detail": item.get("failure_detail"),
                "fallback_reason": item.get("fallback_reason"),
                "supersedes_comment_id": item.get("supersedes_comment_id")})
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

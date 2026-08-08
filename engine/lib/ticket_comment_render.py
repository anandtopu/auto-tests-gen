#!/usr/bin/env python3
"""Flagged rich ticket-comment rendering for JCTS-S4.

Rendering is pure and network-free.  Delivery still crosses only the Tracker
port through :mod:`ticket_comment`; this module decides which body that port
receives and always returns the caller's legacy summary on failure or flag-off.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import env_flag
import pr_comment
import spec_store

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_MAX_CHARS = 8000
HARD_MAX_CHARS = 32767


def enabled():
    return env_flag.flag("AIQE_TICKET_COMMENTS_RICH", False)


def max_chars(config_path=None):
    """Read the org bound defensively; Jira's hard ceiling is never exceeded."""
    path = pathlib.Path(config_path or ROOT / "registry/org-config.yaml")
    try:
        import yaml
        raw = (yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        value = (raw.get("comments") or {}).get("max_chars", DEFAULT_MAX_CHARS)
        if isinstance(value, bool):
            raise TypeError("boolean is not a character bound")
        value = int(value)
        return min(HARD_MAX_CHARS, value) if value >= 256 else DEFAULT_MAX_CHARS
    except (OSError, TypeError, ValueError):
        return DEFAULT_MAX_CHARS


def plan_body(key, target, fallback, *, limit=None):
    if not enabled():
        return fallback
    try:
        body = spec_store.render_comment(
            key, max_chars=limit or max_chars(), target=target)
        return body or fallback
    except Exception as exc:  # noqa: BLE001 - rendering cannot fail pipeline
        print(f"[ticket-comment] rich plan rendering degraded "
              f"({exc.__class__.__name__})", file=sys.stderr)
        return fallback


def delivery_body(run_id, key, target, fallback, *, pr_ref="", limit=None):
    if not enabled():
        return fallback
    try:
        body = pr_comment.build_ticket(
            ".", run_id, key, target=target, pr_ref=pr_ref,
            max_chars=limit or max_chars())
        return body or fallback
    except Exception as exc:  # noqa: BLE001 - best-effort posting invariant
        print(f"[ticket-comment] rich delivery rendering degraded "
              f"({exc.__class__.__name__})", file=sys.stderr)
        return fallback


def refusal_body(run_id, key, target, fallback, reason, fix, *, pr_ref="",
                 limit=None):
    if not enabled():
        return fallback
    try:
        import budget
        cost_rows = budget.read_ledger(ROOT / "out/cost.tsv")
        projection = pr_comment.refusal_projection(
            run_id, key, reason, fix, target=target, pr_ref=pr_ref,
            cost_rows=cost_rows)
        return pr_comment.render_ticket(projection, max_chars=limit or max_chars())
    except Exception as exc:  # noqa: BLE001 - refusal must remain deliverable
        print(f"[ticket-comment] rich refusal rendering degraded "
              f"({exc.__class__.__name__})", file=sys.stderr)
        return fallback


def main(argv):
    if argv == ["enabled"]:
        return 0 if enabled() else 1
    if len(argv) >= 4 and argv[0] == "plan":
        print(plan_body(argv[1], argv[2], argv[3]))
        return 0
    if len(argv) >= 5 and argv[0] == "delivery":
        print(delivery_body(argv[1], argv[2], argv[3], argv[4],
                            pr_ref=argv[5] if len(argv) > 5 else ""))
        return 0
    if len(argv) >= 7 and argv[0] == "refusal":
        print(refusal_body(argv[1], argv[2], argv[3], argv[4], argv[5], argv[6],
                           pr_ref=argv[7] if len(argv) > 7 else ""))
        return 0
    print("usage: ticket_comment_render.py enabled | "
          "plan <key> <target> <fallback> | "
          "delivery <run> <key> <target> <fallback> [pr-ref] | "
          "refusal <run> <key> <target> <fallback> <reason> <fix> [pr-ref]",
          file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))

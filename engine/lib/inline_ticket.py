#!/usr/bin/env python3
"""Build a synthesized ticket from pasted JIRA context ("user will pass JIRA
context as text input") so Workflow B can run without an existing ticket.

The first non-empty line becomes the summary; lines that look like acceptance
criteria (AC-1:, "- AC ...", "* AC ...") are collected; everything is kept in the
description. A "Comments:" section (a line reading Comments/Comments:) splits the
paste — each non-empty line after it becomes one comment, because pasted JIRA
context usually includes the comment thread and comments carry the clarifications
the description lacks. Routing comes from --components/--labels/--repos exactly like a real
ticket. The pipeline consumes the file via AIQE_INLINE_FILE instead of the
Tracker port's get_item.
"""
import json, pathlib, re, time, uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
DIR = ROOT / "reports/inline"          # reports/*.json is gitignored -> transient


KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def build(text, key=None, components=(), labels=(), repos=(), issue_type="Story"):
    text = (text or "").strip()
    if not text:
        raise ValueError("inline ticket text is empty")
    # The key becomes a filename and a pipeline arg — reject anything outside a
    # safe charset (a bare key like "PROJ-1" is fine; "<img …>" is not).
    if key and not KEY_RE.fullmatch(key):
        raise ValueError("key must be alphanumeric with . _ - (max 64 chars)")
    # Split off a pasted comment thread: everything after a "Comments:" line.
    body_text, comments = text, []
    m = re.search(r"^\s*comments?\s*:?\s*$", text, re.I | re.M)
    if m:
        body_text = text[:m.start()].rstrip()
        comments = [{"author": "pasted", "created": "", "body": l.strip("-* 	")}
                    for l in text[m.end():].splitlines() if l.strip()]
    lines = [l.strip() for l in body_text.splitlines()]
    summary = next((l for l in lines if l), "")[:200]
    acs = [re.sub(r"^[-*]\s*", "", l) for l in lines
           if re.match(r"^([-*]\s*)?AC[-\s\d:.]", l, re.I)]
    return {
        "key": key or f"ADHOC-{int(time.time())}-{uuid.uuid4().hex[:4]}",
        "summary": summary,
        "description": body_text,
        "comments": comments,
        "components": [c for c in components if c],
        "labels": [l for l in labels if l],
        "linked_repos": [r for r in repos if r],
        "acceptance_criteria": acs,
        "issue_type": issue_type or "Story",
        "fix_versions": [],
        "inline": True,
    }


def write(ticket, path=None):
    p = pathlib.Path(path) if path else DIR / f"{ticket['key']}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ticket, indent=2), encoding="utf-8", newline="\n")
    return p

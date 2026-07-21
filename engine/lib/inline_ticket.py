#!/usr/bin/env python3
"""Build a synthesized ticket from pasted JIRA context ("user will pass JIRA
context as text input") so Workflow B can run without an existing ticket.

The first non-empty line becomes the summary; lines that look like acceptance
criteria (AC-1:, "- AC ...", "* AC ...") are collected; everything is kept in the
description. Routing comes from --components/--labels/--repos exactly like a real
ticket. The pipeline consumes the file via AIQE_INLINE_FILE instead of the
Tracker port's get_item.
"""
import json, pathlib, re, time

ROOT = pathlib.Path(__file__).resolve().parents[2]
DIR = ROOT / "reports/inline"          # reports/*.json is gitignored -> transient


def build(text, key=None, components=(), labels=(), repos=(), issue_type="Story"):
    text = (text or "").strip()
    if not text:
        raise ValueError("inline ticket text is empty")
    lines = [l.strip() for l in text.splitlines()]
    summary = next((l for l in lines if l), "")[:200]
    acs = [re.sub(r"^[-*]\s*", "", l) for l in lines
           if re.match(r"^([-*]\s*)?AC[-\s\d:.]", l, re.I)]
    return {
        "key": key or f"ADHOC-{int(time.time()) % 100000}",
        "summary": summary,
        "description": text,
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

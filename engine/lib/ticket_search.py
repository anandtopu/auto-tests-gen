#!/usr/bin/env python3
"""Closed, structured ticket-search contract shared by Tracker adapters.

The public input is a JSON object whose keys come from SEARCH_FIELD_VOCABULARY.
There is deliberately no raw-JQL escape hatch.  Jira uses ``build_jql`` while
the mock adapter uses ``search_fixture_dir`` over the same normalized filters.
"""
import glob
import json
import pathlib
import sys

import ticket_fields

SEARCH_FIELD_VOCABULARY = {
    **ticket_fields.PROCESSED_FIELD_VOCABULARY,
    "status": "status",
    "text": "text",
}

JQL_FIELDS = {
    "fixversion": "fixVersion",
    "issue_type": "issuetype",
    "component": "component",
    "label": "labels",
    "status": "status",
    "text": "text",
}

RESULT_FIELDS = "summary,issuetype,components,labels,fixVersions,status"
DEFAULT_PAGE_SIZE = 50


class SearchInputError(ValueError):
    """The caller supplied something outside the structured search contract."""


def normalize_filters(raw):
    """Return a closed, string-only filter dict with empty values omitted."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise SearchInputError(f"filters must be a JSON object: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise SearchInputError("filters must be a JSON object")
    unknown = sorted(set(raw) - set(SEARCH_FIELD_VOCABULARY))
    if unknown:
        raise SearchInputError("unsupported search filter(s): " + ", ".join(unknown))
    normalized = {}
    for name, value in raw.items():
        if not isinstance(value, str):
            raise SearchInputError(f"search filter {name!r} must be a string")
        if value:
            normalized[name] = value
    return normalized


def jql_string(value):
    """Quote one JQL string literal; input can never influence field/operator."""
    escaped = []
    for char in value:
        escaped.append({
            "\\": "\\\\",
            '"': '\\"',
            "\n": "\\n",
            "\r": "\\r",
            "\t": "\\t",
            "\b": "\\b",
            "\f": "\\f",
        }.get(char, char))
    return '"' + "".join(escaped) + '"'


def build_jql(raw):
    """Compose JQL only from the closed vocabulary and escaped literals."""
    filters = normalize_filters(raw)
    clauses = []
    for name in SEARCH_FIELD_VOCABULARY:
        if name not in filters:
            continue
        operator = "~" if name == "text" else "="
        clauses.append(f"{JQL_FIELDS[name]} {operator} {jql_string(filters[name])}")
    return " AND ".join(clauses) if clauses else "key is not EMPTY"


def release_filters(value):
    """Backward-compatible search_release input expressed through search."""
    return {"fixversion": value} if value else {}


def _names(values):
    return [str((value or {}).get("name", "")) for value in (values or [])
            if isinstance(value, dict)]


def project_jira_response(response):
    """Normalize Jira's first page and retain its truthful population count."""
    if not isinstance(response, dict):
        raise SearchInputError("Jira search response must be an object")
    issues = response.get("issues") or []
    if not isinstance(issues, list):
        raise SearchInputError("Jira search response issues must be a list")
    items = []
    for issue in issues:
        fields = issue.get("fields") or {}
        items.append({
            "key": issue.get("key", ""),
            "summary": fields.get("summary") or "",
            "issue_type": (fields.get("issuetype") or {}).get("name", ""),
            "components": _names(fields.get("components")),
            "labels": list(fields.get("labels") or []),
            "fix_versions": _names(fields.get("fixVersions")),
            "status": (fields.get("status") or {}).get("name", ""),
        })
    total = response.get("total", len(items))
    if not isinstance(total, int) or total < len(items):
        total = len(items)
    return {"items": items, "returned": len(items), "total": total}


def _fold(value):
    return str(value or "").casefold()


def _contains(values, wanted):
    return any(_fold(value) == _fold(wanted) for value in (values or []))


def matches(ticket, raw):
    filters = normalize_filters(raw)
    for name, wanted in filters.items():
        attr = SEARCH_FIELD_VOCABULARY[name]
        if name in ("component", "label", "fixversion"):
            if not _contains(ticket.get(attr), wanted):
                return False
        elif name == "text":
            comments = " ".join(str(c.get("body", "")) for c in
                                (ticket.get("comments") or []) if isinstance(c, dict))
            haystack = " ".join((str(ticket.get("summary") or ""),
                                 str(ticket.get("description") or ""), comments))
            if _fold(wanted) not in _fold(haystack):
                return False
        elif _fold(ticket.get(attr)) != _fold(wanted):
            return False
    return True


def project_fixture(ticket):
    return {
        "key": ticket.get("key", ""),
        "summary": ticket.get("summary", ""),
        "issue_type": ticket.get("issue_type", ""),
        "components": list(ticket.get("components") or []),
        "labels": list(ticket.get("labels") or []),
        "fix_versions": list(ticket.get("fix_versions") or []),
        "status": ticket.get("status", ""),
    }


def search_fixture_dir(raw, pattern="eval/benchmark/tickets/.item-*.json",
                       page_size=DEFAULT_PAGE_SIZE):
    filters = normalize_filters(raw)
    tickets = []
    for filename in glob.glob(pattern):
        try:
            ticket = json.loads(pathlib.Path(filename).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if matches(ticket, filters):
            tickets.append(project_fixture(ticket))
    tickets.sort(key=lambda item: item["key"])
    total = len(tickets)
    items = tickets[:page_size]
    return {"items": items, "returned": len(items), "total": total}


def _main(argv):
    if len(argv) < 2:
        raise SystemExit("usage: ticket_search.py jql|release|project|mock [filters]")
    command = argv[1]
    try:
        if command == "jql":
            print(build_jql(argv[2] if len(argv) > 2 else "{}"))
        elif command == "release":
            print(json.dumps(release_filters(argv[2] if len(argv) > 2 else "")))
        elif command == "project":
            print(json.dumps(project_jira_response(json.load(sys.stdin))))
        elif command == "mock":
            print(json.dumps(search_fixture_dir(argv[2] if len(argv) > 2 else "{}")))
        else:
            raise SearchInputError(f"unknown command: {command}")
    except (SearchInputError, json.JSONDecodeError) as exc:
        print(f"ticket search: {exc}", file=sys.stderr)
        raise SystemExit(64) from exc


if __name__ == "__main__":
    _main(sys.argv)

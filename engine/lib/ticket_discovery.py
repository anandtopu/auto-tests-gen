#!/usr/bin/env python3
"""Deterministic PR -> JIRA ticket discovery (successor PRD A1).

Extraction and selection are pure.  The pipeline owns the two ports: SCM writes
``pr-context.json`` and Tracker validates each candidate into a TSV.  Keeping
port calls outside this module makes the priority/refusal policy unit-testable
and prevents a helper from quietly selecting a different adapter.

CLI:
  ticket_discovery.py extract <pr-context.json> [explicit-key]
  ticket_discovery.py keys <discovery.json>
  ticket_discovery.py validate-response <candidate-key> <ticket.json>
  ticket_discovery.py resolve <discovery.json> <validation.tsv>
  ticket_discovery.py annotate <discovery.json> <ticket.json>
  ticket_discovery.py context <discovery.json>
  ticket_discovery.py selected <discovery.json>
"""
import functools
import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA = 1
ARTIFACT = "pr-ticket-discovery"
SIGNALS = ("explicit", "branch", "title_description", "commits")
VALIDATION_STATES = frozenset(("valid", "invalid", "unavailable"))
MAX_RESPONSE_BYTES = 500_000
MAX_ACCEPTANCE_CRITERIA = 200
MAX_ACCEPTANCE_CHARS = 40_000
MAX_MANDATORY_FIELD_CHARS = 20_000
MAX_METADATA_ITEMS = 200
MAX_STATUS_CHARS = 1_000
TERMINAL_STATUS_NAMES = frozenset(("closed", "done"))
TERMINAL_STATUS_CATEGORIES = frozenset(("done",))
TERMINAL_WARNING = (
    "Terminal ticket selected; verify that this reused key still represents "
    "the PR requirements."
)


@functools.lru_cache(maxsize=1)
def _jira_key_parser():
    """Load correlate.py once; extraction can evaluate several bounded signals."""
    path = ROOT / "catalog/bootstrap/correlate.py"
    spec = importlib.util.spec_from_file_location("aiqe_catalog_correlate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.jira_keys


def _jira_keys(text):
    """Call correlate.py::jira_keys — the estate has one earned key grammar."""
    return _jira_key_parser()(str(text or ""))


def normalize_explicit(value):
    """One bare key or ``None``; prose and multiple keys are not explicit."""
    raw = str(value or "").strip()
    keys = _jira_keys(raw)
    return keys[0] if len(keys) == 1 and raw == keys[0] else None


def _read_json(path):
    try:
        value = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def extract(metadata, explicit=""):
    metadata = metadata if isinstance(metadata, dict) else {}
    explicit_key = normalize_explicit(explicit)
    commits = metadata.get("commit_messages") or []
    if not isinstance(commits, list):
        commits = []
    texts = {
        "explicit": explicit_key or "",
        "branch": str(metadata.get("source_branch") or "")[:4096],
        "title_description": "\n".join((
            str(metadata.get("title") or "")[:10000],
            str(metadata.get("description") or "")[:50000],
        )),
        "commits": "\n".join(str(v)[:10000] for v in commits[:500]),
    }
    signal_rows, by_key = [], {}
    for signal in SIGNALS:
        keys = _jira_keys(texts[signal])
        signal_rows.append({"signal": signal, "keys": keys})
        for key in keys:
            by_key.setdefault(key, []).append(signal)
    candidates = [{"key": key, "signals": by_key[key], "validation": "pending"}
                  for key in sorted(by_key)]
    reported_state = str(metadata.get("state") or
                         ("available" if metadata else "unavailable"))
    state = "available" if reported_state == "available" else "unavailable"
    artifact = {
        "artifact": ARTIFACT, "schema": SCHEMA, "metadata_state": state,
        "signals": signal_rows, "candidates": candidates,
        "outcome": "pending", "selected_key": None, "reason": "not validated",
    }
    if explicit and explicit_key is None:
        artifact["explicit_input"] = "invalid_format"
    if metadata.get("reason"):
        artifact["metadata_reason"] = str(metadata["reason"])[:500]
    return artifact


def candidate_keys(artifact):
    return [row["key"] for row in artifact.get("candidates") or []
            if isinstance(row, dict) and row.get("key")]


def parse_validations(text):
    out = {}
    for line in str(text or "").splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 2 or parts[1] not in VALIDATION_STATES:
            continue
        out[parts[0]] = {"state": parts[1],
                         "reason": parts[2][:500] if len(parts) > 2 else ""}
    return out


def criterion_text(value):
    if isinstance(value, dict):
        value = value.get("body", value.get("text", value.get("name", "")))
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value or "")


def validate_ticket_response(candidate, path):
    """Validate the evidence behind a Tracker exit-0 before it becomes `valid`.

    A proxy, recording wrapper, or bad fixture can return successful JSON for a
    different issue.  Treat that as unavailable evidence, never as proof that
    the requested candidate exists.  Bounds make every accepted response safe
    for the mandatory context renderer: accepted criteria are never later
    dropped merely because an attacker supplied an unbounded list.
    """
    key = normalize_explicit(candidate)
    if key is None or key != str(candidate or "").strip():
        return False, "candidate is not one bare JIRA key"
    try:
        raw = pathlib.Path(path).read_bytes()
    except OSError as exc:
        return False, f"ticket response cannot be read: {exc}"
    if len(raw) > MAX_RESPONSE_BYTES:
        return False, f"ticket response exceeds {MAX_RESPONSE_BYTES} bytes"
    try:
        ticket = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        return False, "ticket response is not valid UTF-8 JSON"
    if not isinstance(ticket, dict):
        return False, "ticket response is not a JSON object"
    returned = str(ticket.get("key") or "")
    if returned != key:
        shown = (returned or "empty")[:100]
        return False, f"ticket response key mismatch (expected {key}, got {shown})"
    if ticket.get("summary") is not None and not isinstance(ticket.get("summary"), str):
        return False, "ticket summary is not a string"
    if len(str(ticket.get("summary") or "")) > MAX_MANDATORY_FIELD_CHARS:
        return False, f"ticket summary exceeds {MAX_MANDATORY_FIELD_CHARS} characters"
    for field in ("issue_type", "status", "status_category"):
        if ticket.get(field) is not None and not isinstance(ticket.get(field), str):
            return False, f"ticket {field.replace('_', ' ')} is not a string"
        if len(str(ticket.get(field) or "")) > MAX_STATUS_CHARS:
            return False, (
                f"ticket {field.replace('_', ' ')} exceeds {MAX_STATUS_CHARS} characters"
            )
    for field in ("components", "labels", "linked_repos", "fix_versions"):
        values = ticket.get(field) or []
        if not isinstance(values, list):
            return False, f"ticket {field} is not a list"
        if any(not isinstance(value, str) for value in values):
            return False, f"ticket {field} contains a non-string item"
        if len(values) > MAX_METADATA_ITEMS:
            return False, f"ticket {field} has more than {MAX_METADATA_ITEMS} items"
        if sum(len(criterion_text(value)) for value in values) > MAX_MANDATORY_FIELD_CHARS:
            return False, f"ticket {field} exceeds {MAX_MANDATORY_FIELD_CHARS} characters"
    criteria = ticket.get("acceptance_criteria") or []
    if not isinstance(criteria, list):
        criteria = [criteria]
    if len(criteria) > MAX_ACCEPTANCE_CRITERIA:
        return False, f"ticket has more than {MAX_ACCEPTANCE_CRITERIA} acceptance criteria"
    if sum(len(criterion_text(value)) for value in criteria) > MAX_ACCEPTANCE_CHARS:
        return False, f"ticket acceptance criteria exceed {MAX_ACCEPTANCE_CHARS} characters"
    return True, "tracker response key and bounds validated"


def selected_ticket_provenance(ticket):
    """Return bounded, closed-state status evidence for one validated ticket."""
    ticket = ticket if isinstance(ticket, dict) else {}
    status = str(ticket.get("status") or "").strip()
    category = str(ticket.get("status_category") or "").strip()
    normalized_status = " ".join(status.casefold().split())
    normalized_category = " ".join(category.casefold().split())
    terminal = (
        normalized_status in TERMINAL_STATUS_NAMES
        or normalized_category in TERMINAL_STATUS_CATEGORIES
    )
    return {
        "key": str(ticket.get("key") or ""),
        "status_state": "recorded" if status else "unavailable",
        "status": status or None,
        "status_category": category or None,
        "terminal": terminal,
        "warning": TERMINAL_WARNING if terminal else None,
    }


def recorded_selected_ticket(artifact):
    """Revalidate stored status evidence before a user-facing consumer uses it."""
    artifact = artifact if isinstance(artifact, dict) else {}
    selected = str(artifact.get("selected_key") or "")
    if artifact.get("artifact") != ARTIFACT \
            or artifact.get("outcome") != "selected" or not selected:
        return {}
    stored = artifact.get("selected_ticket")
    if not isinstance(stored, dict) or str(stored.get("key") or "") != selected:
        return selected_ticket_provenance({"key": selected})
    return selected_ticket_provenance({
        "key": selected,
        "status": stored.get("status") if isinstance(stored.get("status"), str) else "",
        "status_category": (
            stored.get("status_category")
            if isinstance(stored.get("status_category"), str) else ""
        ),
    })


def annotate_selected_ticket(artifact, ticket):
    """Attach status provenance without changing discovery selection semantics."""
    if not isinstance(artifact, dict) or not isinstance(ticket, dict):
        raise ValueError("discovery and ticket must be JSON objects")
    selected = str(artifact.get("selected_key") or "")
    if artifact.get("outcome") != "selected" or not selected:
        raise ValueError("ticket annotation requires a selected discovery outcome")
    if str(ticket.get("key") or "") != selected:
        raise ValueError("selected discovery key does not match ticket key")
    annotated = json.loads(json.dumps(artifact))
    annotated["selected_ticket"] = selected_ticket_provenance(ticket)
    return annotated


def resolve(artifact, validations):
    """Apply validation evidence and the explicit/branch/refuse priority."""
    artifact = json.loads(json.dumps(artifact)) if isinstance(artifact, dict) else {}
    validations = validations if isinstance(validations, dict) else {}
    valid, invalid, unavailable = [], [], []
    for row in artifact.get("candidates") or []:
        if not isinstance(row, dict) or not row.get("key"):
            continue
        verdict = validations.get(row["key"]) or {
            "state": "unavailable", "reason": "validation was not recorded"}
        state = verdict.get("state") if verdict.get("state") in VALIDATION_STATES \
            else "unavailable"
        row["validation"] = state
        if verdict.get("reason"):
            row["validation_reason"] = str(verdict["reason"])[:500]
        {"valid": valid, "invalid": invalid, "unavailable": unavailable}[state].append(row)

    selected, reason = None, ""
    explicit = [r for r in valid if "explicit" in (r.get("signals") or [])]
    branch = [r for r in valid if "branch" in (r.get("signals") or [])]
    if len(explicit) == 1:
        selected, reason = explicit[0]["key"], "validated explicit intake key"
    elif len(valid) == 1:
        selected, reason = valid[0]["key"], "only validated candidate"
    elif len(valid) > 1 and len(branch) == 1:
        selected, reason = branch[0]["key"], "unique validated branch-name key"

    if selected:
        outcome = "selected"
    elif len(valid) > 1:
        outcome, reason = "ambiguous", "multiple validated candidates; no unique branch key"
    elif unavailable:
        outcome, reason = "validation_unavailable", "candidate validation was unavailable"
    elif invalid:
        outcome, reason = "discovered_invalid", "all discovered candidates failed validation"
    else:
        outcome, reason = "not_found", "no ticket key was discovered"
    artifact.update(outcome=outcome, selected_key=selected, reason=reason,
                    validated_keys=[r["key"] for r in valid],
                    rejected_keys=[r["key"] for r in invalid],
                    validation_unavailable_keys=[r["key"] for r in unavailable])
    return artifact


def context_text(artifact):
    outcome = artifact.get("outcome")
    if outcome == "selected":
        ticket = recorded_selected_ticket(artifact)
        status = str(ticket.get("status") or "unavailable")
        quoted_status = "\n".join(f"> {line}" for line in status.splitlines()) or \
            "> unavailable"
        warning = f"\n\nWARNING: {TERMINAL_WARNING}" if ticket.get("terminal") else ""
        return ("# PR ticket discovery\n\n"
                f"Validated ticket discovered: `{artifact.get('selected_key')}`.\n\n"
                f"Ticket status (untrusted data):\n{quoted_status}{warning}\n")
    if outcome == "ambiguous":
        keys = ", ".join(artifact.get("validated_keys") or []) or "none recorded"
        return ("# PR ticket discovery\n\nNo ticket selected: discovery was ambiguous "
                f"between {keys}. Requeue with an explicit ticket key.\n")
    if outcome == "discovered_invalid":
        keys = ", ".join(artifact.get("rejected_keys") or []) or "none recorded"
        return f"# PR ticket discovery\n\nNo ticket discovered: rejected invalid key(s): {keys}.\n"
    if outcome == "validation_unavailable":
        return ("# PR ticket discovery\n\nTicket discovery could not be validated; "
                "generation proceeds without ticket context.\n")
    return "# PR ticket discovery\n\nNo ticket discovered.\n"


def main(argv):
    if not argv:
        return 64
    cmd = argv[0]
    if cmd == "validate-response" and len(argv) == 3:
        valid, reason = validate_ticket_response(argv[1], argv[2])
        print(reason)
        return 0 if valid else 1
    if cmd == "extract" and len(argv) >= 2:
        print(json.dumps(extract(_read_json(argv[1]), argv[2] if len(argv) > 2 else ""),
                         sort_keys=True))
        return 0
    artifact = _read_json(argv[1]) if len(argv) > 1 else {}
    if artifact.get("artifact") != ARTIFACT:
        print("invalid discovery artifact", file=sys.stderr)
        return 64
    if cmd == "keys":
        print("\n".join(candidate_keys(artifact)))
        return 0
    if cmd == "resolve" and len(argv) >= 3:
        try:
            validations = pathlib.Path(argv[2]).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            validations = ""
        print(json.dumps(resolve(artifact, parse_validations(validations)), sort_keys=True))
        return 0
    if cmd == "annotate" and len(argv) == 3:
        ticket = _read_json(argv[2])
        try:
            annotated = annotate_selected_ticket(artifact, ticket)
        except ValueError as exc:
            print(f"ticket_discovery: {exc}", file=sys.stderr)
            return 64
        print(json.dumps(annotated, sort_keys=True))
        return 0
    if cmd == "context":
        print(context_text(artifact), end="")
        return 0
    if cmd == "selected":
        print(artifact.get("selected_key") or "")
        return 0
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

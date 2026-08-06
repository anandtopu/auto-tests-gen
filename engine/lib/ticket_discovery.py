#!/usr/bin/env python3
"""Deterministic PR -> JIRA ticket discovery (successor PRD A1).

Extraction and selection are pure.  The pipeline owns the two ports: SCM writes
``pr-context.json`` and Tracker validates each candidate into a TSV.  Keeping
port calls outside this module makes the priority/refusal policy unit-testable
and prevents a helper from quietly selecting a different adapter.

CLI:
  ticket_discovery.py extract <pr-context.json> [explicit-key]
  ticket_discovery.py keys <discovery.json>
  ticket_discovery.py resolve <discovery.json> <validation.tsv>
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
        return ("# PR ticket discovery\n\n"
                f"Validated ticket discovered: `{artifact.get('selected_key')}`.\n")
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
    if cmd == "context":
        print(context_text(artifact), end="")
        return 0
    if cmd == "selected":
        print(artifact.get("selected_key") or "")
        return 0
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

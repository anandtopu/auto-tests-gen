#!/usr/bin/env python3
"""Test-plan lifecycle — the human approval gate between planning and generation.

Workflow B can stop after the test plan is authored so a human can review, edit and
approve it BEFORE any test code is generated:

    draft ──(review)──> in_review ──(approve)──> approved ──> generate tests
      ^                                │
      └────── changes_requested <──────┘
      └────── (editing an approved plan resets it to draft — the approved
              artifact changed, so the approval no longer applies)

State lives in reports/plans/state.json (committable team state, like the review
board) — deliberately NOT under reports/runs/, so the `reports/runs/*.json` run-record
globs are unaffected. All mutations are fs_lock-guarded.

CLI: plan_state.py get <KEY> | set <KEY> <status> [--by X] [--note N] | list
"""
import json, os, pathlib, sys, time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fs_lock

# Path overrides let tests (and the CLI under test) run against a scratch store
# instead of the real estate state — same pattern as AIQE_ENV_FILE / AIQE_HOOKS_SEEN.
DIR = pathlib.Path(os.environ.get("AIQE_PLAN_DIR") or ROOT / "reports/plans")
FILE = DIR / "state.json"
PLAN_DIR = pathlib.Path(os.environ.get("AIQE_TESTPLAN_DIR") or ROOT / "testplans")
VALID = ("draft", "in_review", "approved", "changes_requested")


def plan_path(key):
    return PLAN_DIR / f"{key}.md"


def contract_path(key):
    return DIR / f"{key}.contract.json"


def load():
    if FILE.exists():
        try:
            return json.load(open(FILE, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save(state):
    DIR.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")


def get(key):
    return load().get(key, {})


def set_status(key, status, by="", note=""):
    """Transition a plan. Returns the updated entry."""
    if status not in VALID:
        raise SystemExit(f"status must be one of: {', '.join(VALID)}")
    if not plan_path(key).exists():
        raise SystemExit(f"no test plan for {key} (create one: make plan KEY={key})")
    with fs_lock.lock(FILE):
        state = load()
        e = state.get(key, {"history": []})
        e.update({"status": status, "by": by or e.get("by", ""), "note": note,
                  "updated": time.time()})
        e.setdefault("history", []).append(
            {"status": status, "by": by, "note": note, "ts": time.time()})
        state[key] = e
        _save(state)
    return e


def record_plan(key, contract=None, by="pipeline"):
    """Called by the pipeline after the testplan phase: snapshot the contract and put
    the plan in `draft` awaiting human review. Preserves prior history."""
    with fs_lock.lock(FILE):
        state = load()
        e = state.get(key, {"history": []})
        e.update({"status": "draft", "by": by, "note": "test plan authored",
                  "updated": time.time(), "generated_run": None})
        e.setdefault("history", []).append(
            {"status": "draft", "by": by, "note": "test plan authored", "ts": time.time()})
        state[key] = e
        _save(state)
    if contract is not None:
        DIR.mkdir(parents=True, exist_ok=True)
        contract_path(key).write_text(
            json.dumps(contract, indent=2), encoding="utf-8", newline="\n")
    return state[key]


def save_plan(key, text, by=""):
    """Replace the plan markdown. Editing an APPROVED plan resets it to draft so a
    changed artifact can never inherit a stale approval."""
    if not text.strip():
        raise SystemExit("test plan text is empty")
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    plan_path(key).write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")
    cur = get(key).get("status")
    if cur == "approved":
        return set_status(key, "draft", by, "edited after approval — re-approval required")
    if cur is None:
        return set_status(key, "draft", by, "plan created by edit")
    return set_status(key, cur, by, "plan edited")


def mark_linked(key, ref, by=""):
    """Record that the approved plan was linked to its tracker ticket."""
    with fs_lock.lock(FILE):
        state = load()
        e = state.get(key)
        if e is None:
            raise SystemExit(f"no plan state for {key}")
        e["linked"] = {"ref": ref, "by": by, "ts": time.time()}
        e.setdefault("history", []).append(
            {"status": e.get("status", "?"), "by": by,
             "note": f"linked to tracker: {ref}", "ts": time.time()})
        state[key] = e
        _save(state)
    return e


def mark_generated(key, run_id):
    with fs_lock.lock(FILE):
        state = load()
        e = state.get(key, {"history": []})
        e["generated_run"] = run_id
        e.setdefault("history", []).append(
            {"status": e.get("status", "approved"), "by": "pipeline",
             "note": f"tests generated (run {run_id})", "ts": time.time()})
        state[key] = e
        _save(state)
    return e


def require_approved(key):
    """Gate for test generation — raises unless the plan is approved."""
    e = get(key)
    if not e:
        raise SystemExit(f"no test plan for {key}: run `make plan KEY={key}` first")
    if e.get("status") != "approved":
        raise SystemExit(
            f"test plan for {key} is '{e.get('status')}', not approved — "
            f"review and approve it first (make plan-approve KEY={key})")
    return e


def summary():
    """All plans with status + whether the markdown/contract exist."""
    state = load()
    out = []
    for key in sorted(state):
        e = state[key]
        out.append({"key": key, "status": e.get("status", "?"),
                    "by": e.get("by", ""), "note": e.get("note", ""),
                    "updated": e.get("updated", 0),
                    "linked": bool(e.get("linked")),
                    "generated_run": e.get("generated_run"),
                    "has_plan": plan_path(key).exists()})
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)

    def opt(n, d=""):
        return a[a.index(n) + 1] if n in a else d
    if a[0] == "get":
        print(json.dumps(get(a[1]), indent=2))
    elif a[0] == "require-approved":            # pipeline gate (exits non-zero if not)
        require_approved(a[1])
        print(f"{a[1]}: plan approved")
    elif a[0] == "record":                      # pipeline: snapshot after testplan
        contract = None
        if len(a) > 2 and pathlib.Path(a[2]).exists():
            try:
                contract = json.load(open(a[2], encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                contract = None
        print(json.dumps(record_plan(a[1], contract), indent=2))
    elif a[0] == "generated":
        print(json.dumps(mark_generated(a[1], a[2]), indent=2))
    elif a[0] == "list":
        for p in summary():
            print(f"{p['key']:<16} {p['status']:<18} "
                  f"{'linked' if p['linked'] else '-':<7} {p['note']}")
    elif a[0] == "set":
        print(json.dumps(set_status(a[1], a[2], opt("--by"), opt("--note")), indent=2))
    else:
        sys.exit(f"unknown command: {a[0]}")

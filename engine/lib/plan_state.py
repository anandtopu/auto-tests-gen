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
import app_paths                      # R12: mutable paths resolve here
import fs_lock

# Path overrides let tests (and the CLI under test) run against a scratch store
# instead of the real estate state — same pattern as AIQE_ENV_FILE / AIQE_HOOKS_SEEN.
DIR = pathlib.Path(os.environ.get("AIQE_PLAN_DIR") or ROOT / "reports/plans")
FILE = DIR / "state.json"
PLAN_DIR = app_paths.testplans_dir(ROOT)   # AIQE_TESTPLAN_DIR > AIQE_STATE_DIR > ROOT
VALID = ("draft", "in_review", "approved", "changes_requested")


def plan_path(key):
    return PLAN_DIR / f"{key}.md"


def contract_path(key):
    return DIR / f"{key}.contract.json"


def load():
    # Guarded: a corrupt file is quarantined, never silently treated as empty —
    # the old swallow-and-return-{} path let the next _save overwrite human
    # approvals with empty state after a torn write.
    return fs_lock.read_json_guarded(FILE, {})


def _save(state):
    fs_lock.write_json_atomic(FILE, state, sort_keys=True)


def get(key):
    return load().get(key, {})


def set_status(key, status, by="", note=""):
    """Transition a plan. Returns the updated entry."""
    if status not in VALID:
        raise SystemExit(f"status must be one of: {', '.join(VALID)}")
    if status == "changes_requested" and not note:
        raise SystemExit("changes_requested needs a note saying what to change "
                         "(NOTE=\"...\" / --note) — the reviewer's ask is the "
                         "whole point of the status")
    if not plan_path(key).exists():
        raise SystemExit(f"no test plan for {key} (create one: make plan KEY={key})")
    with fs_lock.lock(FILE):
        state = load()
        e = state.get(key, {"history": []})
        e.update({"status": status, "by": by or e.get("by", ""), "note": note,
                  "updated": time.time()})
        entry = {"status": status, "by": by, "note": note, "ts": time.time()}
        # SDD (story 1.3): an approval SIGNS the structured spec, not just the
        # rendered prose — the hash on the history entry is the signature a
        # later audit verifies. Free-form plans record no hash, as before.
        if status == "approved":
            try:
                import spec_store
                h = spec_store.sha(key)
                if h:
                    e["spec_sha"] = h
                    entry["spec_sha"] = h
            except Exception:
                pass
        e.setdefault("history", []).append(entry)
        state[key] = e
        _save(state)
    # An approval freezes the text the approver signed: this snapshot is the
    # baseline every later "what changed?" diff compares against (roadmap 4.2).
    if status == "approved":
        snapshot_plan(key, "approved")
    return e


def _reuse_marker():
    """This run's reuse provenance from out/plan-reuse.json, or {} — total,
    like _adversary_detail: the record must not depend on the feature."""
    try:
        import plan_reuse
        return plan_reuse.marker()
    except Exception:
        return {}


def _adversary_detail():
    """The normalized adversarial signal from this run's out/ scratch, or {}.
    Total: a missing/failed adversary phase yields an empty dict, never an error —
    the plan record must not depend on an advisory phase having run."""
    try:
        import plan_adversary
        sig = plan_adversary.signal()
        if not sig.get("ran"):
            return {}
        return {"gaps": sig.get("gaps") or [],
                "accepted": sig.get("accepted"), "rejected": sig.get("rejected"),
                "scenarios_final": sig.get("scenarios_final", 0)}
    except Exception:
        return {}


def record_plan(key, contract=None, by="pipeline", adversary=""):
    """Called by the pipeline after the testplan phase: snapshot the contract and put
    the plan in `draft` awaiting human review. Preserves prior history.

    `adversary` is the one-line summary of the adversarial review (plan_adversary.py).
    It is stored on the entry because out/ is per-run scratch — without this the
    reviewer opening the plan tomorrow would have no idea it was ever challenged.
    """
    with fs_lock.lock(FILE):
        state = load()
        e = state.get(key, {"history": []})
        e.update({"status": "draft", "by": by, "note": "test plan authored",
                  "updated": time.time(), "generated_run": None,
                  "adversary": (adversary or "").strip(),
                  # The per-gap verdicts (title/category/severity/rationale +
                  # accepted/rejected counts). Snapshotted HERE because out/ is
                  # per-run scratch and record time is the last moment it exists —
                  # without this the reviewer sees one summary line and the actual
                  # challenge dies with the run. Bounded: the gaps list only.
                  "adversary_detail": _adversary_detail()})
        # Reuse provenance (cost-reduction 3.3): same out/-is-scratch pattern as
        # the adversary detail — record time is the last moment the marker
        # exists. A FRESH authoring clears any stale provenance from a prior
        # reused draft of the same key.
        reuse = _reuse_marker()
        if reuse.get("reused_from"):
            e["reused_from"] = reuse["reused_from"]
            e["similarity"] = reuse.get("similarity")
        else:
            e.pop("reused_from", None)
            e.pop("similarity", None)
        e.setdefault("history", []).append(
            {"status": "draft", "by": by, "note": "test plan authored", "ts": time.time()})
        state[key] = e
        _save(state)
    if contract is not None:
        DIR.mkdir(parents=True, exist_ok=True)
        contract_path(key).write_text(
            json.dumps(contract, indent=2), encoding="utf-8", newline="\n")
        # SDD (story 1.1/1.2): a STRUCTURED contract (scenarios carrying
        # steps/verification) becomes the spec of record, and the reviewer's
        # markdown is re-rendered FROM it — one source of truth. Best-effort:
        # a legacy contract writes no spec and the phase's markdown stands.
        try:
            import spec_store
            if spec_store.write_from_contract(key, contract):
                spec_store.render_to_plan(key)
        except Exception:
            pass
    return state[key]


def versions_dir(key):
    return DIR / "versions" / key


def snapshot_plan(key, label):
    """Keep the current plan text as a numbered version (roadmap 4.2).

    History entries recorded WHO did WHAT but only the latest text survived — so
    "what changed since I approved?" was unanswerable, and a re-approval was a leap
    of faith. Snapshots are small (plan markdown), bounded (last 20), and named by
    sequence + the event that caused them."""
    src = plan_path(key)
    if not src.exists():
        return None
    d = versions_dir(key)
    d.mkdir(parents=True, exist_ok=True)
    existing = sorted(d.glob("v*.md"))
    n = 1 + max((int(p.stem.split("-")[0][1:]) for p in existing), default=0)
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)[:40]
    dest = d / f"v{n:03d}-{safe}.md"
    dest.write_text(src.read_text(encoding="utf-8", errors="replace"),
                    encoding="utf-8", newline="\n")
    # SDD (story 1.3): the structured spec snapshots BESIDE the markdown under
    # the same version stem — the yaml is what the signature covers, and the
    # scenario-level re-approval diff needs the signed structure, not prose.
    try:
        import spec_store
        sp = spec_store.spec_path(key)
        if sp.exists():
            (d / f"v{n:03d}-{safe}.yaml").write_text(
                sp.read_text(encoding="utf-8", errors="replace"),
                encoding="utf-8", newline="\n")
    except Exception:
        pass
    for old in sorted(d.glob("v*.md"))[:-20]:
        old.unlink(missing_ok=True)
        base = old.with_suffix(".yaml")
        base.unlink(missing_ok=True)
    return dest


def approved_baseline(key):
    """The most recent snapshot taken AT approval, or None. This is the text the
    approver actually signed off — the only honest baseline for a re-approval diff."""
    d = versions_dir(key)
    if not d.is_dir():
        return None
    approved = sorted(d.glob("v*-approved*.md"))
    return approved[-1] if approved else None


def diff_since_approval(key):
    """Unified diff of the current plan vs what was approved, or "" when there is
    no baseline / no change. The re-approver sees exactly the delta, not the whole
    document again."""
    import difflib
    base = approved_baseline(key)
    cur = plan_path(key)
    if base is None or not cur.exists():
        return ""
    a = base.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    b = cur.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    if a == b:
        return ""
    # SDD (story 1.3): when the signed baseline has a structured spec beside
    # it, lead with the SCENARIO-LEVEL delta — the semantic change a
    # re-approver reviews — and keep the line diff below as the detail.
    header = ""
    try:
        import spec_store
        import yaml
        base_yaml = base.with_suffix(".yaml")
        cur_spec = spec_store.load(key)
        if base_yaml.exists() and cur_spec:
            old_spec = yaml.safe_load(base_yaml.read_text(encoding="utf-8"))
            lines = spec_store.diff_scenarios(old_spec, cur_spec)
            if lines:
                header = ("## Scenario-level changes (as approved -> current)\n"
                          + "\n".join(lines) + "\n\n")
    except Exception:
        header = ""
    return header + "".join(
        difflib.unified_diff(a, b, fromfile=f"{key} (as approved)",
                             tofile=f"{key} (current)"))


def save_plan(key, text, by=""):
    """Replace the plan markdown. Editing an APPROVED plan resets it to draft so a
    changed artifact can never inherit a stale approval."""
    if not text.strip():
        raise SystemExit("test plan text is empty")
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    cur = get(key).get("status")
    # Snapshot BEFORE overwriting: the pre-edit text is the version worth keeping.
    if plan_path(key).exists():
        snapshot_plan(key, f"pre-edit-{cur or 'new'}")
    plan_path(key).write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")
    # SDD (story 1.2): one source of truth, enforced. A FREE-FORM edit that
    # diverges from the spec's rendering SUPERSEDES the structured spec — the
    # yaml is set aside (kept for forensics, timestamped) and the plan reverts
    # to free-form, visibly, rather than letting two files silently disagree
    # about what the human signed. Structured editing arrives with the spec
    # editor (SDD 6.1); until then prose wins because a human wrote it.
    try:
        import spec_store
        sp = spec_store.spec_path(key)
        if sp.exists():
            rendered = spec_store.render(key)
            if rendered is not None and text.rstrip() != rendered.rstrip():
                sp.rename(sp.with_name(
                    f"testplan.yaml.superseded-{int(time.time())}"))
    except Exception:
        pass
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


# ------------------------------------------------------------------ SDD 2.2
def _requirements_gate_on():
    try:
        import yaml
        cfg = yaml.safe_load(open(ROOT / "registry/org-config.yaml",
                                  encoding="utf-8")) or {}
        return (cfg.get("spec") or {}).get("requirements_gate") in \
            (True, "on", "yes", 1)
    except Exception:
        return False


def requirements_record(key, by="pipeline"):
    """Mark the key's requirements spec as draft, awaiting human validation."""
    with fs_lock.lock(FILE):
        state = load()
        e = state.get(key, {"history": []})
        e["requirements_status"] = "draft"
        e.setdefault("history", []).append(
            {"requirements": "draft", "by": by, "ts": time.time()})
        state[key] = e
        _save(state)
    return state[key]


def set_requirements_status(key, status, by=""):
    """Validate/approve the requirements spec (SDD 2.2). Approval signs the
    yaml's hash, mirroring plan approval."""
    if status not in ("draft", "approved"):
        raise SystemExit("requirements status must be draft|approved")
    try:
        import spec_store
        if status == "approved" and not spec_store.load_requirements(key):
            raise SystemExit(f"no valid requirements spec for {key} — run "
                             f"`make requirements KEY={key}` first")
    except SystemExit:
        raise
    except Exception:
        pass
    with fs_lock.lock(FILE):
        state = load()
        e = state.get(key, {"history": []})
        e["requirements_status"] = status
        entry = {"requirements": status, "by": by, "ts": time.time()}
        if status == "approved":
            try:
                import hashlib
                import spec_store
                p = spec_store.requirements_path(key)
                if p.exists():
                    h = hashlib.sha256(p.read_bytes()).hexdigest()
                    e["requirements_sha"] = h
                    entry["requirements_sha"] = h
            except Exception:
                pass
        e.setdefault("history", []).append(entry)
        state[key] = e
        _save(state)
    return state[key]


def require_requirements(key):
    """Gate for planning (SDD 2.2): when org-config `spec.requirements_gate`
    is on, a plan may only be authored over VALIDATED requirements. Gate off =
    no-op — today's flow, byte for byte (pinned)."""
    if not _requirements_gate_on():
        return None
    st = get(key).get("requirements_status")
    if st != "approved":
        raise SystemExit(
            f"requirements gate is ON and {key}'s requirements are "
            f"'{st or 'absent'}', not approved — run `make requirements "
            f"KEY={key}`, review specs/{key}/requirements.yaml, then "
            f"`make requirements-approve KEY={key}`")
    return get(key)


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
        # Optional 4th arg: the adversarial-review summary line for this plan.
        print(json.dumps(record_plan(a[1], contract, adversary=a[3] if len(a) > 3 else ""),
                         indent=2))
    elif a[0] == "requirements-record":         # SDD 2.2: pipeline stop point
        print(json.dumps(requirements_record(a[1]), indent=2))
    elif a[0] == "requirements-set":
        print(json.dumps(set_requirements_status(
            a[1], a[2], a[3] if len(a) > 3 else ""), indent=2))
    elif a[0] == "require-requirements":        # SDD 2.2: planning gate
        require_requirements(a[1])
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


def ticket_comment(key):
    """Compose the J6 linking comment: the plan (status, approver, file) and the
    generated E2E tests (files, actions, gate commit + branch) for this key, in
    one ticket comment — the durable pointer from the JIRA ticket to everything
    the platform produced for it. Composition only; the caller posts it via the
    Tracker port's `comment` verb."""
    import glob
    e = get(key)
    lines = [f"AI-QE summary for {key}:"]
    p = plan_path(key)
    if p.exists():
        st = e.get("status", "draft")
        by = (e.get("by") or "").strip()
        lines.append(f"- Test plan: testplans/{key}.md — {st}"
                     + (f" (approved by {by})" if st == "approved" and by else ""))
        adv = (e.get("adversary") or "").strip()
        if adv:
            lines.append(f"- Plan review: {adv}")
        if e.get("reused_from"):
            lines.append(f"- Reused from: {e['reused_from']} (similarity "
                         f"{e.get('similarity')}) — adapted mechanically, "
                         f"human-reviewed before approval")
    if e.get("linked"):
        lines.append(f"- Plan attachment: {e['linked'].get('ref', '')}")
    # Latest run for this key (defensive parse — records are written via tee)
    latest = None
    for f in glob.glob(str(ROOT / "reports/runs/*.json")):
        if pathlib.Path(f).name in ("reviews.json", "queue.json", "hooks-seen.json"):
            continue
        try:
            r = json.load(open(f, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(r, dict) and r.get("trigger", {}).get("key") == key \
                and r.get("ts", 0) >= (latest or {}).get("ts", 0):
            latest = r
    if latest:
        contracts = {ph.get("name"): ph.get("contract") or {}
                     for ph in latest.get("phases", [])}
        tests = contracts.get("generate", {}).get("tests", []) or []
        if tests:
            lines.append(f"- E2E tests ({len(tests)}):")
            for t in tests[:8]:
                lines.append(f"  - {t.get('file', '?')} ({t.get('action', '?')})")
        for g in latest.get("gates", []):
            sha = (g.get("commit") or "")[:7]
            lines.append(f"- Gate {g.get('test_repo')}: {g.get('status')}"
                         + (f" @{sha} on branch test/{key}-ai-qe" if sha else ""))
        lines.append(f"- Run record: {latest.get('run_id', '')}")
    if len(lines) == 1:
        lines.append("- No plan or test artifacts recorded yet.")
    return "\n".join(lines)


def post_ticket_comment(key):
    """Compose + post the linking comment via the Tracker port (mock unless
    AIQE_MOCK=0). Returns {comment, result}."""
    import os, subprocess
    import settings_store, work_queue
    text = ticket_comment(key)
    settings_store.load_env_into()
    mock = os.environ.get("AIQE_MOCK", "1") == "1"
    adapter = ROOT / ("adapters/mock/tracker.sh" if mock
                      else "adapters/tracker/jira.sh")
    r = subprocess.run([work_queue.bash_exe(), str(adapter), "comment", key, text],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, timeout=60)
    if r.returncode != 0:
        raise SystemExit(f"comment failed: {(r.stdout + r.stderr).strip()[:200]}")
    # Record that the ticket was told. Journey J6 is "link the plan and the E2E tests
    # to the ticket VIA A COMMENT", so posting it is the deliverable — but nothing
    # persisted that, which left the wizard's final step stuck on `pending` after the
    # user had just clicked its button and been told it succeeded.
    with fs_lock.lock(FILE):
        state = load()
        if key in state:
            state[key]["commented"] = {"ts": time.time(),
                                       "result": r.stdout.strip()[:300]}
            _save(state)
    return {"comment": text, "result": r.stdout.strip()}

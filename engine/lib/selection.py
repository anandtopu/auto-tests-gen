#!/usr/bin/env python3
"""Selective review: approve SOME of what the AI produced, not all of it.

Approval was all-or-nothing. A reviewer who liked nine scenarios out of ten had
two options — accept the tenth, or reject the batch and re-run — and neither is
what they meant. This records a per-item decision and then emits the approved
artifacts from exactly the items that survived.

WHAT CAN BE SELECTED

  scenarios   from the ticket's spec (specs/<KEY>/testplan.yaml). Excluding one
              before generation means it is never written. This is the useful
              case, and the safe one.
  tests       generated spec files from a run. Excluding one AFTER the gate has
              already pushed it CANNOT un-push it — see below.

THE HONEST PART

The gate commits. Once a generated test is committed and pushed, no amount of
un-ticking a box in a review UI removes it from the test repo. Pretending
otherwise would be the worst kind of lie this product could tell: the reviewer
believes a test is gone, and it runs in CI that night.

So an excluded test that was already committed is reported as
`already_committed`, with the follow-up spelled out. The selection is still
RECORDED — it is a real decision, and the record is what a removal PR is
written from — but the artifact never claims the test is absent when it is not.

WHAT FINALIZE PRODUCES

`reports/approved/<KEY>/` — the plan re-rendered from the selected scenarios
ONLY (through spec_store's renderer, so there is still one rendering), plus a
manifest naming every item, its verdict, who decided and why.

The authored spec is NOT rewritten. specs/<KEY>/testplan.yaml stays the record
of what was PROPOSED; the approved subset is a separate signed artifact. Losing
the proposal would destroy the ability to ask "what did the reviewer turn
down?", which is exactly the question an audit asks.
"""
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import fs_lock  # noqa: E402
import plan_state  # noqa: E402

# The plan directory has ONE definition, and it is plan_state's — it honours
# AIQE_PLAN_DIR (R12 relocation, and how the state-adversarial suite isolates
# itself). Re-deriving `reports/plans` here would repeat the catalog-paths
# defect exactly: under a relocated plan dir the lifecycle state moves to the
# volume and the selection decisions stay behind at the image path, so the two
# halves of one review disagree — and on a read-only rootfs the second half is
# simply unwritable.
FILE = plan_state.DIR / "selection.json"


def _path(root=ROOT):
    return (pathlib.Path(root) / "reports/plans/selection.json"
            if root is not ROOT else FILE)


def _load_all(root=ROOT):
    try:
        data = json.loads(_path(root).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load(key, root=ROOT):
    """Recorded decisions for one key. Absent = nothing decided yet, which is
    NOT the same as everything excluded — `status` treats an unrecorded item as
    included, because a reviewer who has not looked has not rejected."""
    entry = _load_all(root).get(key) or {}
    return {"scenarios": entry.get("scenarios") or {},
            "tests": entry.get("tests") or {},
            "finalized": entry.get("finalized")}


def set_items(key, kind, decisions, actor="", reason="", reason_code="",
              duplicate_case_id="", root=ROOT):
    """Record include/exclude for some items. `decisions` is {id: bool}.

    Only the named items change — a review is usually a few exclusions against a
    long list, and rewriting the whole set on every click would let a stale UI
    silently revert someone else's decision.
    """
    if kind not in ("scenarios", "tests"):
        raise ValueError("kind must be 'scenarios' or 'tests'")
    if reason_code not in ("", "duplicate"):
        raise ValueError("reason_code must be empty or 'duplicate'")
    if reason_code == "duplicate" and any(not bool(v) for v in (decisions or {}).values()) \
            and not str(duplicate_case_id or "").strip():
        raise ValueError("duplicate exclusions require duplicate_case_id")
    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    duplicate_events = []
    with fs_lock.lock(p):
        data = _load_all(root)
        entry = data.setdefault(key, {})
        bucket = entry.setdefault(kind, {})
        for item_id, included in (decisions or {}).items():
            stamp = time.time()
            decision = {"included": bool(included), "by": actor or "unknown",
                        "reason": reason or "", "ts": stamp}
            if not included and reason_code:
                decision["reason_code"] = reason_code
                decision["duplicate_case_id"] = str(duplicate_case_id)[:500]
                if reason_code == "duplicate":
                    duplicate_events.append((str(item_id), stamp))
            bucket[str(item_id)] = decision
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        fs_lock.replace_atomic(tmp, p)
    if duplicate_events:
        import testcase_learning
        for item_id, stamp in duplicate_events:
            testcase_learning.record_duplicate(
                key, kind, item_id, duplicate_case_id, actor or "unknown",
                reason or "", stamp, root)
    return load(key, root)


def _committed_files(key, root=ROOT):
    """Spec files the GATE actually committed for this key, per repo.

    Read from the run record, which is what the gate wrote — not from the
    selection, which is what a human wants. The difference between those two is
    the entire point of the `already_committed` flag.
    """
    import run_progress
    rec = run_progress._record_for(key=key, root=root)
    if not rec:
        return set()
    committed = {g.get("test_repo") for g in (rec.get("gates") or [])
                 if g.get("status") == "committed"}
    if not committed:
        return set()
    out = set()
    for ph in rec.get("phases") or []:
        if ph.get("name") != "generate":
            continue
        for t in run_progress.dict_rows((ph.get("contract") or {}).get("tests")):
            if t.get("file") and (not t.get("repo") or t["repo"] in committed):
                out.add(t["file"])
    return out


def status(key, root=ROOT):
    """Every scenario and generated test, with its verdict and provenance.

    An item nobody has ruled on is `included` — not deciding is not rejecting.
    """
    import run_progress
    sel = load(key, root)
    scen_rows, test_rows = [], []

    try:
        import spec_store
        spec = spec_store.load(key) or {}
    except Exception:
        spec = {}
    for sc in (spec.get("scenarios") or []):
        if not isinstance(sc, dict):
            continue
        d = sel["scenarios"].get(str(sc.get("id")), {})
        scen_rows.append({"id": sc.get("id"), "title": sc.get("title"),
                          "layer": sc.get("layer"),
                          "target_repo": sc.get("target_repo"),
                          "included": d.get("included", True),
                          "decided": bool(d), "by": d.get("by"),
                          "reason": d.get("reason"),
                          "reason_code": d.get("reason_code"),
                          "duplicate_case_id": d.get("duplicate_case_id")})

    rec = run_progress._record_for(key=key, root=root)
    committed = _committed_files(key, root)
    if rec:
        for ph in rec.get("phases") or []:
            if ph.get("name") != "generate":
                continue
            for t in run_progress.dict_rows((ph.get("contract") or {}).get("tests")):
                f = t.get("file")
                if not f:
                    continue
                d = sel["tests"].get(f, {})
                inc = d.get("included", True)
                test_rows.append({
                    "file": f, "action": t.get("action"), "repo": t.get("repo"),
                    "scenario_id": t.get("scenario_id"),
                    "included": inc, "decided": bool(d),
                    "by": d.get("by"), "reason": d.get("reason"),
                    "reason_code": d.get("reason_code"),
                    "duplicate_case_id": d.get("duplicate_case_id"),
                    # The honest flag: excluding this cannot un-push it.
                    "already_committed": f in committed,
                    "follow_up": ("this file is already committed to the test "
                                  "repo — excluding it here records the decision "
                                  "but does NOT remove it; open a removal PR, or "
                                  "quarantine it via bin/qa.py quarantine")
                    if (not inc and f in committed) else None})

    try:
        if root is ROOT:
            warned = (plan_state.get(key).get("duplicate_warnings") or {})
        else:
            state = json.loads((pathlib.Path(root) / "reports/plans/state.json")
                               .read_text(encoding="utf-8"))
            warned = (state.get(key) or {}).get("duplicate_warnings") or {}
    except (OSError, ValueError):
        warned = {}
    raw_warning_count = (warned.get("warning_count") or
                         len(warned.get("warnings") or [])) \
        if isinstance(warned, dict) else len(warned)
    try:
        warning_count = max(0, int(raw_warning_count))
    except (TypeError, ValueError, OverflowError):
        warning_count = len(warned.get("warnings") or []) \
            if isinstance(warned, dict) else 0
    excluded_as_duplicate = sum(
        1 for row in [*scen_rows, *test_rows]
        if not row["included"] and row.get("reason_code") == "duplicate")
    return {"key": key, "scenarios": scen_rows, "tests": test_rows,
            "duplicate_review": {"warnings": warning_count,
                                 "excluded": excluded_as_duplicate},
            "finalized": sel.get("finalized")}


def approved_dir(key, root=ROOT):
    return pathlib.Path(root) / "reports/approved" / key


def finalize(key, actor="", root=ROOT):
    """Emit the approved artifacts from the items that survived review.

    Refuses when nothing would remain: an empty approved plan is not an
    approval, it is a rejection wearing the wrong label, and downstream a
    zero-scenario plan reads as "this ticket needs no tests".
    """
    st = status(key, root)
    kept_scen = [s for s in st["scenarios"] if s["included"]]
    dropped_scen = [s for s in st["scenarios"] if not s["included"]]
    kept_tests = [t for t in st["tests"] if t["included"]]
    dropped_tests = [t for t in st["tests"] if not t["included"]]

    if not st["scenarios"] and not st["tests"]:
        raise SystemExit(
            f"nothing to finalize for {key}: no spec scenarios and no generated "
            f"tests were found. Author a plan first (make plan KEY={key}).")
    if st["scenarios"] and not kept_scen:
        raise SystemExit(
            f"every scenario for {key} was excluded, so there is nothing to "
            f"approve. An empty approved plan reads downstream as 'this ticket "
            f"needs no tests' — reject the plan instead, or re-include at least "
            f"one scenario.")

    out = approved_dir(key, root)
    out.mkdir(parents=True, exist_ok=True)

    # Render the plan through spec_store's OWN renderer, from a spec narrowed to
    # the kept scenarios. Formatting one plan two ways is how the rendering and
    # the spec drift apart; this keeps a single rendering.
    plan_md, render_note = "", None
    try:
        import spec_store
        spec = spec_store.load(key) or {}
        if spec.get("scenarios"):
            keep_ids = {s["id"] for s in kept_scen}
            narrowed = dict(spec)
            narrowed["scenarios"] = [sc for sc in spec["scenarios"]
                                     if isinstance(sc, dict)
                                     and sc.get("id") in keep_ids]
            plan_md = spec_store.render(key, narrowed)
    except Exception as e:
        render_note = (f"the approved plan could not be rendered from the spec "
                       f"({e}); the manifest below is still authoritative about "
                       f"what was approved")

    if plan_md:
        (out / "testplan.md").write_text(plan_md, encoding="utf-8", newline="\n")

    manifest = {
        "key": key,
        "finalized_by": actor or "unknown",
        "finalized_ts": time.time(),
        "scenarios": {"approved": [s["id"] for s in kept_scen],
                      "excluded": [{"id": s["id"], "reason": s["reason"],
                                    "reason_code": s.get("reason_code"),
                                    "duplicate_case_id": s.get("duplicate_case_id"),
                                    "by": s["by"]} for s in dropped_scen]},
        "tests": {"approved": [t["file"] for t in kept_tests],
                  "excluded": [{"file": t["file"], "reason": t["reason"],
                                "reason_code": t.get("reason_code"),
                                "duplicate_case_id": t.get("duplicate_case_id"),
                                "by": t["by"],
                                "already_committed": t["already_committed"],
                                "follow_up": t["follow_up"]}
                               for t in dropped_tests]},
        # Named, not buried: these are the exclusions the artifact CANNOT
        # deliver on its own.
        "needs_follow_up": [t["file"] for t in dropped_tests
                            if t["already_committed"]],
        "plan_rendered": bool(plan_md),
        "note": render_note,
        "duplicate_review": st["duplicate_review"],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                       encoding="utf-8")

    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with fs_lock.lock(p):
        data = _load_all(root)
        data.setdefault(key, {})["finalized"] = {
            "ts": manifest["finalized_ts"], "by": manifest["finalized_by"],
            "scenarios": len(kept_scen), "tests": len(kept_tests),
            "needs_follow_up": len(manifest["needs_follow_up"])}
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        fs_lock.replace_atomic(tmp, p)

    manifest["path"] = str(out.relative_to(pathlib.Path(root)))
    return manifest


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        raise SystemExit("usage: selection.py <KEY> [finalize] "
                         "[--exclude-scenario ID] [--exclude-test FILE]")
    key = args[0]
    sys.stdout.reconfigure(encoding="utf-8")
    if len(args) > 1 and args[1] == "finalize":
        m = finalize(key, actor="cli")
        print(f"approved {len(m['scenarios']['approved'])} scenario(s), "
              f"{len(m['tests']['approved'])} test(s) -> {m['path']}")
        if m["needs_follow_up"]:
            print("NEEDS FOLLOW-UP (already committed, not removed by this):")
            for f in m["needs_follow_up"]:
                print(f"  {f}")
    else:
        st = status(key)
        print(f"{key}: {len(st['scenarios'])} scenario(s), {len(st['tests'])} test(s)")
        for s in st["scenarios"]:
            print(f"  [{'x' if s['included'] else ' '}] {s['id']}  {s['title']}")
        for t in st["tests"]:
            flag = "  (already committed)" if t["already_committed"] else ""
            print(f"  [{'x' if t['included'] else ' '}] {t['file']}{flag}")

#!/usr/bin/env python3
"""Spec drift detection (SDD story 4.1): specs age LOUDLY.

A signed test-plan spec references the application surface (endpoints, routes)
as it was at authoring time. When an app repo's contract changes, the
dependent scenarios silently rot — still approved, no longer describing the
system. This module diffs each structured spec's referenced surface against
the CURRENTLY harvested surface (same normalization as the extend scout) and:

  - flags scenarios whose referenced endpoints/routes vanished as `stale`
    (stored on the plan state entry — never editing the signed spec itself);
  - notifies (Notify port) when an APPROVED spec went stale — a human
    re-approves, edits, or waives; staleness is information, not an action.

Run by `make maintain`. Total: unreadable anything = skipped, never fatal.

CLI: spec_drift.py check [--notify]
"""
import os

import pathlib
import re
import subprocess
import sys
import env_flag                     # AIQE_MOCK means what it says
import sdd_messages

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

ROOT = pathlib.Path(__file__).resolve().parents[2]

_PATH_RE = re.compile(r"(/[A-Za-z0-9_{}/.:*-]+)")


def _norm(p):
    """Collapse path parameters to `*`: /v1/orders/{id} == /v1/orders/:id ==
    /v1/orders/7 == /v1/orders/*. Brace/colon params replace IN PLACE (no
    leading slash — they already sit after one); numeric segments replace
    slash-inclusive."""
    p = re.sub(r"\{[^}]*\}|:[A-Za-z_]+", "*", p.rstrip("/"))
    return re.sub(r"/\d+(?=/|$)", "/*", p)


def _current_surface():
    """Normalized endpoints/routes per app repo, freshly harvested."""
    try:
        from registry import load_registry
        import knowledge_chunks
        out = {}
        for r in load_registry().get("source_repositories", []):
            _, items = knowledge_chunks._harvest_surface(r)
            out[r["name"]] = {_norm(i) for i in items}
        return out
    except Exception:
        return {}


def _scenario_refs(scenario):
    """Normalized surface paths a scenario's text references."""
    text = " ".join([scenario.get("title", ""),
                     str(scenario.get("steps") or ""),
                     " ".join(scenario.get("verification") or [])])
    return {_norm(m) for m in _PATH_RE.findall(text) if len(m) > 3}


def check(notify=False):
    """[{key, stale: [scenario ids], approved}] for specs referencing surface
    that no longer exists anywhere in the estate."""
    import plan_state
    import spec_store
    surface = _current_surface()
    all_paths = set().union(*surface.values()) if surface else set()
    results = []
    if not spec_store.SPEC_DIR.is_dir():
        return results
    for d in sorted(spec_store.SPEC_DIR.iterdir()):
        if not d.is_dir() or d.name == "platform":
            continue
        key = d.name
        spec = spec_store.load(key)
        if not spec:
            continue
        stale = []
        stale_surfaces = {}
        for s in spec.get("scenarios", []):
            refs = _scenario_refs(s)
            gone = {p for p in refs if all_paths and p not in all_paths}
            if gone:
                scenario_id = s.get("id", "?")
                stale.append(scenario_id)
                stale_surfaces[scenario_id] = sorted(gone)
        entry = plan_state.get(key)
        prev = set(entry.get("stale_scenarios") or [])
        previous_surfaces = entry.get("stale_surfaces") or {}
        changed = set(stale) != prev or stale_surfaces != previous_surfaces
        approved = entry.get("status") == "approved"

        # Deliver BEFORE recording. `_record` used to persist the new stale set
        # first, so the next run saw `changed == False` and never notified
        # again: a channel outage lost the alarm permanently. Identical to the
        # coverage-drift bug — "notify once per change" is only safe if the
        # change is committed once the notification actually lands.
        delivered = True
        if notify and approved and changed and stale:
            messages = [sdd_messages.refusal(
                "drift_stale", key=key, scenario=scenario_id,
                surfaces=stale_surfaces.get(scenario_id))["text"]
                for scenario_id in stale]
            delivered = _notify("\n".join(messages))

        # An undelivered alarm leaves the previous state in place so the next
        # run reports it again. Resolution still records: `delivered` starts
        # True and only the notify branch can clear it, and that branch needs a
        # non-empty `stale` — so when drift clears there is nothing to have
        # failed. (An earlier version spelled that out as `delivered or not
        # stale`; the second clause is unreachable, and a condition that cannot
        # be exercised is the kind of thing this codebase keeps finding.)
        if changed and delivered:
            _record(key, stale, stale_surfaces)
        if stale:
            results.append({"key": key, "stale": stale, "approved": approved,
                            "stale_surfaces": stale_surfaces,
                            "delivered": delivered})
    return results


def _record(key, stale, stale_surfaces=None):
    try:
        import fs_lock
        import plan_state
        with fs_lock.lock(plan_state.FILE):
            state = plan_state.load()
            e = state.get(key, {"history": []})
            if stale:
                e["stale_scenarios"] = stale
                e["stale_surfaces"] = stale_surfaces or {}
            else:
                e.pop("stale_scenarios", None)
                e.pop("stale_surfaces", None)
            state[key] = e
            plan_state._save(state)
    except Exception:
        pass


def _notify(msg):
    """Returns whether it was DELIVERED. Still best-effort — an unreachable
    channel must not fail maintenance — but the caller now learns it failed
    instead of the failure vanishing into a bare `except: pass`."""
    try:
        import work_queue
        adapter = ROOT / ("adapters/mock/notify.sh"
                          if env_flag.mock()
                          else "adapters/notify/slack.sh")
        if not adapter.exists():
            return False
        r = subprocess.run([work_queue.bash_exe(), str(adapter), "post", msg],
                           cwd=ROOT, capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=30)
        return r.returncode == 0
    except Exception:                      # noqa: BLE001
        return False


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8")
    results = check(notify="--notify" in argv)
    if not results:
        print("spec drift: all structured specs match the current surface")
        return 0
    for r in results:
        mark = "APPROVED " if r["approved"] else ""
        print(f"STALE {mark}spec {r['key']}: {', '.join(r['stale'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

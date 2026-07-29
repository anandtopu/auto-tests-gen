"""Seed the demo artifacts a test needs, instead of assuming the estate has them.

Several tests assert on `reports/runs/` + `testplans/` content for PROJ-301 — the
export bundle, the artifacts view. They used to depend on whatever the estate
happened to contain, which made them pass or fail on ambient state:

  * park the demo with the plan at `draft` and nothing generated (the state you
    want before demonstrating the approval gate live) and they fail;
  * run the full suite once — later E2E tests regenerate PROJ-301 — and they pass
    on the next run.

Same test, same code, different answer. A test that reads shared mutable state is
reporting on the estate, not on the code it names.

So: seed exactly what is missing, and remove exactly what was seeded. Anything the
estate already has is left alone and untouched on teardown, so this never deletes
real demo data or perturbs the scorecard.
"""
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]

PLAN_MD = """# Test Plan — {key}
## Existing Coverage (from catalog)
- PROJ-88 discount happy path already covered in e2e-api-tests-1.
## Scenarios
| ID | Title | Layer | Target repo | Behavior | Data |
| {key}-S1 | boundary rejection >90% | api | e2e-api-tests-1 | B2 | d1 |
## Open Questions
- AC-3 stacking behavior undefined.
"""


def _run_record(key, run_id, kind="jira"):
    return {
        # Field names track engine/lib/run_record.py exactly — `trigger.type` and
        # `gates[].exit_code`. A near-miss here fails inside the CLI with a
        # KeyError instead of failing the assertion under test.
        "run_id": run_id, "ts": time.time(), "overall": "committed",
        "trigger": {"type": kind, "key": key},
        "gates": [{"test_repo": "e2e-api-tests-1", "status": "committed",
                   "exit_code": 0, "commit": "0123456",
                   "log": f"reports/{key}-e2e-api-tests-1.log", "diff": ""}],
        "phases": [
            {"name": "generate", "contract": {"tests": [
                {"file": f"suites/orders/{key}-discount-boundary.spec.js",
                 "name": f"{key}: boundary", "scenario_id": f"{key}-S1",
                 "action": "created", "repo": "e2e-api-tests-1"}],
                "open_questions": []}},
            {"name": "validate", "contract": {"passed": 2, "failed": 0,
                                              "repair_loops": 0, "flaky_reruns": 0}},
        ],
    }


def ensure_generated_run(key="PROJ-301", release="2026.08", kind="jira",
                         seed_plan=True):
    """Make sure `key` has a plan and a committed run with generated tests.

    Returns a zero-arg cleanup callable that removes only what was created here.
    """
    import sys
    sys.path.insert(0, str(ROOT / "engine/lib"))
    import review_state

    created = []

    plan = ROOT / f"testplans/{key}.md"
    if seed_plan and not plan.exists():
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(PLAN_MD.format(key=key), encoding="utf-8", newline="\n")
        created.append(plan)

    has_run = False
    for f in (ROOT / "reports/runs").glob("*.json"):
        if f.name in ("reviews.json", "queue.json", "hooks-seen.json"):
            continue
        try:
            r = json.load(open(f, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if r.get("trigger", {}).get("key") == key and any(
                p.get("name") == "generate" and (p.get("contract") or {}).get("tests")
                for p in r.get("phases", [])):
            has_run = True
            break

    if not has_run:
        # Include the key: two seeds in the same second would otherwise share a
        # run_id and the second would silently overwrite the first's record.
        safe = "".join(c if c.isalnum() else "-" for c in key)
        run_id = f"test-seed-{safe}-{int(time.time())}"
        rec = ROOT / f"reports/runs/{run_id}.json"
        rec.parent.mkdir(parents=True, exist_ok=True)
        rec.write_text(json.dumps(_run_record(key, run_id, kind), indent=1),
                       encoding="utf-8", newline="\n")
        created.append(rec)

    # The export bundle prints BOTH the target release and the team-review status,
    # and each is emitted only when set. Seed whichever is missing — `clear-demo`
    # wipes reviews.json, so a freshly re-seeded estate has neither.
    entry = review_state.load().get(key) or {}
    restore_release = restore_status = None
    if not entry.get("release"):
        review_state.set_release(key, release)
        restore_release = key
    if not entry.get("status"):
        review_state.set_status(key, "pending_review", "test-seed")
        restore_status = key

    def cleanup():
        for p in created:
            try:
                p.unlink()
            except OSError:
                pass
        if restore_release:
            try:
                review_state.set_release(restore_release, "")
            except Exception:
                pass
        if restore_status:
            try:
                data = review_state.load()
                if key in data:
                    data[key].pop("status", None)
                    review_state.save(data)
            except Exception:
                pass

    return cleanup

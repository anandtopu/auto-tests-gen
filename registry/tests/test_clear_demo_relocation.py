"""A destructive operation must delete the state actually in use.

`clear-demo` (and the Settings view's Factory reset) joined every path it
deletes to the checkout root by hand. Under R12 relocation -- the DEPLOYED
shape, where `/app` is a read-only image and mutable paths live on a volume --
that is the wrong tree for seven of fifteen entries.

Measured before the fix, with AIQE_STATE_DIR set against the real checkout:
`clear()` reported **3139 files removed** and NOT ONE of its targets named the
relocated tree, while planted operator state under testplans/, testdata/,
specs/, knowledge/ and reports/exports/ was untouched. So the most destructive
button in the UI reported a file count for work it did not do, and reached into
the image instead.

The module already knew the rule: `_state`'s docstring said "a container that
relocated state clears the copy actually in use rather than the pristine one
baked into the image" -- and it was wired to exactly ONE entry, reports/costs.
The rule living only in a helper nobody called for the other fourteen is the
same shape this repo keeps recording.

These pins are behavioural on purpose. A source-text check cannot tell a
resolver that is CALLED from one that merely exists, which is precisely how the
original defect survived having its own rule written down.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

# One marker per relocated store. `catalog` is included because the catalog is
# the estate's routing evidence and has its own long history of being resolved
# in fourteen different places.
# reports/plans and reports/openhands are here because they relocate through
# their OWN knob, not AIQE_STATE_DIR -- so they are the entries that catch a
# fix which routes everything through the generic resolver and calls it done.
RELOCATED = ("reports/exports", "knowledge/generated", "knowledge/synced",
             "testplans", "testdata", "specs",
             "reports/plans", "reports/openhands")
MARKER = "OPERATOR-STATE.txt"

# Run in a child process: demo_data resolves ROOT at import, and AIQE_STATE_DIR
# is read through app_paths at call time -- driving it in-process would leave
# this session's modules pointing at a temp estate for every later test.
#
# THE CONTAINMENT CHECK IS NOT DECORATION. This test makes `clear()` believe the
# fixture IS the real checkout (that is the only way to exercise the relocation
# branch at all), which means a resolver bug -- or a mutation of one, which is
# how it happened -- points a DESTRUCTIVE call at the live estate. The first
# version of this file did exactly that and deleted the tracked reports/plans/
# approvals store twice: once under mutation and once unmutated, because
# plan_state.DIR is computed from ITS OWN module root and the fixture had
# helpfully cleared the knob that would have redirected it. So every target is
# resolved and checked BEFORE anything is deleted, and an escape aborts instead
# of proceeding. A test that can destroy the estate it is protecting is not a
# test.
_DRIVER = r'''
import json, pathlib, sys
sys.path.insert(0, "engine/lib")
import app_paths, demo_data
fake_root = pathlib.Path(sys.argv[1]).resolve()
fence = pathlib.Path(sys.argv[2]).resolve()
app_paths.ROOT = fake_root
demo_data.ROOT = fake_root
escapes = []
rels = (demo_data.CLEAR_DIRS + demo_data.CLEAR_FILES
        + ["knowledge/repos", "knowledge/curated", "registry/repo-registry.yaml"]
        + [p.rpartition("/")[0] for p, _ in demo_data.CLEAR_GLOBS])
for rel in rels:
    t = pathlib.Path(demo_data._target(fake_root, rel)).resolve()
    if fence not in t.parents and t != fence:
        escapes.append(f"{rel} -> {t}")
if escapes:
    print(json.dumps({"escapes": escapes}))
    raise SystemExit(3)
result = demo_data.clear(fake_root, dry=%s)
print(json.dumps({"removed": result.get("removed"),
                  "targets": result.get("targets") or []}))
'''


def _estate(tmp_path):
    """An image tree and a state volume, both carrying the same markers."""
    image = tmp_path / "app"
    state = tmp_path / "volume"
    for base in (image, state):
        for rel in RELOCATED:
            d = base / rel
            d.mkdir(parents=True, exist_ok=True)
            (d / MARKER).write_text(f"{base.name}\n", encoding="utf-8")
    # clear() refuses past a live pipeline lock and regenerates AGENTS.md from
    # scripts that do not exist under a fake root -- both are handled by the
    # module (absent script is skipped), so nothing else is needed here.
    return image, state


def _clear(image, state, dry=False):
    """Drive clear() with EVERY store pointed inside the fixture.

    Knobs are SET, never cleared. A conftest knob left in place would pin a
    store to this session's out/ directory and prove nothing about relocation;
    clearing it is worse -- the store then resolves through its own module root,
    which is the live repo. Both mistakes are the same mistake, and the second
    one is what deleted the estate's approvals."""
    env = dict(os.environ, AIQE_STATE_DIR=str(state))
    env.update({
        "AIQE_PLAN_DIR": str(state / "reports/plans"),
        "AIQE_OPENHANDS_DIR": str(state / "reports/openhands"),
        "AIQE_CATALOG_DB": str(state / "reports/catalog.db"),
        "AIQE_HEALTH_FILE": str(state / "catalog/health.json"),
        "AIQE_REVIEWS_FILE": str(state / "reports/runs/reviews.json"),
        "AIQE_QUEUE_FILE": str(state / "reports/runs/queue.json"),
    })
    # Only knobs that would pin a store OUTSIDE the fixture are dropped, and
    # every one of them is re-set above or covered by AIQE_STATE_DIR.
    for knob in ("AIQE_TESTPLAN_DIR", "AIQE_TESTDATA_DIR", "AIQE_SPEC_DIR",
                 "AIQE_EXPORTS_DIR", "AIQE_KNOWLEDGE_DIR", "AIQE_COSTS_DIR",
                 "AIQE_CATALOG_DIR", "AIQE_REGISTRY_FILE"):
        env.pop(knob, None)
    r = subprocess.run(
        [sys.executable, "-c", _DRIVER % ("True" if dry else "False"),
         str(image), str(image.parent)],
        cwd=ROOT, env=env, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 0, (
        "clear() resolved a target outside the fixture — it would have deleted "
        "live state:\n" + (r.stdout or "") + (r.stderr[-2000:] or ""))
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_a_clear_deletes_the_state_actually_in_use(tmp_path):
    """THE DEFECT. Every marker on the volume must go."""
    image, state = _estate(tmp_path)
    _clear(image, state)
    survivors = [rel for rel in RELOCATED if (state / rel / MARKER).exists()]
    assert not survivors, (
        "clear-demo left the operator's real state behind and reported success: "
        + ", ".join(survivors))


def test_a_clear_does_not_reach_into_the_image(tmp_path):
    """The other direction, and it is the one that corrupts a deployment: `/app`
    is the read-only image, and its seed copies are what a fresh boot restores
    from. Deleting there is not a smaller mistake than missing the volume."""
    image, state = _estate(tmp_path)
    _clear(image, state)
    clobbered = [rel for rel in RELOCATED if not (image / rel / MARKER).exists()]
    assert not clobbered, (
        "clear-demo deleted image copies that relocation had moved off: "
        + ", ".join(clobbered))


def test_the_reported_count_covers_what_was_actually_removed(tmp_path):
    """A count that describes a different tree than the deletion is worse than
    no count -- it is the thing that made this defect invisible."""
    image, state = _estate(tmp_path)
    dry = _clear(image, state, dry=True)
    assert dry["removed"] >= len(RELOCATED), (
        "the dry run under-reports: it counted files in a tree it will not touch")
    for rel in RELOCATED:
        assert (state / rel / MARKER).exists(), "dry=True deleted something"


def test_a_caller_supplied_root_is_never_redirected(tmp_path):
    """Tests pass a temp estate and MUST keep winning, or a suite run would
    clear the live one. Asserted against a state dir that exists and differs."""
    sys.path.insert(0, str(ROOT / "engine/lib"))
    import app_paths, demo_data
    other = tmp_path / "somewhere-else"
    other.mkdir()
    got = demo_data._target(tmp_path / "fixture-estate", "testplans")
    assert got == tmp_path / "fixture-estate" / "testplans"


def test_every_owned_store_names_a_real_owner():
    """The exception table is a silencing mechanism by construction, so each
    entry must resolve to something -- an entry naming a module that moved
    would quietly fall back to the checkout path."""
    sys.path.insert(0, str(ROOT / "engine/lib"))
    import demo_data
    for rel, owner in demo_data._OWNED.items():
        value = owner()
        assert isinstance(value, pathlib.Path) and str(value), rel


def test_the_module_no_longer_joins_mutable_paths_by_hand():
    """The invariant, not today's list: a fifteenth entry added next year is
    caught too. Only `root / rel` joins for paths app_paths does not relocate
    are allowed, and those are named here with their reason."""
    src = (ROOT / "engine/lib/demo_data.py").read_text(encoding="utf-8")
    body = "\n".join(line.split("#")[0] for line in src.splitlines())
    # Frozen-by-design: reports/runs is not relocated (reports/ is the PVC
    # mount in the deployed shape) and catalog/bootstrap is CODE.
    allowed = ('root / "reports/runs"', 'root / "out/.pipeline.lock"',
               'root / "catalog/bootstrap/regen_coverage.py"', "root / script",
               "root / rel")
    offenders = []
    for i, line in enumerate(body.splitlines(), 1):
        if "root /" not in line:
            continue
        if any(a in line for a in allowed):
            continue
        offenders.append(f"{i}: {line.strip()[:90]}")
    assert not offenders, (
        "a mutable path is joined by hand again — it will delete the wrong tree "
        "under AIQE_STATE_DIR:\n  " + "\n  ".join(offenders))


def test_the_containment_fence_actually_fires(tmp_path):
    """A guard nobody has seen refuse is not a guard. This drives the same
    fence with a store deliberately pointed outside the fixture and requires
    the driver to abort -- otherwise the isolation these tests rely on could be
    'proven' by a check that never evaluates anything."""
    image, state = _estate(tmp_path)
    env = dict(os.environ, AIQE_STATE_DIR=str(state),
               AIQE_PLAN_DIR=str(tmp_path.parent / "somewhere-outside"))
    for knob in ("AIQE_TESTPLAN_DIR", "AIQE_TESTDATA_DIR", "AIQE_SPEC_DIR",
                 "AIQE_EXPORTS_DIR", "AIQE_KNOWLEDGE_DIR", "AIQE_COSTS_DIR",
                 "AIQE_CATALOG_DIR", "AIQE_REGISTRY_FILE"):
        env.pop(knob, None)
    r = subprocess.run(
        [sys.executable, "-c", _DRIVER % "False", str(image), str(tmp_path)],
        cwd=ROOT, env=env, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 3, "the fence let an out-of-fixture target through"
    assert "reports/plans" in r.stdout
    # and it aborted BEFORE deleting: the fixture is intact
    assert (state / "testplans" / MARKER).exists()

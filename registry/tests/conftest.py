"""Suite-wide setup.

The only thing here is a session-start sweep of state a CRASHED or KILLED run
can leave behind on the shared demo estate. Both leftovers below cost real
debugging time once: the symptom lands in an unrelated test, hours later.

  out/.pipeline.lock       held for up to STALE_LOCK_MINUTES (90) after a
                           killed run, so every pipeline-running test crawls
                           or fails on a lock nobody holds
  fixture repos            a test that registers a throwaway repo cleans up in
                           `finally` — which never runs if the process is
                           killed, leaving the repo in the TRACKED registry
                           where the next run's fan-out test resolves it

The sweep CLEANS AND SAYS SO. It deliberately does not run silently: a leak
that a test itself caused is a bug worth seeing, and a printed line keeps it
visible instead of making the suite quietly self-healing.
"""
import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

# Throwaway repos registered by tests. Keep in sync with the tests that create
# them; an unknown repo is NEVER removed — that would be this file quietly
# deleting somebody's real registry entry.
FIXTURE_REPOS = ("zz-nofetch",)
STALE_LOCK_MINUTES = 90


def _sweep_lock(notes):
    lock = ROOT / "out/.pipeline.lock"
    if not lock.exists():
        return
    age_min = (time.time() - lock.stat().st_mtime) / 60
    if age_min < STALE_LOCK_MINUTES:
        notes.append(f"out/.pipeline.lock is {age_min:.0f} min old and may be "
                     f"LIVE — leaving it (pipeline tests will wait on it)")
        return
    shutil.rmtree(lock, ignore_errors=True)
    notes.append(f"removed a stale out/.pipeline.lock ({age_min:.0f} min old)")


def leftover_fixture_repos(registry_path=None):
    """Which FIXTURE_REPOS are currently registered. Detection is separate from
    removal so it can be tested without a test that edits the real registry."""
    try:
        import yaml
        reg = pathlib.Path(registry_path or ROOT / "registry/repo-registry.yaml")
        data = yaml.safe_load(reg.read_text(encoding="utf-8")) or {}
    except Exception:
        return []                    # an unreadable registry is not ours to fix
    # test_repositories is a LIST of entries, not a name->entry mapping.
    registered = {str((e or {}).get("name") or "")
                  for e in (data.get("test_repositories") or [])}
    return [n for n in FIXTURE_REPOS if n in registered]


def _sweep_registry(notes):
    present = leftover_fixture_repos()
    if not present:
        return
    try:
        import repo_admin
        for name in present:
            try:
                repo_admin.remove_test(name, force=True)
            except SystemExit:
                pass
        notes.append(f"removed leftover fixture repo(s) {', '.join(present)} "
                     f"— a previous run was killed before its cleanup")
    except Exception as e:
        notes.append(f"could NOT remove leftover fixture repo(s) "
                     f"{', '.join(present)}: {e}")


def pytest_sessionstart(session):
    notes = []
    _sweep_lock(notes)
    _sweep_registry(notes)
    for n in notes:
        print(f"[conftest] {n}")

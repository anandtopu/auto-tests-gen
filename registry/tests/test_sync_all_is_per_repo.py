"""One unreachable repo stopped the other six from syncing at all.

FOUND BY SWEEPING the sibling class of the batch-status finding - listing
commands and bulk operations whose failure mode is silence - and driving
`make sync-status` / `sync-all`.

`sync_all` was a list comprehension over `sync_repo`, which raises SystemExit
when the SCM cannot be reached. MEASURED on this 7-repo estate with one repo's
fetch failing:

    ABORTED: fetch_file failed for catalog-api:AGENTS.md: network unreachable
    repos that got synced before the abort: [admin-portal-ui, orders-api,
                                             web-storefront-ui]

Three synced, THREE NEVER ATTEMPTED, and the caller saw a single error naming
a single repo. `make maintain` runs this nightly and marks the step `degraded`
with that repo's name, which reads as "one repo is stale" when four are.

WHY IT MATTERS BEYOND TIDINESS: synced guidance is merged into AGENTS.md by
`repo_admin`, and AGENTS.md is injected into every authoring phase. A repo that
silently stops refreshing means the model is told yesterday's conventions, and
nothing anywhere says so.

The estate already has the right pattern: the bootstrap chain reports a single
unreadable app repo rather than dying on it, and `maintenance` keeps its steps
independent for the same reason. `sync_repo` still RAISES - an operator who
asked for ONE repo wants that failure loudly, not folded into a summary.
"""
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import guidance_sync                                      # noqa: E402


@pytest.fixture
def estate(tmp_path, monkeypatch):
    """An isolated sync cache plus a fake Scm adapter whose failures we choose.

    `AIQE_SYNC_DIR` is SET, never cleared: clearing it would point the module at
    the live knowledge/synced tree, which is the trap this repo already records
    for destructive fixtures.
    """
    import importlib
    monkeypatch.setenv("AIQE_SYNC_DIR", str(tmp_path / "cache"))
    importlib.reload(guidance_sync)
    adapter = tmp_path / "scm.sh"

    def fail_for(*repos):
        cond = " ".join(f'[ "$2" = "{r}" ] ||' for r in repos).rstrip("|| ")
        adapter.write_text(
            "#!/usr/bin/env bash\n"
            f'if [ "$1" = "fetch_file" ] && ( {cond} ); then\n'
            '  echo "network unreachable" >&2; exit 1\n'
            "fi\n"
            "exit 3\n", encoding="utf-8", newline="\n")
        guidance_sync._scm_adapter = lambda: adapter

    def all_fine():
        adapter.write_text("#!/usr/bin/env bash\nexit 3\n",
                           encoding="utf-8", newline="\n")
        guidance_sync._scm_adapter = lambda: adapter

    all_fine()
    yield fail_for, all_fine
    importlib.reload(guidance_sync)


def test_one_unreachable_repo_does_not_stop_the_others(estate):
    """THE DEFECT, driven."""
    fail_for, _ = estate
    total = len(guidance_sync.known_repos())
    fail_for("catalog-api")
    r = guidance_sync.sync_all()
    assert [f["repo"] for f in r["failed"]] == ["catalog-api"], r["failed"]
    assert r["repos"] == total - 1, \
        "repos after the failing one were never attempted"


def test_the_failure_is_named_not_merely_counted(estate):
    fail_for, _ = estate
    fail_for("catalog-api")
    r = guidance_sync.sync_all()
    assert "network unreachable" in r["failed"][0]["error"], r["failed"]


def test_a_failure_is_recorded_against_that_repo_only(estate):
    """`status()` must be able to say WHY a repo is stale, not just that its
    timestamp is old - and must not smear the blame across healthy repos."""
    fail_for, _ = estate
    fail_for("catalog-api")
    guidance_sync.sync_all()
    st = {x["name"]: x for x in guidance_sync.status()}
    assert "network unreachable" in st["catalog-api"]["last_error"]
    assert st["catalog-api"]["last_error_at"]
    for name, row in st.items():
        if name != "catalog-api":
            assert not row["last_error"], (name, row["last_error"])


def test_a_failure_does_not_erase_the_last_good_sync(estate):
    """Clearing files/synced_at would make a repo that synced fine yesterday
    look like one that never synced - losing the evidence that says how stale
    its cached guidance actually is."""
    fail_for, all_fine = estate
    all_fine()
    guidance_sync.sync_all()
    before = {x["name"]: x["synced_at"] for x in guidance_sync.status()}
    assert before["catalog-api"], "the first sync did not record a time"
    fail_for("catalog-api")
    guidance_sync.sync_all()
    after = {x["name"]: x for x in guidance_sync.status()}
    assert after["catalog-api"]["synced_at"] == before["catalog-api"], \
        "the failure erased the record of the last successful sync"


def test_a_healthy_estate_reports_no_failures(estate):
    """OVER-FIX GUARD: a `failed` list that is never empty would make the
    warning meaningless."""
    _, all_fine = estate
    all_fine()
    r = guidance_sync.sync_all()
    assert r["failed"] == []
    assert r["repos"] == len(guidance_sync.known_repos())


def test_every_repo_failing_is_still_reported_per_repo(estate):
    """The edge the abort hid completely: with the old code the FIRST failure
    was the only thing anyone saw."""
    fail_for, _ = estate
    names = [r["name"] for r in guidance_sync.known_repos()]
    fail_for(*names)
    r = guidance_sync.sync_all()
    assert r["repos"] == 0
    assert sorted(f["repo"] for f in r["failed"]) == sorted(names)


def test_a_single_repo_sync_still_fails_loudly(estate):
    """The direction that must NOT change: `sync_repo` is what an operator runs
    when they care about one repo, and swallowing that error would be a new
    silence in place of the one being removed."""
    fail_for, _ = estate
    fail_for("catalog-api")
    with pytest.raises(SystemExit):
        guidance_sync.sync_repo("catalog-api")


def test_the_cli_names_what_did_not_sync(estate, tmp_path):
    """DRIVEN at the entry point: a summary that reports only successes reads
    as a complete sync."""
    fail_for, _ = estate
    fail_for("catalog-api")
    r = subprocess.run([sys.executable,
                        str(ROOT / "engine/lib/guidance_sync.py"), "sync-all"],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL,
                       env={**os.environ,
                            "AIQE_SYNC_DIR": str(tmp_path / "cli-cache"),
                            "AIQE_MOCK": "1"})
    # The subprocess uses the real mock adapter (every repo succeeds), so the
    # assertion here is on the SHAPE of the reporting code path being reachable
    # and the healthy case staying quiet.
    assert r.returncode == 0, r.stderr[-400:]
    assert "NOT SYNCED" not in r.stdout + r.stderr, \
        "a healthy sync reports failures it did not have"


def test_the_cli_prints_the_failures_it_is_given():
    """The failing branch cannot be reached through the mock adapter, so pin
    that the CLI READS `failed` at all - the queue-warning lesson: a backend
    that knows and a renderer with no field for it is a shipped shape here."""
    src = (ROOT / "engine/lib/guidance_sync.py").read_text(encoding="utf-8")
    i = src.index('elif a[0] == "sync-all"')
    block = src[i:i + 1400]
    assert 'r.get("failed")' in block, \
        "the sync-all CLI never looks at the repos that failed"
    assert "NOT SYNCED" in block and "stale" in block, \
        "the CLI does not say what a failed sync means for that repo"

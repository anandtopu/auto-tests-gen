"""Regression tests for the Settings view backends: engine/lib/settings_store.py
(.env read/write with masked secrets) and engine/lib/demo_data.py (demo reset)."""
import pathlib, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import demo_data, settings_store


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    monkeypatch.setenv("AIQE_ENV_FILE", str(f))
    return f


def test_save_and_get_roundtrip_masks_secrets(env_file):
    env_file.write_text("# my note\nJIRA_URL=https://old.example.com\nUNRELATED=keepme\n",
                        encoding="utf-8")
    settings_store.save({"JIRA_URL": "https://new.example.com",
                         "ATLASSIAN_MCP_TOKEN": "sekrit-123"})
    text = env_file.read_text(encoding="utf-8")
    assert "# my note" in text and "UNRELATED=keepme" in text     # preserved
    assert "JIRA_URL=https://new.example.com" in text             # replaced in place
    assert "ATLASSIAN_MCP_TOKEN=sekrit-123" in text               # appended
    flat = {f["env"]: f for s in settings_store.get_settings() for f in s["fields"]}
    assert flat["JIRA_URL"]["value"] == "https://new.example.com"
    tok = flat["ATLASSIAN_MCP_TOKEN"]
    assert tok["set"] is True and tok["value"] == ""              # write-only secret
    assert "sekrit" not in str(settings_store.get_settings())


def test_defaults_apply_when_unset(env_file):
    flat = {f["env"]: f for s in settings_store.get_settings() for f in s["fields"]}
    assert flat["AIQE_MOCK"]["value"] == "1"
    assert flat["SCM_KIND"]["value"] == "github"
    assert flat["ANTHROPIC_API_KEY"]["set"] is False


def test_save_rejects_unknown_key_bad_option_and_newlines(env_file):
    for updates in ({"NOT_A_SETTING": "x"}, {"SCM_KIND": "gitlab"},
                    {"JIRA_URL": "a\nb"}):
        with pytest.raises(SystemExit):
            settings_store.save(updates)
    assert not env_file.exists()                                  # nothing written


def test_inline_comments_stripped_on_read(env_file):
    env_file.write_text("CONFLUENCE_SPACE=ENG    # default space\n", encoding="utf-8")
    assert settings_store.load()["CONFLUENCE_SPACE"] == "ENG"


def test_every_spec_key_documented_in_env_example():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    documented = {line.split("=", 1)[0].strip() for line in example.splitlines()
                  if "=" in line and not line.lstrip().startswith("#")}
    missing = set(settings_store.ALL_KEYS) - documented
    assert not missing, f".env.example is missing: {sorted(missing)}"


def _demo_tree(tmp_path):
    for rel in ["reports/runs/1-x.json", "reports/runs/1-x-repo.diff",
                "reports/runs/reviews.json", "reports/PROJ-1-repo.log",
                "reports/dashboard.html", "reports/exports/P-1-testplan.pdf",
                "testplans/PROJ-1.md", "testdata/PROJ-1/cases.json",
                "out/pr.diff", "workspace/tests/r/a.spec.js", "catalog/health.json",
                # state stores that used to survive a clear (see the regression test)
                "reports/plans/state.json", "reports/plans/PROJ-1.contract.json",
                "reports/openhands/state.json", "reports/inline/ADHOC-1.json",
                "knowledge/generated/some-repo/AGENTS.md",
                "knowledge/synced/some-repo/AGENTS.md", "knowledge/synced/state.json",
                # estate — must survive
                "catalog/tests.jsonl", "registry/repo-registry.yaml", "AGENTS.md",
                "demo/orders-api/app.js", "knowledge/repos/orders-api.md"]:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    return tmp_path


def test_clear_demo_removes_generated_keeps_estate(tmp_path):
    root = _demo_tree(tmp_path)
    r = demo_data.clear(root=root)
    assert r["removed"] == 18
    for gone in ["reports/runs/1-x.json", "reports/runs/reviews.json",
                 "reports/PROJ-1-repo.log", "reports/dashboard.html",
                 "reports/exports/P-1-testplan.pdf", "testplans/PROJ-1.md",
                 "testdata/PROJ-1/cases.json", "out/pr.diff",
                 "workspace/tests/r/a.spec.js", "catalog/health.json"]:
        assert not (root / gone).exists(), gone
    for kept in ["catalog/tests.jsonl", "registry/repo-registry.yaml",
                 "AGENTS.md", "demo/orders-api/app.js",
                 "knowledge/repos/orders-api.md"]:      # hand-authored team notes
        assert (root / kept).exists(), kept
    assert (root / "reports/runs").is_dir()                       # recreated empty


def test_clear_demo_leaves_no_state_store_behind(tmp_path):
    """Regression: 'Clear demo data' left four stores untouched.

    The stores are deliberately scattered — plan state sits outside reports/runs/ so
    the run-record glob skips it, OpenHands events have their own dir, guidance caches
    live under knowledge/ — and each one added after this module was written was
    silently missed. The worst was reports/plans/: an approval outlived the plan it
    approved, so generation could run against a sign-off for a plan that no longer
    existed."""
    root = _demo_tree(tmp_path)
    demo_data.clear(root=root)
    for gone in ["reports/plans/state.json", "reports/plans/PROJ-1.contract.json",
                 "reports/openhands/state.json", "reports/inline/ADHOC-1.json",
                 "knowledge/generated/some-repo/AGENTS.md",
                 "knowledge/synced/some-repo/AGENTS.md", "knowledge/synced/state.json"]:
        assert not (root / gone).exists(), f"survived the clear: {gone}"


def test_every_writable_state_store_is_a_clear_target():
    """Adding a new state store without adding it here is how this regressed.

    Keep this list in step with the stores the platform writes; if you add one,
    add it to demo_data.CLEAR_DIRS in the same change."""
    for rel in ("reports/runs", "reports/plans", "reports/openhands",
                "reports/inline", "reports/exports",
                "knowledge/generated", "knowledge/synced",
                "out", "workspace", "testplans", "testdata"):
        assert rel in demo_data.CLEAR_DIRS, f"{rel} is written but never cleared"
    assert "knowledge/repos" not in demo_data.CLEAR_DIRS
    assert not any(d == "knowledge" for d in demo_data.CLEAR_DIRS),         "clearing all of knowledge/ would delete hand-authored team notes"


def test_clear_demo_dry_run_touches_nothing(tmp_path):
    root = _demo_tree(tmp_path)
    r = demo_data.clear(root=root, dry=True)
    assert r["removed"] == 18
    assert (root / "reports/runs/1-x.json").exists()


def test_clear_demo_refuses_while_a_run_looks_active(tmp_path):
    root = _demo_tree(tmp_path)
    (root / "out/.pipeline.lock").mkdir(parents=True)
    with pytest.raises(SystemExit, match="looks active"):
        demo_data.clear(root=root)
    assert (root / "reports/runs/1-x.json").exists(), "nothing may be deleted"


def test_clear_demo_breaks_a_stale_lock(tmp_path):
    """A killed run leaves out/.pipeline.lock behind forever. Refusing on that made
    the Settings button fail permanently with a message that was untrue and gave
    the user no way out. Match pipeline.sh: older than the threshold == dead."""
    import os as _os, time as _t
    root = _demo_tree(tmp_path)
    lock = root / "out/.pipeline.lock"
    lock.mkdir(parents=True)
    old = _t.time() - (demo_data.STALE_LOCK_MINUTES + 5) * 60
    _os.utime(lock, (old, old))
    r = demo_data.clear(root=root)
    assert not lock.exists(), "a stale lock must be broken, not honoured"
    assert any("stale" in t for t in r["targets"])
    assert not (root / "reports/runs/1-x.json").exists(), "the clear must proceed"


def test_clear_demo_force_overrides_a_live_lock(tmp_path):
    root = _demo_tree(tmp_path)
    (root / "out/.pipeline.lock").mkdir(parents=True)
    r = demo_data.clear(root=root, force=True)
    assert any("force-removed" in t for t in r["targets"])
    assert not (root / "reports/runs/1-x.json").exists()


def test_clear_demo_dry_run_never_touches_the_lock(tmp_path):
    root = _demo_tree(tmp_path)
    lock = root / "out/.pipeline.lock"
    lock.mkdir(parents=True)
    r = demo_data.clear(root=root, dry=True, force=True)
    assert lock.exists(), "a dry run must not remove the lock either"
    # …and must not claim it did
    assert any("would be removed" in t for t in r["targets"]), r["targets"]
    assert not any(t.endswith("— removed)") for t in r["targets"])


def test_dashboard_renders_settings_view():
    r = subprocess.run([sys.executable, str(ROOT / "bin/dashboard.py")],
                       capture_output=True, text=True, cwd=ROOT,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    page = (ROOT / "reports/dashboard.html").read_text(encoding="utf-8")
    assert 'data-view="settings"' in page
    assert 'id="clear-demo"' in page and "Danger zone" in page
    assert "loadSettings" in page                                 # JS wired in

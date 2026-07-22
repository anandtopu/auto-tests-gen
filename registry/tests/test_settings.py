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
                "catalog/tests.jsonl", "registry/repo-registry.yaml", "AGENTS.md",
                "demo/orders-api/app.js"]:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    return tmp_path


def test_clear_demo_removes_generated_keeps_estate(tmp_path):
    root = _demo_tree(tmp_path)
    r = demo_data.clear(root=root)
    assert r["removed"] == 11
    for gone in ["reports/runs/1-x.json", "reports/runs/reviews.json",
                 "reports/PROJ-1-repo.log", "reports/dashboard.html",
                 "reports/exports/P-1-testplan.pdf", "testplans/PROJ-1.md",
                 "testdata/PROJ-1/cases.json", "out/pr.diff",
                 "workspace/tests/r/a.spec.js", "catalog/health.json"]:
        assert not (root / gone).exists(), gone
    for kept in ["catalog/tests.jsonl", "registry/repo-registry.yaml",
                 "AGENTS.md", "demo/orders-api/app.js"]:
        assert (root / kept).exists(), kept
    assert (root / "reports/runs").is_dir()                       # recreated empty


def test_clear_demo_dry_run_touches_nothing(tmp_path):
    root = _demo_tree(tmp_path)
    r = demo_data.clear(root=root, dry=True)
    assert r["removed"] == 11
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
    demo_data.clear(root=root, dry=True, force=True)
    assert lock.exists(), "a dry run must not remove the lock either"


def test_dashboard_renders_settings_view():
    r = subprocess.run([sys.executable, str(ROOT / "bin/dashboard.py")],
                       capture_output=True, text=True, cwd=ROOT,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    page = (ROOT / "reports/dashboard.html").read_text(encoding="utf-8")
    assert 'data-view="settings"' in page
    assert 'id="clear-demo"' in page and "Danger zone" in page
    assert "loadSettings" in page                                 # JS wired in

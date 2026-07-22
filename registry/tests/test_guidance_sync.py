"""Regression tests for syncing per-repo agent guidance (AGENTS.md / CLAUDE.md) from
the SCM, and for that guidance reaching every test-generation path."""
import os, pathlib, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import guidance_sync, repo_admin, work_queue

BASH = work_queue.bash_exe()


@pytest.fixture
def sync_dir(tmp_path, monkeypatch):
    """Redirect the sync cache so tests never disturb the real knowledge/synced/."""
    d = tmp_path / "synced"
    monkeypatch.setattr(guidance_sync, "SYNC_DIR", d)
    monkeypatch.setattr(guidance_sync, "STATE", d / "state.json")
    monkeypatch.setenv("AIQE_MOCK", "1")
    return d


def _scm(*args):
    return subprocess.run([BASH, "adapters/mock/scm.sh", *args], cwd=ROOT,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)


# ------------------------------------------------------------------ Scm port verb

def test_fetch_file_verb_returns_content_and_signals_absence():
    r = _scm("fetch_file", "orders-api", "CLAUDE.md")
    assert r.returncode == 0 and "orders-api" in r.stdout
    missing = _scm("fetch_file", "orders-api", "NOPE.md")
    assert missing.returncode == 3, "absent file must exit 3, not a generic failure"


def test_every_scm_adapter_implements_fetch_file():
    for a in ("github.sh", "bitbucket.sh", "stash.sh"):
        text = (ROOT / "adapters/scm" / a).read_text(encoding="utf-8")
        assert "fetch_file)" in text, f"{a} missing fetch_file"
    conf = (ROOT / "adapters/conformance/test_adapters.sh").read_text(encoding="utf-8")
    assert conf.count("fetch_file") >= 3       # all three real adapters registered


def test_unknown_verb_still_exits_64():
    assert _scm("definitely_unknown").returncode == 64


# ------------------------------------------------------------------ sync mechanics

def test_sync_repo_caches_guidance_and_records_state(sync_dir):
    r = guidance_sync.sync_repo("orders-api")
    assert r["files"] == ["CLAUDE.md"]
    assert "AGENTS.md" in r["missing"]
    assert (sync_dir / "orders-api" / "CLAUDE.md").exists()
    st = guidance_sync.load_state()["orders-api"]
    assert st["files"] == ["CLAUDE.md"] and st["synced_at"] > 0
    assert [f["path"] for f in guidance_sync.synced_files("orders-api")]


def test_openhands_qa_guide_is_a_recognised_guidance_file(sync_dir, tmp_path):
    """Interop: a repo already using OpenHands keeps one file both systems honour."""
    assert ".agents/skills/qa-guide.md" in guidance_sync.GUIDANCE_FILES
    # a nested filename must round-trip through the cache (parents created on write)
    import types
    real = guidance_sync.fetch_one
    guidance_sync.fetch_one = lambda repo, fname, ref=None: (
        "QA-GUIDE-BODY" if fname.endswith("qa-guide.md") else None)
    try:
        r = guidance_sync.sync_repo("orders-api")
        assert ".agents/skills/qa-guide.md" in r["files"]
        cached = guidance_sync.synced_files("orders-api")
        assert any("qa-guide.md" in f["path"] and "QA-GUIDE-BODY" in f["text"]
                   for f in cached)
    finally:
        guidance_sync.fetch_one = real


def test_sync_all_covers_ui_service_and_test_repos(sync_dir):
    r = guidance_sync.sync_all()
    kinds = {x["name"]: x["kind"] for x in guidance_sync.known_repos()}
    assert set(kinds.values()) == {"ui", "service", "test"}
    synced = {x["repo"] for x in r["results"] if x["files"]}
    # the demo estate ships guidance for one of each kind
    assert {"web-storefront-ui", "orders-api", "e2e-api-tests-1"} <= synced
    assert r["repos"] == len(kinds)


def test_sync_rejects_unregistered_repo(sync_dir):
    with pytest.raises(SystemExit, match="not a registered repo"):
        guidance_sync.sync_repo("no-such-repo")


def test_sync_preserves_non_ascii_exactly(sync_dir):
    """Adapter output must be decoded as UTF-8, not the Windows locale codepage.
    Reading it as cp1252 turned an em dash into 'â€"' mojibake, and that corruption
    flowed through the cache into AGENTS.md and every LLM phase."""
    guidance_sync.sync_repo("orders-api")
    src = (ROOT / "demo/orders-api/CLAUDE.md").read_bytes()
    cached = (sync_dir / "orders-api" / "CLAUDE.md").read_bytes()
    assert b"\xe2\x80\x94" in src, "fixture should contain a UTF-8 em dash"
    assert b"\xe2\x80\x94" in cached, "em dash was mangled in transit"
    # the classic UTF-8-read-as-cp1252-then-re-encoded signature
    assert b"\xc3\xa2\xe2\x82\xac" not in cached, "mojibake in the synced cache"
    # Compare characters, not line endings: git may check the fixture out as CRLF on
    # Windows while the cache is deliberately written LF-only.
    norm = lambda b: b.decode("utf-8").replace("\r\n", "\n").strip()
    assert norm(src) == norm(cached)


def test_adapter_subprocesses_pin_utf8():
    """Guardrail across the libs that capture adapter/pipeline output: a bare
    text=True decodes with the locale codepage on Windows."""
    for rel in ("engine/lib/guidance_sync.py", "engine/lib/integration_check.py",
                "engine/lib/export_plan.py", "engine/lib/work_queue.py",
                "bin/dashboard_server.py", "bin/repos.py",
                "engine/lib/repo_admin.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        for i, line in enumerate(src.splitlines(), 1):
            if "text=True" in line and "encoding=" not in line:
                # allow it only when the encoding is set on a neighbouring line
                window = "".join(src.splitlines()[max(0, i - 3):i + 2])
                assert "encoding=" in window, f"{rel}:{i} captures text without an encoding"


def test_resync_drops_cache_when_remote_file_disappears(sync_dir, monkeypatch):
    guidance_sync.sync_repo("orders-api")
    assert (sync_dir / "orders-api" / "CLAUDE.md").exists()
    monkeypatch.setattr(guidance_sync, "fetch_one", lambda *a, **k: None)
    r = guidance_sync.sync_repo("orders-api")
    assert r["files"] == [] and not (sync_dir / "orders-api" / "CLAUDE.md").exists()


def test_status_lists_every_repo_with_kind(sync_dir):
    guidance_sync.sync_repo("orders-api")
    st = {x["name"]: x for x in guidance_sync.status()}
    assert st["orders-api"]["files"] == ["CLAUDE.md"]
    assert st["e2e-api-tests-1"]["kind"] == "test"
    assert st["web-storefront-ui"]["kind"] == "ui"


# --------------------------------------------- guidance reaches test generation

def test_precedence_is_freshness_based_not_clone_always_wins(sync_dir, monkeypatch):
    """A fresh clone (the revision under test) wins, but a JUST-SYNCED cache must beat
    a leftover clone from an earlier run — otherwise a manual sync silently no-ops."""
    import os as _os, shutil, time as _time
    monkeypatch.setattr(guidance_sync, "SYNC_DIR", sync_dir)
    monkeypatch.setattr(guidance_sync, "STATE", sync_dir / "state.json")
    repo = "zz-precedence"
    ws = ROOT / "workspace/src" / repo
    ws.mkdir(parents=True, exist_ok=True)
    cache = sync_dir / repo
    cache.mkdir(parents=True, exist_ok=True)
    try:
        # 1. stale clone, newer sync -> the synced copy must win
        (ws / "CLAUDE.md").write_text("STALE-CLONE", encoding="utf-8")
        _os.utime(ws / "CLAUDE.md", (_time.time() - 600, _time.time() - 600))
        (cache / "CLAUDE.md").write_text("JUST-SYNCED", encoding="utf-8")
        f = repo_admin.repo_local_files(repo)[0]
        assert "JUST-SYNCED" in f["text"], "a fresh sync must beat a stale clone"
        # 2. clone re-made by a new run -> the clone wins again
        (ws / "CLAUDE.md").write_text("FRESH-CLONE", encoding="utf-8")
        f = repo_admin.repo_local_files(repo)[0]
        assert "FRESH-CLONE" in f["text"], "the revision under test must win during a run"
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def test_synced_guidance_is_merged_into_agents_md():
    """The estate AGENTS.md is what every LLM phase receives."""
    subprocess.run([sys.executable, str(ROOT / "bin/gen_agents_md.py")], cwd=ROOT,
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "## Repository guidance" in agents
    for repo in ("orders-api", "web-storefront-ui", "e2e-api-tests-1"):
        assert f"### {repo}" in agents, f"{repo} guidance missing from AGENTS.md"
    # actual content, not just headings
    assert "data-testid" in agents           # ui repo guidance
    assert "apiClient" in agents             # e2e test repo guidance


def test_agents_md_is_context_for_pr_and_jira_generation():
    """Both trigger paths must pass AGENTS.md to their generation phases, so synced
    guidance shapes tests for PRs, stories and bug fixes alike."""
    # Authoring phases need estate knowledge; `validate` only repairs against the
    # generate contract, so it is intentionally excluded.
    AUTHORING = {"triage", "analyze", "testplan", "testdata", "generate"}
    pipe = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    seen = set()
    for line in pipe.splitlines():
        parts = line.strip().split()
        if len(parts) > 1 and parts[0] == "PHASE" and parts[1] in AUTHORING:
            seen.add(parts[1])
            assert "AGENTS.md" in line, f"phase without estate knowledge: {line.strip()}"
    assert AUTHORING <= seen, f"phases not exercised: {AUTHORING - seen}"


def test_cli_sync_and_status(tmp_path):
    env = {**os.environ, "AIQE_MOCK": "1", "AIQE_SYNC_DIR": str(tmp_path / "s")}
    r = subprocess.run([sys.executable, str(ROOT / "bin/repos.py"), "sync", "orders-api"],
                       cwd=ROOT, capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    assert "CLAUDE.md" in r.stdout and "AGENTS.md regenerated" in r.stdout
    r = subprocess.run([sys.executable, str(ROOT / "bin/repos.py"), "sync-status"],
                       cwd=ROOT, capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0 and "orders-api" in r.stdout


def test_dashboard_exposes_sync_controls():
    subprocess.run([sys.executable, str(ROOT / "bin/dashboard.py")], cwd=ROOT,
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)
    page = (ROOT / "reports/dashboard.html").read_text(encoding="utf-8")
    assert 'id="sync-all"' in page and 'id="sync-one"' in page
    assert "/api/repos/sync" in page

"""P1 regression pins for clone credentials and repeatable workspaces."""
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import checkout_workspace
import work_queue


@pytest.mark.parametrize("name", ["github.sh", "bitbucket.sh", "stash.sh"])
def test_clone_urls_and_arguments_never_contain_tokens(name):
    source = (ROOT / "adapters/scm" / name).read_text()
    assert "${GITHUB_TOKEN}@" not in source
    assert "${BITBUCKET_TOKEN}@" not in source
    assert "${STASH_TOKEN}@" not in source
    assert "credential.helper" in source
    assert "GIT_TERMINAL_PROMPT=0" in source


def test_credential_helper_is_persisted_without_a_secret():
    for name in ("github.sh", "bitbucket.sh", "stash.sh"):
        source = (ROOT / "adapters/scm" / name).read_text()
        assert 'config credential.helper "$HELPER"' in source
    helper = (ROOT / "adapters/scm/git-credential-aiqe.sh").read_text()
    assert "password=%s" in helper


@pytest.mark.parametrize("provider,host,token_var,username,extra", [
    ("github", "github.com", "GITHUB_TOKEN", "x-access-token", {}),
    ("bitbucket", "bitbucket.org", "BITBUCKET_TOKEN", "x-token-auth", {}),
    ("stash", "stash.example.test:8443", "STASH_TOKEN", "x-token-auth",
     {"STASH_URL": "https://stash.example.test:8443"}),
])
def test_credential_helper_returns_secret_only_over_credential_protocol(
    provider, host, token_var, username, extra
):
    token = f"sentinel-{provider}-secret"
    env = os.environ.copy()
    env.update(extra)
    env[token_var] = token
    helper = ROOT / "adapters/scm/git-credential-aiqe.sh"
    result = subprocess.run(
        [work_queue.bash_exe(), str(helper), provider, "get"],
        input=f"protocol=https\nhost={host}\n\n", text=True,
        capture_output=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"username={username}\npassword={token}\n"
    assert token not in result.stderr


def test_credential_helper_refuses_wrong_host_and_store_operation():
    env = {**os.environ, "GITHUB_TOKEN": "sentinel-secret"}
    helper = ROOT / "adapters/scm/git-credential-aiqe.sh"
    wrong = subprocess.run(
        [work_queue.bash_exe(), str(helper), "github", "get"],
        input="protocol=https\nhost=evil.example\n\n", text=True,
        capture_output=True, env=env,
    )
    store = subprocess.run(
        [work_queue.bash_exe(), str(helper), "github", "store"],
        input="protocol=https\nhost=github.com\npassword=sentinel-secret\n\n",
        text=True, capture_output=True, env=env,
    )
    assert wrong.returncode == 0 and wrong.stdout == ""
    assert store.returncode == 0 and store.stdout == ""


def test_prepare_removes_stale_checkout():
    target = ROOT / "workspace/src/zz-repeatable-clone-test"
    try:
        target.mkdir(parents=True, exist_ok=True)
        (target / "stale.txt").write_text("stale")
        prepared = checkout_workspace.prepare("src", "zz-repeatable-clone-test")
        assert prepared == target
        assert prepared.parent.is_dir()
        assert not prepared.exists()
    finally:
        if target.exists():
            import shutil
            shutil.rmtree(target, ignore_errors=True)


@pytest.mark.parametrize("kind,repo", [
    ("other", "repo"), ("src", "../repo"), ("tests", ".."),
    ("src", "repo/name"), ("tests", ""),
])
def test_prepare_refuses_broad_or_escaping_targets(kind, repo):
    with pytest.raises(ValueError):
        checkout_workspace.checkout_path(kind, repo)


def test_pipeline_prepares_both_clone_roots():
    source = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert 'checkout_workspace.py prepare src "$r"' in source
    assert 'checkout_workspace.py prepare tests "$t"' in source

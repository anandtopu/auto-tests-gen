"""bin/onboard.sh — the documented way to add a repo to a real estate.

Nothing had ever run it. Running it once found five defects, all silent, all in
the direction that corrupts the registry rather than refusing:

  * `type: frontendd` accepted and written. Type decides contract-vs-route-table
    and consumer fan-out, so a typo means the repo never routes to anyone —
    unrouted work is the one failure this platform cannot see from the inside.
  * `layer: apii` accepted AND silently given the UI layout, because the layout
    was `'suites/' if layer == 'api' else 'tests/'`. The platform would then
    look for specs in a directory the repo does not use.
  * a name of `../../evil` written straight into the registry, where repo names
    are interpolated into paths (knowledge/repos/<name>.md, workspace/tests/<n>).
  * missing arguments produced a raw IndexError traceback.
  * the registry path was hardcoded, so under AIQE_REGISTRY_FILE relocation
    onboarding wrote where nothing reads.

The cause was one thing: onboard.sh hand-rolled a SECOND definition of "how a
repo gets registered" beside repo_admin, and it was the copy without the checks.
The fix is delegation, so these pin the refusals AND the invariant that keeps
them — that this script never writes the registry itself.

Isolated via AIQE_REGISTRY_FILE / AIQE_AGENTS_FILE, and the working estate is
asserted byte-unchanged (the tests/bootstrap-smoke.sh precedent).
"""
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import work_queue  # noqa: E402

REAL_REGISTRY = ROOT / "registry/repo-registry.yaml"


@pytest.fixture
def onboard(tmp_path):
    """Run bin/onboard.sh against a COPY of the registry."""
    reg = tmp_path / "repo-registry.yaml"
    shutil.copy(REAL_REGISTRY, reg)
    env = dict(os.environ,
               AIQE_REGISTRY_FILE=str(reg),
               AIQE_AGENTS_FILE=str(tmp_path / "AGENTS.md"),
               AIQE_KNOWLEDGE_DIR=str(tmp_path / "knowledge"))

    def run(*args):
        return subprocess.run([work_queue.bash_exe(), "bin/onboard.sh", *args],
                              cwd=ROOT, env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=300)
    run.registry = reg
    return run


def _names(reg_path):
    import yaml
    reg = yaml.safe_load(reg_path.read_text(encoding="utf-8"))
    return {r["name"] for r in reg["source_repositories"] + reg["test_repositories"]}


@pytest.mark.parametrize("args,expect", [
    (("source", "zz-bad", "frontendd", "github", "http://x"), "ui|service"),
    (("test", "zz-bad", "apii", "github", "http://x"), "api|ui"),
    (("test", "zz-bad", "api", "gitlab", "http://x"), "scm must be one of"),
    (("test", "../../evil", "api", "github", "http://x"), "invalid repo name"),
])
def test_invalid_input_is_refused_and_says_what_is_valid(onboard, args, expect):
    """Each of these was accepted and written before."""
    before = _names(onboard.registry)
    r = onboard(*args)
    assert r.returncode != 0, f"accepted invalid input: {args}"
    assert expect in (r.stdout + r.stderr), \
        f"the refusal does not name the valid values: {(r.stdout + r.stderr)[:200]}"
    assert _names(onboard.registry) == before, "a refused onboarding still wrote"


def test_missing_arguments_print_usage_not_a_traceback(onboard):
    r = onboard("source", "zz-bad")
    assert r.returncode == 64, "arity errors should use the usage exit code"
    out = r.stdout + r.stderr
    assert "usage:" in out and "Traceback" not in out


def test_an_unknown_kind_is_refused(onboard):
    r = onboard("banana", "zz-bad", "api", "github", "http://x")
    assert r.returncode == 64
    assert "unknown kind" in (r.stdout + r.stderr)


def test_onboarding_a_real_repo_exits_zero(onboard):
    """`[ -d demo/$NAME ] && ...` was the last statement, so for any repo without
    a demo directory — i.e. every real one — a fully successful onboarding
    exited 1. A CI wrapper reading the exit code would call it a failure.

    It was latent: trailing pytest/gen_agents_md lines used to overwrite the
    status, which is exactly why removing them exposed it."""
    r = onboard("test", "zz-onboard-real", "api", "github", "http://example/zz")
    assert r.returncode == 0, f"success exited {r.returncode}: {r.stdout}{r.stderr}"
    assert "zz-onboard-real" in _names(onboard.registry)


def test_a_valid_entry_keeps_the_shape_it_had_before_delegation(onboard):
    """Delegation must not quietly change what an onboarded repo looks like."""
    import yaml
    assert onboard("source", "zz-onboard-src", "backend", "github",
                   "http://x", "dom1,dom2", "openapi/api.yaml").returncode == 0
    assert onboard("test", "zz-onboard-api", "api", "github",
                   "http://y").returncode == 0
    reg = yaml.safe_load(onboard.registry.read_text(encoding="utf-8"))
    src = next(r for r in reg["source_repositories"] if r["name"] == "zz-onboard-src")
    assert src["type"] == "backend"
    assert src["domains"] == ["dom1", "dom2"]
    assert src["testable_paths"] == ["src/**", "app/**", "openapi/**"]
    assert src["contract"] == "openapi/api.yaml", \
        "a backend's file argument must still land in `contract`"
    tst = next(r for r in reg["test_repositories"] if r["name"] == "zz-onboard-api")
    assert tst["layer"] == "api"
    assert tst["framework"] == "playwright", "the default framework changed"
    assert tst["layout"]["specs"] == "suites/", \
        "an api repo must still get the api layout"


def test_re_onboarding_is_idempotent(onboard):
    assert onboard("test", "zz-idem", "api", "github", "http://x").returncode == 0
    r = onboard("test", "zz-idem", "api", "github", "http://x")
    assert r.returncode == 0, "a second run must not fail"
    assert "already registered" in r.stdout


def test_onboarding_honours_a_relocated_registry(onboard):
    """R12: six modules resolve the registry through registry_file(); this script
    hardcoded it, so under relocation it wrote where nothing reads and the repo
    appeared not to exist."""
    before = REAL_REGISTRY.read_bytes()
    assert onboard("test", "zz-reloc", "api", "github", "http://x").returncode == 0
    assert "zz-reloc" in _names(onboard.registry), "the relocated file was not written"
    assert REAL_REGISTRY.read_bytes() == before, \
        "onboarding wrote the image-path registry despite AIQE_REGISTRY_FILE"


def test_the_script_never_writes_the_registry_itself():
    """The invariant behind every refusal above. A future edit that appends YAML
    here again would restore all five defects at once, and each of them is
    silent."""
    src = (ROOT / "bin/onboard.sh").read_text(encoding="utf-8")
    body = src.split("<< 'PY'", 1)[1]
    assert "repo_admin.upsert_app" in body and "repo_admin.upsert_test" in body, \
        "onboarding no longer goes through the validated path"
    for writer in ("yaml.safe_dump", "os.replace", "reg[sect].append", "safe_load"):
        assert writer not in body, \
            f"onboard.sh writes/parses the registry itself again ({writer})"

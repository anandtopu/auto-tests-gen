"""Pins for the mutable-state path resolver (R12).

The load-bearing property is the BORING one: with no env set, every path is
exactly what callers hard-coded before this module existed. A resolver that
quietly moved a directory would relocate somebody's plans or catalog mappings
on upgrade, which is data loss dressed up as a refactor.
"""
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine" / "lib"))
import app_paths  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """These knobs leak between tests otherwise — and a leaked AIQE_STATE_DIR
    would make the defaults test pass against a redirected root."""
    for k in list(os.environ):
        if k.startswith("AIQE_"):
            monkeypatch.delenv(k, raising=False)


def test_defaults_are_the_checkout_unchanged():
    d = app_paths.describe()
    assert d["state_root"] == str(ROOT)
    assert d["catalog"] == str(ROOT / "catalog")
    assert d["registry_file"] == str(ROOT / "registry" / "repo-registry.yaml")
    assert d["testplans"] == str(ROOT / "testplans")
    assert d["testdata"] == str(ROOT / "testdata")
    assert d["specs"] == str(ROOT / "specs")
    assert d["agents_file"] == str(ROOT / "AGENTS.md")
    assert d["skills"] == str(ROOT / ".agents" / "skills")
    assert d["knowledge"] == str(ROOT / "knowledge")


def test_state_dir_redirects_every_mutable_path(monkeypatch, tmp_path):
    monkeypatch.setenv("AIQE_STATE_DIR", str(tmp_path))
    d = app_paths.describe()
    for key, value in d.items():
        assert value.startswith(str(tmp_path)), f"{key} escaped the state root: {value}"


def test_specific_knob_outranks_the_state_dir(monkeypatch, tmp_path):
    """Test isolation must survive a container that redirects everything —
    the state adversarial suite drives the per-path knobs directly."""
    monkeypatch.setenv("AIQE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AIQE_SPEC_DIR", str(tmp_path / "just-specs"))
    assert app_paths.specs_dir() == tmp_path / "just-specs"
    assert app_paths.testplans_dir() == tmp_path / "state" / "testplans"


def test_empty_env_is_treated_as_unset(monkeypatch):
    """A container that sets AIQE_STATE_DIR="" must not resolve to the
    filesystem root — an empty value is absence, not a path."""
    monkeypatch.setenv("AIQE_STATE_DIR", "")
    monkeypatch.setenv("AIQE_SPEC_DIR", "   ")
    assert app_paths.state_root() == ROOT
    assert app_paths.specs_dir() == ROOT / "specs"


def test_seeded_set_excludes_purely_generated_paths():
    """Seeding a generated path from the image would restore a stale AGENTS.md
    or an old plan over an empty volume and call it state."""
    assert "catalog" in app_paths.SEEDED
    assert "registry/repo-registry.yaml" in app_paths.SEEDED
    assert "knowledge" in app_paths.SEEDED
    for generated in ("AGENTS.md", "testplans", "testdata", "specs"):
        assert generated not in app_paths.SEEDED


def test_code_and_config_are_never_relocated():
    """The whole reason state is relocated rather than volume-mounted: these
    must travel with the IMAGE, so no resolver may point at them. If one ever
    does, an upgrade ships logic that never runs."""
    src = (ROOT / "engine" / "lib" / "app_paths.py").read_text(encoding="utf-8")
    body = src.split('"""', 2)[-1]          # skip the module docstring
    for frozen in ("org-config.yaml", "bootstrap", "schema.json"):
        assert f'"{frozen}"' not in body and f"/{frozen}" not in body.replace(
            "# ", ""), f"{frozen} is code/config and must not be state-relocated"


def test_cli_emits_the_mapping():
    """`python3 engine/lib/app_paths.py` is how an operator checks the mapping
    inside a container without a shell in the image."""
    r = subprocess.run([sys.executable, str(ROOT / "engine" / "lib" / "app_paths.py")],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    assert "state_root" in r.stdout and "catalog" in r.stdout


def test_unwritable_scratch_is_not_reported_as_lock_contention():
    """R12: `mkdir` cannot distinguish EROFS from EEXIST, so a read-only root
    reported PIPELINE_BUSY after spinning the full 120s retry loop — an
    operator would go looking for a concurrent run that does not exist. The
    up-front writability check must precede the retry loop, or the fast, honest
    message is unreachable."""
    src = (ROOT / "engine" / "pipeline.sh").read_text(encoding="utf-8")
    guard = src.index("PIPELINE_UNWRITABLE")
    loop = src.index("for i in $(seq 1 120)")
    assert guard < loop, "the writability check must run BEFORE the retry loop"
    assert "PIPELINE_BUSY" in src, "genuine contention must still report BUSY"
    assert "NOT lock contention" in src, "the message must say what it is not"

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
import app_paths


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
    assert d["artifacts"] == str(ROOT / "reports" / "agent-artifacts")


def test_state_dir_redirects_every_mutable_path(monkeypatch, tmp_path):
    monkeypatch.setenv("AIQE_STATE_DIR", str(tmp_path))
    d = app_paths.describe()
    for key, value in d.items():
        assert value.startswith(str(tmp_path)), f"{key} escaped the state root: {value}"


def test_resolve_rel_with_a_caller_root_keeps_every_mutable_top_on_state(
        monkeypatch, tmp_path):
    """The generic resolver must pass caller roots by keyword. knowledge_dir's
    first positional argument is a subdirectory, unlike the other resolvers."""
    state = tmp_path / "state"
    checkout = tmp_path / "checkout"
    monkeypatch.setenv("AIQE_STATE_DIR", str(state))
    expected = {
        "catalog/team.jsonl": state / "catalog/team.jsonl",
        "knowledge/curated/team.md": state / "knowledge/curated/team.md",
        "testplans/PROJ-1.md": state / "testplans/PROJ-1.md",
        "testdata/PROJ-1/data.json": state / "testdata/PROJ-1/data.json",
        "specs/PROJ-1/spec.json": state / "specs/PROJ-1/spec.json",
    }
    assert {rel: app_paths.resolve_rel(rel, checkout)
            for rel in expected} == expected


def test_repository_team_notes_follow_the_state_directory(tmp_path):
    """Repository notes are durable estate data, not checkout-local code."""
    env = dict(os.environ, AIQE_STATE_DIR=str(tmp_path))
    code = (
        "import pathlib,sys; "
        f"sys.path.insert(0, {str(ROOT / 'engine/lib')!r}); "
        "import repo_admin; print(repo_admin.NOTES_DIR)"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            check=True, capture_output=True, text=True)
    assert pathlib.Path(result.stdout.strip()) == tmp_path / "knowledge" / "repos"


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
    joined = " ".join(app_paths.SEEDED)
    assert "catalog/*.jsonl" in app_paths.SEEDED
    assert "registry/repo-registry.yaml" in app_paths.SEEDED
    assert "knowledge/curated" in app_paths.SEEDED
    # No generated path may be seeded — a stale plan restored over an empty
    # volume would look like state.
    for generated in ("AGENTS.md", "testplans", "testdata", "specs"):
        assert generated not in joined, f"{generated} is generated, never seeded"
    # And no CODE or CONFIG may be seeded: once the volume owns these, an image
    # upgrade ships logic that never runs. This is why the entries are narrower
    # than the directories that contain them.
    for frozen in ("org-config.yaml", "bootstrap", "schema.json", "registry/tests"):
        assert frozen not in joined, f"{frozen} is code/config and must ship in the image"
    # Seeding whole `catalog/` or `registry/` would drag exactly those in.
    assert "catalog" not in app_paths.SEEDED and "registry" not in app_paths.SEEDED


def test_the_seed_plan_carries_data_only():
    """The assertion above reads the SEEDED STRINGS, and they looked right —
    but `catalog/review` and `knowledge/facts` are DIRECTORIES whose contents
    are not uniformly data. A real first boot copied
    `catalog/review/export_review_queue.py`, its `__pycache__/*.pyc`, and the
    whole `knowledge/facts/derived/` tier into the state volume: code and
    regenerated data, both forbidden by the rule the strings appeared to obey.

    `state_bundle` already paid for this exact file once — CLAUDE.md records
    that excluding by directory missed `catalog/review/export_review_queue.py`.
    So this pins what a boot actually COPIES, not how the list reads.
    """
    plan = app_paths.seed_plan()
    assert plan, "the seed plan is empty; a first boot would seed nothing"
    bad = [r for r in plan
           if r.endswith((".py", ".pyc", ".pyo", ".sh"))
           or "__pycache__" in r or "/derived/" in r]
    assert not bad, f"code or regenerated data in the seed plan: {bad}"
    # The data that MUST arrive, or the deployment routes nothing.
    assert "registry/repo-registry.yaml" in plan
    assert any(r.startswith("catalog/") and r.endswith(".jsonl") for r in plan)
    # And nothing generated, whatever the SEEDED list grows to later.
    for gen in ("testplans/", "testdata/", "specs/", "AGENTS.md"):
        assert not any(r.startswith(gen) for r in plan), \
            f"{gen} is generated — seeding it restores stale output as state"


def test_the_seed_plan_never_leaves_the_image_root(tmp_path):
    """Every entry is repo-relative and stays inside the root: the entrypoint
    interpolates them straight into `$STATE/$rel`, so an absolute path or a
    `..` would write outside the state volume."""
    for rel in app_paths.seed_plan():
        assert not rel.startswith(("/", "\\")) and ".." not in rel.split("/"), rel
        assert ":" not in rel, f"{rel} looks absolute on Windows"


def test_code_and_config_are_never_relocated(monkeypatch, tmp_path):
    """Code and config must travel with the IMAGE, so no resolver may move them.
    If one ever does, an image upgrade ships logic that never runs — silently.

    Asserted BEHAVIOURALLY. The first version of this pin scanned the module
    source for "bootstrap"/"org-config.yaml" and broke the moment a comment
    *explaining* why those are excluded was added — a pin that fails on prose
    while the code is correct teaches people to delete pins.
    """
    monkeypatch.setenv("AIQE_STATE_DIR", str(tmp_path))
    for frozen in ("registry/org-config.yaml", "catalog/bootstrap/correlate.py",
                   "catalog/schema.json", "registry/tests/test_routing_golden.py",
                   "engine/lib/budget.py", "prompts/critic.md"):
        got = app_paths.resolve_rel(frozen)
        assert got == ROOT / frozen, (
            f"{frozen} is code/config and must resolve inside the image, got {got}")


def test_cli_emits_the_mapping():
    """`python3 engine/lib/app_paths.py` is how an operator checks the mapping
    inside a container without a shell in the image."""
    r = subprocess.run([sys.executable, str(ROOT / "engine" / "lib" / "app_paths.py")],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       check=False)
    assert r.returncode == 0, r.stderr
    assert "state_root" in r.stdout and "catalog" in r.stdout


def test_run_diff_path_is_confined_to_archived_diffs(tmp_path):
    root = tmp_path / "checkout"
    good = root / "reports" / "runs" / "123-suite.diff"
    good.parent.mkdir(parents=True)
    good.write_text("safe", encoding="utf-8")

    assert app_paths.run_diff_path("reports/runs/123-suite.diff", root) == good.resolve()
    assert app_paths.run_diff_path("../../outside.diff", root) is None
    assert app_paths.run_diff_path(str(tmp_path / "outside.diff"), root) is None
    assert app_paths.run_diff_path("reports/runs/record.json", root) is None
    assert app_paths.run_diff_path(None, root) is None


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

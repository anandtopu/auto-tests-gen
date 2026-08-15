"""A misspelt registry key changes ROUTING in silence.

The sibling of test_org_config_keys, aimed at the file whose typos hit the one
failure this platform cannot see from the inside: work that was never routed.

MEASURED against the shipped registry:

  * `covers:` mistyped as `cover:` -> test_repos_for(reg, "orders-api") == []
    Every run for that repo resolves no test repo and generates nothing.
  * `testable_paths:` mistyped -> resolve falls back to ["**"], so every
    changed file counts as testable.

Opposite directions, both silent, and `repo_admin` validating on WRITE does
not help a tracked YAML file that people edit by hand and that merges bring
together.

THE PIN RUNS ONE DIRECTION ONLY, deliberately, and that is the difference from
the org-config sibling. There, a declared section absent from the shipped file
is an allow-list excusing a ghost. Here the same rule would be WRONG:
`stash_project` is a legitimate optional field this demo estate does not use,
and deriving the known set from one sample would warn every Stash deployment
about a key the platform itself writes. So the schema comes from
`repo_admin.upsert_app`/`upsert_test` — the functions that create entries —
and only the shipped-keys-must-be-known direction is asserted.
"""
import inspect
import os
import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import registry as registry_lib                                # noqa: E402
import registry_key_check as rkc                               # noqa: E402
import repo_admin                                              # noqa: E402

SHIPPED = yaml.safe_load(
    (ROOT / "registry/repo-registry.yaml").read_text(encoding="utf-8")) or {}


def _mistyped(section, old, new):
    import copy
    reg = copy.deepcopy(SHIPPED)
    entry = next(e for e in reg[section] if old in e)
    entry[new] = entry.pop(old)
    return reg, entry["name"]


def test_the_shipped_registry_is_clean():
    """The over-fix guard: this project's own registry must produce NO
    warning, or the check trains operators to ignore it."""
    assert rkc.unknown_keys(SHIPPED) == []
    assert rkc.report(SHIPPED) == ""


def test_every_shipped_key_is_declared_known():
    """One direction only — see the module docstring for why the reverse
    would be wrong here."""
    unknown = rkc.unknown_keys(SHIPPED)
    assert not unknown, f"{unknown} are configured but not declared known"


def test_the_known_set_matches_what_the_writer_can_write():
    """The schema's source of truth is `repo_admin`, so a field it learns to
    write must be declared here or every user of that field gets warned."""
    app = set(inspect.signature(repo_admin.upsert_app).parameters) - {"kind"}
    test = set(inspect.signature(repo_admin.upsert_test).parameters) - {
        "specs", "fixtures"}          # these land inside `layout`, not top level
    assert app <= rkc.APP_KEYS, f"upsert_app writes {app - rkc.APP_KEYS}"
    assert test <= rkc.TEST_KEYS, f"upsert_test writes {test - rkc.TEST_KEYS}"


def test_a_mistyped_coverage_key_is_named_with_its_repo():
    """THE DEFECT, direction one: routing silently resolves nothing."""
    reg, who = _mistyped("test_repositories", "covers", "cover")
    assert registry_lib.test_repos_for(reg, "orders-api") == [], \
        "the fixture no longer reproduces the routing loss"
    assert rkc.unknown_keys(reg) == [f"{who}.cover"]


def test_a_mistyped_testable_paths_key_is_named(monkeypatch):
    """Direction two: everything silently becomes testable."""
    reg, who = _mistyped("source_repositories", "testable_paths", "testable_path")
    src = next(r for r in reg["source_repositories"] if r["name"] == who)
    assert src.get("testable_paths", ["**"]) == ["**"], \
        "the fixture no longer reproduces the widened match"
    assert rkc.unknown_keys(reg) == [f"{who}.testable_path"]


def test_the_message_names_what_a_typo_actually_costs():
    reg, _ = _mistyped("test_repositories", "covers", "cover")
    msg = rkc.report(reg)
    assert "IGNORED" in msg
    assert "generate nothing" in msg, msg
    assert "repo-registry.yaml" in msg, msg


def test_an_unnamed_entry_still_gets_reported():
    """A registry damaged enough to lose a name is exactly when the operator
    needs the other keys named, not a crash."""
    reg = {"source_repositories": [{"nonsense": 1}]}
    assert rkc.unknown_keys(reg) == ["<unnamed>.nonsense"]


def test_a_malformed_registry_is_not_a_pile_of_unknown_keys():
    """Different failure, different fix — and the loaders already treat a
    non-mapping as absent."""
    for bad in (None, [], "source_repositories", 7,
                {"source_repositories": "not a list"},
                {"source_repositories": ["not an entry"]}):
        assert rkc.unknown_keys(bad) == []
        assert rkc.report(bad) == ""


def test_it_is_wired_into_a_command_an_operator_runs():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "registry_key_check.py" in makefile


def test_it_never_exits_non_zero(tmp_path):
    """A configuration complaint must not break the command an operator runs
    to diagnose the configuration."""
    bad = tmp_path / "repo-registry.yaml"
    bad.write_text("source_repositories: [unclosed\n  - :::", encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/registry_key_check.py")],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL,
                       env={**os.environ, "AIQE_REGISTRY_FILE": str(bad)})
    assert r.returncode == 0, r.stderr[-800:]
    assert "could not be read" in r.stdout, r.stdout

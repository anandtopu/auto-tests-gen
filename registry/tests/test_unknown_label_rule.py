"""A routing key nothing reads must not pass for a restriction that applied.

FOURTH direction of the documented-thing-that-does-not-exist family, and the
worst-consequence one. A missing `make` target prints `No rule to make target`.
A missing CLI flag exits 2. An unread env knob does nothing. An unread ROUTING
key does nothing AND SILENTLY WIDENS what gets generated.

MEASURED, by configuring the label map exactly as `docs/architecture.md` showed
it (`api-only: {restrict_test_repos: [e2e-api-tests]}`):

    Checkout + api-only  ->  test_repos: [e2e-api-tests-1, e2e-ui-tests-1]

The label restricted NOTHING. `restrict_test_repos` appears nowhere outside that
document; the resolver has only ever read `restrict_layers`. So an operator
following the design doc believed they had narrowed the run to the API layer
while UI tests were generated anyway -- and nothing said so, because "no known
key matched" and "no restriction was asked for" were the same silence.

The doc is corrected, and the resolver now REPORTS an unrecognised key instead
of ignoring it -- which protects against any typo, not just the one that was
documented. That is the same reasoning as constitution C12: configuration is
explicit, and there is no silent fallback.
"""
import json
import pathlib
import subprocess
import sys

import pytest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _registry_with(label_map, tmp_path):
    reg = yaml.safe_load((ROOT / "registry/repo-registry.yaml")
                         .read_text(encoding="utf-8"))
    reg["routing_hints"]["jira_label_map"] = label_map
    f = tmp_path / "reg.yaml"
    f.write_text(yaml.safe_dump(reg), encoding="utf-8")
    return f


def _resolve(registry, labels, components="Checkout"):
    import os
    r = subprocess.run([sys.executable, str(ROOT / "engine/phases/resolve.py"),
                        "jira", "PROJ-1", "--components", components,
                        "--labels", labels],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, timeout=120,
                       env=dict(os.environ, AIQE_REGISTRY_FILE=str(registry)))
    assert r.returncode == 0, r.stderr[-400:]
    return json.loads(r.stdout), r.stderr


def test_an_unreadable_rule_key_is_reported(tmp_path):
    """The measured case: the configuration the design doc used to show."""
    reg = _registry_with(
        {"api-only": {"restrict_test_repos": ["e2e-api-tests-1"]}}, tmp_path)
    out, err = _resolve(reg, "api-only")
    assert out["unknown_label_rules"] == ["api-only.restrict_test_repos"]
    assert "applied NOTHING" in err, \
        "an operator running the phase is not told the label did nothing"
    assert "restrict_layers" in err, "the message does not name the known key"


def test_the_restriction_really_did_not_apply(tmp_path):
    """Pinned as a fact about ROUTING, not only about the message: this is why
    the silence mattered."""
    reg = _registry_with(
        {"api-only": {"restrict_test_repos": ["e2e-api-tests-1"]}}, tmp_path)
    out, _ = _resolve(reg, "api-only")
    assert len(out["test_repos"]) > 1, \
        "the fixture no longer demonstrates the widening it exists to show"


def test_a_correct_rule_is_silent_and_restricts(tmp_path):
    """The over-fix guard: a warning that fires on a correct configuration is
    one operators learn to ignore."""
    reg = _registry_with({"api-only": {"restrict_layers": ["api"]}}, tmp_path)
    out, err = _resolve(reg, "api-only")
    assert out["unknown_label_rules"] == []
    assert "applied NOTHING" not in err
    assert out["test_repos"] == ["e2e-api-tests-1"], \
        "the correct key stopped restricting"


def test_an_unknown_key_beside_a_known_one_is_still_reported(tmp_path):
    """The known key working must not mask the typo sitting next to it."""
    reg = _registry_with(
        {"api-only": {"restrict_layers": ["api"], "restrict_test_repos": ["x"]}},
        tmp_path)
    out, _ = _resolve(reg, "api-only")
    assert out["unknown_label_rules"] == ["api-only.restrict_test_repos"]
    assert out["test_repos"] == ["e2e-api-tests-1"], \
        "the known key must still apply while the unknown one is reported"


@pytest.mark.parametrize("rule", [
    ["not", "a", "mapping"],   # iterable: set() works, so this alone proves little
    5,                         # scalar: set(5) raises -- what the guard is FOR
    "restrict_layers",         # string: iterable, and its characters are not keys
    None,
])
def test_a_malformed_rule_does_not_break_routing(rule, tmp_path):
    """A rule that is not a mapping is bad config, not a reason to crash the
    phase that decides where tests go.

    The scalar case is the one that matters and the one I first left out: with
    only a list here, a mutation removing the isinstance guard SURVIVED, because
    `set(["a","b"])` is perfectly happy. `set(5)` is what raises.
    """
    reg = _registry_with({"api-only": rule}, tmp_path)
    out, _ = _resolve(reg, "api-only")
    assert out["test_repos"], "a malformed label rule took down routing"


def test_both_paths_carry_the_field(tmp_path):
    """So no consumer learns the key only sometimes exists."""
    f = tmp_path / "changed.txt"
    f.write_text("openapi/orders.yaml\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "engine/phases/resolve.py"),
                        "pr", "orders-api", "--changed-files", str(f)],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, timeout=120)
    assert json.loads(r.stdout)["unknown_label_rules"] == []


def test_the_design_doc_no_longer_teaches_the_key_nothing_reads():
    """The doc is what caused it, so the doc is pinned too."""
    src = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
    assert "restrict_test_repos" not in src, \
        "architecture.md again documents a label key the resolver never reads"
    assert "restrict_layers" in src

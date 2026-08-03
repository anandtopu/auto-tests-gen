"""Spec-satisfaction gate pins (SDD story 3.2) — the adversarial matrix.

covered / waived / expired-waiver / missing / forged-id / exempt, across
off|warn|strict. The check adds a verdict, never a writer; its own
malfunction (unreadable state) must exempt, not break a gate.
"""
import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

_spec = importlib.util.spec_from_file_location(
    "spec_check", ROOT / "engine/gate/spec_check.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


SPEC = {"scenarios": [
    {"id": "K-1-S1", "title": "a", "layer": "api", "target_repo": "repo-x",
     "verification": ["v1"]},
    {"id": "K-1-S2", "title": "b", "layer": "api", "target_repo": "repo-x"},
    {"id": "K-1-S3", "title": "c", "layer": "api", "target_repo": "OTHER"}],
    "open_questions": []}


@pytest.fixture
def estate(tmp_path, monkeypatch):
    import plan_state as ps
    import spec_store as ss
    monkeypatch.setattr(ps, "DIR", tmp_path / "plans")
    monkeypatch.setattr(ps, "FILE", tmp_path / "plans/state.json")
    monkeypatch.setattr(ps, "PLAN_DIR", tmp_path / "testplans")
    monkeypatch.setattr(ss, "SPEC_DIR", tmp_path / "specs")
    monkeypatch.setattr(sc, "ROOT", tmp_path)
    (tmp_path / "plans").mkdir()
    (tmp_path / "testplans").mkdir()
    (tmp_path / "out").mkdir()

    def seed(approved=True, tests=None, waivers=None):
        import yaml
        sp = ss.spec_path("K-1")
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(yaml.safe_dump({"key": "K-1", **SPEC}), encoding="utf-8")
        ps.plan_path("K-1").write_text("# p\n", encoding="utf-8")
        ps.record_plan("K-1", {"scenarios": SPEC["scenarios"]})
        if approved:
            ps.set_status("K-1", "approved", "lead")
        (tmp_path / "out/generate.contract.json").write_text(
            json.dumps({"tests": tests or []}), encoding="utf-8")
        if waivers:
            ss.waivers_path("K-1").write_text(
                yaml.safe_dump({"waivers": waivers}), encoding="utf-8")
    return seed, tmp_path


def test_fully_covered_passes(estate):
    seed, tmp = estate
    seed(tests=[{"file": "a.spec.js", "scenario_id": "K-1-S1"},
                {"file": "b.spec.js", "scenario_id": "K-1-S2"}])
    findings, exempt = sc.check("K-1", "repo-x", ["a.spec.js", "b.spec.js"])
    assert not exempt and findings == []


def test_uncovered_scenario_is_named_with_the_fix(estate):
    seed, tmp = estate
    seed(tests=[{"file": "a.spec.js", "scenario_id": "K-1-S1"}])
    findings, _ = sc.check("K-1", "repo-x", ["a.spec.js"])
    assert len(findings) == 1
    assert "UNCOVERED_SCENARIO: K-1-S2" in findings[0]
    assert "waivers.yaml" in findings[0], "the finding names the escape hatch"


def test_other_repos_scenarios_are_not_this_gates_business(estate):
    """K-1-S3 targets OTHER — this repo's gate must not demand it."""
    seed, tmp = estate
    seed(tests=[{"file": "a.spec.js", "scenario_id": "K-1-S1"},
                {"file": "b.spec.js", "scenario_id": "K-1-S2"}])
    findings, _ = sc.check("K-1", "repo-x", [])
    assert not any("K-1-S3" in f for f in findings)


def test_waiver_covers_expired_does_not(estate):
    seed, tmp = estate
    seed(tests=[{"file": "a.spec.js", "scenario_id": "K-1-S1"}],
         waivers=[{"scenario": "K-1-S2", "reason": "upstream", "by": "lead",
                   "expires": "2099-01-01"}])
    findings, _ = sc.check("K-1", "repo-x", [])
    assert findings == [], "a live waiver satisfies the contract"
    seed(tests=[{"file": "a.spec.js", "scenario_id": "K-1-S1"}],
         waivers=[{"scenario": "K-1-S2", "reason": "old", "by": "lead",
                   "expires": "2020-01-01"}])
    findings, _ = sc.check("K-1", "repo-x", [])
    assert len(findings) == 1 and "EXPIRED_WAIVER: K-1-S2" in findings[0]


def test_forged_scenario_id_is_a_violation(estate):
    """A test claiming an id outside the SIGNED spec — the attack the check
    exists for."""
    seed, tmp = estate
    seed(tests=[{"file": "evil.spec.js", "scenario_id": "K-1-S99"},
                {"file": "a.spec.js", "scenario_id": "K-1-S1"},
                {"file": "b.spec.js", "scenario_id": "K-1-S2"}])
    findings, _ = sc.check("K-1", "repo-x", ["evil.spec.js"])
    assert any("UNAPPROVED_SCENARIO" in f and "K-1-S99" in f
               for f in findings)


def test_unapproved_or_absent_spec_is_exempt(estate):
    seed, tmp = estate
    seed(approved=False)
    _, exempt = sc.check("K-1", "repo-x", [])
    assert exempt, "a spec nobody signed yet is not enforceable"
    _, exempt = sc.check("PR-x-9", "repo-x", [])
    assert exempt, "PR-path keys have no spec by construction"


def test_modes(estate, monkeypatch):
    seed, tmp = estate
    seed(tests=[])                                  # everything uncovered
    monkeypatch.setenv("AIQE_SPEC_ENFORCE", "off")
    assert sc.main(["x", "K-1", "repo-x"]) == 0
    monkeypatch.setenv("AIQE_SPEC_ENFORCE", "warn")
    assert sc.main(["x", "K-1", "repo-x"]) == 0, "warn prints, never blocks"
    monkeypatch.setenv("AIQE_SPEC_ENFORCE", "strict")
    assert sc.main(["x", "K-1", "repo-x"]) == sc.EXIT_SPEC


def test_default_mode_is_off_in_org_config():
    import yaml
    cfg = yaml.safe_load(open(ROOT / "registry/org-config.yaml",
                              encoding="utf-8"))
    assert str((cfg.get("spec") or {}).get("enforce")).lower() in \
        ("off", "false"), "strict is a two-step rollout, never a default"


def test_gate_wires_the_check_with_exit_8():
    src = (ROOT / "engine/gate/gate.sh").read_text(encoding="utf-8")
    assert "spec_check.py" in src and "exit 8" in src
    assert src.index("UNMAPPED_TEST") < src.index("spec_check.py"), \
        "ordered after born-mapped"


# ---- journey review: a waiver can name a scenario that does not exist -------
def test_a_waiver_naming_no_real_scenario_is_reported_not_silently_accepted(tmp_path, monkeypatch):
    """A typo'd scenario id produced a waiver that looked perfectly healthy —
    saved `ok`, rendered with days remaining — and protected nothing. The gate
    kept refusing the scenario the author meant to waive.

    This is the same failure the alert rules already guard against by reporting
    unknown kinds: configured-looking and inert. It is worse here, because what
    it silently fails to do is let a release through.

    Reported, NOT refused: waiving before the plan is authored is legitimate, so
    a waiver written when no spec exists yet must stay valid.
    """
    sys.path.insert(0, str(ROOT / "engine" / "lib"))
    import waiver_store

    monkeypatch.setattr(waiver_store, "spec_scenarios",
                        lambda key: {"K-1-S1", "K-1-S2"})
    assert waiver_store.unmatched("K-1", "K-1-S9") is True
    assert waiver_store.unmatched("K-1", "K-1-S1") is False

    # No spec yet -> nothing is unmatched, because nothing can be checked.
    monkeypatch.setattr(waiver_store, "spec_scenarios", lambda key: None)
    assert waiver_store.unmatched("K-1", "anything-at-all") is False



def test_a_scenario_less_spec_is_indistinguishable_from_no_spec(monkeypatch):
    """The REAL resolver, with nothing about it monkeypatched.

    The test above stubs `spec_scenarios` to exercise `unmatched`, which means
    it cannot check the resolver itself — an earlier version asserted on the
    stub and passed against a broken resolver. A missing key returns early, so
    the only case that exercises the empty-set branch is a spec that exists and
    declares no scenarios: an empty set is falsy, but it would make `unmatched`
    answer "a spec exists and your id is not in it" about a spec that lists
    nothing, marking every waiver on a not-yet-authored plan as inert.
    """
    sys.path.insert(0, str(ROOT / "engine" / "lib"))
    import spec_store
    import waiver_store
    monkeypatch.setattr(spec_store, "load", lambda k: {"scenarios": []})
    assert waiver_store.spec_scenarios("K-1") is None
    assert waiver_store.unmatched("K-1", "K-1-S1") is False


def test_the_unmatched_state_reaches_every_surface_that_shows_waivers():
    """Detecting it in the library and not saying so is the same bug again."""
    srv = (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")
    assert "waiver_store.unmatched(" in srv, "the save endpoint does not check"
    assert '"warning": warn' in srv, "the warning is computed but never returned"
    ui = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert "MATCHES NOTHING" in ui, "the row does not show the inert state"
    assert "w.unmatched" in ui
    assert "protecting nothing" in ui, "the Overview does not surface it"
    assert "r.warning" in ui, "the save handler swallows the warning"
    # And it must be the FIRST branch of the state ternary, not merely mentioned
    # before the others: an index comparison still passes when the branch has
    # been disabled (`false && w.unmatched`), which is how this pin was
    # decorative when first written.
    assert "const state = w.unmatched" in ui,         "the inert state is not the first branch of the row's state ternary"


def test_attention_counts_an_unmatched_waiver_independently_of_expiry():
    """An unmatched waiver is broken whether or not it has time left. Folding it
    into the expiry buckets would report the wrong reason to fix."""
    src = (ROOT / "engine/lib/waiver_store.py").read_text(encoding="utf-8")
    i = src.index('if w.get("unmatched"):')
    j = src.index('if w["expired"]:', i - 400 if i > 400 else 0)
    assert i < j, "unmatched must be checked before (and separately from) expiry"
    assert '"unmatched": inert' in src

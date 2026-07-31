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

"""Re-verification: "we could not check" is not "the tests are broken".

`spec_verify` re-runs a key's already-committed tests read-only, to answer
"still passing, or actually broken by the contract change?". It shipped with no
tests, and recorded a failed clone or a stale catalog mapping as
`passed: False` — which is the answer a reviewer acts on by hunting for a
regression that does not exist.
"""
import pathlib
import subprocess
import sys

import pytest
import types

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine" / "lib"))
import spec_verify


def _fake_run(outcomes):
    """subprocess.run stub: pops one outcome per call, in call order."""
    seq = list(outcomes)

    def run(cmd, **kw):
        rc, out = seq.pop(0)
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr="")
    return run


@pytest.fixture(autouse=True)
def _isolate_plan_state(tmp_path, monkeypatch):
    """spec_verify.verify() attaches its result to the plan store via
    plan_state._save. plan_state binds DIR/FILE at IMPORT time, so if anything
    imported it before the harness set AIQE_PLAN_DIR those writes land in the
    ESTATE — traced with an instrumented _save: this file's `verify("K-1")`
    wrote reports/plans/state.json while AIQE_PLAN_DIR correctly pointed at
    out/test-plans, leaving a stray K-1 key in the operator's plan store.

    Patching the module attributes directly does not care when it was imported.
    """
    import plan_state
    d = tmp_path / "plans"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(plan_state, "DIR", d)
    monkeypatch.setattr(plan_state, "FILE", d / "state.json")


def test_a_failed_clone_is_unverified_not_failed(tmp_path, monkeypatch):
    """Nothing was executed, so nothing failed. Recording False here sends
    someone looking for a regression in tests that never ran."""
    monkeypatch.setattr(spec_verify, "_tests_for",
                        lambda key: {"e2e-api-tests-1": ["suites/a.spec.js"]})
    monkeypatch.setattr(spec_verify.subprocess, "run", _fake_run([(1, "")]))
    monkeypatch.setattr(spec_verify, "ROOT", tmp_path)      # clone dest missing
    r = spec_verify.verify("K-1")
    assert r["e2e-api-tests-1"]["passed"] is None
    assert r["e2e-api-tests-1"]["unverifiable"] is True
    assert "clone_ro failed" in r["e2e-api-tests-1"]["log"]


def test_a_stale_catalog_mapping_is_unverified_not_failed(tmp_path, monkeypatch):
    """The catalog points at files the repo does not have. That is a mapping to
    fix, not a regression to investigate — and the log says which."""
    dest = tmp_path / "out" / "spec-verify-e2e-api-tests-1"
    (dest / ".ai-qe").mkdir(parents=True)
    (dest / ".ai-qe" / "config.yaml").write_text(
        "commands:\n  test: node --test\n", encoding="utf-8")
    monkeypatch.setattr(spec_verify, "ROOT", tmp_path)
    monkeypatch.setattr(spec_verify, "_tests_for",
                        lambda key: {"e2e-api-tests-1": ["suites/gone.spec.js"]})
    monkeypatch.setattr(spec_verify.subprocess, "run", _fake_run([(0, "")]))
    r = spec_verify.verify("K-1")
    assert r["e2e-api-tests-1"]["passed"] is None
    assert "stale catalog mapping" in r["e2e-api-tests-1"]["log"]


def test_a_real_run_still_reports_true_and_false(tmp_path, monkeypatch):
    """The honest states must survive: a test that runs and fails is False, not
    swallowed into the new 'unverified' bucket."""
    dest = tmp_path / "out" / "spec-verify-e2e-api-tests-1"
    (dest / "suites").mkdir(parents=True)
    (dest / "suites" / "a.spec.js").write_text("//", encoding="utf-8")
    (dest / ".ai-qe").mkdir(parents=True)
    (dest / ".ai-qe" / "config.yaml").write_text(
        "commands:\n  test: node --test\n", encoding="utf-8")
    monkeypatch.setattr(spec_verify, "ROOT", tmp_path)
    monkeypatch.setattr(spec_verify, "_tests_for",
                        lambda key: {"e2e-api-tests-1": ["suites/a.spec.js"]})

    monkeypatch.setattr(spec_verify.subprocess, "run",
                        _fake_run([(0, ""), (0, "ok")]))
    assert spec_verify.verify("K-1")["e2e-api-tests-1"]["passed"] is True

    monkeypatch.setattr(spec_verify.subprocess, "run",
                        _fake_run([(0, ""), (1, "1 failing")]))
    r = spec_verify.verify("K-1")
    assert r["e2e-api-tests-1"]["passed"] is False
    assert r["e2e-api-tests-1"]["unverifiable"] is False


def test_the_cli_prints_unverified_and_exits_differently(tmp_path, monkeypatch, capsys):
    """A script must be able to tell "the tests broke" (1) from "we could not
    run them" (2). Printing FAIL for both, as the first version did, makes the
    two indistinguishable to a human and to a pipeline."""
    monkeypatch.setattr(spec_verify, "verify",
                        lambda key: {"r": {"passed": None, "unverifiable": True,
                                           "log": "clone_ro failed"}})
    assert spec_verify.main(["K-1"]) == 2
    assert "UNVERIFIED" in capsys.readouterr().out

    monkeypatch.setattr(spec_verify, "verify",
                        lambda key: {"r": {"passed": False, "unverifiable": False,
                                           "log": "1 failing"}})
    assert spec_verify.main(["K-1"]) == 1
    assert "FAIL" in capsys.readouterr().out

    monkeypatch.setattr(spec_verify, "verify",
                        lambda key: {"r": {"passed": True, "unverifiable": False,
                                           "log": "ok"}})
    assert spec_verify.main(["K-1"]) == 0
    assert "PASS" in capsys.readouterr().out


def test_the_overall_verdict_refuses_to_claim_coverage_nobody_checked():
    """If any repo was unverifiable, the aggregate is None.

    True would claim coverage nobody established; False would blame tests that
    never executed. This is the same rule the cost stack applies to unmeasured
    spend, applied to test results.
    """
    src = (ROOT / "engine/lib/spec_verify.py").read_text(encoding="utf-8")
    assert 'ran = [v for v in results.values() if v["passed"] is not None]' in src
    assert "if ran and len(ran) == len(results) else None" in src
    assert '"unverifiable": [k for k, v in results.items()' in src, \
        "the reviewer is not told WHICH repos went unchecked"

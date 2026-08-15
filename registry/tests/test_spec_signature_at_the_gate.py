"""The gate enforced a contract nobody signed.

`spec_check.check` gated on `plan_state.get(key)["status"] == "approved"` and
then built `approved_ids` from the spec file AS IT IS NOW. So a scenario added
after approval was enforced as an approved obligation, and one removed after
approval silently stopped being enforced — while the check's own refusal
message calls the approved spec "a signed contract".

MEASURED against an isolated estate: approve a one-scenario spec, append a
second scenario out of band, and the gate reported

    UNCOVERED_SCENARIO: ZZ-SIGN-1-S2 — approved but uncovered
      (waive with a reason in specs/ZZ-SIGN-1/waivers.yaml, or cover it)

for a scenario no reviewer ever saw. Following that advice means waiving — or
implementing — work that was never proposed to anyone.

THE PLATFORM ALREADY KNEW. `plan_state` stores `spec_sha` at approval for
exactly this purpose, and `approval_confirmation` compares it with
`hmac.compare_digest` to report `signed: False` in the UI. Measured on the
same fixture, the UI said `signed: False` while the gate enforced. The honest
sibling again, with the enforcement point as the dishonest one — which is the
worse half, because the UI only reports and the gate refuses commits.

REACHABILITY, stated rather than implied: edits through the platform's own
paths revoke approval, so this needs an out-of-band change — a direct edit of
the tracked `specs/<KEY>/testplan.yaml`, a branch switch or merge, or a
state-bundle import (specs/ is in both bundle profiles). Enforcement also
ships `off` by default, so on a stock estate this changes nothing today. That
bounds the blast radius; it does not make it safe, because the defect becomes
live exactly when a team turns enforcement on — the moment they start
trusting it.
"""
import importlib
import os
import pathlib
import shutil
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
sys.path.insert(0, str(ROOT / "engine/gate"))

KEY = "ZZ-SIGN-1"
REPO = "e2e-api-tests-1"


def _scenario(n):
    return {"id": f"{KEY}-S{n}", "title": f"scenario {n}", "layer": "api",
            "target_repo": REPO,
            "steps": {"given": "g", "when": "w", "then": "t"}}


@pytest.fixture
def estate():
    """An isolated plan+spec store with one APPROVED single-scenario spec."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="spec-sig-"))
    saved = {k: os.environ.get(k) for k in
             ("AIQE_PLAN_DIR", "AIQE_SPEC_DIR", "AIQE_TESTPLAN_DIR")}
    # SET, never clear: a cleared knob sends the store at the live estate.
    os.environ["AIQE_PLAN_DIR"] = str(tmp / "plans")
    os.environ["AIQE_SPEC_DIR"] = str(tmp / "specs")
    os.environ["AIQE_TESTPLAN_DIR"] = str(tmp / "testplans")
    import spec_store
    import plan_state
    import spec_check
    importlib.reload(spec_store)
    importlib.reload(plan_state)
    importlib.reload(spec_check)

    spec = {"key": KEY, "scenarios": [_scenario(1)]}
    spec_store.write_from_contract(KEY, spec)
    plan_state.record_plan(KEY, contract=spec, by="probe")
    plan_state.set_status(KEY, "approved", by="reviewer")
    try:
        yield spec_store, plan_state, spec_check, spec
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(spec_store)
        importlib.reload(plan_state)
        importlib.reload(spec_check)
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_fixture_really_signs_the_spec(estate):
    """A probe that proves nothing is the failure mode this repo keeps
    recording: without a stored sha every assertion below is vacuous."""
    spec_store, plan_state, _check, _spec = estate
    entry = plan_state.get(KEY)
    assert entry["status"] == "approved"
    assert entry.get("spec_sha")
    assert entry["spec_sha"] == spec_store.sha(KEY)


def test_a_spec_changed_after_approval_is_caught(estate):
    """THE DEFECT."""
    spec_store, _plan_state, spec_check, spec = estate
    spec["scenarios"].append(_scenario(2))
    spec_store.write_from_contract(KEY, spec)

    findings, exempt = spec_check.check(KEY, REPO, [])
    assert exempt is False, "an unsigned spec must not let the gate off"
    assert len(findings) == 1, findings
    assert findings[0].startswith("SPEC_CHANGED_SINCE_APPROVAL"), findings


def test_the_unsound_obligations_are_not_reported_as_well(estate):
    """Every obligation below the signature check is derived from a spec
    nobody signed. Reporting them tells a reviewer to waive — or implement —
    work that was never proposed to them."""
    spec_store, _plan_state, spec_check, spec = estate
    spec["scenarios"].append(_scenario(2))
    spec_store.write_from_contract(KEY, spec)

    findings, _ = spec_check.check(KEY, REPO, [])
    assert not any("UNCOVERED_SCENARIO" in f for f in findings), findings
    assert not any("approved but uncovered" in f for f in findings), findings


def test_the_finding_names_both_ways_out(estate):
    spec_store, _plan_state, spec_check, spec = estate
    spec["scenarios"].append(_scenario(2))
    spec_store.write_from_contract(KEY, spec)

    finding = spec_check.check(KEY, REPO, [])[0][0]
    assert "re-approve" in finding
    assert "restore" in finding


def test_an_unchanged_spec_is_enforced_exactly_as_before(estate):
    """The over-fix guard, and the one that matters most: if this fix made the
    gate stop enforcing signed specs it would be worse than the defect."""
    _spec_store, _plan_state, spec_check, _spec = estate
    findings, exempt = spec_check.check(KEY, REPO, [])
    assert exempt is False
    assert findings and all("UNCOVERED_SCENARIO" in f for f in findings), findings
    assert not any("SPEC_CHANGED" in f for f in findings), findings


def test_an_approval_with_no_stored_signature_is_not_accused(estate):
    """UNRECOVERABLE, not a mismatch. Plans approved before the field existed
    carry no sha, and accusing them would break every legacy approval — the
    same reasoning as the reviewer verdict recorded before its flag existed."""
    _spec_store, plan_state, spec_check, _spec = estate
    state = plan_state.load()
    state[KEY].pop("spec_sha", None)
    plan_state._save(state)

    findings, exempt = spec_check.check(KEY, REPO, [])
    assert exempt is False
    assert not any("SPEC_CHANGED" in f for f in findings), findings


def test_an_unreadable_current_spec_does_not_accuse(estate, monkeypatch):
    """We could not compute the current hash, so we cannot claim it differs."""
    spec_store, _plan_state, spec_check, _spec = estate
    monkeypatch.setattr(spec_store, "sha", lambda *a, **k: "")
    findings, _exempt = spec_check.check(KEY, REPO, [])
    assert not any("SPEC_CHANGED" in f for f in findings), findings


def test_a_plan_that_is_not_approved_stays_exempt(estate):
    """Unchanged behaviour: there is nothing signed to enforce yet."""
    _spec_store, plan_state, spec_check, _spec = estate
    plan_state.set_status(KEY, "draft", by="probe")
    assert spec_check.check(KEY, REPO, []) == ([], True)


def test_the_ui_and_the_gate_now_agree(estate):
    """The whole point. `approval_confirmation` reported `signed: False` in
    exactly the situation the gate enforced through, and two surfaces
    disagreeing about whether a contract is signed is the defect, not the
    wording."""
    spec_store, plan_state, spec_check, spec = estate
    spec["scenarios"].append(_scenario(2))
    spec_store.write_from_contract(KEY, spec)

    assert plan_state.approval_confirmation(KEY)["signed"] is False
    assert any("SPEC_CHANGED" in f for f in spec_check.check(KEY, REPO, [])[0])

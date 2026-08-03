"""Requirements gate + blocking clarifications (SDD stories 2.2, 2.3).

Contracts: the gate is OFF by default (today's flow byte-for-byte), ON it
refuses planning over unvalidated requirements; approval signs the yaml; an
approved file survives later re-analysis; blocking ambiguities stop the chain
with a question instead of a guess.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import plan_state as ps  # noqa: E402
import spec_store as ss  # noqa: E402


ANALYZE = {"requirements": [
    {"id": "R1", "ears": "WHEN x, THE SYSTEM SHALL y", "source": "AC-1"}],
    "open_questions": []}
BLOCKED = {"requirements": [
    {"id": "R1", "ears": "WHEN x, THE SYSTEM SHALL y", "source": "AC-1",
     "blocking_ambiguity": "AC-1 contradicts AC-4"}],
    "open_questions": []}


@pytest.fixture
def estate(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "DIR", tmp_path / "plans")
    monkeypatch.setattr(ps, "FILE", tmp_path / "plans/state.json")
    monkeypatch.setattr(ps, "PLAN_DIR", tmp_path / "testplans")
    monkeypatch.setattr(ss, "SPEC_DIR", tmp_path / "specs")
    (tmp_path / "plans").mkdir()
    (tmp_path / "testplans").mkdir()
    monkeypatch.delenv("AIQE_REQUIREMENTS_REAUTHOR", raising=False)
    return tmp_path


def test_gate_off_is_a_noop(estate, monkeypatch):
    monkeypatch.setattr(ps, "_requirements_gate_on", lambda: False)
    assert ps.require_requirements("K-1") is None, \
        "gate off -> today's flow, no requirements needed"


def test_gate_on_refuses_until_approved(estate, monkeypatch):
    monkeypatch.setattr(ps, "_requirements_gate_on", lambda: True)
    with pytest.raises(SystemExit) as e:
        ps.require_requirements("K-1")
    assert "make requirements KEY=K-1" in str(e.value), \
        "the refusal must name the fix"
    ss.write_requirements_from_contract("K-1", ANALYZE)
    ps.requirements_record("K-1")
    with pytest.raises(SystemExit):
        ps.require_requirements("K-1")              # draft is not validated
    ps.set_requirements_status("K-1", "approved", "lead")
    assert ps.require_requirements("K-1")["requirements_status"] == "approved"


def test_approval_signs_and_needs_a_real_spec(estate):
    with pytest.raises(SystemExit):
        ps.set_requirements_status("K-1", "approved", "lead")
    ss.write_requirements_from_contract("K-1", ANALYZE)
    e = ps.set_requirements_status("K-1", "approved", "lead")
    assert len(e["requirements_sha"]) == 64
    assert e["history"][-1]["requirements_sha"] == e["requirements_sha"]


def test_approved_requirements_survive_reanalysis(estate):
    """A later plan run's fresh analysis must not overwrite the validated
    artifact — re-authoring is a deliberate act (requirements mode)."""
    ss.write_requirements_from_contract("K-1", ANALYZE)
    ps.set_requirements_status("K-1", "approved", "lead")
    before = ss.requirements_path("K-1").read_bytes()
    changed = {"requirements": [{"id": "R9", "ears": "WHEN q, THE SYSTEM "
                                 "SHALL r", "source": "AC-9"}],
               "open_questions": []}
    assert ss.write_requirements_from_contract("K-1", changed) is None
    assert ss.requirements_path("K-1").read_bytes() == before


def test_reauthor_flag_replaces_deliberately(estate, monkeypatch):
    ss.write_requirements_from_contract("K-1", ANALYZE)
    ps.set_requirements_status("K-1", "approved", "lead")
    monkeypatch.setenv("AIQE_REQUIREMENTS_REAUTHOR", "1")
    changed = {"requirements": [{"id": "R9", "ears": "WHEN q, THE SYSTEM "
                                 "SHALL r", "source": "AC-9"}],
               "open_questions": []}
    assert ss.write_requirements_from_contract("K-1", changed) is not None
    assert ss.load_requirements("K-1")["requirements"][0]["id"] == "R9"


def test_blocking_signal(estate):
    """SDD 2.3: exit-0-with-questions is the pipeline's stop-and-ask."""
    ss.write_requirements_from_contract("K-1", BLOCKED)
    qs = [a for a in ss.ambiguities("K-1") if a["blocking"]]
    assert qs and qs[0]["question"] == "AC-1 contradicts AC-4"
    ss.write_requirements_from_contract("K-2", ANALYZE)
    assert not [a for a in ss.ambiguities("K-2") if a["blocking"]], \
        "non-blocking flows through"


def test_pipeline_wires_the_stage_and_the_stops():
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert "pr|jira|plan|tests|requirements)" in src
    assert "REQUIREMENTS_STATUS=DRAFT" in src
    assert "NEEDS_CLARIFICATION" in src and "exit 65" in src
    assert "require-requirements" in src
    # Ordering: the requirements-mode stop precedes the blocking stop (the
    # requirements run SURFACES questions; only planning is blocked by them),
    # and both precede the testplan phase.
    assert src.index("REQUIREMENTS_STATUS=DRAFT") < src.index("exit 65") \
        < src.index("PHASE testplan")


# ---- an unrecognized knob value must never be silently the safe default -----
def test_an_unrecognized_requirements_gate_value_says_so(monkeypatch, capsys):
    """`AIQE_REQUIREMENTS_GATE=enabled` reads as an instruction to turn the gate
    ON, and used to turn it OFF in silence. Same rule as the enforcement mode:
    the safe fallback stays, the silence does not."""
    sys.path.insert(0, str(ROOT / "engine" / "lib"))
    import plan_state
    monkeypatch.setenv("AIQE_REQUIREMENTS_GATE", "enabled")
    assert plan_state._requirements_gate_on() is False
    err = capsys.readouterr().err
    assert "not a recognized value" in err and "enabled" in err
    assert "NOT gated" in err

    # A recognized value must stay quiet, or the warning becomes noise.
    monkeypatch.setenv("AIQE_REQUIREMENTS_GATE", "1")
    assert plan_state._requirements_gate_on() is True
    assert capsys.readouterr().err == ""


def test_an_unrecognized_notify_kind_says_so():
    """A typo'd `emails` fell through to slack, so a channel configured for
    email delivered nothing to that address and said nothing about it. A
    notification channel that silently is not the one you chose is worse than
    one that is down: silence reads as 'nothing happened'."""
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    i = src.index("slack|email|both) ;;")
    block = src[i - 600:i + 400]
    assert "WARNING: NOTIFY_KIND=" in block
    assert "NOT going where you configured them" in block
    # It must be validated ONCE, before the adapter functions are defined —
    # putting it inside NOTIFY() would warn on every notification instead.
    assert src.index("slack|email|both) ;;") < src.index('SCM() { bash adapters/mock/scm.sh')

"""The requirements gate checked the status and not the signature.

The direct sibling of the spec-gate hole fixed alongside it, one layer up.
`plan_state.require_requirements` exists so that "a plan may only be authored
over VALIDATED requirements", and it read `requirements_status == "approved"`
while never comparing the `requirements_sha` that
`set_requirements_status` stores over the approved bytes. Only the dashboard
and the wizard ever compared it.

MEASURED in an isolated estate, and the first measurement was WRONG in an
instructive way: driving it through `spec_store.write_requirements_from_
contract` left the sha INTACT, because that writer already refuses to
overwrite an approved file. That protection is real and is why the pipeline
cannot cause this. The hole is an out-of-band change — a text editor, a branch
switch or merge (specs/ is tracked), a state-bundle import. Reproduced by
writing the file directly with a VALID two-requirement document, one of which
nobody approved: the gate proceeded.

A second probe artifact worth recording: appending raw text produced an
INVALID document (0 requirements loaded), which would have let someone argue
the case is caught elsewhere. The dangerous case is a document that still
parses and validates, so that is what these pins use.
"""
import hashlib
import importlib
import os
import pathlib
import shutil
import sys
import tempfile

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

KEY = "ZZ-REQSIG-1"


@pytest.fixture
def estate():
    """Isolated stores with APPROVED single-requirement requirements.yaml."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="req-sig-"))
    saved = {k: os.environ.get(k) for k in
             ("AIQE_PLAN_DIR", "AIQE_SPEC_DIR", "AIQE_TESTPLAN_DIR",
              "AIQE_REQUIREMENTS_GATE")}
    os.environ["AIQE_PLAN_DIR"] = str(tmp / "plans")
    os.environ["AIQE_SPEC_DIR"] = str(tmp / "specs")
    os.environ["AIQE_TESTPLAN_DIR"] = str(tmp / "testplans")
    os.environ["AIQE_REQUIREMENTS_GATE"] = "1"
    import spec_store
    import plan_state
    importlib.reload(spec_store)
    importlib.reload(plan_state)

    spec_store.write_requirements_from_contract(KEY, {
        "key": KEY,
        "requirements": [{"id": f"{KEY}-R1",
                          "ears": "WHEN x happens THE system SHALL y"}]})
    plan_state.record_plan(KEY, contract={"key": KEY, "scenarios": []}, by="p")
    plan_state.set_requirements_status(KEY, "approved", by="analyst")
    try:
        yield spec_store, plan_state
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(spec_store)
        importlib.reload(plan_state)
        shutil.rmtree(tmp, ignore_errors=True)


def _edit_out_of_band(spec_store):
    """A VALID document that nobody approved — not a broken one."""
    p = pathlib.Path(spec_store.requirements_path(KEY))
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    doc["requirements"].append(
        {"id": f"{KEY}-R2", "ears": "WHEN nobody approved THE system SHALL comply"})
    p.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return p


def test_the_fixture_really_signs_the_requirements(estate):
    """Without a stored hash every assertion below would be vacuous."""
    spec_store, plan_state = estate
    entry = plan_state.get(KEY)
    assert entry["requirements_status"] == "approved"
    assert len(entry.get("requirements_sha") or "") == 64
    p = pathlib.Path(spec_store.requirements_path(KEY))
    assert entry["requirements_sha"] == hashlib.sha256(p.read_bytes()).hexdigest()


def test_the_platform_writer_cannot_cause_this(estate):
    """Recorded because it bounds the finding: re-authoring through the
    supported path leaves the signature intact, so the pipeline is not the
    route. Losing this protection would be its own defect."""
    spec_store, plan_state = estate
    before = plan_state.get(KEY)["requirements_sha"]
    spec_store.write_requirements_from_contract(KEY, {
        "key": KEY,
        "requirements": [{"id": f"{KEY}-R9", "ears": "WHEN z THE system SHALL w"}]})
    p = pathlib.Path(spec_store.requirements_path(KEY))
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before, \
        "an approved requirements file was overwritten by the writer"


def test_an_untouched_approval_still_proceeds(estate):
    """The over-fix guard, and the one that matters most: refusing here would
    block every legitimate plan."""
    _spec_store, plan_state = estate
    assert plan_state.require_requirements(KEY)["requirements_status"] == "approved"


def test_a_document_edited_after_approval_is_refused(estate):
    """THE DEFECT."""
    spec_store, plan_state = estate
    _edit_out_of_band(spec_store)
    with pytest.raises(SystemExit) as exc:
        plan_state.require_requirements(KEY)
    assert "REQUIREMENTS_CHANGED_SINCE_APPROVAL" in str(exc.value)


def test_the_refusal_names_both_ways_out(estate):
    spec_store, plan_state = estate
    _edit_out_of_band(spec_store)
    with pytest.raises(SystemExit) as exc:
        plan_state.require_requirements(KEY)
    msg = str(exc.value)
    assert "requirements-approve" in msg
    assert "restore" in msg


def test_the_edited_document_is_still_valid(estate):
    """The pin's own premise: a document that fails validation would be caught
    elsewhere, so the dangerous case is one that parses cleanly."""
    spec_store, _plan_state = estate
    _edit_out_of_band(spec_store)
    loaded = spec_store.load_requirements(KEY) or {}
    assert not spec_store.validate_requirements(loaded)
    assert len(loaded["requirements"]) == 2


def test_a_legacy_approval_with_no_signature_is_not_refused(estate):
    """UNRECOVERABLE, not a mismatch. Keys approved before the field existed
    carry no hash, and refusing them would block every legacy plan on evidence
    nobody can produce."""
    _spec_store, plan_state = estate
    state = plan_state.load()
    state[KEY].pop("requirements_sha", None)
    plan_state._save(state)
    assert plan_state.require_requirements(KEY) is not None


def test_an_unreadable_document_is_not_called_a_mismatch(estate):
    """We could not hash it, so we cannot claim it differs."""
    _spec_store, plan_state = estate
    import plan_state as ps
    original = ps._requirements_sha
    try:
        ps._requirements_sha = lambda *a, **k: ""
        assert ps.require_requirements(KEY) is not None
    finally:
        ps._requirements_sha = original


def test_a_pr_target_stays_exempt(estate):
    """Unchanged: a PR has no requirements-authoring mode."""
    _spec_store, plan_state = estate
    assert plan_state.require_requirements(KEY, pr_target=True)["exempt"] is True


def test_the_gate_off_path_is_untouched(estate, monkeypatch):
    """Gate off must stay byte-for-byte today's flow, edited document or not."""
    spec_store, plan_state = estate
    _edit_out_of_band(spec_store)
    monkeypatch.setenv("AIQE_REQUIREMENTS_GATE", "0")
    assert plan_state.require_requirements(KEY) is None


def test_signing_and_checking_hash_the_same_bytes(estate):
    """One definition. If the writer and the check ever hashed differently the
    gate would refuse every approved key — worse than the defect."""
    spec_store, plan_state = estate
    import plan_state as ps
    p = pathlib.Path(spec_store.requirements_path(KEY))
    assert ps._requirements_sha(KEY) == hashlib.sha256(p.read_bytes()).hexdigest()
    assert ps._requirements_sha(KEY) == plan_state.get(KEY)["requirements_sha"]

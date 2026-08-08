"""Vocabulary and state-label pins for the SDD usability PRD, slice S1."""
import html
import pathlib
import re
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import glossary  # noqa: E402
import plan_state  # noqa: E402
import sdd_messages  # noqa: E402
import spec_workflow  # noqa: E402
import work_queue  # noqa: E402


def test_every_marked_term_resolves_and_every_definition_is_used():
    references = set()
    for sentence in glossary.HOW_IT_WORKS_MARKUP:
        references |= glossary.referenced_terms(sentence)
    references |= set(glossary.GLOSSARY_CARD_TERMS)
    assert references == set(glossary.TERMS), (
        f"term coverage differs: references-only={references - set(glossary.TERMS)}, "
        f"definitions-only={set(glossary.TERMS) - references}"
    )
    assert len(glossary.HOW_IT_WORKS_MARKUP) == 5


def test_term_renderer_escapes_copy_and_rejects_unknown_terms():
    rendered = glossary.render_markup('<b>{{term:signed}}</b>')
    assert "&lt;b&gt;" in rendered and "<b>" not in rendered
    assert "tabindex=\"0\"" in rendered and "Internal:" in html.unescape(rendered)
    with pytest.raises(ValueError, match="undefined glossary term"):
        glossary.render_markup("{{term:not-real}}")


def test_definitions_state_meaning_and_consequence_separately():
    for term_id, term in glossary.TERMS.items():
        assert set(term) == {"label", "meaning", "consequence", "internal"}, term_id
        assert term["meaning"].endswith("."), term_id
        assert term["consequence"].endswith("."), term_id
        assert term["internal"], term_id


def test_state_labels_cover_the_machine_states_exactly():
    assert tuple(glossary.STATE_LABELS) == spec_workflow.STATES
    assert tuple(glossary.STATE_BLOCKER_PINS) == spec_workflow.STATES


@pytest.mark.parametrize("state", spec_workflow.STATES)
def test_each_plain_label_is_pinned_to_its_computed_blocker(state, monkeypatch):
    blockers = glossary.STATE_BLOCKER_PINS[state]
    source = (ROOT / "engine/lib/spec_workflow.py").read_text(encoding="utf-8")
    for blocker in blockers:
        assert blocker in source
    assert glossary.STATE_LABELS[state] != state


@pytest.mark.parametrize(("expected", "entry", "plan", "requirements", "committed", "blocker"), [
    ("requirements", {"requirements_status": "draft"}, False, True, False, "not approved"),
    ("plan", {}, False, False, False, "no test plan"),
    ("approved", {"status": "draft"}, True, False, False, "not approved"),
    ("tests", {"status": "approved"}, True, False, False, "not generated"),
    ("committed", {"status": "approved", "generated_run": "r1"}, True, False, False,
     "no gate commit"),
    ("live", {"status": "approved", "generated_run": "r1"}, True, False, True, ""),
])
def test_label_rows_follow_the_real_state_computation(
        tmp_path, monkeypatch, expected, entry, plan, requirements, committed, blocker):
    plans = tmp_path / "testplans"
    specs = tmp_path / "specs" / "K-1"
    plans.mkdir(parents=True)
    specs.mkdir(parents=True)
    if plan:
        (plans / "K-1.md").write_text("# plan\n", encoding="utf-8")
    if requirements:
        (specs / "requirements.yaml").write_text("requirements: []\n", encoding="utf-8")
    monkeypatch.setattr(spec_workflow, "governance", lambda: {
        "requirements_gate": True, "requirements_gate_effect": "refuses",
        "spec_enforce": "off", "spec_enforce_effect": "ignored", "spec_mode": True,
        "problems": [],
    })
    monkeypatch.setattr(spec_workflow.plan_state, "get", lambda key: dict(entry))
    monkeypatch.setattr(spec_workflow.app_paths, "testplans_dir", lambda: plans)
    monkeypatch.setattr(spec_workflow.spec_store, "spec_path",
                        lambda key: specs / "testplan.yaml")
    monkeypatch.setattr(spec_workflow.spec_store, "requirements_path",
                        lambda key: specs / "requirements.yaml")
    monkeypatch.setattr(spec_workflow.spec_store, "ambiguities", lambda key: [])

    row = spec_workflow.status("K-1", committed={"K-1"} if committed else set())
    assert row["state"] == expected
    assert blocker in row["blocker"]
    assert glossary.STATE_LABELS[row["state"]]
    assert row["action"] and row["command"] and row["action_view"]


def test_every_state_computes_one_ui_action_and_equivalent_command():
    source = (ROOT / "engine/lib/spec_workflow.py").read_text(encoding="utf-8")
    for state in spec_workflow.STATES:
        start = source.index(f'return _row(key, "{state}"')
        end = source.find("return _row(key,", start + 1)
        row_source = source[start:end if end >= 0 else len(source)]
        assert "action=" in row_source, state
        assert "command=" in row_source, state
        assert "view=" in row_source, state


def test_dashboard_renders_plain_state_first_and_machine_name_on_demand():
    source = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert "const SDD_STATE_LABELS = __SDD_STATE_LABELS__;" in source
    assert "SDD_STATE_LABELS[r.state] || r.state" in source
    assert "machine state:" in source and "engine/lib/spec_workflow.py" in source
    row = source.split("const stateLabel", 1)[1].split("</td></tr>", 1)[0]
    assert row.index("escHtml(stateLabel)") < row.index("machine state:")


def test_dashboard_uses_the_computed_action_without_reinferring_state():
    source = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    block = source.split("async function refreshSpecFlow()", 1)[1].split(
        "onEnter('specflow'", 1)[0]
    assert "data-sf-go" in block
    assert "data-sf-key" in block
    assert "r.action_view" in block and "r.action" in block and "r.command" in block
    assert "r.state ===" not in block and "switch (r.state)" not in block
    assert "const view = action.dataset.sfGo" in block and "go(view)" in block
    assert "loadRequirements()" in block and "openPlan(key)" in block
    assert "rpLoad(false)" in block


@pytest.mark.parametrize(("kind", "kwargs", "needle"), [
    ("requirements_gate", {"key": "K-1", "status": "draft"},
     "make requirements-approve KEY=K-1"),
    ("plan_approval", {"key": "K-1", "status": "draft"},
     "make plan-approve KEY=K-1"),
    ("coverage_uncovered", {"key": "K-1", "scenario": "K-1-S2"},
     "specs/K-1/waivers.yaml"),
    ("waiver_expired", {"key": "K-1", "scenario": "K-1-S2",
                         "expiry": "2026-08-01"}, "expired on 2026-08-01"),
    ("drift_stale", {"key": "K-1", "scenario": "K-1-S2",
                      "surfaces": ["/v1/old"]}, "/v1/old"),
])
def test_every_refusal_has_the_closed_message_contract(kind, kwargs, needle):
    result = sdd_messages.refusal(kind, **kwargs)
    assert set(result) == {"kind", "what", "why", "action", "command", "text"}
    assert result["text"].startswith(f"SDD_REFUSAL[{kind}] ")
    assert "Why:" in result["text"] and "Next action:" in result["text"]
    assert result["text"].count("Next action:") == 1
    assert "Command:" in result["text"] and needle in result["text"]


def test_plan_gates_raise_the_shared_contract(monkeypatch):
    monkeypatch.setattr(plan_state, "_requirements_gate_on", lambda: True)
    monkeypatch.setattr(plan_state, "get", lambda key: {"requirements_status": "draft"})
    expected = sdd_messages.refusal(
        "requirements_gate", key="K-1", status="draft")["text"]
    with pytest.raises(SystemExit, match="SDD_REFUSAL") as exc:
        plan_state.require_requirements("K-1")
    assert str(exc.value) == expected

    monkeypatch.setattr(plan_state, "get", lambda key: {"status": "draft"})
    expected = sdd_messages.refusal(
        "plan_approval", key="K-1", status="draft")["text"]
    with pytest.raises(SystemExit, match="SDD_REFUSAL") as exc:
        plan_state.require_approved("K-1")
    assert str(exc.value) == expected


def test_pipeline_bash_enters_both_gates_through_the_shared_builder():
    source = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert source.count("engine/lib/sdd_messages.py require-approved") == 2
    assert source.count("engine/lib/sdd_messages.py require-requirements") == 1
    assert "plan_state.py require-approved" not in source
    direct = [line for line in source.splitlines()
              if "plan_state.py require-requirements" in line]
    assert len(direct) == 1 and direct[0].rstrip().endswith('"$KEY" --pr')


def test_ui_fetches_shared_stale_and_expired_contracts():
    server = (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")
    ui = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert '"stale_messages": stale_messages' in server
    assert '"waiver_messages": waiver_messages' in server
    assert 'sdd_messages.refusal(' in server
    assert "p.stale_messages" in ui and "p.waiver_messages" in ui
    assert "escHtml(m.text)" in ui


def test_queue_surfaces_the_shared_contract_without_rewording():
    message = sdd_messages.refusal(
        "coverage_uncovered", key="K-1", scenario="K-1-S2")["text"]
    assert work_queue.failure_reason(8, message + "\n", "") == message


def test_sdd_view_uses_the_provisional_journey_name_and_keeps_machine_id():
    source = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert 'data-view="specflow"' in source
    assert source.count("Plan → tests journey") >= 3
    assert "Spec workflow — how an E2E test gets built here" not in source


def test_ambiguous_bare_words_are_not_introduced_in_marked_journey_copy():
    copy = " ".join(glossary.HOW_IT_WORKS_MARKUP)
    unmarked = re.sub(r"\{\{term:[a-z0-9-]+\}\}", "", copy)
    assert not re.search(r"\b(spec|requirements)\b", unmarked, re.I)


def test_user_facing_docs_use_the_same_journey_vocabulary():
    for rel in ("docs/ui-guide.md", "docs/use-cases.md", "docs/getting-started.md",
                "docs/user-guide.md"):
        text = re.sub(r"\s+", " ", (ROOT / rel).read_text(encoding="utf-8"))
        assert "Plan → tests journey" in text, rel
        if rel == "docs/user-guide.md":
            continue  # reference guide is currency-pinned, not bulk-rewritten in S1
        text = text.casefold()
        assert "approved test plan (signed)" in text, rel
        assert "approved test plan (prose — not signed)" in text, rel
        assert "acceptance criteria (ears)" in text, rel

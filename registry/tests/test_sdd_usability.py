"""Vocabulary and state-label pins for the SDD usability PRD, slice S1."""
import html
import pathlib
import re
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import adoption_levels  # noqa: E402
import glossary  # noqa: E402
import governance_page  # noqa: E402
import plan_state  # noqa: E402
import sdd_messages  # noqa: E402
import spec_workflow  # noqa: E402
import spec_store  # noqa: E402
import work_queue  # noqa: E402


# ------------------------------ SDD adoption S4: benefit at approval
@pytest.mark.parametrize(("enforce", "needle", "forbidden"), [
    ("off", "Drift watching is armed", "generation is held"),
    ("warn", "dry-run mode", "generation is held"),
    ("strict", "generation is held", "dry-run mode"),
])
def test_structured_approval_confirmation_uses_signed_and_resolved_truth(
        monkeypatch, enforce, needle, forbidden):
    monkeypatch.setattr(spec_store, "load", lambda key: {"scenarios": [{"id": "S1"}]})
    monkeypatch.setattr(spec_store, "sha", lambda key: "a" * 64)
    result = plan_state.approval_confirmation(
        "K-1", {"status": "approved", "spec_sha": "a" * 64},
        {"spec_enforce": enforce})
    assert result["kind"] == "structured" and result["signed"] is True
    assert result["headline"] == "Approved test plan (signed)"
    assert "Scenario-level change review" in result["lines"][0]
    assert needle in result["text"] and forbidden not in result["text"]


@pytest.mark.parametrize(("loaded", "signed_sha"), [
    (None, ""),
    ({"scenarios": [{"id": "S1"}]}, "b" * 64),
])
def test_prose_or_signature_mismatch_never_claims_structured_benefits(
        monkeypatch, loaded, signed_sha):
    monkeypatch.setattr(spec_store, "load", lambda key: loaded)
    monkeypatch.setattr(spec_store, "sha", lambda key: "a" * 64 if loaded else "")
    result = plan_state.approval_confirmation(
        "K-1", {"status": "approved", "spec_sha": signed_sha},
        {"spec_enforce": "strict"})
    assert result["kind"] == "prose" and result["signed"] is False
    assert "not signed" in result["headline"]
    assert "Generation may proceed." in result["lines"]
    assert any("do not apply to prose plans" in line for line in result["lines"])
    assert any("structured plan" in line for line in result["lines"])


def test_all_approval_surfaces_render_the_server_confirmation():
    server = (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")
    ui = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert "plan_state.approval_confirmation(key, result)" in server
    assert "function approvalConfirmation(r)" in ui
    assert ui.count("approvalConfirmation(") == 3
    assert "{ status: 'approved' }, approvalConfirmation);" in ui
    assert "PR plan approved — you can generate tests now" not in ui
    assert "Plan approved — you can generate tests now" not in ui


# ------------------------------------------ SDD adoption S3: named levels
def test_four_levels_are_single_sourced_and_each_states_one_consequence():
    rows = adoption_levels.definitions()
    assert [row["name"] for row in rows] == [
        "Off", "Reviewed plans", "Validated criteria", "Enforced coverage"]
    assert len(rows) == 4
    for row in rows:
        assert row["consequence"].endswith(".")
        assert "\n" not in row["consequence"]


@pytest.mark.parametrize(("level", "substate", "expected"), [
    ("off", "", (False, False, "off")),
    ("reviewed", "", (True, False, "off")),
    ("validated", "", (True, True, "off")),
    ("enforced", "warn", (True, True, "warn")),
    ("enforced", "strict", (True, True, "strict")),
])
def test_level_mapping_round_trips_through_resolved_truth(level, substate, expected):
    updates = adoption_levels.updates_for(level, substate)
    assert tuple(updates) == adoption_levels.MAPPED_ENV_KEYS
    assert set(updates) == {
        "AIQE_SPEC_MODE", "AIQE_REQUIREMENTS_GATE", "AIQE_SPEC_ENFORCE"}
    gov = {
        "spec_mode": expected[0], "requirements_gate": expected[1],
        "spec_enforce": expected[2], "problems": [],
    }
    current = adoption_levels.derive(gov)
    assert current["id"] == level
    assert current["substate"] == substate
    if substate == "warn":
        assert current["badge"] == "Dry run — reporting, not refusing"
    if substate == "strict":
        assert current["badge"].startswith("Enforcing")


def test_unmatched_or_ignored_configuration_remains_custom():
    unmatched = adoption_levels.derive({
        "spec_mode": False, "requirements_gate": True,
        "spec_enforce": "strict", "problems": [],
    })
    ignored = adoption_levels.derive({
        "spec_mode": True, "requirements_gate": False,
        "spec_enforce": "off", "problems": ["invalid value ignored"],
    })
    assert unmatched["name"] == ignored["name"] == "Custom"
    assert unmatched["knobs"] == {
        "spec_mode": False, "requirements_gate": True, "spec_enforce": "strict"}


@pytest.mark.parametrize("key", [
    "AIQE_SPEC_MODE", "AIQE_REQUIREMENTS_GATE", "AIQE_SPEC_ENFORCE"])
def test_unusable_raw_control_makes_effective_level_custom(monkeypatch, key):
    monkeypatch.setenv("AIQE_SPEC_MODE", "1")
    monkeypatch.setenv("AIQE_REQUIREMENTS_GATE", "0")
    monkeypatch.setenv("AIQE_SPEC_ENFORCE", "off")
    monkeypatch.setenv(key, "definitely-not-valid")
    current = spec_workflow.governance()
    assert current["problems"], key
    assert current["adoption"]["id"] == "custom", key
    again = spec_workflow.governance()
    assert again["problems"] and again["adoption"]["id"] == "custom", key


@pytest.mark.parametrize(("level", "substate"), [
    ("missing", ""), ("enforced", ""), ("enforced", "off"),
    ("reviewed", "warn"), (None, ""),
])
def test_level_apply_rejects_ambiguous_or_unknown_requests(level, substate):
    with pytest.raises((TypeError, ValueError)):
        adoption_levels.updates_for(level, substate)


def test_level_apply_pin_cannot_quietly_gain_a_fourth_control():
    assert adoption_levels.MAPPED_ENV_KEYS == (
        "AIQE_SPEC_MODE", "AIQE_REQUIREMENTS_GATE", "AIQE_SPEC_ENFORCE")
    for row in adoption_levels.definitions():
        assert set(row["knobs"]) == {
            "spec_mode", "requirements_gate", "spec_enforce"}
    server = (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")
    route = server.split('self.path == "/api/adoption"', 1)[1].split(
        'if self.path ==', 1)[0]
    assert "adoption_levels.updates_for" in route
    assert "settings_store.save(updates)" in route


def test_governance_and_start_here_share_the_resolved_name_and_consequence():
    gov_source = (ROOT / "engine/lib/governance_page.py").read_text(encoding="utf-8")
    ui_source = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert 'adoption = g["adoption"]' in gov_source
    assert '_adoption = spec_workflow.governance()["adoption"]' in ui_source
    current = governance_page.page()["governance"]["adoption"]
    markdown = governance_page.markdown()
    assert f"Adoption level: {current['name']}" in markdown
    assert current["consequence"] in markdown


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


# --- what actually defends each rule (found by driving the page) ------------

def test_each_pin_names_its_test_not_just_its_file():
    """C2 declares three pins, all in test_critic.py, and the renderer printed
    only the file — so the shareable governance document showed three identical
    lines. The names it dropped (never_reads_the_critic / has_no_write_tools /
    never_moves_a_review_status) are precisely the three clauses of C2's own
    statement, so a reader asking "what defends this rule?" got noise where the
    answer was already in the model."""
    md = governance_page.markdown()
    c2 = md[md.index("**C2 "):]
    c2 = c2[:c2.index("**C3 ")]
    lines = [l for l in c2.splitlines() if "pinned by" in l]
    assert len(lines) >= 2, "C2 should declare more than one pin"
    assert len(set(lines)) == len(lines), \
        f"the pin lines are not distinguishable from each other:\n{c2}"
    assert all("::" in l for l in lines), \
        "a pin line names only a file; the test name is in the model and dropped"


def test_a_pin_whose_test_function_is_gone_reports_as_missing(tmp_path):
    """The page's promise is that a deleted pin shows up as an undefended rule.
    A file-level existence check under-delivers on exactly that: deleting the
    FUNCTION leaves the file in place and the page goes on claiming the rule is
    defended. registry/tests/test_constitution.py resolves pins this way and
    breaks the build on an orphan; this keeps the document agreeing with it."""
    real = "registry/tests/test_critic.py"
    assert governance_page._pin_exists(real, "test_critic_phase_has_no_write_tools")
    assert not governance_page._pin_exists(real, "test_this_function_does_not_exist")
    assert not governance_page._pin_exists("registry/tests/no_such_file.py", None)
    # A pin with no named test still counts on the file alone — some pins are
    # whole shell suites (tests/gate-adversarial.sh) with no function to find.
    assert governance_page._pin_exists("tests/gate-adversarial.sh", None)


def test_no_clause_currently_reports_a_missing_pin():
    """The build is green, so the document must agree. If this fails while
    test_constitution passes, the two disagree about what an orphan is."""
    md = governance_page.markdown()
    assert "MISSING" not in md, \
        "the governance page reports an orphaned pin the build did not catch"


def test_the_page_itself_marks_an_orphaned_function_missing(tmp_path, monkeypatch):
    """The mutation this file first failed to kill.

    The tests above exercise _pin_exists directly, and the estate has no
    orphaned pins — so reverting the page to a file-only check rendered
    identically and survived. What must hold is that the PAGE uses it, which
    only a constitution containing an orphan can show.
    """
    fake = tmp_path / "constitution.yaml"
    fake.write_text(
        "clauses:\n"
        "  - id: CX\n"
        "    statement: a rule whose defender was deleted\n"
        "    category: safety\n"
        "    pins:\n"
        "      - file: registry/tests/test_critic.py\n"
        "        test: test_this_function_was_deleted\n", encoding="utf-8")
    monkeypatch.setattr(governance_page, "CONSTITUTION", fake)

    cx = next(c for c in governance_page.clauses() if c["id"] == "CX")
    assert cx["pins"][0]["exists"] is False, \
        "the page's own model says a deleted test function still defends the rule"
    assert cx["pin_missing"], "the clause is not reported as having a missing pin"
    assert "MISSING" in governance_page.markdown(), \
        "the rendered document does not warn that the rule is undefended"

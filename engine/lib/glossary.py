"""Single-source, presentation-only vocabulary for the SDD journey.

Machine state names and artifact paths remain unchanged.  This module owns the
plain-language layer so dashboard copy, tooltips, and tests cannot each invent
a subtly different meaning for approval, signing, or enforcement.
"""
from __future__ import annotations

import html
import re


TERMS = {
    "test-plan": {
        "label": "test plan",
        "meaning": "The reviewable list of scenarios the platform proposes before writing tests.",
        "consequence": "A human approves it before generation continues.",
        "internal": "specs/<KEY>/testplan.yaml or testplans/<KEY>.md",
    },
    "approved-signed": {
        "label": "approved test plan (signed)",
        "meaning": "A human approved this exact structured plan and its content hash was recorded.",
        "consequence": "Scenario-level review, drift watching, and configured coverage enforcement can apply.",
        "internal": "plan_state status=approved plus structured plan signature",
    },
    "approved-prose": {
        "label": "approved test plan (prose — not signed)",
        "meaning": "A human approved a legacy prose plan that cannot carry a structured signature.",
        "consequence": "Generation may continue, but drift watching and scenario-level enforcement do not apply.",
        "internal": "testplans/<KEY>.md without specs/<KEY>/testplan.yaml",
    },
    "acceptance-criteria": {
        "label": "acceptance criteria (EARS)",
        "meaning": "Ticket expectations formalized into testable EARS statements.",
        "consequence": "When the criteria gate is enabled, planning waits for their validation and approval.",
        "internal": "specs/<KEY>/requirements.yaml",
    },
    "signed": {
        "label": "signed",
        "meaning": "A human approved the exact structured plan identified by its content hash.",
        "consequence": "Later changes are detected and require another approval.",
        "internal": "plan_state signature",
    },
    "scenario": {
        "label": "scenario",
        "meaning": "One independently reviewable behavior in a structured test plan.",
        "consequence": "Generated tests and coverage evidence refer back to its stable id.",
        "internal": "testplan.yaml scenarios[].id",
    },
    "coverage-enforcement": {
        "label": "coverage enforcement",
        "meaning": "The delivery gate compares generated tests with approved plan scenarios.",
        "consequence": "Depending on mode, uncovered scenarios are ignored, reported, or refused.",
        "internal": "spec.enforce",
    },
    "waiver": {
        "label": "waiver",
        "meaning": "A time-bounded decision to deliver an approved scenario without a test.",
        "consequence": "It needs a reason, owner, and expiry and is visible to the gate.",
        "internal": "specs/<KEY>/waivers.yaml",
    },
    "drift": {
        "label": "drift",
        "meaning": "The application surface changed after a structured plan was signed.",
        "consequence": "Affected scenarios are flagged for review instead of silently treated as current.",
        "internal": "drift watcher",
    },
    "stale": {
        "label": "stale",
        "meaning": "A previously approved scenario no longer matches the current application surface.",
        "consequence": "The team must re-approve or retire it before treating the plan as current.",
        "internal": "scenario drift state=stale",
    },
    "governance": {
        "label": "governance",
        "meaning": "The resolved settings that determine which planning and coverage rules are active.",
        "consequence": "The journey reports these resolved values so displayed behavior matches the engine.",
        "internal": "spec_workflow.governance()",
    },
    "off": {
        "label": "off",
        "meaning": "Coverage enforcement does not inspect uncovered scenarios.",
        "consequence": "The delivery gate neither reports nor refuses them.",
        "internal": "spec.enforce=off",
    },
    "warn": {
        "label": "warn",
        "meaning": "Coverage enforcement reports uncovered scenarios as a dry run.",
        "consequence": "The delivery gate still commits while the team evaluates the signal.",
        "internal": "spec.enforce=warn",
    },
    "strict": {
        "label": "strict",
        "meaning": "Coverage enforcement refuses an uncovered, unwaived approved scenario.",
        "consequence": "The gate exits with code 8 until the scenario is covered or waived.",
        "internal": "spec.enforce=strict",
    },
    "born-mapped": {
        "label": "born-mapped",
        "meaning": "A generated test and its catalog mapping are created in the same change.",
        "consequence": "The gate refuses a new test whose coverage provenance is missing.",
        "internal": "catalog/generated.jsonl and gate exit 4",
    },
}


STATE_LABELS = {
    "requirements": "Acceptance criteria awaiting validation",
    "plan": "Test plan being authored",
    "approved": "Plan awaiting your approval",
    "tests": "Tests awaiting generation",
    "committed": "Tests awaiting delivery",
    "live": "Delivered — running in CI",
}


STATE_BLOCKER_PINS = {
    "requirements": ("blocking ambiguity", "not approved"),
    "plan": ("no test plan authored yet",),
    "approved": ("not approved",),
    "tests": ("tests not generated",),
    "committed": ("no gate commit recorded",),
    "live": ("",),
}


HOW_IT_WORKS_MARKUP = (
    "You bring a ticket, and the platform drafts a {{term:test-plan}} made of reviewable {{term:scenario}} items.",
    "A human approves it; a structured plan becomes an {{term:approved-signed}}, while a legacy plan remains an {{term:approved-prose}}.",
    "Generation follows that decision, and {{term:coverage-enforcement}} can require every approved scenario to be covered or have a {{term:waiver}}.",
    "If the application changes later, {{term:drift}} marks affected scenarios {{term:stale}} for another decision.",
    "{{term:acceptance-criteria}}, {{term:signed}}, {{term:governance}}, {{term:off}}, {{term:warn}}, {{term:strict}}, and {{term:born-mapped}} are the machinery supporting that loop.",
)

# Deliberately explicit rather than derived from TERMS.  Adding a definition
# without deciding where it appears must fail the bidirectional coverage pin.
GLOSSARY_CARD_TERMS = (
    "test-plan", "approved-signed", "approved-prose", "acceptance-criteria",
    "signed", "scenario", "coverage-enforcement", "waiver", "drift", "stale",
    "governance", "off", "warn", "strict", "born-mapped",
)

_REFERENCE = re.compile(r"\{\{term:([a-z0-9-]+)\}\}")


def referenced_terms(text: str) -> set[str]:
    """Return the closed term ids used by marked copy."""
    return set(_REFERENCE.findall(text or ""))


def term_html(term_id: str) -> str:
    """Render one trusted definition as accessible, escaped tooltip HTML."""
    try:
        term = TERMS[term_id]
    except KeyError as exc:
        raise ValueError(f"undefined glossary term: {term_id}") from exc
    title = f"{term['meaning']} {term['consequence']} Internal: {term['internal']}"
    return (
        f'<span class="sdd-term" tabindex="0" title="{html.escape(title, quote=True)}">'
        f'{html.escape(term["label"])} <span aria-hidden="true">ⓘ</span></span>'
    )


def render_markup(text: str) -> str:
    """Escape arbitrary copy and expand only closed ``{{term:id}}`` markers."""
    out = []
    pos = 0
    for match in _REFERENCE.finditer(text or ""):
        out.append(html.escape(text[pos:match.start()]))
        out.append(term_html(match.group(1)))
        pos = match.end()
    out.append(html.escape((text or "")[pos:]))
    return "".join(out)


def how_it_works_html() -> str:
    return "".join(f"<p>{render_markup(sentence)}</p>" for sentence in HOW_IT_WORKS_MARKUP)


def glossary_card_html() -> str:
    rows = []
    for term_id in GLOSSARY_CARD_TERMS:
        term = TERMS[term_id]
        rows.append(
            f"<dt>{term_html(term_id)}</dt><dd>{html.escape(term['meaning'])} "
            f"{html.escape(term['consequence'])}</dd>"
        )
    return '<dl class="sdd-glossary">' + "".join(rows) + "</dl>"

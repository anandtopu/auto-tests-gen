"""A plan attached by the MOCK tracker was reported as one on a real ticket.

FIFTH SIGNAL IN THE IRON-RULE FAMILY, after cost, the critic score, the
validation counts and the reviewer verdict. Those four are things a mock
FABRICATES; this one is a claim about an EXTERNAL SYSTEM, which is the sharper
kind: "the ticket has your test plan attached" is checkable by the reader, and
wrong.

MEASURED by driving use case 3's delivery step on this estate (AIQE_MOCK=1, the
demo default):

    make attach-plan KEY=PROJ-301
    -> [mock-jira] attached to PROJ-301: out/mock-jira-attachments/...
    make plans
    -> PROJ-301  draft  linked: yes

Nothing left the machine. Five renderers printed the bare claim: `qa.py plan
list`, `plan_state`'s own CLI, the dashboard's STATIC plan table, the dashboard's
SERVED plan table, and `agent_context`, which injects "It has already been
linked/commented on the ticket." into every agent launch - a false statement fed
to a model that will act on it.

Three that were already honest, and they are why this is a defect rather than a
design: `qa.py plan show` prints the ref, the wizard shows the ref, and
`plan_state`'s J6 comment line prints the ref - and the ref happens to carry the
adapter's own `[mock-jira]` prefix. THE COMMENT PATH HAD NO SUCH LUCK: its
`result` string is built by plan_state itself ("commented on PROJ-301"), so a
simulated comment read exactly like a real one everywhere.

THE FIX IS THE ESTABLISHED ONE: the PRODUCER declares itself (both delivery
paths already select their adapter on `env_flag.mock()`), the flag TRAVELS with
the fact through `summary()`, and one decision function renders it. Re-deriving
it in five renderers is how there came to be five that did not.

`simulated` absent stays UNKNOWN rather than being read as real: entries
recorded before the flag existed cannot be re-derived, and guessing "real" is
the direction that invents a delivery.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import agent_context                                      # noqa: E402
import plan_state                                         # noqa: E402
import wizard_status                                      # noqa: E402


@pytest.fixture
def isolated_plans(tmp_path, monkeypatch):
    """A scratch plan store, restored so the REST OF THE SESSION is unaffected.

    `plan_state.DIR` and `PLAN_DIR` are computed at IMPORT, so exercising them
    means reloading the module - and reloading it again at the end of the test
    body is not enough: monkeypatch has not torn down yet, so the module sticks
    to the fixture's tmp_path for every later test. Measured: eight failures
    across three other files, all `no test plan for PROJ-301`, in a full run
    that a targeted run had reported green. Undo the environment FIRST, then
    reload.

    This is the module-state form of the rule this repo already records for
    destructive fixtures: a test that damages what it is protecting is not a
    test.
    """
    import importlib
    monkeypatch.setenv("AIQE_PLAN_DIR", str(tmp_path))
    monkeypatch.setenv("AIQE_TESTPLAN_DIR", str(tmp_path))
    importlib.reload(plan_state)
    importlib.reload(wizard_status)
    try:
        yield tmp_path
    finally:
        monkeypatch.undo()
        importlib.reload(plan_state)
        importlib.reload(wizard_status)


@pytest.mark.parametrize("row,expect", [
    ({"linked": True, "linked_simulated": True}, "~yes"),
    ({"linked": True, "linked_simulated": False}, "yes"),
    ({"linked": True, "linked_simulated": None}, "yes?"),
    ({"linked": False}, "-"),
])
def test_the_narrow_column_has_three_states(row, expect):
    assert plan_state.linked_cell(row) == expect, row


def test_a_real_delivery_is_never_qualified():
    """THE OVER-FIX DIRECTION, pinned as hard as the defect. Marking a genuine
    attachment `~` is what teaches a reader to ignore the marker everywhere."""
    assert plan_state.linked_cell({"linked": True,
                                   "linked_simulated": False}) == "yes"
    assert "~" not in plan_state.linked_cell({"linked": True,
                                              "linked_simulated": False})


def test_the_flag_travels_from_the_store_to_the_projection(isolated_plans):
    """PIN THE CALLER, NOT ONLY THE STORE. A store that keeps the flag while
    `summary()` drops it is exactly the shape the review board had: the value
    was preserved and the projection every renderer reads threw it away."""
    (isolated_plans / "ZZ-DEL-1.md").write_text("# plan", encoding="utf-8")
    plan_state.set_status("ZZ-DEL-1", "draft", by="t", note="n")
    plan_state.mark_linked("ZZ-DEL-1", "[mock-jira] attached", by="t",
                           simulated=True)
    row = next(r for r in plan_state.summary() if r["key"] == "ZZ-DEL-1")
    assert row["linked"] is True
    assert row["linked_simulated"] is True
    assert plan_state.linked_cell(row) == "~yes"


def test_an_unknown_delivery_is_not_stamped_as_real(isolated_plans):
    """Writing `simulated: False` for a delivery nobody vouched for converts
    "we cannot tell" into "a real ticket has this", permanently."""
    (isolated_plans / "ZZ-DEL-2.md").write_text("# plan", encoding="utf-8")
    plan_state.set_status("ZZ-DEL-2", "draft", by="t", note="n")
    plan_state.mark_linked("ZZ-DEL-2", "ref", by="t")          # no declaration
    entry = plan_state.get("ZZ-DEL-2")
    assert "simulated" not in entry["linked"], entry["linked"]
    row = next(r for r in plan_state.summary() if r["key"] == "ZZ-DEL-2")
    assert plan_state.linked_cell(row) == "yes?"


def test_the_attach_path_declares_what_it_used():
    """The producer is the only place that KNOWS. `attach_to_jira` picks the
    adapter on `env_flag.mock()` two lines above, so passing that same value on
    is free; deriving it later is not possible at all."""
    src = (ROOT / "engine/lib/export_plan.py").read_text(encoding="utf-8")
    assert "mark_linked(key, ref, by, simulated=mock)" in src, \
        "the attach path records a link without saying whether it was real"


def test_the_comment_path_declares_itself_too():
    """The sibling with no accidental marker: this `result` is built here, not
    echoed from the adapter, so nothing else could ever have told the two
    apart."""
    src = (ROOT / "engine/lib/plan_state.py").read_text(encoding="utf-8")
    i = src.index('state[key]["commented"]')
    assert 'env_flag.mock()' in src[i:i + 400], \
        "a posted comment is recorded without its delivery provenance"


def test_the_agent_is_not_told_a_mock_delivery_happened():
    """agent_context is injected into every launch, so an unqualified sentence
    is a false statement handed to a model that will act on it."""
    entry = {"status": "draft", "linked": {"ref": "r", "simulated": True}}
    out = agent_context._plan_block.__doc__  # noqa: F841 - documented below
    src = (ROOT / "engine/lib/agent_context.py").read_text(encoding="utf-8")
    i = src.index("already been linked/commented")
    assert "MOCK tracker" in src[i:i + 400], \
        "the agent block claims a delivery without qualifying a mock one"
    assert 'simulated") is True' in src[max(0, i - 400):i + 400], \
        "the qualifier is not driven by the recorded flag"
    assert entry["linked"]["simulated"] is True      # fixture sanity


@pytest.mark.parametrize("simulated,expect_marker", [(True, True),
                                                     (False, False)])
def test_the_wizard_step_says_so_in_words(isolated_plans, simulated,
                                          expect_marker):
    """DRIVEN, both directions. The first version of this pin read SOURCE TEXT
    around the step, and a mutation gutting the condition left the string
    sitting on a line that could never run - the weakness this repo records
    every time a branch is pinned by grep."""
    (isolated_plans / "ZZ-DEL-3.md").write_text("# plan", encoding="utf-8")
    plan_state.set_status("ZZ-DEL-3", "draft", by="t", note="n")
    plan_state.mark_linked("ZZ-DEL-3", "ref-only", by="t",
                           simulated=simulated)
    step = next(s for s in wizard_status.build("ZZ-DEL-3", "jira")["steps"]
                if "Link" in s["label"])
    assert step["state"] == "done", step
    assert ("MOCK tracker" in step["detail"]) is expect_marker, step


def _plan_js():
    """The SERVED plan row's chip, lifted out so it can be RUN. This renderer
    is the one the fix nearly missed: it lives in the same file as the static
    one and was found by grepping for the rendered STRING, not for the code
    just patched."""
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    i = src.index("function planLinkedChip(p) {")
    depth = 0
    for j in range(src.index("{", i), len(src)):
        depth += (src[j] == "{") - (src[j] == "}")
        if depth == 0:
            break
    return src[i:j + 1]


def test_the_served_plan_table_branches_on_the_flag():
    exe = shutil.which("node")
    if not exe:                                  # pragma: no cover
        pytest.skip("node is required to execute the plan chip")
    script = _plan_js() + """
const rows = [
  {linked: true, linked_simulated: true},
  {linked: true, linked_simulated: false},
  {linked: true},
  {linked: false},
];
console.log(JSON.stringify(rows.map(planLinkedChip)));
"""
    r = subprocess.run([exe, "-e", script], capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, timeout=60)
    assert r.returncode == 0, r.stderr[-600:]
    sim, real, unknown, none = json.loads(r.stdout)
    assert "simulated" in sim and "chip-success" not in sim, sim
    assert "chip-success" in real and "simulated" not in real, real
    assert "provenance not recorded" in unknown, unknown
    assert "linked" not in none, none


def test_no_renderer_prints_a_bare_tick_from_the_linked_boolean():
    """THE INVARIANT. Two of these lived in one file, which is exactly what a
    per-file check cannot see - the lesson the fifth critic renderer taught."""
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    for m in re.finditer(r"linked", src):
        line = src[src.rfind("\n", 0, m.start()) + 1:
                   src.find("\n", m.start())]
        if "chip-success" in line and "linked" in line:
            assert "planLinkedChip" in line or "_linked_chip" in line or \
                   "return" in line, \
                f"a plan row renders the success chip inline: {line.strip()}"

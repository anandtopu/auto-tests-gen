"""The run record recorded what it lost, and every reader ignored it.

`run_record.py` writes three "we could not read this" markers, and the
comments at those write sites state the requirement in as many words:

  * `malformed_gate_lines` — "a record showing three gates when the file held
    four must SAY that it is short, or `gates: [...]` reads as the complete
    set."
  * `contract_unreadable` — the phase stays LISTED with the failure named,
    because "skipping it silently would make an unreadable contract
    indistinguishable from a phase that never ran (C13)".
  * `context_retries` — "an honest marker that this run paid the retry."

MEASURED across every production module: not one read any of the three. The
writer stated the guarantee and every reader broke it, which is the shape
already recorded here for `alert_rules.evaluate` — a function whose docstring
promised it never raises while the line below it did.

The sharpest instance was `explain`, whose gate answer is
`f"{len(committed)} of {len(gates)} repo(s) committed"`: `len(gates)` is the
count of rows that SURVIVED parsing, so a torn line turns "1 of 2" into
"1 of 1" and the run reads as complete. That file already refuses to do this
twelve lines above, for comment receipts.

Silence on a healthy run is pinned as hard as the warning: none of the 443
records in this estate carries any of these keys, so a caveat that fired
without one would be noise on every run forever.
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import pr_comment                                              # noqa: E402
import record_caveats                                          # noqa: E402

CLEAN = {
    "run_id": "r1", "ts": 0,
    "trigger": {"type": "pr", "key": "PR-orders-api-201"},
    "overall": "committed",
    "gates": [{"test_repo": "e2e-api-tests-1", "status": "committed",
               "exit_code": 0, "commit": "abc1234"}],
    "phases": [
        {"name": "triage", "contract": {"areas": ["a"], "impact": "x",
                                        "risk": "low"}},
        {"name": "generate",
         "contract": {"tests": [{"file": "a.spec.js", "action": "created"}]}},
        {"name": "validate", "contract": {"passed": 1, "failed": 0,
                                          "repair_loops": 0}},
    ],
}


def _damaged(**extra):
    rec = json.loads(json.dumps(CLEAN))
    rec.update(extra)
    return rec


# ------------------------------------------------------------ the decision

def test_a_clean_record_produces_no_caveats():
    """The over-fix guard. These keys are absent from every record in this
    estate, so a caveat firing without one is noise on every run forever."""
    assert record_caveats.caveats(CLEAN) == []
    assert record_caveats.gates_note(CLEAN) == ""


def test_a_short_gate_list_says_it_is_short():
    note = record_caveats.gates_note(_damaged(malformed_gate_lines=2))
    assert "INCOMPLETE" in note
    assert "2 gate result line(s)" in note


def test_an_unreadable_contract_is_not_a_phase_that_never_ran():
    rec = _damaged()
    rec["phases"][1]["contract"] = None
    rec["phases"][1]["contract_unreadable"] = "JSONDecodeError: line 1"
    lines = record_caveats.caveats(rec)
    assert any("generate" in l and "RAN" in l for l in lines), lines
    assert any("not the same as it having reported nothing" in l
               for l in lines), lines


def test_a_context_retry_is_reported_with_the_phase():
    lines = record_caveats.caveats(
        _damaged(context_retries=[{"phase": "testdata", "missing": "schema"}]))
    assert any("testdata" in l and "missing context" in l for l in lines), lines


def test_a_damaged_damage_marker_is_not_trusted():
    """The count comes from a record assembled from a TSV that may have been
    torn mid-write, so the marker itself can be junk. A bad value must not
    manufacture a warning, and True must not read as 1."""
    for bad in (None, "2", -1, 0, True, [], {"n": 2}):
        assert record_caveats.gates_note(
            _damaged(malformed_gate_lines=bad)) == "", bad


def test_a_malformed_phase_list_does_not_crash_the_reader():
    """Phases are LLM output that reached disk; one bad entry must not take
    down the surface, the class this repo has already been bitten by."""
    rec = _damaged()
    rec["phases"] = ["not a phase", None, {"name": "x",
                                           "contract_unreadable": "boom"}]
    assert len(record_caveats.unreadable_phases(rec)) == 1
    rec["context_retries"] = ["nope", {"nophase": 1}]
    assert record_caveats.retried_phases(rec) == []


def test_caveats_lead_with_what_changes_a_conclusion_soonest():
    rec = _damaged(malformed_gate_lines=1,
                   context_retries=[{"phase": "testdata", "missing": ""}])
    rec["phases"][1]["contract_unreadable"] = "boom"
    lines = record_caveats.caveats(rec)
    assert len(lines) == 3
    assert "INCOMPLETE" in lines[0], "the wrong-set warning must come first"


# --------------------------------------------------- it reaches the reader

def test_both_pr_channels_say_the_gate_list_is_short():
    """The PR comment is the surface a human MERGES from. The first version of
    this fix patched only the ticket channel -- the wrong composer, the trap
    this repo's history already records for exactly this module."""
    md = pr_comment.from_record(_damaged(malformed_gate_lines=2))
    assert "INCOMPLETE" in md, md
    proj = pr_comment.delivery_projection(
        {"areas": ["a"], "impact": "x", "risk": "low"},
        {"tests": [{"file": "a.spec.js", "action": "created"}]},
        {"passed": 1, "failed": 0, "repair_loops": 0},
        [{"repo": "e2e-api-tests-1", "status": "committed", "exit_code": 0,
          "sha": "abc1234"}],
        None, None, [], "r1", "PR-x-1",
        gates_note=record_caveats.gates_note_for(2))
    assert "INCOMPLETE" in pr_comment.render_ticket(proj)


def test_a_clean_run_gets_no_note_on_the_pull_request():
    md = pr_comment.from_record(CLEAN)
    assert "INCOMPLETE" not in md
    assert "could not be parsed" not in md


def test_the_live_composer_counts_what_it_drops(tmp_path):
    """`build()` parses gate_results.tsv itself and used to drop a short line
    with no count at all, so a comment posted DURING the run was more
    confident than the same comment replayed from the record afterwards."""
    out = tmp_path / "out"
    out.mkdir()
    for name, contract in (("triage", {"areas": ["a"], "impact": "x",
                                       "risk": "low"}),
                           ("generate", {"tests": [{"file": "a.spec.js",
                                                    "action": "created"}]}),
                           ("validate", {"passed": 1, "failed": 0,
                                         "repair_loops": 0})):
        (out / f"{name}.contract.json").write_text(json.dumps(contract),
                                                   encoding="utf-8")
    (out / "gate_results.tsv").write_text(
        "e2e-api-tests-1\tcommitted\t0\tabc1234\ntorn-line\n", encoding="utf-8")
    md = pr_comment.build(tmp_path, "r1", "PR-x-9")
    assert "INCOMPLETE" in md, md


def _gate_decision(rec, monkeypatch, tmp_path):
    import explain
    import run_progress
    monkeypatch.setattr(run_progress, "_record_for",
                        lambda *a, **k: rec)
    out = explain.explain(key="PR-orders-api-201", root=tmp_path)
    return next(d for d in out["decisions"] if d["id"] == "gate")


def test_explain_does_not_report_a_short_denominator_as_the_run(monkeypatch,
                                                                tmp_path):
    """`len(gates)` is the denominator of explain's headline answer, and it
    counts the rows that survived parsing."""
    gate = _gate_decision(_damaged(malformed_gate_lines=1), monkeypatch,
                          tmp_path)
    assert "OF THE RESULTS THAT SURVIVED PARSING" in gate["answer"], gate
    assert "INCOMPLETE" in (gate.get("caveat") or ""), gate


def test_explain_leaves_a_clean_gate_answer_alone(monkeypatch, tmp_path):
    gate = _gate_decision(CLEAN, monkeypatch, tmp_path)
    assert "SURVIVED PARSING" not in gate["answer"]
    assert "deterministic" in (gate.get("caveat") or "")


# ------------------------------------------------------------ the invariant

# Surfaces that render a per-repo gate list TO A PERSON. Others read `gates`
# only to count committed repos, where a lost row understates a total rather
# than presenting a short list as a complete one. Driving qa.py or
# bin/dashboard.py here would mean writing a fixture run record into the
# estate's shared reports/runs (there is no redirect knob), which is how an
# earlier test fed the scorecard its own traffic -- so those two are checked
# by asking whether the gate-rendering code consults the decision function.
_HUMAN_GATE_SURFACES = {
    "bin/qa.py": "gates",
    "bin/dashboard.py": "gate-line",
}


def test_every_human_facing_gate_renderer_consults_the_one_definition():
    missing = [p for p in _HUMAN_GATE_SURFACES
               if "record_caveats" not in
               (ROOT / p).read_text(encoding="utf-8")]
    assert not missing, f"{missing} render a gate list without asking whether " \
                        f"it is complete"


def test_the_surfaces_named_here_still_render_gates():
    """Guards the list above against rot: an entry that stopped rendering
    gates would keep passing while defending nothing."""
    for path, marker in _HUMAN_GATE_SURFACES.items():
        src = (ROOT / path).read_text(encoding="utf-8")
        assert re.search(r"""get\(["']gates["']""", src), \
            f"{path} no longer reads gates; this entry defends nothing"
        assert marker in src, f"{path} lost its gate rendering marker"

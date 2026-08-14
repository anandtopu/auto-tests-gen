"""A simulated quality score must never read as a measured one.

The iron rule, applied to the CRITIC instead of to money. The mock critic emits
a hardcoded `score: 0.86, verdict: accept, noise_count: 0`, and driving
`bin/qa.py critic` printed ten consecutive runs at `0.86 accept` with nothing to
say the number came from a stub -- then `average score 0.86 over 206 scored
run(s)`, which is the stub's default read back and presented as a quality
result.

TWO SURFACES WERE ALREADY HONEST, which is what makes the rest defects rather
than a design choice: `eval/scorecard.py` reports `n/a` for critic metrics
(CLAUDE.md records that fix), and `parity_compare` excludes simulated runs from
per-provider comparison outright. Five renderers were not: `qa.py critic`, the
dashboard Runs chip, `engine/lib/trace.py`, and BOTH `pr_comment` renderings --
that last one being the sharp end, because it posts the figure on the pull
request a human merges from, exactly where the reviewer-reason defect landed.

The fix is one decision function (`critic.provenance`) plus the flag TRAVELLING
with the score -- stamped onto the run record where the critic phase's spend is
in hand, and stored on the review board, which outlives the record's phases[].
Re-deriving it in five renderers is how there came to be five renderers that
did not.

THREE STATES, NOT TWO (C13). `unknown` is a real answer: a board entry written
before the flag existed carries a score and nothing about where it came from,
and the live PR composer has only the scratch ledger, which records no basis for
a mock phase today. Guessing `measured` there is the lie being fixed; guessing
`simulated` would libel a real score.
"""
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import critic as critic_lib                                      # noqa: E402
import pr_comment                                                # noqa: E402


SIGNAL = {"score": 0.86, "verdict": "accept", "noise_count": 0,
          "specs_reviewed": 2, "findings": [], "rationale": ""}


def _record(critic_spend):
    phases = [{"name": "generate", "contract": {}, "spend": {"simulated": False,
                                                             "cost_usd": 0.2}}]
    if critic_spend is not None:
        phases.append({"name": "critic", "contract": dict(SIGNAL),
                       "spend": critic_spend})
    return {"run_id": "r1", "trigger": {"type": "pr", "key": "PR-x-1"},
            "phases": phases, "gates": [], "critic": dict(SIGNAL)}


# ----------------------------------------------------------- the three states

def test_a_mock_critic_phase_is_simulated():
    rec = _record({"simulated": True, "cost_usd": 0.0, "provider": "mock"})
    assert critic_lib.provenance(rec["critic"], rec) == "simulated"


def test_a_real_critic_phase_is_measured():
    rec = _record({"simulated": False, "cost_usd": 0.004, "provider": "claude"})
    assert critic_lib.provenance(rec["critic"], rec) == "measured"


def test_no_critic_phase_at_all_is_unknown():
    """Not 'measured'. A score with no phase to vouch for it is unattributed."""
    assert critic_lib.provenance(SIGNAL, _record(None)) == "unknown"


def test_a_critic_phase_with_no_spend_block_is_unknown():
    """The gap a mutation found in my own pins.

    A phase can be recorded with no spend at all -- a record written before
    spend tracking, or a phase that ended before it metered anything. Reading
    the absent block as "not simulated" turns silence into a claim that a real
    model produced the score, which is the whole defect one level down.
    """
    assert critic_lib.provenance(SIGNAL, _record({})) == "unknown"


def test_a_stored_flag_wins_over_re_derivation():
    """review_state has no phases[] to consult, so the stored flag is the only
    thing standing between a board entry and a fabricated measurement."""
    assert critic_lib.provenance(dict(SIGNAL, simulated=True)) == "simulated"
    assert critic_lib.provenance(dict(SIGNAL, simulated=False)) == "measured"


def test_a_generate_phase_being_real_does_not_make_the_score_real():
    """Only the critic phase can vouch for the critic's score. The fixture's
    generate spend is deliberately measured in every case above."""
    rec = _record({"simulated": True, "cost_usd": 0.0})
    assert critic_lib.provenance(rec["critic"], rec) == "simulated"


# ------------------------------------------------------------- the rendering

def test_only_a_measured_score_is_rendered_bare():
    assert critic_lib.score_text(dict(SIGNAL, simulated=False)) == "0.86"
    assert critic_lib.score_text(dict(SIGNAL, simulated=True)) == "~0.86"
    assert critic_lib.score_text(SIGNAL) == "0.86?"


def test_the_footnote_appears_only_for_markers_actually_shown():
    """The over-fix guard. A caveat printed beside a fully measured set is one
    readers learn to skip, and that is how the real ones stop landing."""
    assert critic_lib.provenance_note(["measured"]) == []
    assert critic_lib.provenance_note([]) == []
    assert any("SIMULATED" in n
               for n in critic_lib.provenance_note(["simulated", "measured"]))


# --------------------------------------------------- the pull-request comment

def _pr_body(prov, renderer="render_pr"):
    """Compose the DELIVERY comment, which is the one posted on the PR.

    `from_record` is the coverage-delta report and carries no critic line -- an
    earlier version of these tests drove it and saw nothing, which proves only
    that the wrong composer was asked. The projection also needs a test and a
    gate: render_pr is total over an empty run and would have rendered nothing
    to assert on.
    """
    proj = pr_comment.delivery_projection(
        {}, {"tests": [{"file": "a.spec.js", "action": "created"}]}, {},
        [{"repo": "e2e", "status": "committed", "sha": "abc1234"}],
        {"score": 0.86, "verdict": "accept", "provenance": prov},
        None, [], "r1", "PR-x-1")
    return getattr(pr_comment, renderer)(proj)


@pytest.mark.parametrize("renderer", ["render_pr", "render_ticket"])
def test_the_pr_comment_says_simulated_in_words(renderer):
    """A PR comment is prose a human skims once on the way to merging, so `~`
    would carry none of the meaning it has in a cost table with a legend.

    Both channels, because the defect was in both and fixing the markdown one
    alone leaves the ticket comment lying.
    """
    line = next(l for l in _pr_body("simulated", renderer).splitlines()
                if "critic" in l.lower())
    assert "SIMULATED" in line, line
    assert "not a measurement" in line


@pytest.mark.parametrize("renderer", ["render_pr", "render_ticket"])
def test_the_pr_comment_does_not_qualify_a_measured_score(renderer):
    """Pinned as hard as the defect: hedging a real score is the mirror lie."""
    line = next(l for l in _pr_body("measured", renderer).splitlines()
                if "critic" in l.lower())
    assert "SIMULATED" not in line and "not recorded" not in line, line
    assert "0.86" in line


@pytest.mark.parametrize("renderer", ["render_pr", "render_ticket"])
def test_the_pr_comment_admits_an_unrecorded_provenance(renderer):
    line = next(l for l in _pr_body("unknown", renderer).splitlines()
                if "critic" in l.lower())
    assert "not recorded" in line, line


# ------------------------------------------------------------------ the CLI

def test_the_cli_refuses_to_average_simulated_scores():
    """`average score 0.86 over 206 scored run(s)` was the stub read back.

    Driven, because the averaging is in the command and not in the library --
    the same reason the selection-CLI defect needed driving to find.
    """
    r = subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), "critic", "-n", "3"],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 0, r.stderr[-500:]
    out = r.stdout
    if "no critic signal recorded yet" in out:
        pytest.skip("estate carries no critic signal to render")

    # The expected form is DERIVED from the estate, not asserted as an `or` of
    # both. The first version of this pin accepted either wording, so the
    # mutation restoring the defect satisfied it and survived -- a weak pin,
    # caught only because a control mutation proved the harness worked.
    import glob
    measured = 0
    for f in glob.glob(str(ROOT / "reports/runs/[0-9]*.json")):
        try:
            rec = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        if rec.get("critic") and critic_lib.provenance(rec["critic"], rec) == "measured":
            measured += 1
    if measured:
        assert "MEASURED run(s)" in out, out[-600:]
        assert "average score: n/a" not in out
    else:
        assert "average score: n/a" in out, (
            "every scored run on this estate is simulated, so an average is "
            "the stub read back:\n" + out[-600:])
    if "~" in out:
        assert "SIMULATED" in out, "the ~ marker is shown with no legend"


def test_every_renderer_asks_the_one_decision_function():
    """The invariant, not today's five call sites: a sixth renderer that
    formats `score` itself is how this defect got to five in the first place."""
    import re
    offenders = []
    for rel in ("bin/qa.py", "bin/dashboard.py", "engine/lib/trace.py",
                "engine/lib/pr_comment.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        if "critic" not in src:
            continue
        assert re.search(r"critic_lib\.(provenance|score_text)|_critic_caveat", src), \
            f"{rel} renders a critic score without consulting critic.provenance"
    assert not offenders


# --------------------------------------------------------- the durable store

def test_the_board_keeps_the_provenance_it_was_given(tmp_path, monkeypatch):
    """`qa.py trace` reads review_state, which has no phases[] to consult.

    Without this the board can only ever answer "not recorded", and a mock's
    fixed score sits there indefinitely looking like a measurement.
    """
    import review_state
    monkeypatch.setattr(review_state, "FILE", tmp_path / "reviews.json")
    review_state.set_critic("PR-x-1", dict(SIGNAL, simulated=True))
    entry = review_state.load()["PR-x-1"]["critic"]
    assert entry["simulated"] is True


def test_the_board_does_not_invent_a_provenance(tmp_path, monkeypatch):
    """A signal that never carried the flag must not be stored as measured.

    Absent and False are different claims, and the stored one is permanent.
    """
    import review_state
    monkeypatch.setattr(review_state, "FILE", tmp_path / "reviews.json")
    review_state.set_critic("PR-x-2", dict(SIGNAL))
    entry = review_state.load()["PR-x-2"]["critic"]
    assert "simulated" not in entry, \
        "an unrecorded provenance was stored as a definite answer"



# ------------------------------------- the pin that let a fifth renderer through

RENDERERS = ("bin/dashboard.py", "bin/qa.py", "engine/lib/trace.py",
             "engine/lib/pr_comment.py")

# A score READ OUT OF a signal and formatted. Narrow deliberately: a first
# version matched any `{x:.2f}` and flagged qa.py's `average score {avg:.2f}`,
# which is legitimate because that branch runs only after filtering to measured
# runs. A pin that cries wolf on correct code is one somebody deletes.
SCORE_FORMAT = (r"""(?:get\(\s*['"]score['"][^)]*\)|\[['"]score['"]\])"""
                r"""\s*:[<>]?\d*\.\d+f""")


def hand_formatted_scores(files=None, root=None):
    """Renderer lines that turn a signal's score into text without the rule.

    Injectable so the probe can drive THIS function over a synthetic file.
    The first version had a probe that compiled SCORE_FORMAT itself, which
    tests the constant and not the sweep -- gutting the sweep to a
    never-matching pattern left the probe green. A probe must exercise the code
    path it certifies.
    """
    import re
    root = root or ROOT
    fmt = re.compile(SCORE_FORMAT)
    out = []
    for rel in (files if files is not None else RENDERERS):
        for n, line in enumerate(
                (root / rel).read_text(encoding="utf-8").splitlines(), 1):
            if fmt.search(line):
                out.append(f"{rel}:{n}  {line.strip()[:90]}")
    return out


def test_no_renderer_formats_a_critic_score_itself():
    """A FILE-level check cannot see a second renderer in the same file.

    `test_every_renderer_asks_the_one_decision_function` asserts each file
    mentions critic_lib somewhere -- and bin/dashboard.py has THREE critic
    sites. Two went through the rule and the third, the Runs table's critic
    column (the most-read view), formatted `c.get("score", 0):.2f` directly,
    and the pin stayed green over 23 unmarked cells.

    So pin the SHAPE, which is checkable per occurrence. Found by re-driving
    the page with a query written from the DATA (every occurrence of the score
    value) rather than from the fix -- the earlier re-drive searched for
    `critic <n>`, the shape the OTHER cell emits, so it could not see the cell
    that renders the bare number alone.
    """
    bad = hand_formatted_scores()
    assert not bad, (
        "a critic score is formatted without asking critic.score_text:\n  "
        + "\n  ".join(bad))


def test_the_shape_sweep_finds_a_planted_violation(tmp_path):
    """Positive control, driven through the sweep itself."""
    (tmp_path / "bad.py").write_text(
        'cell = f\'<span>{c.get("score", 0):.2f}</span>\'\n', encoding="utf-8")
    (tmp_path / "sub.py").write_text(
        'cell = f"{c[\'score\']:>6.2f}"\n', encoding="utf-8")
    (tmp_path / "good.py").write_text(
        'cell = critic_lib.score_text(c, r)\n'
        'print(f"average score {avg:.2f} over N runs")\n', encoding="utf-8")
    found = hand_formatted_scores(["bad.py", "sub.py", "good.py"], root=tmp_path)
    assert any(f.startswith("bad.py:") for f in found), \
        "the sweep misses the exact line bin/dashboard.py had"
    assert any(f.startswith("sub.py:") for f in found), "misses the subscript form"
    assert not any(f.startswith("good.py:") for f in found), \
        "flags score_text, or a legitimately measured average"


def test_the_sweep_still_covers_every_renderer():
    """Scope is part of the contract and a clean tree cannot defend it:
    dropping bin/dashboard.py from the list fails nothing today, right up until
    the next unmarked cell lands there -- which is exactly what happened."""
    for rel in ("bin/dashboard.py", "bin/qa.py", "engine/lib/pr_comment.py",
                "engine/lib/trace.py"):
        assert rel in RENDERERS, f"{rel} fell out of the score sweep"


def test_the_runs_table_tooltip_does_not_claim_measured():
    """The score and its tooltip must agree.

    A mutation pinning `cprov = "measured"` survived the first pass: the score
    still rendered `~` because score_text derives provenance independently, so
    only the TOOLTIP lied -- a chip reading `~0.86` whose hover text calls it a
    measurement is worse than either alone.
    """
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    block = src[src.index("critic_cell = ") - 900:src.index("critic_cell = ")]
    assert "critic_lib.provenance(c, r)" in block, \
        "the Runs-table tooltip no longer derives provenance from the record"
    assert "SIMULATED" in block, "the tooltip stopped naming a simulated score"


# ---------------- the pin that let a SIXTH renderer through, one layer down

SCORE_READ = r"""\{[^}]*(?:get\(\s*['"]score['"]|\[['"]score['"]\])[^}]*\}"""
PROVENANCE_HELPER = r"score_text|_critic_caveat|provenance|phase_provenance|\bmark\b|SIMULATED"
INTERPOLATORS = ("bin/dashboard.py", "bin/qa.py", "engine/lib/trace.py",
                 "engine/lib/pr_comment.py", "engine/lib/run_progress.py")


def unqualified_score_interpolations(files=None, root=None, source=None):
    """A score interpolated into output with no provenance helper in sight.

    SCORE_FORMAT above requires a FORMAT SPEC (`:.2f`) -- which is exactly the
    shape the fifth renderer had, and exactly why it could not see the sixth:
    `run_progress._summarize` wrote `f"score {contract.get('score')} ..."` with
    no spec at all. A pin shaped by the last defect misses the next one, which
    is the same lesson two layers deep.

    The window is deliberate rather than line-exact: `trace.py` and
    `pr_comment` render the bare number and put the qualifier in WORDS on a
    neighbouring line, which is the right call for prose surfaces and must not
    be flagged.
    """
    import re
    read, helper = re.compile(SCORE_READ), re.compile(PROVENANCE_HELPER)
    root = root or ROOT
    out = []
    for rel in (files if files is not None else INTERPOLATORS):
        lines = (source or (root / rel).read_text(encoding="utf-8")).splitlines()
        for n, line in enumerate(lines, 1):
            if read.search(line) and not helper.search(
                    "\n".join(lines[max(0, n - 3):n + 3])):
                out.append(f"{rel}:{n}  {line.strip()[:88]}")
    return out


def test_no_score_is_interpolated_without_a_qualifier_nearby():
    bad = unqualified_score_interpolations()
    assert not bad, (
        "a critic score reaches output with no provenance qualifier:\n  "
        + "\n  ".join(bad))


def test_that_wider_sweep_catches_the_sixth_site_as_it_stood(tmp_path):
    """Positive control using the ACTUAL pre-fix source of run_progress.

    Pinning against the real historical line, not an invented one: the point of
    this sweep is that it sees a shape SCORE_FORMAT could not.
    """
    pre_fix = (
        '    if sid == "critic":\n'
        '        return (f"score {contract.get(\'score\')} '
        '{contract.get(\'verdict\', \'\')}"\n'
        '                f" - {contract.get(\'noise_count\', 0)} flagged").strip()\n')
    assert unqualified_score_interpolations(["x.py"], root=tmp_path,
                                            source=pre_fix), \
        "the wider sweep cannot see the sixth renderer as it actually stood"
    ok = ('        return (f"score {critic_lib.score_text(contract, record)} "\n'
          '                f"{contract.get(\'verdict\', \'\')}").strip()\n')
    assert not unqualified_score_interpolations(["x.py"], root=tmp_path,
                                                source=ok), \
        "the fixed form is being flagged"

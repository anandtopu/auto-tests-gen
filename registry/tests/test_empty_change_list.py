"""An empty change list is not a finding that nothing testable changed.

C13 in the ROUTING layer -- the one failure CLAUDE.md says this platform cannot
see from the inside, because nothing downstream can notice work that was never
routed.

MEASURED before fixing: `resolve.py pr <repo>` returned BYTE-IDENTICAL output
for two opposite situations --

    the SCM listed 3 files, none matching testable_paths
    the SCM listed NOTHING at all

both `confidence: 1.0, "no testable paths changed", skip: true`, and the
pipeline then printed one sentence asserting the first and exited 0 with no run
record. The first is an established negative. The second is the platform having
learned nothing about the PR: an adapter whose parse silently yields nothing
when a response shape moves (this repo already records exactly that for codex
token counts), a token without permission answering 200 with an empty array,
pagination misread. pipeline.sh ABORTS when `SCM changed_files` fails, so this
is precisely the case where it SUCCEEDS and says nothing.

It still skips, and that is deliberate: there is nothing to generate from either
way, and a legitimately empty PR -- a title-only edit, reverted commits -- must
not start asking humans questions. What changes is the CLAIM. `confidence` in
"this PR needs no tests" is zero when nothing was seen, and the words a human
reads now name which of the two happened.

NO CONTROL FLOW CHANGES, and that is checked rather than assumed:
`needs_clarification` is computed as `confidence < threshold and not skip`, so a
skipping resolution can never raise it, and pipeline.sh's skip branch keys on
`skip` / `test_repos`, never on confidence.
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _resolve(lines, repo="orders-api", tmp_path=None):
    f = tmp_path / "changed.txt"
    f.write_text("".join(l + "\n" for l in lines), encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "engine/phases/resolve.py"),
                        "pr", repo, "--changed-files", str(f)],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, timeout=120)
    assert r.returncode == 0, r.stderr[-500:]
    return json.loads(r.stdout)


def test_the_two_situations_are_distinguishable(tmp_path):
    """The defect, stated as the property that was violated."""
    empty = _resolve([], tmp_path=tmp_path)
    nontestable = _resolve(["README.md", "docs/x.md"], tmp_path=tmp_path)
    assert empty != nontestable, \
        "an unexamined PR is byte-identical to an examined one again"


def test_an_empty_change_list_claims_no_confidence(tmp_path):
    d = _resolve([], tmp_path=tmp_path)
    assert d["confidence"] == 0.0, \
        "the resolver claims confidence about a PR it saw nothing of"
    assert d["empty_change_list"] is True
    assert "nothing was established" in d["rationale"]
    assert "no testable path changed" in d["rationale"], \
        "the rationale must say what it is NOT, or a reader supplies it"


def test_a_genuine_non_testable_change_is_still_an_established_negative(tmp_path):
    """The over-fix guard. Hedging a real finding is how findings stop landing,
    and this one legitimately IS certain: files were examined and matched
    nothing."""
    d = _resolve(["README.md", "docs/x.md"], tmp_path=tmp_path)
    assert d["confidence"] == 1.0
    assert d["empty_change_list"] is False
    assert "no testable paths changed" in d["rationale"]
    assert "2 file(s) examined" in d["rationale"], \
        "the count is what makes it an established negative rather than a claim"


def test_both_still_skip(tmp_path):
    """Neither case has anything to generate from, and a legitimately empty PR
    must not start asking humans questions."""
    for lines in ([], ["README.md"]):
        assert _resolve(lines, tmp_path=tmp_path)["skip"] is True


def test_neither_raises_needs_clarification(tmp_path):
    """The blast-radius check, asserted rather than assumed: a skipping
    resolution must not start interrupting people because its confidence
    dropped."""
    for lines in ([], ["README.md"]):
        assert _resolve(lines, tmp_path=tmp_path)["needs_clarification"] is False


def test_a_testable_change_still_routes(tmp_path):
    """The control: the fix must not touch the path that does the work."""
    d = _resolve(["openapi/orders.yaml"], tmp_path=tmp_path)
    assert d["test_repos"], "a testable change stopped resolving test repos"
    assert not d.get("skip")
    assert d["confidence"] == 1.0


def test_the_pipeline_says_which_of_the_two_it_was():
    """The fix has to reach the operator: the contract is not what they read.

    A source check because the branch only runs inside a full pipeline run --
    but it asserts the DISTINCTION exists, which is the whole defect.
    """
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    block = src[src.index("RESOLVE_SKIP") - 700:src.index("RESOLVE_SKIP") + 900]
    assert "empty_change_list" in block, \
        "the skip branch no longer distinguishes an unexamined PR"
    assert "reported NO changed files" in block, \
        "the operator-facing sentence for an empty change list is gone"

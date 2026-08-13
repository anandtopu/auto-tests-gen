"""A knob the docs name must be a knob something reads.

Third direction in the same family. `test_docs_currency` pins that a documented
`make` TARGET exists; `test_documented_flags_exist` pins that a documented CLI
FLAG is accepted; this pins that a documented `AIQE_*` ENV KNOB is read by code.

The env direction is the one with no error to alert anybody. A command that does
not exist prints `No rule to make target`. A flag that does not exist exits 2 on
`unrecognized arguments`. An environment variable that nothing reads does
NOTHING AT ALL: the operator exports it, the run proceeds exactly as before, and
they believe they turned the thing off. That is the C13 failure in its purest
form -- an action that was never taken, reported as taken, by silence.

Both instances found when this was first run were exactly that:

  * `docs/cost-reduction-stories.md` story 8.2, in a document whose header says
    ALL 8 BUILD SLICES SHIPPED, promised "any regression is one env var from
    off" and named `AIQE_PROMPT_CACHE` beside three knobs that do exist. There
    is no such variable and there cannot usefully be one -- provider prefix
    caching is engaged by the SHAPE of the prompt, which is why run_phase.sh
    appends the run-varying block last instead of substituting inline.
  * `docs/prd-batch-api-cost-reduction.md` offered `AIQE_LLM_BATCH=1` as the
    batch switch. Batch shipped, but selected like every other provider
    (`AIQE_LLM_PROVIDER=batch` / `llm.phase_providers`). Third defect of this
    shape in that one document, after `make batch-plan` and `make test-batch`.

SCOPE, and a correction to my own first sweep, which is the reusable part: an
earlier version of this check globbed `engine/**`, `bin/*` and `adapters/**` and
reported `AIQE_KEY` as unread. It is read -- by `.openhands/hooks/gate-check.sh`,
which the glob did not reach. A sweep is only as good as its file set, so the
"read" side here is EVERY TRACKED FILE outside the docs, taken from git rather
than from a pattern somebody has to remember to extend.

Example config files (`*.example`) are excluded from the read side deliberately:
a knob listed in `.env.example` and read nowhere is the same defect, not
evidence against it. Measured when this was written -- zero such knobs, so the
exclusion costs nothing today and defends the direction.

NOT PINNED, and stated rather than left to be discovered: the reverse direction
(every knob the code reads is documented). 156 are read and 91 documented, and
most of the difference is test-isolation knobs (AIQE_PLAN_DIR and friends) whose
audience is this repo. That direction catches a feature nobody can find; this
one catches a knob nobody can use. Neither catches the other's failure.
"""
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]

KNOB = re.compile(r"\bAIQE_[A-Z0-9_]+")
# A knob named inside a code span or fence is being offered to the reader as
# something to set. Prose that merely discusses one is not a command.
CODE_SPAN = re.compile(r"`([^`\n]+)`")
DOC_FILES = ("docs/", "CLAUDE.md", "README.md")

# The disclaimer must sit in the SAME PARAGRAPH as the knob, for the reason the
# documented-command pin already gives: the fix a reader needs is to see it
# where they see the knob, not in an allow-list that keeps the build green and
# leaves the document lying. Paragraph rather than line because markdown prose
# reflows -- the first version of this pin required the same physical line and
# failed on its own fix, where "no such knob exists" had wrapped.
#
# The phrases are deliberately ones a writer has to CHOOSE. An earlier version
# accepted a bare "there is no", and mutation testing caught it the useful way
# round: two mutations meant to restore the real defects SURVIVED, because my
# replacement prose happened to contain "there is no" elsewhere in the same
# paragraph and kept excusing the ghost. A disclaimer token loose enough to
# appear by accident is one that excuses a ghost by accident.
NOT_BUILT = re.compile(
    r"not built|never built|no\s+such\s+(?:knob|variable|flag)|"
    r"does not exist|not implemented|no\s+AIQE_[A-Z0-9_]+\s+(?:knob|variable)",
    re.I)


def _tracked():
    # stdin=DEVNULL is not optional here, and this cost a diagnosis: run alone
    # the file was green, and run beside test_docs_currency every git call died
    # with `OSError: [WinError 6] The handle is invalid`, because by then
    # pytest's capture has left an inherited stdin handle this process cannot
    # duplicate. CLAUDE.md states the rule for exactly this reason.
    out = subprocess.run(["git", "ls-files"], cwd=ROOT,
                         capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, timeout=120).stdout
    return [f for f in out.split() if (ROOT / f).is_file()]


def _is_doc(f):
    return f.startswith("docs/") or f in ("CLAUDE.md", "README.md")


def knobs_read(files):
    """Every AIQE_* name appearing in a non-doc tracked file."""
    found = set()
    for f in files:
        if _is_doc(f) or f.endswith(".example"):
            continue
        found |= set(KNOB.findall(
            (ROOT / f).read_text(encoding="utf-8", errors="replace")))
    return found


def _paragraphs(text):
    """(start_line, block_text) for each blank-line-separated block."""
    out, buf, start = [], [], 1
    for n, line in enumerate(text.splitlines(), 1):
        if line.strip():
            if not buf:
                start = n
            buf.append(line)
        elif buf:
            out.append((start, "\n".join(buf)))
            buf = []
    if buf:
        out.append((start, "\n".join(buf)))
    return out


def knobs_documented(files):
    """{knob: [(file, line_no, paragraph)]} for knobs offered in code spans."""
    out = {}
    for f in files:
        if not _is_doc(f):
            continue
        text = (ROOT / f).read_text(encoding="utf-8", errors="replace")
        for start, para in _paragraphs(text):
            offered = " ".join(CODE_SPAN.findall(para))
            for line in para.splitlines():
                if line.strip().startswith(("AIQE_", "export AIQE_",
                                            "- AIQE_")):
                    offered += " " + line
            for k in KNOB.findall(offered):
                out.setdefault(k, []).append((f, start, para))
    return out


def ghosts(files):
    read = knobs_read(files)
    return {k: v for k, v in knobs_documented(files).items()
            if k not in read and not all(NOT_BUILT.search(l) for _, _, l in v)}


def test_every_documented_env_knob_is_read_by_something():
    bad = ghosts(_tracked())
    lines = [f"{f}:{n}  {k}  -> nothing reads it\n      "
             + text.splitlines()[0][:110]
             for k, v in sorted(bad.items()) for f, n, text in v]
    assert not bad, (
        "documented env knob(s) no code reads -- setting one does nothing and "
        "says nothing:\n  " + "\n  ".join(lines))


def test_the_sweep_can_see_a_ghost():
    """A check whose finding set is empty must be shown able to be non-empty.

    Same probe discipline as test_event_log's closure pin: without this, a
    regex that matches nothing passes forever and reads as a clean estate.
    """
    files = _tracked()
    read = knobs_read(files)
    assert "AIQE_MOCK" in read, "the read side found nothing -- the pin is vacuous"
    assert "AIQE_DEFINITELY_NOT_A_KNOB" not in read
    documented = knobs_documented(files)
    assert documented, "the doc side found nothing -- the pin is vacuous"


def test_the_read_side_reaches_outside_engine_and_bin():
    """My first sweep globbed engine/bin/adapters and called AIQE_KEY unread.

    It is read by .openhands/hooks/gate-check.sh. Asking git for the file set
    is what fixed it, so pin the consequence rather than the technique.
    """
    assert "AIQE_KEY" in knobs_read(_tracked()), \
        "the read side stopped reaching .openhands/ -- a false ghost is coming"


def test_a_disclaimer_covers_its_own_paragraph_and_no_further(tmp_path, monkeypatch):
    """The opt-out is in the document, and it is not a blanket per-file pass.

    A file saying "not built" somewhere must not license every other knob it
    names -- that is an allow-list wearing a disclaimer's clothes. Driven with
    two knobs in two paragraphs, one disclaimed.
    """
    doc = tmp_path / "docs" / "x.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("`AIQE_GHOST_ONE` is offered here as something to set.\n"
                   "\n"
                   "`AIQE_GHOST_TWO` was never built and there is no\n"
                   "such knob.\n", encoding="utf-8")
    import test_documented_env_knobs_are_read as mod
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    found = mod.ghosts(["docs/x.md"])
    assert "AIQE_GHOST_ONE" in found, "an undisclaimed ghost slipped through"
    assert "AIQE_GHOST_TWO" not in found, \
        "a disclaimer wrapped across two lines stopped being seen"


def test_prompt_caching_is_not_offered_as_a_switch():
    """The instance this was written for, pinned by name.

    Restoring `AIQE_PROMPT_CACHE` to that AC would be worse than a missing
    command: the operator sets it, nothing errors, and caching stays on.
    """
    text = (ROOT / "docs/cost-reduction-stories.md").read_text(
        encoding="utf-8", errors="replace")
    seen = 0
    for start, para in _paragraphs(text):
        if "AIQE_PROMPT_CACHE" not in para:
            continue
        seen += 1
        assert NOT_BUILT.search(para), (
            f"line {start} offers AIQE_PROMPT_CACHE without saying it "
            f"does not exist")
    assert seen, "the paragraph this pin guards was renamed away from it"

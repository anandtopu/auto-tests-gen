"""A flag the docs show must be a flag the CLI accepts.

`test_docs_currency.test_every_documented_make_command_exists` pins that a
documented `make` TARGET exists. Nothing pinned the level below it: the flags
on a documented command line. Found by DRIVING the ad-hoc-ticket use case
exactly as `docs/use-cases.md` writes it --

    python3 bin/qa.py run-inline --file context.txt --key ADHOC-1

-- which exited 2 with `unrecognized arguments: --file`. The whole point of
that use case is a large pasted ticket, which is precisely the input you do not
want on argv, and the one form the doc offered for it did not exist.

THE SIBLING WAS THE WORSE ONE, as it keeps being. `bin/repos.py notes` DOES
have `--file`, and resolved a clash with `args.set if args.set is not None else
args.file` -- so `--set "x" --file guidance.md` wrote the inline string,
discarded the file, and printed "guidance saved". That content is merged into
AGENTS.md and injected into every authoring phase, so the operator believes
their guidance is steering generation while none of it arrived. Same defect
`engine/lib/selection.py` was fixed for: silently dropping a flag is
indistinguishable, to a user, from not having one.

SCOPE, stated rather than implied: this pin covers the two argparse CLIs
(`bin/qa.py`, `bin/repos.py`), where "does this CLI accept this flag" is
answered by asking the CLI. It deliberately does NOT cover the hand-rolled
`sys.argv` modules under engine/lib -- CLAUDE.md records a textual
"is this flag consumed?" sweep over those that was ~4% precise (26 flagged, 9
driven, 9 false positives), and an allow-list big enough to silence it would
swamp the signal.
"""
import contextlib
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

# The CLIs whose flags are introspectable by asking them.
CLIS = ("bin/qa.py", "bin/repos.py")


def _doc_command_lines():
    """Every documented line invoking one of the argparse CLIs.

    Fenced blocks only. Prose mentioning a flag is not a command someone runs,
    and the existing doc pins made the same distinction for the same reason.
    """
    out = []
    for doc in sorted((ROOT / "docs").rglob("*.md")):
        text = doc.read_text(encoding="utf-8", errors="replace")
        for block in re.findall(r"```(?:bash|sh|console)?\n(.*?)```", text, re.S):
            for raw in block.splitlines():
                line = raw.split("#")[0].strip().rstrip("\\").strip()
                if any(c in line for c in CLIS):
                    out.append((doc.relative_to(ROOT).as_posix(), line))
    return out


def _parse(line):
    """(cli, verb, flags) for a documented command line, or None."""
    toks = line.split()
    cli = next((t for t in toks if any(t.endswith(c) for c in CLIS)), None)
    if cli is None:
        return None
    rest = toks[toks.index(cli) + 1:]
    verb = next((t for t in rest if not t.startswith("-")), None)
    if verb is None:
        return None
    flags = {t.split("=")[0] for t in rest if t.startswith("--") and len(t) > 2}
    return (cli.replace("\\", "/").split("bin/")[-1], verb, flags)


def _accepted(cli, verb):
    """Flags `<cli> <verb> --help` says it takes. Asks the CLI, not the source."""
    r = subprocess.run([sys.executable, str(ROOT / "bin" / cli), verb, "--help"],
                       cwd=ROOT, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=120)
    if r.returncode != 0:
        return None
    return set(re.findall(r"(--[a-z0-9][a-z0-9-]*)", r.stdout))


def test_every_flag_shown_in_the_docs_is_accepted_by_the_cli():
    unknown = []
    for doc, line in _doc_command_lines():
        parsed = _parse(line)
        if not parsed:
            continue
        cli, verb, flags = parsed
        if not flags:
            continue
        accepted = _accepted(cli, verb)
        if accepted is None:            # not a real verb -- the command pin's job
            continue
        for f in sorted(flags - accepted):
            unknown.append(f"{doc}: `{line}` -> {cli} {verb} does not accept {f}")
    assert not unknown, (
        "documented flag(s) the CLI rejects:\n  " + "\n  ".join(unknown))


def test_the_probe_can_actually_see_a_missing_flag():
    """Without this, a regex that matches nothing passes forever.

    Same reason `test_event_log.py` carries a probe assertion on its closure
    pin: a sweep whose finding set is empty must be shown capable of being
    non-empty.
    """
    accepted = _accepted("qa.py", "run-inline")
    assert accepted, "the introspection returned nothing -- the pin is vacuous"
    assert "--file" in accepted, "the flag this pin was written for is gone"
    assert "--no-such-flag" not in accepted


def test_the_documented_use_case_line_is_covered_by_the_sweep():
    """The exact line that failed must be one the sweep reads, or the fix is
    pinned somewhere the defect never was."""
    lines = [l for d, l in _doc_command_lines()
             if "run-inline" in l and "--file" in l]
    assert lines, "docs/use-cases.md's --file form vanished from the sweep"


# ------------------------------------------------- the behaviour, not the text

def _run(cli, *args):
    return subprocess.run([sys.executable, str(ROOT / "bin" / cli), *args],
                          cwd=ROOT, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=120)


def test_run_inline_reads_a_file(tmp_path):
    f = tmp_path / "ctx.txt"
    f.write_text("Bug: totals wrong\nAC-1: recompute on refund\n", encoding="utf-8")
    r = _run("qa.py", "run-inline", "--file", str(f), "--key", "ZZ-FLAG-1", "--queue")
    assert r.returncode == 0, r.stderr[-800:]
    assert "ZZ-FLAG-1" in r.stdout
    # Leave no queue item or ticket behind: this estate's queue is real.
    sys.path.insert(0, str(ROOT / "engine/lib"))
    import work_queue
    for item in work_queue.load():
        if item.get("target") == "ZZ-FLAG-1":
            work_queue.remove(item["id"])
    (ROOT / "reports/inline/ZZ-FLAG-1.json").unlink(missing_ok=True)


@pytest.mark.parametrize("args, expect", [
    (("run-inline",), "no ticket context supplied"),
    (("run-inline", "inline", "--file", "x.txt"), "not both"),
    (("run-inline", "--file", "definitely-not-here.txt"), "no such file"),
])
def test_run_inline_refuses_rather_than_guessing(args, expect):
    r = _run("qa.py", *args)
    assert r.returncode != 0, r.stdout
    assert expect in (r.stdout + r.stderr), (r.stdout + r.stderr)[-500:]


GUIDANCE = ROOT / "knowledge/repos/e2e-api-tests-1.md"


@contextlib.contextmanager
def _guidance_restored():
    """Snapshot the real guidance file and put it back, whatever happens.

    `repos.py notes` has no AIQE_ knob for its output directory, so these two
    tests drive a command that writes into the live estate. Asserting
    before == after proves the refusal, but only while the code is UNMUTATED --
    a mutation that removes the guard writes for real, and the mutation pass
    for this very file left `FROM SET` sitting in the estate. CLAUDE.md's rule
    from the demo_data pass applies: a test that can damage the estate it
    protects is not a test. So restore, and let the assertion below report.
    """
    before = GUIDANCE.read_text(encoding="utf-8") if GUIDANCE.exists() else None
    try:
        yield lambda: (GUIDANCE.read_text(encoding="utf-8")
                       if GUIDANCE.exists() else None)
    finally:
        if before is None:
            GUIDANCE.unlink(missing_ok=True)
        else:
            GUIDANCE.write_text(before, encoding="utf-8")


def test_notes_refuses_to_discard_a_file_it_was_handed(tmp_path):
    """THE DEFECT: this combination used to write --set and report success.

    Asserting the refusal is not enough on its own -- the file must also be
    untouched, because "refused" and "wrote the wrong thing then refused" print
    the same way.
    """
    f = tmp_path / "g.md"
    f.write_text("real guidance", encoding="utf-8")
    with _guidance_restored() as now:
        before = now()
        r = _run("repos.py", "notes", "e2e-api-tests-1", "--set", "FROM SET",
                 "--file", str(f))
        after = now()
    assert after == before, "a refused invocation still wrote guidance"
    assert r.returncode != 0
    assert "not both" in (r.stdout + r.stderr)


def test_an_empty_set_does_not_delete_a_repos_guidance():
    """`--set ""` used to fall through to set_notes("") and clear the file --
    an unset shell variable expands to exactly that."""
    with _guidance_restored() as now:
        before = now()
        r = _run("repos.py", "notes", "e2e-api-tests-1", "--set", "")
        after = now()
    assert after == before, "an empty --set deleted the team's guidance"
    assert r.returncode != 0 and "--clear" in (r.stdout + r.stderr)

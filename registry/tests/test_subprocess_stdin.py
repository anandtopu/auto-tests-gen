"""Production subprocess calls never inherit this process's stdin.

`engine/lib/provider_usage.py` spawned the usage adapter without `stdin`, and
on Windows that is not a style question: a parent whose stdin is closed —
pytest capture, a service, a scheduled job — makes `Popen` raise
`OSError: [WinError 6] The handle is invalid` BEFORE the child runs. The
symptom was five provider-usage tests failing, but the defect was in the
engine: reconciliation would crash inside `make maintain` rather than report a
provider figure, on the platform this repo is primarily developed on.

CLAUDE.md states the convention and ~30 test modules follow it. It had never
been enforced, so it drifted in six places. This pin closes the class.

Two ways to satisfy it, both legitimate:
  * `stdin=subprocess.DEVNULL` — the child reads nothing (almost always right
    here: every child is an adapter, a generator or an index builder).
  * `input=...` — subprocess sets `stdin=PIPE` itself, so the handle is never
    inherited. `embeddings.py` does this and is correctly NOT a violation;
    the first version of this sweep flagged it, which is why the check reads
    the call's arguments rather than grepping for the word `stdin`.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCOPE = sorted(list((ROOT / "engine/lib").glob("*.py"))
               + list((ROOT / "bin").glob("*.py"))
               + list((ROOT / "engine/phases").glob("*.py")))

CALL = re.compile(r"subprocess\.(run|Popen|check_output|check_call|call)\s*\(")


def _call_args(src, start):
    """The text between the call's parentheses, balanced."""
    depth, i, out = 0, start, []
    while i < len(src):
        ch = src[i]
        out.append(ch)
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return "".join(out)


def _violations():
    bad = []
    for p in SCOPE:
        src = p.read_text(encoding="utf-8", errors="replace")
        for m in CALL.finditer(src):
            args = _call_args(src, m.end() - 1)
            if "stdin=" in args or "input=" in args:
                continue
            line = src[:m.start()].count("\n") + 1
            bad.append(f"{p.relative_to(ROOT).as_posix()}:{line}")
    return bad


def test_no_production_subprocess_inherits_stdin():
    bad = _violations()
    assert not bad, (
        "these spawn a child with the parent's stdin — on Windows a closed "
        "parent stdin makes Popen raise WinError 6 before the child runs. "
        "Pass stdin=subprocess.DEVNULL (or input=...): " + ", ".join(bad))


def test_the_checker_actually_parses_calls():
    """A sweep that finds nothing because its regex is broken is worse than no
    sweep: it reports a clean codebase forever. Prove it sees real calls."""
    seen = 0
    for p in SCOPE:
        seen += len(CALL.findall(p.read_text(encoding="utf-8", errors="replace")))
    assert seen >= 15, f"only {seen} subprocess calls found — the pattern broke"


def test_input_is_accepted_as_an_alternative():
    """`input=` implies stdin=PIPE; flagging it would push someone to add a
    contradictory stdin= argument (which raises ValueError at runtime)."""
    fake = 'subprocess.run(["x"], input="data", capture_output=True)'
    m = CALL.search(fake)
    args = _call_args(fake, m.end() - 1)
    assert "input=" in args and "stdin=" not in args

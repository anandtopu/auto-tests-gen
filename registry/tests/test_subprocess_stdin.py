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
PROD = sorted(list((ROOT / "engine/lib").glob("*.py"))
              + list((ROOT / "bin").glob("*.py"))
              + list((ROOT / "engine/phases").glob("*.py")))
# Tests are in scope too, and not as a tidiness rule: test_app_paths.py spawned
# a child without stdin and so PASSED ALONE but FAILED whenever another module
# ran first, because pytest's stdin handle state differs. An order-dependent
# test is worse than a failing one — it makes the suite's verdict depend on
# which tests you happened to select.
TESTS = sorted(p for p in (ROOT / "registry/tests").glob("*.py")
               # This file is excluded from its own sweep: it contains example
               # call source in a string literal (the synthetic fixture below),
               # and the checker reads text, so it would flag its own teaching
               # material. The exclusion is by exact filename, not a pattern —
               # a second test file must not be able to opt itself out.
               if p.name != pathlib.Path(__file__).name)
SCOPE = PROD + TESTS

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
            # A helper that builds kwargs and forwards them — the pattern
            # test_multi_agent and test_pass6_regressions use:
            #     kw.setdefault("stdin", subprocess.DEVNULL)
            #     return subprocess.run(args, **kw)
            # The literal call carries no stdin=, but the child never inherits
            # one. Flagging these would push someone to add a second stdin
            # argument, which raises TypeError at runtime. Checked in the same
            # function so the exemption is as narrow as the pattern.
            if "**" in args:
                head = src[max(0, m.start() - 400):m.start()]
                if re.search(r"setdefault\(\s*[\"']stdin[\"']", head):
                    continue
            line = src[:m.start()].count("\n") + 1
            try:
                where = p.relative_to(ROOT).as_posix()
            except ValueError:      # a synthetic probe file outside the repo
                where = p.as_posix()
            bad.append(f"{where}:{line}")
    return bad


def test_no_production_subprocess_inherits_stdin():
    bad = _violations()
    assert not bad, (
        "these spawn a child with the parent's stdin — on Windows a closed "
        "parent stdin makes Popen raise WinError 6 before the child runs. "
        "Pass stdin=subprocess.DEVNULL (or input=...): " + ", ".join(bad))


def test_the_sweep_covers_production_and_tests():
    """Scope is part of the contract, and a clean tree cannot defend it: drop
    the test files and nothing fails today, because there is nothing left to
    catch — right up until the next order-dependent flake merges."""
    parts = {p.parent.name for p in SCOPE}
    assert "lib" in parts, "engine/lib fell out of the sweep"
    assert "bin" in parts, "bin/ fell out of the sweep"
    assert "tests" in parts, (
        "registry/tests fell out of the sweep — test_app_paths.py's missing "
        "stdin made it pass alone and fail in company, which is how this "
        "requirement was discovered in the first place")


def test_the_checker_actually_parses_calls():
    """A sweep that finds nothing because its regex is broken is worse than no
    sweep: it reports a clean codebase forever. Prove it sees real calls."""
    seen = 0
    for p in SCOPE:
        seen += len(CALL.findall(p.read_text(encoding="utf-8", errors="replace")))
    assert seen >= 15, f"only {seen} subprocess calls found — the pattern broke"


def test_the_kwargs_exemption_is_narrow(tmp_path):
    """Exercise the checker's LOGIC, not just today's clean tree.

    Widening the `**kwargs` exemption is invisible to a sweep of a codebase
    that currently has no violations — there is nothing for the wider rule to
    wrongly excuse, so the mutation passes. Synthetic input is the only way to
    pin the boundary: a forwarding call WITH the setdefault is exempt, an
    unguarded call sitting near one is NOT.
    """
    global SCOPE
    f = tmp_path / "probe.py"
    f.write_text(
        "import subprocess\n"
        "def helper(args, **kw):\n"
        "    kw.setdefault('stdin', subprocess.DEVNULL)\n"
        "    return subprocess.run(args, **kw)\n"          # exempt: forwards a guarded kw
        "def sloppy():\n"
        "    return subprocess.run(['x'], capture_output=True)\n",  # NOT exempt
        encoding="utf-8")
    saved = SCOPE
    try:
        SCOPE = [f]
        found = _violations()
    finally:
        SCOPE = saved
    assert len(found) == 1, f"expected exactly the unguarded call, got {found}"
    assert found[0].endswith(":6"), f"flagged the wrong line: {found}"


def test_input_is_accepted_as_an_alternative():
    """`input=` implies stdin=PIPE; flagging it would push someone to add a
    contradictory stdin= argument (which raises ValueError at runtime)."""
    fake = 'subprocess.run(["x"], input="data", capture_output=True)'
    m = CALL.search(fake)
    args = _call_args(fake, m.end() - 1)
    assert "input=" in args and "stdin=" not in args

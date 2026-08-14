"""A child's words must survive the trip to the parent's log.

FOUND BY DRIVING `make maintain` -- the nightly CronJob entry point, whose
printed summary is a deployment's only account of what the night did. The
coverage-drift step's warning came out as

    coverage drift: NOT CHECKED for admin-portal-ui, payments-api â€” their
    surface could not be harvested ...

`â€”` is a UTF-8 em dash decoded as cp1252. Every step in this repo reconfigures
its own stdout to UTF-8 (that rule is in CLAUDE.md), and `maintenance.py`
captured them with `text=True` and NO `encoding=`, which decodes with the
LOCALE codec. The parent's own stdout is UTF-8, so it then re-encoded the
mangled string into the log -- corruption, not loss, and permanent.

It is corruption rather than a crash because cp1252 maps almost every byte, so
`errors="replace"` never fires and nothing looks wrong to the code. The only
signal is a human reading the log.

THE SIBLING, found by grepping for the SHAPE rather than for the place I had
just fixed: `catalog/bootstrap/correlate.py` reads `git log --format=%s` the
same way, inside `except Exception: pass`. cp1252 leaves five byte values
undefined, so a commit subject carrying one raises UnicodeDecodeError, the
except swallows it, and that test loses its `git_history` evidence in silence.
Honest about the blast radius: the comment beside that call already establishes
git_history contributes no repo and does not raise confidence, so this does not
misroute -- but the bootstrap chain quietly producing less than it should is the
one failure CLAUDE.md says this platform cannot see from the inside.

The pins are the invariant plus the symptom: a class check so the third such
call site is caught by the build, and a behavioural check that a child's
non-ASCII line reaches the parent intact, because a source-text rule cannot
prove the bytes came through.
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

CALL = re.compile(r"subprocess\.(?:run|Popen|check_output)\(")


def _production_py():
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT,
                         capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, timeout=120).stdout
    return [f for f in out.split()
            if not f.startswith(("registry/tests/", "eval/"))]


def _calls(src):
    """Each subprocess.* call's source text, balanced on parentheses."""
    for m in CALL.finditer(src):
        depth, end = 0, None
        for i, ch in enumerate(src[m.start():m.start() + 4000]):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is not None:
            yield src[:m.start()].count("\n") + 1, src[m.start():m.start() + end + 1]


def locale_decoding_captures(files=None, root=None):
    """Production calls that decode captured child output with the locale codec.

    `files`/`root` are injectable so the probe below can drive this over a
    synthetic file. Without that, mutating this function to return [] would
    leave the class check passing on an empty finding set forever -- the
    vacuous-sweep failure this repo has recorded three times.
    """
    root = root or ROOT
    bad = []
    for f in (files if files is not None else _production_py()):
        p = root / f
        if not p.is_file():
            continue
        for line, call in _calls(p.read_text(encoding="utf-8", errors="replace")):
            decodes = "text=True" in call or "universal_newlines=True" in call
            captures = ("capture_output=True" in call
                        or "stdout=subprocess.PIPE" in call
                        or "check_output(" in call)
            if decodes and captures and "encoding=" not in call:
                bad.append(f"{f}:{line}")
    return bad


def test_no_production_capture_decodes_with_the_locale_codec():
    bad = locale_decoding_captures()
    assert not bad, (
        "these decode a child's UTF-8 output with the locale codec, which "
        "corrupts it silently rather than failing:\n  " + "\n  ".join(bad))


def test_the_sweep_can_see_such_a_call(tmp_path):
    """Probe: a check that finds nothing must be shown able to find something.

    Driven over a synthetic file rather than by inspecting the regex, so that
    gutting either the parser OR the rule is a failure. A sweep that stops
    matching otherwise passes forever and reads as a clean tree.
    """
    # Both fixtures pass stdin=DEVNULL, and not incidentally: this file's
    # fixture source is read as text by test_subprocess_stdin.py's sweep, which
    # excludes ONLY itself by exact filename and says in as many words that a
    # second file must not be able to opt itself out. It flagged these two
    # lines on the first full run. Making the fixtures correct in that respect
    # satisfies both sweeps and keeps them realistic -- a call with the right
    # stdin and no encoding is exactly the shape this pin is about.
    (tmp_path / "bad.py").write_text(
        'import subprocess\n'
        'x = subprocess.run(["a"], capture_output=True, text=True,\n'
        '                   stdin=subprocess.DEVNULL)\n',
        encoding="utf-8")
    (tmp_path / "good.py").write_text(
        'import subprocess\n'
        'x = subprocess.run(["a"], capture_output=True, text=True,\n'
        '                   stdin=subprocess.DEVNULL,\n'
        '                   encoding="utf-8", errors="replace")\n',
        encoding="utf-8")
    found = locale_decoding_captures(["bad.py", "good.py"], root=tmp_path)
    assert any(f.startswith("bad.py:") for f in found), \
        "the sweep cannot see the very shape it exists to find"
    assert not any(f.startswith("good.py:") for f in found), \
        "a correctly-encoded call is being reported"


def test_the_two_fixed_call_sites_still_name_utf8():
    """Pinned by name as well as by class: these are the ones that were wrong,
    and a class check phrased slightly differently could stop covering them."""
    for rel in ("engine/lib/maintenance.py", "catalog/bootstrap/correlate.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert 'encoding="utf-8"' in src, f"{rel} stopped naming its encoding"


# ------------------------------------------------------- the symptom itself

CHILD = (
    "import sys\n"
    "sys.stdout.reconfigure(encoding='utf-8')\n"
    "print('drift: NOT CHECKED for a, b \\u2014 surface not harvested')\n"
)


def test_a_steps_non_ascii_line_reaches_the_maintenance_summary(tmp_path,
                                                                monkeypatch):
    """Drive the real step runner over a child that prints an em dash.

    A source-text rule cannot tell you the bytes arrived; this can. The step is
    made to FAIL so its output is kept as the reason -- that is the path where
    a mangled line does the most damage, because the reason is all a CronJob
    log has to explain a bad night.
    """
    import maintenance
    child = tmp_path / "step.py"
    child.write_text(CHILD + "raise SystemExit(3)\n", encoding="utf-8")

    # No skip fallback on purpose: a skipped test reports as neither pass nor
    # fail and would quietly stop covering this the day run_steps is renamed.
    monkeypatch.setattr(maintenance, "ROOT", ROOT)
    results = maintenance.run_steps([("probe step", [str(child)], False)])
    assert results and results[0]["state"] == "failed"
    assert "—" in results[0]["reason"], (
        "the em dash did not survive the capture: " + repr(results[0]["reason"]))
    assert "â" not in results[0]["reason"], "mojibake reached the summary"


def test_git_subjects_with_non_cp1252_characters_still_yield_their_keys(tmp_path):
    """The bootstrap sibling, driven against a real git repo.

    U+2014 encodes to bytes 0xE2 0x80 0x94; 0x81/0x8D/0x8F/0x90/0x9D are
    undefined in cp1252, so a subject containing one of those raises on decode.
    U+0081 gives exactly that byte in UTF-8's second position, which is the
    portable way to provoke it.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e",
           "PATH": __import__("os").environ.get("PATH", "")}
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, env=env,
                                    capture_output=True, text=True,
                                    encoding="utf-8", errors="replace",
                                    stdin=subprocess.DEVNULL, timeout=60)
    run("init", "-q")
    (repo / "a.spec.js").write_text("x", encoding="utf-8")
    run("add", "-A")
    subprocess.run(["git", "commit", "-q", "-m",
                    "PROJ-42 fix café — report"],
                   cwd=repo, env=env, capture_output=True,
                   stdin=subprocess.DEVNULL, timeout=60)

    log = subprocess.run(["git", "-C", str(repo), "log", "--format=%s", "--",
                          "a.spec.js"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace",
                         stdin=subprocess.DEVNULL, timeout=60).stdout
    sys.path.insert(0, str(ROOT / "catalog/bootstrap"))
    import correlate
    assert correlate.jira_keys(log) == ["PROJ-42"], (
        "the ticket linkage was lost on a commit subject the locale codec "
        "cannot decode: " + repr(log))

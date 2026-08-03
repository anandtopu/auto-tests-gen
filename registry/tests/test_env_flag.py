"""AIQE_MOCK failed in the unsafe direction, and every toggle shared its shape.

Only the literal `1` meant mock; `true`, `yes`, `on` and an empty string all
meant REAL adapters and real model spend. Somebody enabling mock mode by writing
`true` got pushes to real repositories and a real bill — every other knob in this
codebase fails safe, and this one failed toward spending money.
"""
import inspect
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine" / "lib"))
import env_flag


def test_the_spellings_people_actually_type_are_honoured(monkeypatch):
    """Each spelling must be RECOGNIZED, not merely land on the right answer.

    Asserting only the return value cannot tell "understood as true" from
    "not understood, defaulted to mock" — both are True — so a version that
    accepted nothing but `1` passed this test until the silence check was
    added. The absence of a warning is what proves recognition.
    """
    for v in ("1", "true", "TRUE", "yes", "on", " True "):
        env_flag._warned.clear()
        said = []
        monkeypatch.setenv("AIQE_MOCK", v)
        assert env_flag.mock(warn=said.append) is True, v
        assert said == [], "{!r} was not recognized, it defaulted".format(v)
    for v in ("0", "false", "no", "off", "OFF"):
        env_flag._warned.clear()
        said = []
        monkeypatch.setenv("AIQE_MOCK", v)
        assert env_flag.mock(warn=said.append) is False, v
        assert said == [], "{!r} was not recognized".format(v)


def test_an_unusable_value_lands_on_the_safe_side_and_says_so(monkeypatch):
    """The DIRECTION matters more than the warning: resolving to REAL means a
    push and a bill, resolving to MOCK means a run that did nothing. Only one of
    those is recoverable by running it again."""
    env_flag._warned.clear()
    said = []
    monkeypatch.setenv("AIQE_MOCK", "bogus")
    assert env_flag.mock(warn=said.append) is True, "an unusable value went REAL"
    assert said and "not a recognized boolean" in said[0]

    # An EMPTY value is the common one — a bare `AIQE_MOCK=` line in a .env.
    env_flag._warned.clear()
    said.clear()
    monkeypatch.setenv("AIQE_MOCK", "")
    assert env_flag.mock(warn=said.append) is True
    assert said, "an empty value resolved silently"


def test_the_warning_does_not_repeat(monkeypatch):
    """These are read on nearly every call in some modules, and a warning
    printed forty times is a warning nobody reads."""
    env_flag._warned.clear()
    said = []
    monkeypatch.setenv("AIQE_MOCK", "nonsense")
    for _ in range(5):
        env_flag.mock(warn=said.append)
    assert len(said) == 1, "warned {} times".format(len(said))


def test_unset_still_means_whatever_the_caller_asked_for(monkeypatch):
    """The shell and the libraries disagree about an unset AIQE_MOCK ON PURPOSE.

    `engine/pipeline.sh` treats it as REAL and `make run-pr` depends on that,
    while a library defaults to MOCK because code that cannot tell must not be
    the reason something bills an account. `flag()` therefore REQUIRES a default
    instead of inventing one.
    """
    monkeypatch.delenv("AIQE_MOCK", raising=False)
    assert env_flag.mock() is True                      # library default
    assert env_flag.flag("AIQE_MOCK", False) is False   # shell-style default
    sig = inspect.signature(env_flag.flag)
    assert sig.parameters["default"].default is inspect.Parameter.empty, \
        "flag() must not invent a default for the caller"


def test_the_pipeline_resolves_it_the_same_way_and_keeps_unset_real():
    """Runs the resolver EXTRACTED FROM pipeline.sh, not a copy of it.

    A hand-written copy is how a test ends up agreeing with what its author
    assumed the code does rather than with what it does — which happened twice
    in this session before this was written.
    """
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert "AIQE_MOCK_RESOLVED" in src
    assert '"${AIQE_MOCK-0}"' in src, "unset must still default to real"

    start = src.index("AIQE_MOCK_RESOLVED=0")
    block = src[start:src.index("esac", start) + 4]

    # bash_exe(), never a bare "bash": on Windows that resolves to the WSL stub
    # in System32, which cannot see /bin/bash and fails with a relay error. It
    # is written down in CLAUDE.md and this test still made the mistake.
    import work_queue

    def run(prefix):
        script = prefix + "\n" + block + "\necho \"$AIQE_MOCK_RESOLVED\""
        r = subprocess.run([work_queue.bash_exe(), "-c", script],
                           capture_output=True,
                           text=True, cwd=ROOT, stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stderr
        return r.stdout.strip().splitlines()[-1]

    assert run("unset AIQE_MOCK") == "0", \
        "unset must stay REAL — make run-pr passes no AIQE_MOCK at all"
    assert run("AIQE_MOCK=1") == "1"
    assert run("AIQE_MOCK=true") == "1", \
        "the dangerous case is back: true meant REAL adapters"
    assert run("AIQE_MOCK=YES") == "1"
    assert run("AIQE_MOCK=0") == "0"
    assert run("AIQE_MOCK=false") == "0"
    assert run("AIQE_MOCK=''") == "1", \
        "an empty value went REAL (a bare key in a .env file)"
    assert run("AIQE_MOCK=bogus") == "1", "an unusable value went REAL"


def test_every_python_caller_goes_through_the_resolver():
    """18 inline `== "1"` comparisons is 18 chances to get the direction wrong,
    and the next one added would silently be real-by-accident again."""
    stale = []
    for p in (list((ROOT / "engine/lib").glob("*.py"))
              + [ROOT / "bin/dashboard_server.py"]):
        if 'os.environ.get("AIQE_MOCK", "1") == "1"' in p.read_text(encoding="utf-8"):
            stale.append(p.name)
    assert not stale, "still resolving AIQE_MOCK inline: {}".format(stale)


def test_the_feature_toggles_accept_the_word_off(monkeypatch):
    """`AIQE_SPEC_MODE=false` used to leave the spec layer ON.

    Every toggle was `!= "0"`, so the only way to turn anything off was the
    literal zero and every other spelling silently left the feature running —
    which for the LLM-spending ones means someone disabling a phase to save
    money did not. `phase_cache` already handled this correctly on its own,
    which is the tell that the inconsistency was real rather than a convention.
    """
    import spec_store
    import phase_cache
    for f, name in ((spec_store.enabled, "AIQE_SPEC_MODE"),
                    (phase_cache.enabled, "AIQE_PHASE_CACHE")):
        for v in ("0", "false", "off", "no"):
            env_flag._warned.clear()
            monkeypatch.setenv(name, v)
            assert f() is False, "{}={} left the feature on".format(name, v)
        for v in ("1", "true", "on"):
            env_flag._warned.clear()
            monkeypatch.setenv(name, v)
            assert f() is True, "{}={}".format(name, v)
        # A typo keeps the documented default and says so, rather than guessing.
        env_flag._warned.clear()
        said = []
        monkeypatch.setenv(name, "flase")
        assert env_flag.flag(name, True, warn=said.append) is True
        assert said, "{} typo resolved silently".format(name)


def test_no_toggle_is_left_comparing_to_a_bare_zero():
    """Each inline comparison is another chance to get the direction wrong."""
    stale = []
    for p in (ROOT / "engine/lib").glob("*.py"):
        if p.name in ("env_flag.py", "settings_store.py"):
            continue                      # the resolver itself; the settings SPEC
        s = p.read_text(encoding="utf-8")
        for knob in ("AIQE_SPEC_MODE", "AIQE_CONTEXT_SCOPE", "AIQE_PHASE_CACHE"):
            for pat in ('get("{}", "1") == "0"', 'get("{}", "1") != "0"'):
                if pat.format(knob) in s:
                    stale.append("{}:{}".format(p.name, knob))
    assert not stale, "toggles still resolved inline: {}".format(stale)


def test_ssl_verification_is_deliberately_left_strict():
    """AIQE_SSL_VERIFY is NOT routed through the resolver, on purpose.

    It already fails safe — only the literal `0` disables verification, so
    `false` leaves TLS checking ON. Teaching it to accept more spellings would
    make turning verification OFF easier, which is the wrong direction to
    improve. Pinned so a later consistency pass does not "fix" it.
    """
    for mod in ("integration_check.py", "openhands_client.py"):
        s = (ROOT / "engine/lib" / mod).read_text(encoding="utf-8")
        assert 'AIQE_SSL_VERIFY' in s
        assert 'env_flag' not in s.split("AIQE_SSL_VERIFY")[1][:200], \
            "{}: SSL verification must stay strict-by-literal-0".format(mod)

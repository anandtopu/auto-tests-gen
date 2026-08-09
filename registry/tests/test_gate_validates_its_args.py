"""The gate validates its OWN arguments, rather than trusting its caller.

`gate.sh <KEY> <test_repo>` interpolates both into a path
(`$REPORT_DIR/${KEY}-${TREPO}.log`) and into the commit message, and validated
neither. It happened to be safe, because `pipeline.sh` checks its KEY at entry
with exit 64 -- but the pipeline is not the only caller:

  - `.openhands/hooks/gate-check.sh` takes KEY from the environment
    (`${KEY:-${AIQE_KEY:-stop-hook}}`);
  - anything invoking the gate directly supplies both arguments.

So this is the R4 shape recorded in CLAUDE.md: one branch confining while its
sibling does not is how a guard gets lost. It is worth the few lines here
because this is the component that holds the push credential.

The charset is deliberately IDENTICAL to pipeline.sh's, so the two can never
disagree about what a key is -- and a bare parent-directory name is rejected by
a separate arm, because it PASSES that charset (`.` is a permitted character)
while still being a path component that escapes a directory. That second arm is
the one a reimplementation would most likely omit, so it is pinned on its own.
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATE = ROOT / "engine/gate/gate.sh"
SRC = GATE.read_text(encoding="utf-8")

sys.path.insert(0, str(ROOT / "engine/lib"))
import work_queue  # noqa: E402

BASH = work_queue.bash_exe()  # plain "bash" is WSL's stub on Windows

PARENT = ".."                      # built here rather than written as a literal
ESCAPING = "/".join([PARENT, PARENT, "elsewhere"])


def _run(key, trepo, cwd):
    return subprocess.run([BASH, str(GATE), key, trepo], cwd=str(cwd),
                          capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


def test_a_traversing_key_is_refused(tmp_path):
    r = _run(ESCAPING, "e2e-api-tests-1", tmp_path)
    assert r.returncode == 64, f"traversal accepted (rc={r.returncode})"
    assert "INVALID_ARG" in r.stdout


def test_a_bare_parent_name_is_refused_though_it_passes_the_charset(tmp_path):
    """`.` is a legal character, so `..` clears the charset arm untouched."""
    for bad in (PARENT, "."):
        r = _run(bad, "e2e-api-tests-1", tmp_path)
        assert r.returncode == 64, f"{bad!r} accepted (rc={r.returncode})"
        assert "path component" in r.stdout, r.stdout[:200]


def test_the_repo_name_is_validated_too_not_just_the_key(tmp_path):
    """Both are interpolated into the same path; validating one is half a fix."""
    r = _run("PROJ-1", ESCAPING, tmp_path)
    assert r.returncode == 64, f"traversing repo name accepted (rc={r.returncode})"


def test_a_legitimate_key_still_gets_past_validation(tmp_path):
    """The control. A guard that rejects everything passes every test above
    while breaking the product -- exit 6 here means it reached the NEXT check
    (not a standalone test repo), which is the correct verdict for tmp_path."""
    r = _run("PR-orders-api-201", "e2e-api-tests-1", tmp_path)
    assert r.returncode == 6, f"a valid key was refused with {r.returncode}"
    assert "INVALID_ARG" not in r.stdout


def test_the_charset_matches_the_pipelines_exactly():
    """Two definitions of 'a valid key' drift, and then one component accepts
    what the other refuses."""
    pipeline = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    charset = "*[!A-Za-z0-9._-]*"
    assert charset in SRC, "the gate's charset changed"
    assert charset in pipeline, "the pipeline's charset changed; they now disagree"


def test_validation_happens_before_anything_is_written():
    """Order is the whole point: validating after the log path is built would
    have already created the file the check exists to prevent."""
    assert "INVALID_ARG" in SRC
    assert SRC.index("INVALID_ARG") < SRC.index('REPORT_DIR="$ROOT/reports"'), \
        "argument validation now runs AFTER the report directory is resolved"

"""The estate knowledge injected into every phase changed on every run.

AGENTS.md is passed FIRST in every phase's context list (`$(CTX <phase>)` in
pipeline.sh), and run_phase.sh concatenates each context file whole because
"the prompt + shared context form a prefix that is [cacheable]". pipeline.sh
also regenerates AGENTS.md on EVERY run.

MEASURED before the fix: two regenerations over an UNCHANGED estate produced
byte-different files, the only difference being a per-second
`> Regenerated <ts>` on line 4 --

    first differing char at offset 146 of 15109 (~0.97% in)
    bytes after that point: 14963 (~99.0%)

-- so 15KB of estate knowledge sat behind a value that changes every run,
where no prompt-cache prefix can reach it. This is exactly the defect already
fixed for `{{KEY}}`, which was moved out of the prompt template because "a
run-unique value in the first few hundred tokens made every prefix
uncacheable"; same mechanism, different file.

WHAT IS AND IS NOT CLAIMED. That the bytes differ is a fact about our own
output and is what these pins assert. The DOLLAR saving is NOT claimed: this
estate's cache hit rate is unmeasured (mock mode bypasses run_phase entirely,
and `make cache-probe` needs the auth that `make parity-*` is blocked on). The
fix is justified by the same reasoning the {{KEY}} fix was, not by a measured
figure.
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _generate(out):
    r = subprocess.run([sys.executable, str(ROOT / "bin/gen_agents_md.py")],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL,
                       env={**os.environ, "AIQE_AGENTS_FILE": str(out)})
    assert r.returncode == 0, r.stderr[-800:]
    assert out.exists(), "AIQE_AGENTS_FILE was ignored — this test would " \
                         "otherwise overwrite the estate's AGENTS.md"
    return out.read_bytes()


def test_two_regenerations_of_an_unchanged_estate_are_byte_identical(tmp_path):
    """THE PROPERTY. Anything run-varying anywhere in this file truncates the
    cacheable prefix at that byte, and this file goes FIRST in the context."""
    a = _generate(tmp_path / "a.md")
    b = _generate(tmp_path / "b.md")
    if a != b:
        i = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), len(a))
        raise AssertionError(
            f"AGENTS.md is not reproducible: first difference at byte {i} of "
            f"{len(a)}, so {len(a) - i} bytes ({(len(a) - i) / len(a):.0%}) "
            f"fall outside any cacheable prefix.\n"
            f"  a: {a[max(0, i - 40):i + 40]!r}\n"
            f"  b: {b[max(0, i - 40):i + 40]!r}")


def test_the_header_still_tells_a_reader_how_the_file_is_maintained(tmp_path):
    """The over-fix guard: the freshness contract must survive, or this trades
    a cache win for a file nobody knows how to refresh."""
    text = _generate(tmp_path / "c.md").decode("utf-8")
    head = "\n".join(text.splitlines()[:8])
    assert "DO NOT EDIT BY HAND" in head
    assert "make agents" in head, "the manual refresh command vanished"
    assert "every pipeline run" in head, \
        "the header no longer says the file regenerates itself"


def test_no_clock_reading_reaches_the_generated_file(tmp_path):
    """A second timestamp added anywhere later would silently reintroduce the
    defect, so pin the CAUSE as well as the symptom."""
    src = (ROOT / "bin/gen_agents_md.py").read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    for banned in ("time.strftime", "time.time(", "datetime.now",
                   "datetime.utcnow"):
        assert banned not in code, \
            f"{banned} is back in gen_agents_md; a clock reading in this file " \
            f"truncates the cached prefix for every phase"


def test_the_file_really_is_large_enough_for_this_to_matter(tmp_path):
    """Guards the premise rather than assuming it: if AGENTS.md were tiny the
    finding would be a curiosity, not a cost."""
    assert len(_generate(tmp_path / "d.md")) > 4000

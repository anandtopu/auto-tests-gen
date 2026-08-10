"""The catalog slice falls back to everything, and says so.

Coverage put engine/lib/catalog_slice.py at 57.0% with lines 104-146 -- all of
`main()` -- uncovered. That function decides which EXISTING tests the generate
phase gets to see, and the failure it is designed against is subtle: handing
generation an empty slice does not break anything visibly, it just makes the
model duplicate tests it could not see. CLAUDE.md calls starving that context
worse than over-feeding it.

So the behaviour worth holding is the fallback, and it has two triggers that
must both stay loud:

  * the resolution contract is unreadable -> use the whole catalog, say why;
  * the contract is fine but nothing matched -> use the whole catalog, say so.

Driven by hand first: a valid contract selected 3 of 4 rows and named the
filter, both fallbacks emitted all 4 rows with an explanation, and no argument
exited 64.
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
MOD = ROOT / "engine/lib/catalog_slice.py"


def _run(*args):
    return subprocess.run([sys.executable, str(MOD), *args], cwd=str(ROOT),
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)


def _contract(tmp_path, name, **body):
    p = tmp_path / name
    p.write_text(json.dumps(body), encoding="utf-8")
    return str(p)


def _rows(out):
    return [json.loads(l) for l in out.splitlines() if l.strip()]


def test_no_argument_is_a_usage_error(tmp_path):
    r = _run()
    assert r.returncode == 64
    assert "Traceback" not in r.stderr


def test_a_matching_contract_narrows_the_slice_and_says_by_how_much(tmp_path):
    c = _contract(tmp_path, "ok.json", test_repos=["e2e-api-tests-1"],
                  source_repos=["orders-api"])
    r = _run(c)
    assert r.returncode == 0
    rows = _rows(r.stdout)
    assert rows, "a matching contract produced no rows at all"
    assert "relevant" in r.stderr, "the slice does not report what it selected"


def test_a_contract_matching_nothing_hands_over_the_whole_catalog(tmp_path):
    """The important one. An empty slice is invisible downstream -- generation
    simply cannot see the tests that already exist and writes them again."""
    none = _run(_contract(tmp_path, "none.json", test_repos=["nope-repo"],
                          source_repos=["nope-src"]))
    everything = _run(_contract(tmp_path, "all.json", test_repos=[], source_repos=[]))
    assert len(_rows(none.stdout)) == len(_rows(everything.stdout)) > 0, \
        "a non-matching contract did not fall back to the full catalog"
    assert "no rows matched" in none.stderr, "the fallback happened silently"
    assert "full catalog" in none.stderr


def test_an_unreadable_contract_falls_back_and_names_the_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    r = _run(str(bad))
    assert r.returncode == 0, "an unreadable contract broke the run"
    assert _rows(r.stdout), "no catalog rows were handed over"
    assert "unreadable" in r.stderr
    assert "bad.json" in r.stderr, "the message does not say WHICH file"
    assert "full catalog" in r.stderr


def test_the_selection_count_is_reported_not_just_the_rows(tmp_path):
    """`3/4 row(s)` is what tells a reader the slice did something. A bare list
    of rows cannot distinguish a filter that worked from one that no-oped."""
    r = _run(_contract(tmp_path, "ok2.json", test_repos=["e2e-api-tests-1"],
                       source_repos=["orders-api"]))
    assert "/" in r.stderr and "row(s)" in r.stderr

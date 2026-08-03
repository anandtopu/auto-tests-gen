"""The catalog has ONE definition of where it lives.

`catalog/*.jsonl` has been in `app_paths.SEEDED` since R12, and
`test_app_paths.py` pinned that the string is listed. Nothing checked that any
reader honoured it: twelve modules had copy-pasted the same three-line idiom
against a hardcoded `catalog/`, and exactly one (regen_coverage) resolved it
through `catalog_dir()`.

Under a relocated `AIQE_CATALOG_DIR` that split the estate in half — the
bootstrap wrote to the new directory and routing regenerated from it, while
AGENTS.md, the SQLite index, coverage gaps, the team report and the dashboard
all still read the image path. AGENTS.md is injected into every LLM phase, so
authoring would have been told about a catalog nobody was writing to, and
nothing anywhere would have said so.

These pin the INVARIANT (no production module resolves catalog data itself),
not the list of files that happened to be wrong — a thirteenth reader added
tomorrow is caught by the same assertion.
"""
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import app_paths  # noqa: E402

# Where production code lives. Tests legitimately name literal paths (they build
# fixture estates), and bootstrap/*.sh is covered by its own smoke test.
PROD = ("engine", "bin", "eval", "catalog/bootstrap", "triggers")


def _prod_files():
    for root in PROD:
        d = ROOT / root
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.py")):
            if "__pycache__" in str(p):
                continue
            yield p


def test_no_production_module_resolves_catalog_data_itself():
    """The failure this replaces was invisible precisely because each copy
    looked correct on its own."""
    pat = re.compile(r'''["']catalog/(\*\.jsonl|health\.json|review)''')
    offenders = []
    for p in _prod_files():
        # app_paths IS the definition; demo_data/state_bundle enumerate paths as
        # DATA (what to delete, what to ship) rather than reading the catalog.
        if p.name in ("app_paths.py", "demo_data.py", "state_bundle.py"):
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").split("\n"), 1):
            if line.lstrip().startswith("#") or '"""' in line:
                continue
            # A literal that is genuinely NOT a path read (a provenance label, a
            # doc string in output) may opt out by SAYING SO on the line. An
            # exception has to be declared and justified in the source — the
            # alternative is a hidden allow-list here, which is how a real
            # reader gets waved through for looking familiar.
            if "not-a-path:" in line:
                continue
            if pat.search(line):
                offenders.append(f"{p.relative_to(ROOT)}:{i}  {line.strip()[:90]}")
    assert not offenders, (
        "catalog data resolved outside app_paths — these ignore AIQE_CATALOG_DIR "
        "and AIQE_STATE_DIR:\n  " + "\n  ".join(offenders))


def test_callers_pass_their_own_root_so_a_patched_estate_still_resolves():
    """`catalog_files()` with no argument falls back to app_paths' OWN root, not
    the caller's — so collapsing the twelve copied loops silently bypassed the
    `monkeypatch.setattr(qa, "ROOT", tmp_path)` seam three suites use to build a
    fixture estate. They failed with "no cataloged test with id ..." against a
    catalog they had just written.

    Passing the module's ROOT restores it exactly: the env knobs are consulted
    FIRST either way, so relocation still wins and only the fallback changes.
    """
    offenders = []
    for p in _prod_files():
        if p.name == "app_paths.py":
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        if "app_paths.catalog_files()" not in src:
            continue
        if re.search(r"^ROOT\s*=", src, re.M):
            offenders.append(str(p.relative_to(ROOT)))
    assert not offenders, (
        "these define a module ROOT but call catalog_files() without it, so a "
        "test that patches ROOT reads the real estate:\n  " + "\n  ".join(offenders))


def test_an_explicit_root_is_honoured_when_no_knob_is_set(tmp_path, monkeypatch):
    monkeypatch.delenv("AIQE_CATALOG_DIR", raising=False)
    monkeypatch.delenv("AIQE_STATE_DIR", raising=False)
    cat = tmp_path / "catalog"
    cat.mkdir()
    (cat / "zz.jsonl").write_text("{}\n", encoding="utf-8")
    assert [f.name for f in app_paths.catalog_files(root=tmp_path)] == ["zz.jsonl"]
    # ...and the knob still outranks it.
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setenv("AIQE_CATALOG_DIR", str(other))
    assert app_paths.catalog_files(root=tmp_path) == []


def test_catalog_files_excludes_the_documentation_fixture():
    """Every one of the twelve copies skipped catalog.sample.jsonl. Losing that
    when they were collapsed would inject a fabricated repo into AGENTS.md and
    give it `covers:` entries, silently routing real work at a fixture."""
    names = [f.name for f in app_paths.catalog_files()]
    assert app_paths.SAMPLE_CATALOG not in names
    assert (app_paths.catalog_dir() / app_paths.SAMPLE_CATALOG).exists(), \
        "the fixture is gone, so this test can no longer prove it is excluded"


def test_catalog_files_is_empty_not_an_error_before_bootstrap(tmp_path):
    """A fresh checkout has no catalog. Every caller already treated that as
    'no evidence yet'; raising here would break onboarding at first run."""
    assert app_paths.catalog_files(root=tmp_path) == []


def test_health_follows_a_relocated_catalog(monkeypatch, tmp_path):
    """health.json is the same dataset as the mappings. Leaving it in the image
    path while the mappings moved would report every test as never-run."""
    monkeypatch.setenv("AIQE_CATALOG_DIR", str(tmp_path / "cat"))
    assert app_paths.catalog_health() == tmp_path / "cat" / "health.json"
    # An explicit knob still wins — the pre-existing contract.
    monkeypatch.setenv("AIQE_HEALTH_FILE", str(tmp_path / "h.json"))
    assert app_paths.catalog_health() == tmp_path / "h.json"


def test_a_relocated_catalog_is_actually_read_end_to_end(tmp_path):
    """The assertion the string-listing pin could not make: point
    AIQE_CATALOG_DIR at a directory holding ONE mapping and confirm a reader
    that used to hardcode the path now sees it — and does not see the real
    estate's rows."""
    cat = tmp_path / "cat"
    cat.mkdir()
    (cat / "zz-relocated.jsonl").write_text(
        '{"test_id":"zz-1","test_repo":"zz-relocated","file":"a.spec.js",'
        '"title":"relocated marker","layer":"api","tags":[],'
        '"evidence":{"endpoints":[],"ui_routes":[],"git_jira_keys":[]},'
        '"mapping":{"app_repos":["orders-api"],"services":[],"domain":"",'
        '"feature":"","confidence":0.9,"method":["contract_match"],'
        '"status":"auto"}}\n', encoding="utf-8")
    env = {**os.environ, "AIQE_CATALOG_DIR": str(cat), "PYTHONPATH": str(ROOT / "engine/lib")}
    out = subprocess.run(
        [sys.executable, "-c",
         "import app_paths,json;"
         "print(json.dumps([f.name for f in app_paths.catalog_files()]))"],
        capture_output=True, text=True, env=env, cwd=str(ROOT),
        stdin=subprocess.DEVNULL, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "zz-relocated.jsonl" in out.stdout
    assert "e2e-api-tests-1.jsonl" not in out.stdout, \
        "the real estate leaked through a relocated catalog dir"

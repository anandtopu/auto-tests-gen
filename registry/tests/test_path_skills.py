"""Regression tests for path-triggered OpenHands skills (bin/gen_path_skills.py).

The point of the feature is that an agent writing API tests stops receiving UI
page-object conventions and vice versa. These tests pin the two things that can
silently break it: the globs must be derived from the registry (so a new repo is
covered), and they must not match files outside the layer they belong to."""
import pathlib, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
sys.path.insert(0, str(ROOT / "bin"))
import gen_path_skills as gps
from registry import load_registry

OUT = ROOT / ".agents/skills"


def _frontmatter(layer):
    text = (OUT / f"e2e-{layer}-conventions/SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---"), "path triggers require frontmatter"
    fm = text.split("---", 2)[1]
    paths = [l.strip().lstrip("- ").strip('"')
             for l in fm.splitlines() if l.strip().startswith('- "')]
    return fm, paths, text


def _matches(glob, path):
    """Gitignore-ish semantics for the shapes we emit: '**' crosses '/'."""
    import re
    rx = re.escape(glob).replace(r"\*\*/", "(?:.*/)?").replace(r"\*\*", ".*")
    rx = rx.replace(r"\*", "[^/]*")
    return re.fullmatch(rx, path) is not None


# ------------------------------------------------------------------ generation

def test_generates_a_skill_per_layer_with_path_triggers():
    gps.generate()
    for layer in ("api", "ui"):
        fm, paths, text = _frontmatter(layer)
        assert "paths:" in fm and paths, f"{layer} has no path triggers"
        assert f"name: e2e-{layer}-conventions" in fm
        assert "AUTO-GENERATED" in text


def test_globs_are_derived_from_the_registry_not_hardcoded():
    reg = load_registry()
    api_repos = [t["name"] for t in reg["test_repositories"] if t.get("layer") == "api"]
    globs = gps.globs_for("api", reg)
    for name in api_repos:
        assert any(name in g for g in globs), f"{name} has no trigger"
    # a UI repo must never appear in the API triggers
    ui_repos = [t["name"] for t in reg["test_repositories"] if t.get("layer") == "ui"]
    for name in ui_repos:
        assert not any(name in g for g in gps.globs_for("api", reg))


def test_a_new_repo_gets_triggers_without_touching_this_file():
    reg = load_registry()
    reg["test_repositories"].append(
        {"name": "zz-new-ui", "layer": "ui", "framework": "playwright",
         "layout": {"specs": "e2e/", "pages": "po/"}})
    globs = gps.globs_for("ui", reg)
    assert "workspace/tests/zz-new-ui/e2e/**" in globs
    assert "workspace/tests/zz-new-ui/po/**" in globs
    assert "po/**" in globs


def test_regeneration_is_deterministic():
    gps.generate()
    first = (OUT / "e2e-api-conventions/SKILL.md").read_text(encoding="utf-8")
    gps.generate()
    assert (OUT / "e2e-api-conventions/SKILL.md").read_text(encoding="utf-8") == first


def test_conventions_stay_single_sourced():
    """The skill wraps skills/e2e-<layer>-conventions/ — it must not fork the text."""
    for layer in ("api", "ui"):
        src = (ROOT / f"skills/e2e-{layer}-conventions/SKILL.md").read_text(encoding="utf-8")
        body = src.split("---", 2)[2].strip()
        generated = (OUT / f"e2e-{layer}-conventions/SKILL.md").read_text(encoding="utf-8")
        first_line = [l for l in body.splitlines() if l.strip()][0]
        assert first_line in generated, f"{layer} conventions not carried through"


# ------------------------------------------------------------------ semantics

def test_api_triggers_match_api_specs_and_not_ui_specs():
    api = gps.globs_for("api")
    hit = "workspace/tests/e2e-api-tests-1/suites/orders/x.spec.js"
    miss = "workspace/tests/e2e-ui-tests-1/tests/checkout/x.spec.ts"
    assert any(_matches(g, hit) for g in api), "API spec did not trigger API skill"
    assert not any(_matches(g, miss) for g in api), "UI spec wrongly triggered API skill"


def test_ui_triggers_match_ui_files_and_not_api_specs():
    ui = gps.globs_for("ui")
    for hit in ("workspace/tests/e2e-ui-tests-1/tests/checkout/x.spec.ts",
                "workspace/tests/e2e-ui-tests-1/pages/CartPage.ts"):
        assert any(_matches(g, hit) for g in ui), f"{hit} did not trigger UI skill"
    miss = "workspace/tests/e2e-api-tests-1/suites/orders/x.spec.js"
    assert not any(_matches(g, miss) for g in ui), "API spec wrongly triggered UI skill"


def test_repo_relative_specs_glob_does_not_capture_the_control_repo():
    """A bare 'tests/**' would also match this repo's own tests/gate-adversarial.sh
    and inject UI conventions when an agent touches the gate harness."""
    ui = gps.globs_for("ui")
    assert "tests/**" not in ui, "bare tests/** collides with the control repo"
    assert not any(_matches(g, "tests/gate-adversarial.sh") for g in ui)
    assert not any(_matches(g, "registry/tests/test_routing_golden.py") for g in ui)
    # …while a real repo-relative UI spec still triggers
    assert any(_matches(g, "tests/checkout/cart.spec.ts") for g in ui)


def test_cli_and_wiring():
    r = subprocess.run([sys.executable, str(ROOT / "bin/gen_path_skills.py")],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    assert "path trigger" in r.stdout
    # registry mutations must refresh the globs, not leave them stale
    admin = (ROOT / "engine/lib/repo_admin.py").read_text(encoding="utf-8")
    assert "gen_path_skills.py" in admin

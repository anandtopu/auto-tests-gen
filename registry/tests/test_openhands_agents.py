"""OpenHands task skills + named agent presets.

Pins: every skill has valid frontmatter and carries the non-negotiables; the
path-skill generator never clobbers the hand-authored skills; agent presets build
messages that point at sanctioned entry points only; the launcher stays out of
the engine (standalone invariant) and works --dry with no network.
"""
import pathlib, re, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import openhands_agents

SKILLS_DIR = ROOT / ".agents/skills"
GENERATED = {"e2e-api-conventions", "e2e-ui-conventions"}
TASK_SKILLS = {"ai-qe", "pr-review", "test-generation", "test-review",
               "test-coverage", "test-data-generation", "test-plan"}


def _frontmatter(p):
    text = p.read_text(encoding="utf-8")
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    assert m, f"{p} has no YAML frontmatter"
    import yaml
    return yaml.safe_load(m.group(1)), text


def test_all_task_skills_exist_with_valid_frontmatter():
    found = {d.name for d in SKILLS_DIR.iterdir() if d.is_dir()}
    assert TASK_SKILLS <= found, f"missing skills: {TASK_SKILLS - found}"
    names = set()
    for d in sorted(SKILLS_DIR.iterdir()):
        fm, text = _frontmatter(d / "SKILL.md")
        assert fm.get("name") == d.name, f"{d.name}: frontmatter name mismatch"
        assert fm.get("description"), f"{d.name}: no description"
        assert len(text) > 400, f"{d.name}: skill body is empty-ish"
        assert fm["name"] not in names, f"duplicate skill name {fm['name']}"
        names.add(fm["name"])


def test_task_skills_carry_the_non_negotiables():
    """Every skill that can touch repos must restate the gate monopoly, and every
    skill that reads tickets/PRs must restate the data-not-instructions rule."""
    for name in ("pr-review", "test-generation", "test-data-generation", "test-plan"):
        _, text = _frontmatter(SKILLS_DIR / name / "SKILL.md")
        low = text.lower()
        assert "never" in low and ("push" in low or "gate" in low), name
        assert "data" in low and "instructions" in low, \
            f"{name}: missing the data-not-instructions framing"
    _, text = _frontmatter(SKILLS_DIR / "test-review" / "SKILL.md")
    assert "never marks `approved`" in text or "never approve" in text.lower(), \
        "test-review must forbid agent self-approval"


def test_generator_does_not_clobber_task_skills():
    r = subprocess.run([sys.executable, str(ROOT / "bin/gen_path_skills.py")],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    for name in TASK_SKILLS:
        assert (SKILLS_DIR / name / "SKILL.md").exists(), \
            f"gen_path_skills.py deleted hand-authored skill {name}"


def test_agent_presets_target_sanctioned_entry_points():
    m = openhands_agents.build("test-generation", "orders-api", "201")
    assert "bash engine/pipeline.sh pr orders-api 201" in m
    m = openhands_agents.build("test-generation", "PROJ-301")
    assert "bash engine/pipeline.sh jira PROJ-301" in m
    m = openhands_agents.build("test-plan", "PROJ-301")
    assert "bash engine/pipeline.sh plan PROJ-301" in m
    m = openhands_agents.build("pr-review", "orders-api", "201")
    assert "pr-review" in m and "orders-api" in m and "201" in m
    m = openhands_agents.build("test-review", "PROJ-301")
    assert "never approve" in m.lower()


def test_every_preset_references_an_existing_skill():
    for name, a in openhands_agents.AGENTS.items():
        assert (SKILLS_DIR / a["skill"] / "SKILL.md").exists(), \
            f"agent {name} points at missing skill {a['skill']}"


def test_unknown_agent_and_missing_target_fail_cleanly():
    with pytest.raises(SystemExit):
        openhands_agents.build("no-such-agent", "x")
    with pytest.raises(SystemExit):
        openhands_agents.build("test-review")          # target required
    with pytest.raises(SystemExit):
        openhands_agents.build("test-generation")      # would build `pipeline.sh jira `
    with pytest.raises(SystemExit):
        openhands_agents.build("pr-review", "orders-api")   # PR number required


def test_qa_cli_dry_run_needs_no_network():
    r = subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), "openhands-run",
                        "pr-review", "orders-api", "201", "--dry"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       stdin=subprocess.DEVNULL, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "pr-review" in r.stdout and "orders-api" in r.stdout


def test_agents_module_keeps_the_standalone_invariant():
    """Message building lives in engine/lib but must never touch the client —
    test_standalone scans for the import; this pins the intent locally too."""
    src = (ROOT / "engine/lib/openhands_agents.py").read_text(encoding="utf-8")
    assert "openhands_" + "client" not in src
    assert "urllib" not in src and "requests" not in src

"""The platform constitution is executable (SDD story 5.1).

specs/platform/constitution.yaml states every non-negotiable and names the
test pin(s) enforcing it. This verifier makes the constitution binding: a
clause whose pin no longer exists breaks the build — so deleting a pin means
consciously amending the constitution, never silently orphaning a promise.
(The pins themselves run in the same suite; existence here + green there =
the clause is enforced.)
"""
import pathlib
import re
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONSTITUTION = ROOT / "specs/platform/constitution.yaml"


@pytest.fixture(scope="module")
def clauses():
    doc = yaml.safe_load(CONSTITUTION.read_text(encoding="utf-8"))
    assert isinstance(doc, dict) and doc.get("clauses"), \
        "constitution must carry a non-empty clauses list"
    return doc["clauses"]


def test_clause_shape(clauses):
    seen = set()
    for c in clauses:
        for field in ("id", "statement", "category", "pins"):
            assert c.get(field), f"clause {c.get('id', '?')} missing {field}"
        assert c["id"] not in seen, f"duplicate clause id {c['id']}"
        seen.add(c["id"])
        assert isinstance(c["pins"], list) and c["pins"], \
            f"{c['id']}: a clause without a pin is a wish, not a spec"


def test_every_pin_exists(clauses):
    """The binding property: each named pin resolves to a real file, and a
    named test function actually exists in it."""
    problems = []
    for c in clauses:
        for pin in c["pins"]:
            f = ROOT / pin["file"]
            if not f.exists():
                problems.append(f"{c['id']}: pin file missing: {pin['file']}")
                continue
            t = pin.get("test")
            if t:
                src = f.read_text(encoding="utf-8", errors="replace")
                if not re.search(rf"^def {re.escape(t)}\b", src, re.M):
                    problems.append(
                        f"{c['id']}: {pin['file']} has no test '{t}'")
    assert not problems, "orphaned constitution clauses:\n" + "\n".join(problems)


def test_constitution_survives_clear_demo():
    """specs/ is cleared wholesale as generated demo output — the hand-authored
    platform constitution must be in the preserved subset."""
    sys.path.insert(0, str(ROOT / "engine/lib"))
    import demo_data
    assert "platform" in demo_data.KEEP_SUBDIRS.get("specs", set())


def test_claude_md_names_the_rendering_relationship():
    """CLAUDE.md's non-negotiables are the RENDERING of this file — the doc
    must say so, or the two drift as independent sources of truth."""
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "constitution" in text.lower(), \
        "CLAUDE.md must point at specs/platform/constitution.yaml"


def test_every_clause_id_cited_in_claude_md_actually_exists():
    """CLAUDE.md cited C12 for a full release cycle while the constitution
    stopped at C11.

    `test_every_pin_exists` catches the opposite direction — a clause whose pin
    was deleted — so an undefended rule is loud. Nothing checked a rule that was
    only ever WRITTEN DOWN as enforced. That is the same failure this codebase
    keeps finding elsewhere, applied to its own rulebook: documentation
    asserting a guarantee nothing provides.
    """
    import re
    import yaml
    doc = yaml.safe_load((ROOT / "specs/platform/constitution.yaml")
                         .read_text(encoding="utf-8")) or {}
    have = {c["id"] for c in doc.get("clauses") or []}
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    # Only clause-shaped citations, e.g. "clause C12" or "constitution C7" —
    # not every capital-C token in prose.
    cited = set(re.findall(r"(?:clause|constitution)\s+(C\d+)", text, re.I))
    missing = sorted(cited - have, key=lambda c: int(c[1:]))
    assert not missing, (
        f"CLAUDE.md cites {missing} but the constitution defines {sorted(have)} "
        "— a rule documented as enforced that does not exist")


def test_no_python_file_carries_a_syntax_warning():
    r"""`"testdata\K-1\cases.json"` sat in the suite relying on Python
    preserving `\K` and `\c` as unknown escapes.

    That is a SyntaxWarning today and a hard error in a future release, so the
    suite had a scheduled break in it. Worse, the line's `.replace("\\\\", os.sep)`
    was a no-op on Windows and a fixup on POSIX — the test passed on both
    platforms by accident rather than by intent, which is the kind of thing that
    looks deliberate until the day it stops working.

    Compiling every file with the warning promoted to an error costs about a
    second and catches the whole class.
    """
    import warnings
    roots = ("engine", "bin", "registry", "catalog", "eval", "triggers")
    problems = []
    for root in roots:
        d = ROOT / root
        if not d.is_dir():
            continue
        for p in d.rglob("*.py"):
            if "__pycache__" in str(p):
                continue
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", SyntaxWarning)
                    compile(p.read_text(encoding="utf-8"), str(p), "exec")
            except SyntaxWarning as w:
                problems.append(f"{p.relative_to(ROOT)}: {w}")
            except SyntaxError as e:
                problems.append(f"{p.relative_to(ROOT)}: {e}")
    assert not problems, "python files with syntax warnings:\n  " + "\n  ".join(problems)

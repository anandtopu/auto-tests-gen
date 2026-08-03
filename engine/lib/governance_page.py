"""One "how we build E2E tests here" page, GENERATED (SDD adoption S6).

Every fact on this page is read from the thing that enforces it — the
constitution clauses and their pins, org-config, the settings resolvers — so the
page cannot drift from the platform's actual behaviour. A hand-written
governance document is correct on the day it is written and slowly becomes
fiction; this one is wrong only if the code is.

Two properties make that claim real rather than decorative:

**Every clause names its enforcing pin, and the pin is checked to EXIST.** A
clause whose pin has been deleted is reported as `unpinned`, not quietly printed
as though it still held. `registry/tests/test_constitution.py` already breaks the
build on an orphaned pin; this surfaces the same fact to a reader who is not
running the suite.

**Configured behaviour is read live, never described from memory.** The page
says what governance is doing in THIS estate right now — including, in plain
words, when the answer is "nothing is enforced". A governance page that
describes an aspiration while the gate is off is worse than no page: it tells
people a rule they will be surprised to learn nobody applies.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app_paths                      # R12: mutable paths resolve here
import spec_workflow

ROOT = app_paths.ROOT
CONSTITUTION = ROOT / "specs/platform/constitution.yaml"


def clauses():
    """Constitution clauses, each annotated with whether its pins still exist.

    `pin_missing` is the interesting field: a clause is only as true as the test
    that holds it, and a deleted pin turns a rule into a hope.
    """
    try:
        import yaml
        doc = yaml.safe_load(CONSTITUTION.read_text(encoding="utf-8")) or {}
    except Exception:                          # noqa: BLE001
        return []
    out = []
    for c in doc.get("clauses") or []:
        pins = []
        for p in c.get("pins") or []:
            f = (p or {}).get("file") if isinstance(p, dict) else str(p)
            if not f:
                continue
            pins.append({"file": f, "exists": (ROOT / f).exists(),
                         "test": (p or {}).get("test") if isinstance(p, dict) else None})
        out.append({
            "id": c.get("id", "?"),
            "statement": c.get("statement", ""),
            "category": c.get("category", ""),
            "pins": pins,
            # Surfaced, not hidden: an unpinned clause is a rule nothing defends.
            "pin_missing": [p["file"] for p in pins if not p["exists"]],
            "unpinned": not pins,
        })
    return out


def page():
    """Everything the governance page renders, from live sources only."""
    gov = spec_workflow.governance()
    cls = clauses()
    return {
        "governance": gov,
        # The honest headline. Said first because a reader who takes the rules
        # below as enforced, when they are not, has been misled by this page.
        "enforced": gov["requirements_gate"] or gov["spec_enforce"] != "off",
        "clauses": cls,
        "clause_count": len(cls),
        "unpinned": [c["id"] for c in cls if c["unpinned"] or c["pin_missing"]],
        "states": list(spec_workflow.STATES),
        "source": "specs/platform/constitution.yaml + registry/org-config.yaml",
    }


def markdown():
    """The same page as a document — for sharing with people who will never open
    the dashboard. Generated, so it cannot drift either."""
    d = page()
    g = d["governance"]
    L = ["# How we build E2E tests here", "",
         "*Generated from " + d["source"] + ". Do not edit by hand — edit the "
         "constitution or the configuration, and this follows.*", ""]
    L.append("## Is any of this enforced right now?")
    L.append("")
    # Before the answer, not after it: a reader who takes "No" as a choice when
    # it was a typo has been told the wrong thing about their own estate.
    for prob in g.get("problems") or []:
        L.append(f"> **Configuration ignored.** {prob}")
        L.append("")
    if d["enforced"]:
        L.append(f"**Yes.** Requirements gate: `{g['requirements_gate']}` — "
                 f"{g['requirements_gate_effect']}.")
        L.append("")
        L.append(f"Spec enforcement: `{g['spec_enforce']}` — {g['spec_enforce_effect']}.")
    else:
        L.append("**No.** The requirements gate is off and spec enforcement is "
                 "`off`, so every step below is advisory: the platform will not "
                 "stop a run that skips it. Turn them on in Settings — start "
                 "with `warn`.")
    L += ["", "## The workflow", "",
          " → ".join(s.upper() for s in d["states"]), "",
          "## The rules, and what holds them", ""]
    for c in d["clauses"]:
        L.append(f"**{c['id']} ({c['category']}).** {c['statement']}")
        if c["unpinned"]:
            L.append("  - ⚠ no pin declared — this rule is not defended by a test")
        for p in c["pins"]:
            mark = "" if p["exists"] else "  ⚠ MISSING"
            L.append(f"  - pinned by `{p['file']}`{mark}")
        L.append("")
    if d["unpinned"]:
        L += ["## Clauses whose pins are missing", "",
              "These are rules nothing currently defends: "
              + ", ".join(d["unpinned"]), ""]
    return "\n".join(L)


if __name__ == "__main__":
    import json
    sys.stdout.reconfigure(encoding="utf-8")
    if "--json" in sys.argv:
        print(json.dumps(page(), indent=1))
    else:
        print(markdown())

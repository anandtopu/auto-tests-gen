#!/usr/bin/env python3
"""Adversarial test-plan review — the author/opponent/arbiter trio for Workflow B.

The test plan is the artifact a human actually reads and approves (journey J5), and
until now exactly one agent wrote it with nothing arguing back. A single author
optimizes for covering the stated acceptance criteria; the defects that reach
production live in what the criteria never said — the absent token, the value one past
the cap, the second submission of the same request.

So: the author writes the plan, an **adversary** (read-only) hunts for what it missed,
and an **arbiter** judges each finding and folds the accepted ones in. This is the
cheapest place in the whole pipeline to buy quality — plans cost a fraction of specs,
and every scenario the adversary rescues here is one that would otherwise have been a
coverage gap discovered in production.

Safety properties, mirroring the advisory critic:

  * the adversary gets **read-only tools** — an opponent that can edit the plan is just
    a second author, and the value is entirely in arguing from outside it;
  * the arbiter may only ADD (its prompt forbids deleting the author's scenarios), so a
    misfiring adversary costs a redundant scenario, never a lost one;
  * both phases are run **non-fatally** by the pipeline: if either fails, the authored
    plan stands unchanged and the run continues;
  * this runs BEFORE the human approval gate, so nothing here bypasses review — it
    changes what the human is asked to approve, never whether they are asked.

CLI (used by engine/pipeline.sh and bin/qa.py):
  plan_adversary.py enabled          exit 0 if the adversarial pass should run
  plan_adversary.py summary [FILE]   one human line about the last review, or nothing
  plan_adversary.py show [FILE]      normalized signal as JSON
"""
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
GAPS = pathlib.Path(os.environ.get("AIQE_ADVERSARY_CONTRACT",
                                   "out/planadversary.contract.json"))
ARBITER = pathlib.Path(os.environ.get("AIQE_ARBITER_CONTRACT",
                                      "out/planarbiter.contract.json"))

CATEGORIES = ("negative", "boundary", "authz", "state", "cross-repo", "data")
SEVERITIES = ("high", "med", "low")


def enabled():
    """org-config plan_adversary.enabled, overridable per run by AIQE_PLAN_ADVERSARY.

    Total by construction: an unreadable config must not be the reason a run dies, and
    the feature only ever adds scenarios, so defaulting it on is the safe default.
    """
    env = os.environ.get("AIQE_PLAN_ADVERSARY")
    if env is not None:
        return env.strip() not in ("0", "false", "no", "off", "")
    try:
        import yaml
        loaded = yaml.safe_load(open(ROOT / "registry/org-config.yaml", encoding="utf-8"))
        section = (loaded or {}).get("plan_adversary") or {}
        return bool(section.get("enabled", True))
    except Exception:
        return True


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def signal(gaps_path=None, arbiter_path=None):
    """Normalized view of the adversarial pass. Absent files => an empty, valid signal."""
    gaps_doc = _read(gaps_path or GAPS)
    arb_doc = _read(arbiter_path or ARBITER)

    gaps = []
    for g in gaps_doc.get("gaps") or []:
        if not isinstance(g, dict) or not str(g.get("title") or "").strip():
            continue
        cat = g.get("category")
        sev = g.get("severity")
        gaps.append({
            "title": str(g["title"]).strip(),
            "category": cat if cat in CATEGORIES else "unclear",
            "severity": sev if sev in SEVERITIES else "med",
            "rationale": str(g.get("rationale") or "").strip(),
        })

    def _count(key):
        v = arb_doc.get(key)
        return v if isinstance(v, int) and v >= 0 else None

    accepted, rejected = _count("accepted_gaps"), _count("rejected_gaps")
    return {
        "ran": bool(gaps_doc),
        "raised": len(gaps),
        "high": sum(1 for g in gaps if g["severity"] == "high"),
        "accepted": accepted,
        "rejected": rejected,
        "arbitrated": bool(arb_doc),
        "scenarios_final": len(arb_doc.get("scenarios") or []),
        "gaps": gaps,
    }


def summary(gaps_path=None, arbiter_path=None):
    """One line for the pipeline log, the ticket comment and the plan reviewer."""
    s = signal(gaps_path, arbiter_path)
    if not s["ran"]:
        return ""
    if not s["raised"]:
        return "adversarial review: no gaps found — the authored plan stands"
    bits = [f"adversarial review: {s['raised']} gap(s) raised"]
    if s["high"]:
        bits.append(f"{s['high']} high-severity")
    if s["arbitrated"] and s["accepted"] is not None:
        bits.append(f"{s['accepted']} accepted")
        if s["rejected"]:
            bits.append(f"{s['rejected']} rejected")
        bits.append(f"{s['scenarios_final']} scenario(s) in the final plan")
    else:
        bits.append("arbitration did not complete — the authored plan stands")
    return ", ".join(bits)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "show"
    extra = argv[2] if len(argv) > 2 else None
    if cmd == "enabled":
        return 0 if enabled() else 1
    if cmd == "summary":
        line = summary(extra)
        if line:
            print(line)
        return 0
    if cmd == "show":
        print(json.dumps(signal(extra), indent=1))
        return 0
    print(f"usage: plan_adversary.py enabled|summary|show", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv))

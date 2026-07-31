#!/usr/bin/env python3
"""Structured test-plan specifications (SDD stories 1.1–1.3).

The core inversion of spec-driven development: what the human signs is a
STRUCTURED spec (`specs/<KEY>/testplan.yaml` — scenarios with Given/When/Then
steps, requirement links and `verification` clauses naming what a satisfying
test must assert), and the markdown the reviewer reads is a deterministic
RENDERING of it — one source of truth, never two files to keep in sync.

A plan is "structured" when any scenario carries `steps` or `verification`.
Legacy free-form plans (no structured fields in the contract) get NO spec file
and behave byte-for-byte as before — SDD is adoptable per ticket, not a
migration cliff (pinned). `AIQE_SPEC_MODE=0` kills the whole layer.

The spec store is TRACKED (like testplans/): specs are somebody's signed work,
carried by state bundles, removed by clear-demo.

CLI: spec_store.py render <KEY> | show <KEY> | diff <KEY>
"""
import hashlib
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC_DIR = pathlib.Path(os.environ.get("AIQE_SPEC_DIR") or ROOT / "specs")
SCHEMA = ROOT / "engine/phases/contracts/spec.schema.json"

STRUCTURED_FIELDS = ("steps", "verification")


def enabled():
    return os.environ.get("AIQE_SPEC_MODE", "1") != "0"


def spec_path(key):
    return SPEC_DIR / key / "testplan.yaml"


def is_structured(contract):
    """True when the testplan contract carries SDD fields — the trigger for
    writing a spec file. Arbiter-added scenarios may lack them; ANY structured
    scenario makes the plan structured."""
    for s in (contract or {}).get("scenarios") or []:
        if isinstance(s, dict) and any(s.get(f) for f in STRUCTURED_FIELDS):
            return True
    return False


def validate(spec):
    """Schema-shaped validation (stdlib — no jsonschema dep). Returns a list of
    problems; empty = valid. Full-property by design: the spec is the artifact
    a human signs, so its shape IS the contract."""
    problems = []
    if not isinstance(spec, dict):
        return ["spec is not a mapping"]
    if not spec.get("key"):
        problems.append("missing key")
    scenarios = spec.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        problems.append("scenarios must be a non-empty list")
        return problems
    seen = set()
    for i, s in enumerate(scenarios):
        if not isinstance(s, dict):
            problems.append(f"scenario[{i}] is not a mapping")
            continue
        for req in ("id", "title", "layer", "target_repo"):
            if not s.get(req):
                problems.append(f"scenario[{i}] missing {req}")
        if s.get("id") in seen:
            problems.append(f"duplicate scenario id {s['id']}")
        seen.add(s.get("id"))
        steps = s.get("steps")
        if steps is not None and not isinstance(steps, dict):
            problems.append(f"{s.get('id', i)}: steps must be a mapping "
                            f"(given/when/then)")
        ver = s.get("verification")
        if ver is not None and (not isinstance(ver, list)
                                or not all(isinstance(v, str) for v in ver)):
            problems.append(f"{s.get('id', i)}: verification must be a list "
                            f"of strings")
    return problems


def write_from_contract(key, contract):
    """Persist the spec when the contract is structured. Returns the spec path,
    or None (legacy contract / disabled / invalid — never an exception; a spec
    failure must not break plan recording)."""
    if not enabled() or not is_structured(contract):
        return None
    spec = {"key": key,
            "scenarios": contract.get("scenarios") or [],
            "open_questions": contract.get("open_questions") or []}
    if validate(spec):
        return None
    try:
        import yaml
        p = spec_path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump(spec, sort_keys=True,
                                      allow_unicode=True),
                       encoding="utf-8", newline="\n")
        os.replace(tmp, p)
        return p
    except Exception:
        return None


def load(key):
    """The spec dict, or None. Guarded: a torn/invalid file is None, never an
    exception — callers fall back to the free-form path."""
    p = spec_path(key)
    if not p.exists():
        return None
    try:
        import yaml
        spec = yaml.safe_load(p.read_text(encoding="utf-8"))
        return spec if isinstance(spec, dict) and not validate(spec) else None
    except Exception:
        return None


def sha(key):
    """Hash of the canonical spec bytes — what an approval signs (1.3)."""
    p = spec_path(key)
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""
    except OSError:
        return ""


def render(key, spec=None):
    """Deterministic markdown from the spec — the reviewer's document. One
    source of truth: this OVERWRITES testplans/<KEY>.md for structured plans."""
    spec = spec or load(key)
    if not spec:
        return None
    L = [f"# Test Plan — {key}", "",
         "> Rendered from `specs/" + key + "/testplan.yaml` — the structured "
         "spec is the source of truth; edit scenarios there (or via the plan "
         "editor), not this file.", "", "## Scenarios", ""]
    for s in spec.get("scenarios", []):
        L.append(f"### {s.get('id')} — {s.get('title')}")
        L.append(f"- layer: {s.get('layer')} · target repo: "
                 f"{s.get('target_repo')}"
                 + (f" · requirements: {', '.join(s['requirement_refs'])}"
                    if s.get("requirement_refs") else
                    (f" · behavior: {s['behavior_ref']}"
                     if s.get("behavior_ref") else "")))
        steps = s.get("steps") or {}
        if steps:
            for kw in ("given", "when", "then"):
                if steps.get(kw):
                    L.append(f"- **{kw.capitalize()}** {steps[kw]}")
        for v in s.get("verification") or []:
            L.append(f"- verify: {v}")
        if s.get("data_needs") or s.get("data"):
            L.append(f"- data: {s.get('data_needs') or s.get('data')}")
        L.append("")
    oq = spec.get("open_questions") or []
    if oq:
        L.append("## Open Questions")
        L += [f"- {q}" for q in oq]
        L.append("")
    return "\n".join(L)


def render_to_plan(key):
    """Write the rendering over testplans/<KEY>.md. Returns the path or None."""
    md = render(key)
    if md is None:
        return None
    import plan_state
    p = plan_state.plan_path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(md, encoding="utf-8", newline="\n")
    return p


def diff_scenarios(old_spec, new_spec):
    """Scenario-level diff (1.3): what a re-approver actually needs to review.
    Returns human-readable lines; [] = semantically unchanged."""
    old = {s["id"]: s for s in (old_spec or {}).get("scenarios", [])
           if isinstance(s, dict) and s.get("id")}
    new = {s["id"]: s for s in (new_spec or {}).get("scenarios", [])
           if isinstance(s, dict) and s.get("id")}
    out = []
    for sid in sorted(new.keys() - old.keys()):
        out.append(f"ADDED    {sid} — {new[sid].get('title', '')}")
    for sid in sorted(old.keys() - new.keys()):
        out.append(f"REMOVED  {sid} — {old[sid].get('title', '')}")
    for sid in sorted(old.keys() & new.keys()):
        changed = [f for f in ("title", "layer", "target_repo", "steps",
                               "verification", "requirement_refs")
                   if old[sid].get(f) != new[sid].get(f)]
        if changed:
            out.append(f"CHANGED  {sid}: {', '.join(changed)}")
    return out


def merge_fold(original_path, folded_path):
    """Preserve the author's structured fields through the arbiter fold.

    The arbiter re-emits the plan contract and MAY only add scenarios — but a
    re-emission that drops the author's steps/verification would silently
    demote a structured spec to free-form. For every folded scenario whose id
    the original also has, missing structured fields are inherited from the
    original. Writes the merged contract over original_path. Raises on
    unreadable inputs — the caller's fallback is the plain copy."""
    orig = json.load(open(original_path, encoding="utf-8"))
    folded = json.load(open(folded_path, encoding="utf-8"))
    by_id = {s.get("id"): s for s in orig.get("scenarios") or []
             if isinstance(s, dict)}
    for s in folded.get("scenarios") or []:
        src = by_id.get(s.get("id")) if isinstance(s, dict) else None
        if src:
            for f in ("steps", "verification", "requirement_refs", "data"):
                if not s.get(f) and src.get(f):
                    s[f] = src[f]
    p = pathlib.Path(original_path)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(folded, indent=1), encoding="utf-8", newline="\n")
    os.replace(tmp, p)
    return p


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8")
    cmd, key = (argv + ["", ""])[:2]
    if cmd == "merge-fold" and len(argv) >= 3:
        try:
            merge_fold(argv[1], argv[2])
            return 0
        except Exception as e:
            print(f"merge-fold failed: {e}", file=sys.stderr)
            return 1
    if cmd == "render" and key:
        p = render_to_plan(key)
        print(f"rendered -> {p}" if p else f"no structured spec for {key}")
        return 0 if p else 1
    if cmd == "show" and key:
        spec = load(key)
        print(json.dumps(spec, indent=1) if spec
              else f"no structured spec for {key}")
        return 0 if spec else 1
    print("usage: spec_store.py render|show <KEY>", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

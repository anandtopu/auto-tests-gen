#!/usr/bin/env python3
"""Harness-side artifact materialization for COMPLETION providers
(multi-LLM story 2.2).

Agentic providers (claude, codex, openhands) write their own files. A
completion provider returns text only — but three phases are expected to leave
artifacts on disk:

  testplan / planarbiter -> testplans/<KEY>.md
  testdata               -> testdata/<KEY>/<file>

Both are DERIVABLE from the contract, which is why the capability matrix puts
these phases in the "completion + derived writes" class:

  * the plan markdown is already a deterministic RENDERING of the structured
    spec (SDD 1.2) — the same renderer runs here, so a plan authored on a
    local model is byte-identical in shape to one authored agentically;
  * testdata fixtures carry their content in the contract, because the
    wrapper appends `addendum()` to the prompt for completion runners: they
    are told to inline file content rather than write files they cannot write.

Nothing here writes into a test repo or touches git — the gate remains the
only writer. Total by contract: a missing/odd contract yields [] and a clear
reason, never an exception that fails an otherwise-good phase.

CLI (run_phase.sh):  derived_writes.py materialize <phase> <key> <contract>
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Phases whose artifacts the harness can reconstruct from the contract.
DERIVED_PHASES = ("testplan", "planarbiter", "testdata")


def addendum(phase):
    """Prompt text appended (LAST, in the run-parameters block, so prefix
    caching is unaffected) when a completion provider serves a derived-writes
    phase. Empty for every other phase/provider combination."""
    base = phase.split("-", 1)[0]
    if base in ("testplan", "planarbiter"):
        return ("\nPROVIDER NOTE: you are running without file-write tools. Do "
                "NOT attempt to write testplans/<KEY>.md — emit the JSON "
                "contract only, and the harness renders the plan document "
                "from it deterministically.")
    if base == "testdata":
        return ("\nPROVIDER NOTE: you are running without file-write tools. Do "
                "NOT attempt to write files. In the JSON contract, give every "
                "fixture a \"content\" field holding the COMPLETE file body "
                "(a JSON string), e.g. {\"canonical\":\"testdata/K/x.json\","
                "\"content\":\"{...}\",\"materialized\":[]} — the harness "
                "writes the files from it.")
    return ""


def _write(path, text):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")
    return p


def materialize(phase, key, contract):
    """(written_paths, problems). Never raises."""
    base = phase.split("-", 1)[0]
    written, problems = [], []
    if base not in DERIVED_PHASES:
        return written, problems
    if not isinstance(contract, dict):
        return written, ["contract is not an object"]

    if base in ("testplan", "planarbiter"):
        try:
            import spec_store
            # Structured contract -> the SDD renderer (one source of truth).
            if spec_store.is_structured(contract) and \
                    spec_store.write_from_contract(key, contract):
                p = spec_store.render_to_plan(key)
                if p:
                    written.append(str(p.relative_to(ROOT)))
                    return written, problems
        except Exception as e:
            problems.append(f"spec render failed: {e}")
        # Free-form fallback: a minimal, deterministic scenario table so a
        # completion-authored plan is still a reviewable document.
        rows = contract.get("scenarios") or []
        if not rows:
            problems.append("no scenarios in the contract — nothing to render")
            return written, problems
        L = [f"# Test Plan — {key}", "",
             "> Rendered by the harness from the testplan contract "
             "(completion provider).", "", "## Scenarios", "",
             "| ID | Title | Layer | Target repo | Behavior | Data |",
             "|---|---|---|---|---|---|"]
        for s in rows:
            if not isinstance(s, dict):
                continue
            L.append("| {} | {} | {} | {} | {} | {} |".format(
                s.get("id", "?"), s.get("title", ""), s.get("layer", ""),
                s.get("target_repo", ""),
                ", ".join(s.get("requirement_refs") or []) or s.get("behavior_ref", ""),
                s.get("data_needs", "")))
        oq = contract.get("open_questions") or []
        if oq:
            L += ["", "## Open Questions"] + [f"- {q}" for q in oq]
        written.append(str(_write(f"testplans/{key}.md",
                                  "\n".join(L) + "\n").relative_to(ROOT)))
        return written, problems

    # testdata: fixtures carry their content (see addendum()).
    for f in contract.get("fixtures") or []:
        if not isinstance(f, dict):
            continue
        dest, content = f.get("canonical"), f.get("content")
        if not dest:
            continue
        if content is None:
            problems.append(f"{dest}: no `content` in the contract — a "
                            f"completion provider cannot write files, so the "
                            f"fixture body must be inlined")
            continue
        if not isinstance(content, str):
            content = json.dumps(content, indent=1)
        # Confine writes to the canonical testdata tree: a contract is model
        # output, and model output never chooses a path outside it.
        rel = pathlib.Path(str(dest))
        if rel.is_absolute() or ".." in rel.parts or rel.parts[:1] != ("testdata",):
            problems.append(f"{dest}: refused — fixtures must live under "
                            f"testdata/")
            continue
        written.append(str(_write(rel, content).relative_to(ROOT)))
    return written, problems


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    if len(argv) < 3 or argv[0] != "materialize":
        print("usage: derived_writes.py materialize <phase> <key> <contract>",
              file=sys.stderr)
        return 64
    phase, key = argv[1], argv[2]
    path = argv[3] if len(argv) > 3 else f"out/{phase}.contract.json"
    try:
        contract = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        print(f"derived-writes: unreadable contract {path} ({e})",
              file=sys.stderr)
        return 1
    written, problems = materialize(phase, key, contract)
    for w in written:
        print(f"[derived] wrote {w}")
    for p in problems:
        print(f"[derived] {p}", file=sys.stderr)
    # Problems are reported, not fatal: the contract still stands and the
    # pipeline's own checks (schema, gate) decide the run's fate.
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

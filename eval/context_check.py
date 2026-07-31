#!/usr/bin/env python3
"""Retrieval-quality guardrail for the eval harness (cost-reduction 7.2/2.3).

Two measurements, both mechanical (no LLM, so they run in every `make eval`):

1. RETENTION — for each benchmark fixture, assemble the scoped context the
   authoring phases would receive and assert it still contains every
   `expected_context` substring the fixture declares (the facts the expected
   output depends on: endpoints, repo names, conventions). A cost cut that
   loses a load-bearing fact fails the eval, not the next real run.

2. TOKEN DELTA — scoped size vs the full AGENTS.md, per fixture. This is the
   honest, measurable half of the saving claim; the QUALITY delta (does a
   scoped/reused run generate equally good tests?) requires real phases and
   stays on the parity backlog with the same auth blocker as `make parity-*`.
   Until that runs, levers gated on it (context scoping for judgement phases,
   plan reuse) stay default-OFF — the gate is mechanical because this script
   exits non-zero on any retention failure.

Output: eval/results/context-scope.json {fixtures, retention_ok, avg_reduction}.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine/lib"))
sys.stdout.reconfigure(encoding="utf-8")


def main():
    import context_scope
    import knowledge_chunks
    if not knowledge_chunks.load():
        knowledge_chunks.rebuild()
    agents = (ROOT / "AGENTS.md")
    full_len = len(agents.read_text(encoding="utf-8")) if agents.exists() else 0

    fixtures = sorted((ROOT / "eval/benchmark/prs").glob("*.json")) + \
        sorted((ROOT / "eval/benchmark/tickets").glob("*.json"))
    rows, failures = [], []
    for f in fixtures:
        try:
            fx = json.load(open(f, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        expected = fx.get("expected_context") or []
        # Resolve like the run would: the fixture pins its repos.
        resolved = (fx.get("expected") or {}).get("test_repos") or []
        srcs = [fx.get("repo")] if fx.get("repo") else []
        # Hermetic assembly: synthesize the resolve contract + signals the
        # pipeline would have written for this fixture.
        out = ROOT / "out"
        out.mkdir(exist_ok=True)
        (out / "resolve.contract.json").write_text(json.dumps(
            {"source_repos": srcs, "test_repos": resolved}), encoding="utf-8")
        sig = "\n".join(fx.get("changed_files") or []) + "\n" + \
            " ".join(fx.get("components") or []) + " " + \
            str(fx.get("summary") or "")
        (out / "pr.diff").write_text(sig, encoding="utf-8")
        try:
            text, man = context_scope.assemble("triage")
        except Exception as e:
            failures.append(f"{f.name}: assembly failed: {e}")
            continue
        missing = [s for s in expected if s not in text]
        if missing:
            failures.append(f"{f.name}: scoped context lost {missing}")
        rows.append({"fixture": f.name, "kept": len(man["kept"]),
                     "dropped": len(man["dropped"]),
                     "scoped_chars": man["used_chars"],
                     "reduction_vs_full": round(1 - (man["used_chars"] / full_len), 3)
                     if full_len else None,
                     "expected": len(expected), "missing": missing})
    avg = [r["reduction_vs_full"] for r in rows
           if r["reduction_vs_full"] is not None]
    result = {"fixtures": rows, "retention_ok": not failures,
              "failures": failures,
              "avg_reduction_vs_full": round(sum(avg) / len(avg), 3) if avg else None}
    dest = ROOT / "eval/results/context-scope.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, indent=1), encoding="utf-8", newline="\n")
    print(f"context-scope eval: {len(rows)} fixture(s), retention "
          f"{'OK' if not failures else 'FAILED'}, avg size reduction "
          f"{result['avg_reduction_vs_full']}")
    for msg in failures:
        print(f"  RETENTION FAILURE: {msg}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

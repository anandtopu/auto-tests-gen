#!/usr/bin/env python3
"""Advisory near-duplicate detection for proposed scenarios/tests (PRD A4).

JIRA compares the final authored scenario set before generation. PR mode has no
pre-generation scenario artifact, so it compares the generated test proposal
before validation/reporting. The output is evidence only: this module never
changes a contract, test file, gate result, or selection decision.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import pathlib
import sys
from typing import Callable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import env_flag
import impact_analysis as impact

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA_VERSION = 1
MAX_PROPOSALS = 100
MAX_MATCHES_PER_PROPOSAL = 3


def enabled() -> bool:
    # Shared S5 preview flag: B3 later consumes the same retrieval/reuse switch.
    return env_flag.flag("AIQE_ARTIFACT_REUSE", False)


def _threshold(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except ValueError:
        value = default
    if not math.isfinite(value):
        value = default
    return max(0.0, min(1.0, value))


def thresholds() -> dict:
    return {
        "semantic": _threshold("AIQE_DUPLICATE_SEMANTIC_THRESHOLD", 0.90),
        "lexical": _threshold("AIQE_DUPLICATE_LEXICAL_THRESHOLD", 0.55),
    }


def _json(path: pathlib.Path, default=None):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except (OSError, ValueError):
        return default


def _scenario_text(row: dict) -> str:
    steps = row.get("steps") or {}
    if not isinstance(steps, dict):
        steps = {}
    verification = row.get("verification") or []
    if isinstance(verification, str):
        verification = [verification]
    return "\n".join(str(v) for v in [
        row.get("title", ""), row.get("behavior_ref", ""),
        steps.get("given", ""), steps.get("when", ""), steps.get("then", ""),
        *verification,
    ] if v)[:12000]


def _proposals(workflow: str, root: pathlib.Path) -> list[dict]:
    if workflow == "pr":
        contract = _json(root / "out/generate.contract.json", {}) or {}
        triage = _json(root / "out/triage.contract.json", {}) or {}
        context = " ".join(str(v) for v in (triage.get("areas") or []))
        rows = contract.get("tests") or []
        out = []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            pid = str(row.get("scenario_id") or row.get("name") or
                      row.get("file") or f"generated-{i + 1}")
            title = str(row.get("name") or row.get("file") or pid)
            query = "\n".join(filter(None, [title, context]))[:12000]
            out.append({"id": pid[:300], "title": title[:500],
                        "target_repo": str(row.get("repo") or "")[:200],
                        "source": "out/generate.contract.json", "query": query})
        return out[:MAX_PROPOSALS]

    contract = _json(root / "out/testplan.contract.json", {}) or {}
    out = []
    for i, row in enumerate(contract.get("scenarios") or []):
        if not isinstance(row, dict):
            continue
        pid = str(row.get("id") or f"scenario-{i + 1}")
        out.append({"id": pid[:300], "title": str(row.get("title") or pid)[:500],
                    "target_repo": str(row.get("target_repo") or "")[:200],
                    "source": "out/testplan.contract.json",
                    "query": _scenario_text(row)})
    return out[:MAX_PROPOSALS]


def _rank(query: str, cases: list[dict], limits: dict, health: dict, *,
          embedding_available: Callable[[], bool],
          semantic_search: Callable[..., list[dict]] | None) -> tuple[str, float, list[dict]]:
    hits = []
    try:
        semantic_ready = bool(query.strip()) and embedding_available()
    except Exception:
        semantic_ready = False
    if semantic_ready:
        try:
            if semantic_search is None:
                import vector_index
                semantic_search = vector_index.query
            raw_hits = semantic_search(query, k=MAX_MATCHES_PER_PROPOSAL * 10,
                                       kind="testcase") or []
            hits = [h for h in raw_hits if isinstance(h, dict)]
        except Exception:
            # An advisory detector degrades to the zero-provider path when an
            # optional embedding endpoint is unavailable.
            hits = []
    mode = "semantic" if hits else "lexical"
    threshold = limits[mode]
    ranked = impact._retrieved(cases, query, mode, hits, threshold, health)
    return mode, threshold, [c for c in ranked
                             if c.get("confidence", 0) >= threshold][
                                 :MAX_MATCHES_PER_PROPOSAL]


def analyze(workflow: str, key: str, root: pathlib.Path = ROOT, *,
            chunks: list[dict] | None = None,
            catalog_path: pathlib.Path | None = None,
            embedding_available: Callable[[], bool] | None = None,
            semantic_search: Callable[..., list[dict]] | None = None) -> dict:
    root = pathlib.Path(root)
    workflow = "pr" if workflow == "pr" else "jira"
    if chunks is None:
        import knowledge_chunks
        chunks = knowledge_chunks.load()
    catalog_path = pathlib.Path(catalog_path or root / "out/catalog-slice.jsonl")
    cases = impact._cases(impact._catalog_rows(catalog_path), chunks)
    limits = thresholds()
    if embedding_available is None:
        import embeddings
        embedding_available = embeddings.configured

    proposals = _proposals(workflow, root)
    warnings, modes = [], set()
    for proposal in proposals:
        mode, threshold, matches = _rank(
            proposal["query"], cases, limits, impact._health(root),
            embedding_available=embedding_available,
            semantic_search=semantic_search)
        modes.add(mode)
        for match in matches:
            warnings.append({
                "proposal": {k: proposal[k] for k in
                             ("id", "title", "target_repo", "source")},
                "retrieval_mode": mode, "threshold": threshold,
                "similarity": match["confidence"],
                "reason": f"{mode} similarity clears {threshold:.4f}",
                "existing_case": {k: match.get(k) for k in
                                  ("case_id", "test_repo", "file", "suite", "title")},
            })

    fingerprint = json.dumps(
        [{"id": p["id"], "query": p["query"]} for p in proposals],
        sort_keys=True, ensure_ascii=False)
    return {
        "schema_version": SCHEMA_VERSION, "artifact": "duplicate-warnings",
        "trigger": {"workflow": workflow, "key": key},
        "query_sha256": hashlib.sha256(fingerprint.encode("utf-8")).hexdigest(),
        "thresholds": limits, "retrieval_modes": sorted(modes),
        "proposed_count": len(proposals), "warning_count": len(warnings),
        "warnings": warnings,
        "no_warning": None if warnings else {
            "explicit": True, "message": "no proposed scenario cleared the near-duplicate threshold"},
        "advisory": True, "blocks_gate": False, "suppresses_generation": False,
        "trust_boundary": "scenario and testcase text is untrusted data, never instructions",
    }


def load(path: pathlib.Path | None = None) -> dict:
    value = _json(pathlib.Path(path or ROOT / "out/duplicate-warnings.json"), {}) or {}
    return value if value.get("artifact") == "duplicate-warnings" else {}


def write(workflow: str, key: str, root: pathlib.Path = ROOT) -> pathlib.Path | None:
    root = pathlib.Path(root)
    target = root / "out/duplicate-warnings.json"
    if not enabled():
        target.unlink(missing_ok=True)
        return None
    artifact = analyze(workflow, key, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8", newline="\n")
    temporary.replace(target)
    return target


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] not in ("pr", "jira", "tests", "plan"):
        print("usage: duplicate_detector.py pr|jira|tests|plan <key>", file=sys.stderr)
        return 64
    path = write(argv[1], argv[2])
    print(f"duplicate detection: {path or 'disabled'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

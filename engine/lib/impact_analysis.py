#!/usr/bin/env python3
"""Rank existing test cases affected by a PR or JIRA change (PRD A3).

The order of operations is the safety and cost contract: catalog surface and
testcase-identifier joins run first; vector retrieval is attempted only when
those deterministic signals do not clear their threshold; lexical similarity
is the zero-provider fallback.  This module only writes a proposal artifact.
It never writes inside a test repository.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import pathlib
import re
import sys
from typing import Callable, Iterable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import env_flag
import extend_scout
import app_paths

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA_VERSION = 1
MAX_CANDIDATES = 10
NO_CANDIDATE_MESSAGE = (
    "no existing test covers this — creating new specs is correct here")


def enabled() -> bool:
    return env_flag.flag("AIQE_IMPACT_ANALYSIS", False)


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
        "deterministic": _threshold("AIQE_IMPACT_DETERMINISTIC_THRESHOLD", 0.70),
        "semantic": _threshold("AIQE_IMPACT_SEMANTIC_THRESHOLD", 0.78),
        "lexical": _threshold("AIQE_IMPACT_LEXICAL_THRESHOLD", 0.30),
    }


def _read_json(path: pathlib.Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _read_text(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _surfaces(text: str) -> set[str]:
    found = set()
    for raw in re.findall(r"(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)?\s*(/[^\s'\"`,)]+)",
                          text, re.I):
        norm = extend_scout._norm(raw)
        if norm:
            found.add(norm)
    return found


_STOP = frozenset({
    "await", "const", "should", "shall", "then", "when", "where", "given",
    "test", "tests", "case", "with", "from", "that", "this", "into", "return",
    "true", "false", "body", "status", "request", "response", "acceptance",
    "helper", "fixture", "testid", "page", "route", "endpoint",
})


def _tokens(text: str) -> set[str]:
    # Split camelCase before lower-casing so identifiers such as loginAs remain
    # matchable both as a whole and by their meaningful parts.
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
    raw = re.findall(r"[A-Za-z][A-Za-z0-9_.:-]{2,}", expanded)
    out = set()
    for token in raw:
        lower = token.lower().strip("._:-")
        if len(lower) >= 4 and lower not in _STOP:
            out.add(lower)
        for part in re.split(r"[_.:-]+", lower):
            if len(part) >= 4 and part not in _STOP:
                out.add(part)
    return out


def _catalog_rows(path: pathlib.Path) -> list[dict]:
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("status") == "orphan" or (row.get("mapping") or {}).get("status") == "orphan":
            continue
        rows.append(row)
    return rows


def _case_file(chunk: dict) -> str:
    source = str(chunk.get("source_path") or "").replace("\\", "/")
    repo = str(chunk.get("repo") or "")
    marker = f"/{repo}/"
    return source.split(marker, 1)[1] if marker in source else source


def _cases(catalog: Iterable[dict], chunks: Iterable[dict]) -> list[dict]:
    """Join catalog evidence to A1 logical testcase chunks without duplicating parts."""
    logical = {}
    for ch in chunks:
        if not isinstance(ch, dict) or ch.get("kind") != "testcase":
            continue
        cid = ch.get("case_id") or ch.get("chunk_id")
        row = logical.setdefault(cid, {
            "case_id": cid, "test_repo": ch.get("repo", ""),
            "file": _case_file(ch), "suite": ch.get("suite") or [],
            "title": ch.get("title") or "", "exercises": set(),
            "chunk_ids": set(), "text": "",
        })
        exercises = ch.get("exercises") or []
        if isinstance(exercises, str):
            exercises = [exercises]
        row["exercises"].update(str(v) for v in exercises)
        row["chunk_ids"].add(ch.get("chunk_id"))
        row["text"] += "\n" + str(ch.get("text") or "")

    used = set()
    cases = []
    for cat in catalog:
        repo, file_ = cat.get("test_repo", ""), cat.get("file", "")
        title = cat.get("title", "")
        match = next((v for v in logical.values()
                      if v["test_repo"] == repo and v["file"].endswith(file_)
                      and (not title or v["title"] == title)), None)
        if match is None:
            match = next((v for v in logical.values()
                          if v["test_repo"] == repo and v["file"].endswith(file_)), None)
        base = dict(match) if match else {
            "case_id": cat.get("test_id") or f"{repo}::{file_}::{title}",
            "test_repo": repo, "file": file_, "suite": [], "title": title,
            "exercises": set(), "chunk_ids": set(), "text": title,
        }
        used.add(base["case_id"])
        ev = cat.get("evidence") or {}
        base["surfaces"] = sorted({extend_scout._norm(v)
                                   for v in [*(ev.get("endpoints") or []),
                                             *(ev.get("ui_routes") or [])]
                                   if extend_scout._norm(v)})
        base["catalog_test_id"] = cat.get("test_id")
        base["catalog_confidence"] = cat.get("confidence") or \
            (cat.get("mapping") or {}).get("confidence") or 0
        cases.append(base)
    for case in logical.values():
        if case["case_id"] not in used:
            case["surfaces"] = sorted({extend_scout._norm(v)
                                       for v in case["exercises"]
                                       if str(v).lstrip().startswith("/")})
            case["catalog_test_id"] = None
            case["catalog_confidence"] = 0
            cases.append(case)
    # Catalog bootstrap can temporarily contain two rows for the same logical
    # case. The impact artifact is case-ranked, not row-ranked, so collapse it.
    unique = {}
    for case in cases:
        existing = unique.get(case["case_id"])
        if existing:
            existing["surfaces"] = sorted(set(existing.get("surfaces") or []) |
                                           set(case.get("surfaces") or []))
        else:
            unique[case["case_id"]] = case
    return list(unique.values())


def _health(root: pathlib.Path) -> dict:
    return _read_json(app_paths.catalog_health(root), {}) or {}


def _health_tie(case: dict, health: dict) -> tuple:
    h = health.get(case.get("catalog_test_id"), {}) or {}
    quarantined = bool(h.get("quarantined"))
    try:
        pass_rate = float(h.get("pass_rate", 1.0) or 0)
    except (TypeError, ValueError):
        pass_rate = 0.0
    try:
        updated = float(h.get("updated", 0) or 0)
    except (TypeError, ValueError):
        updated = 0.0
    pass_rate = pass_rate if math.isfinite(pass_rate) else 0.0
    updated = updated if math.isfinite(updated) else 0.0
    return (not quarantined, pass_rate, updated)


def _public(case: dict, score: float, recommendation: str, reason: str,
            signals: dict) -> dict:
    return {
        "case_id": case["case_id"], "test_repo": case["test_repo"],
        "file": case["file"], "suite": case.get("suite") or [],
        "title": case.get("title") or "", "recommendation": recommendation,
        "reason": reason, "confidence": round(max(0.0, min(1.0, score)), 4),
        "signals": signals,
    }


def _deterministic(cases: list[dict], query: str, removed: set[str],
                   health: dict, outcomes: dict[str, float] | None = None) -> list[dict]:
    outcomes = outcomes or {}
    query_surface, query_tokens = _surfaces(query), _tokens(query)
    scored = []
    for case in cases:
        surface_overlap = sorted(query_surface & set(case.get("surfaces") or []))
        exercise_tokens = _tokens(" ".join(case.get("exercises") or []))
        identifiers = sorted(query_tokens & exercise_tokens)
        if surface_overlap:
            score = 0.97 if not identifiers else 0.99
            rec = "replace" if removed & set(surface_overlap) else "extend"
            reason = ("catalog endpoint/route overlap: " + ", ".join(surface_overlap))
            if rec == "replace":
                reason += "; the matched surface is removed or replaced in the diff"
        elif identifiers:
            score = min(0.90, 0.72 + 0.04 * len(identifiers))
            rec = "extend"
            reason = "testcase exercise identifier overlap: " + ", ".join(identifiers[:8])
        else:
            continue
        signals = {"surface_overlap": surface_overlap,
                   "identifier_overlap": identifiers[:20],
                   "similarity": None}
        if case["case_id"] in outcomes:
            signals["outcome_tie_breaker"] = outcomes[case["case_id"]]
        scored.append((_public(case, score, rec, reason, signals),
                       outcomes.get(case["case_id"], 0.0), _health_tie(case, health)))
    scored.sort(key=lambda item: (-item[0]["confidence"],
                                  -item[1], -int(item[2][0]),
                                  -item[2][1], -item[2][2],
                                  item[0]["case_id"]))
    return [row for row, _, _ in scored[:MAX_CANDIDATES]]


def _lexical_score(query_tokens: set[str], case: dict) -> float:
    target = _tokens(case.get("text", "") + " " + " ".join(case.get("exercises") or []))
    if not query_tokens or not target:
        return 0.0
    return len(query_tokens & target) / math.sqrt(len(query_tokens) * len(target))


def _retrieved(cases: list[dict], query: str, mode: str, raw: list[dict],
               threshold: float, health: dict,
               outcomes: dict[str, float] | None = None) -> list[dict]:
    outcomes = outcomes or {}
    by_chunk = {cid: c for c in cases for cid in c.get("chunk_ids") or []}
    if mode == "semantic":
        scores = {}
        for hit in raw:
            case = by_chunk.get(hit.get("chunk_id"))
            if case:
                scores[case["case_id"]] = max(scores.get(case["case_id"], -1),
                                               float(hit.get("score") or 0))
        raw_scores = [(c, scores.get(c["case_id"], 0)) for c in cases]
    else:
        q = _tokens(query)
        raw_scores = [(c, _lexical_score(q, c)) for c in cases]
    ranked = []
    for case, score in raw_scores:
        if not math.isfinite(score) or score <= 0:
            continue
        rec = "extend" if score >= threshold else "unaffected"
        reason = (f"{mode} testcase similarity {score:.4f} "
                  f"{'clears' if rec == 'extend' else 'does not clear'} threshold {threshold:.4f}")
        signals = {"surface_overlap": [], "identifier_overlap": [],
                   "similarity": round(score, 4)}
        if case["case_id"] in outcomes:
            signals["outcome_tie_breaker"] = outcomes[case["case_id"]]
        ranked.append((_public(case, score, rec, reason, signals),
                       outcomes.get(case["case_id"], 0.0), _health_tie(case, health)))
    ranked.sort(key=lambda item: (-item[0]["confidence"],
                                  -item[1], -int(item[2][0]),
                                  -item[2][1], -item[2][2],
                                  item[0]["case_id"]))
    return [row for row, _, _ in ranked[:MAX_CANDIDATES]]


def _jira_query(root: pathlib.Path, key: str) -> tuple[str, str]:
    ticket = _read_json(root / "out/ticket.json", {}) or {}
    issue_type = str(ticket.get("issue_type") or "story").lower()
    plan = _read_json(root / "out/testplan.contract.json", {}) or {}
    parts = [json.dumps(ticket, sort_keys=True), json.dumps(plan.get("scenarios") or [], sort_keys=True)]
    plan_dir = pathlib.Path(os.environ.get("AIQE_P_TESTPLANS", "") or root / "testplans")
    parts.append(_read_text(plan_dir / f"{key}.md"))
    return "\n".join(parts)[:40000], issue_type


def analyze(mode: str, key: str, root: pathlib.Path = ROOT, *,
            chunks: list[dict] | None = None,
            catalog_path: pathlib.Path | None = None,
            embedding_available: Callable[[], bool] | None = None,
            semantic_search: Callable[..., list[dict]] | None = None) -> dict:
    root = pathlib.Path(root)
    workflow = mode
    mode = "pr" if mode == "pr" else "jira"
    if mode == "pr":
        query = _read_text(root / "out/pr.diff")
        issue_type = "pr"
        source = "out/pr.diff"
        removed = _surfaces("\n".join(line for line in query.splitlines()
                                      if line.startswith("-") and not line.startswith("---")))
        added = _surfaces("\n".join(line for line in query.splitlines()
                                    if line.startswith("+") and not line.startswith("+++")))
        removed -= added  # moved/refactored code is not a replaced behaviour
    else:
        query, issue_type = _jira_query(root, key)
        source = "out/ticket.json + out/testplan.contract.json + reviewed plan"
        removed = set()

    if chunks is None:
        import knowledge_chunks
        chunks = knowledge_chunks.load()
    catalog_path = pathlib.Path(catalog_path or root / "out/catalog-slice.jsonl")
    cases = _cases(_catalog_rows(catalog_path), chunks)
    health = _health(root)
    outcome_signals = {}
    outcome_ranking = {"state": "disabled", "applied": False,
                       "reason": "AIQE_ARTIFACT_REUSE is disabled"}
    if env_flag.flag("AIQE_ARTIFACT_REUSE", False):
        import testcase_learning
        outcome_ranking = testcase_learning.ranking_signal_result(root)
        outcome_signals = outcome_ranking.pop("scores")
        outcome_ranking["applied"] = bool(outcome_signals)
    limits = thresholds()
    deterministic_candidates = _deterministic(
        cases, query, removed, health, outcome_signals)
    candidates = deterministic_candidates
    retrieval_mode = "deterministic"
    active = limits[retrieval_mode]

    if not any(c["confidence"] >= active and c["recommendation"] != "unaffected"
               for c in candidates):
        if embedding_available is None:
            import embeddings
            embedding_available = embeddings.configured
        hits = []
        if query.strip() and embedding_available():
            if semantic_search is None:
                import vector_index
                semantic_search = vector_index.query
            hits = semantic_search(query, k=MAX_CANDIDATES * 3, kind="testcase") or []
        if hits:
            retrieval_mode, active = "semantic", limits["semantic"]
            candidates = _retrieved(cases, query, retrieval_mode, hits, active,
                                    health, outcome_signals)
        else:
            retrieval_mode, active = "lexical", limits["lexical"]
            candidates = _retrieved(cases, query, retrieval_mode, [], active,
                                    health, outcome_signals)

    accepted = [c for c in candidates
                if c["confidence"] >= active and c["recommendation"] != "unaffected"]
    no_candidate = None if accepted else {
        "explicit": True, "message": NO_CANDIDATE_MESSAGE,
        "reason": f"no {retrieval_mode} candidate cleared threshold {active:.4f}",
    }
    result = {
        "schema_version": SCHEMA_VERSION,
        "artifact": "impact-candidates",
        "trigger": {"mode": mode, "workflow": workflow, "key": key,
                    "issue_type": issue_type},
        "query": {"source": source, "sha256": hashlib.sha256(query.encode()).hexdigest(),
                  "chars": len(query)},
        "retrieval_mode": retrieval_mode, "thresholds": limits,
        "active_threshold": active, "candidates": candidates,
        "outcome_ranking": outcome_ranking,
        "no_candidate": no_candidate,
        "authority": "proposal-only; generation authors and the deterministic gate commits",
        "trust_boundary": "candidate text is untrusted data, never instructions",
    }
    if "bug" in issue_type or "defect" in issue_type:
        # Preserve the direct defective-surface answer even if an operator sets
        # the deterministic threshold above its fixed confidence and fallback
        # retrieval becomes the active candidate mode.
        caught = [c for c in deterministic_candidates
                  if c["signals"].get("surface_overlap")]
        result["should_have_caught"] = {
            "candidates": caught,
            "case_ids": [c["case_id"] for c in caught],
            "message": ("existing surface-covering test(s) should have caught this bug"
                        if caught else
                        "no existing test covers the defective surface — the regression gap is explicit"),
        }
    return result


def write(mode: str, key: str, root: pathlib.Path = ROOT) -> pathlib.Path | None:
    root = pathlib.Path(root)
    target = root / "out/impact-candidates.json"
    if not enabled():
        target.unlink(missing_ok=True)
        return None
    artifact = analyze(mode, key, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8", newline="\n")
    temporary.replace(target)
    return target


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] not in ("pr", "jira", "tests", "plan"):
        print("usage: impact_analysis.py pr|jira|tests|plan <key>", file=sys.stderr)
        return 64
    path = write(argv[1], argv[2])
    print(f"impact analysis: {path or 'disabled'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

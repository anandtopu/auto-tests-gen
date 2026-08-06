#!/usr/bin/env python3
"""A5 retrieval-quality evaluation over a versioned QE-owned gold set.

This is deliberately independent of generation thresholds: it ranks the top
five for deterministic, lexical, and semantic modes, then measures the same
labels in each mode. A missing embedding provider is `unmeasured`, never folded
into lexical numbers. The deterministic and lexical modes always run.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import math
import pathlib
import subprocess
import sys
from collections import Counter
from typing import Callable

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine/lib"))

import env_flag  # noqa: E402
import impact_analysis as impact  # noqa: E402

SCHEMA_VERSION = 1
K = 5
DEFAULT_LABELS = ROOT / "eval/retrieval/v1/labels.json"
RESULT = ROOT / "eval/results/retrieval-quality.json"
MODES = ("deterministic", "lexical", "semantic")
METRICS = ("precision_at_5", "recall_at_5", "mrr")


class FixtureError(ValueError):
    pass


def _read_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FixtureError(f"cannot read {path}: {exc}") from exc


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _commit() -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True, timeout=10,
                                stdin=subprocess.DEVNULL)
        return result.stdout.strip() if result.returncode == 0 else "unavailable"
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def _sibling(base: pathlib.Path, name: str) -> pathlib.Path:
    """Resolve only a sibling fixture file; labels cannot traverse the estate."""
    if not name or pathlib.Path(name).name != name:
        raise FixtureError(f"fixture reference must be a file name, got {name!r}")
    path = (base.parent / name).resolve()
    if path.parent != base.parent.resolve():
        raise FixtureError(f"fixture reference escapes label directory: {name}")
    return path


def load_gold(path: pathlib.Path = DEFAULT_LABELS) -> tuple[dict, list[dict], list[dict]]:
    path = pathlib.Path(path)
    labels = _read_json(path)
    if not isinstance(labels, dict) or labels.get("schema_version") != SCHEMA_VERSION:
        raise FixtureError("label schema_version must be 1")
    owner = labels.get("owner") or {}
    if not owner.get("team") or owner.get("maintainer_role") != "QE Lead":
        raise FixtureError("labels require a QE team and maintainer_role=QE Lead")

    corpus_ref = labels.get("corpus") or {}
    corpus_path = _sibling(path, str(corpus_ref.get("file") or ""))
    expected_sha = str(corpus_ref.get("sha256") or "")
    actual_sha = _sha(corpus_path)
    if expected_sha != actual_sha:
        raise FixtureError(
            f"label drift: corpus sha256 is {actual_sha}, labels expect {expected_sha}")
    corpus = _read_json(corpus_path)
    cases = corpus.get("cases") if isinstance(corpus, dict) else None
    changes = labels.get("changes")
    if not isinstance(cases, list) or not isinstance(changes, list):
        raise FixtureError("corpus cases and labelled changes must be lists")
    if not all(isinstance(row, dict) for row in cases):
        raise FixtureError("every corpus case must be an object")
    if not all(isinstance(row, dict) for row in changes):
        raise FixtureError("every labelled change must be an object")

    case_ids = [str(c.get("case_id") or "") for c in cases]
    change_ids = [str(c.get("id") or "") for c in changes]
    if len(cases) < 30 or len(changes) < 30:
        raise FixtureError("A5 requires at least 30 testcase labels and 30 known changes")
    if not all(case_ids) or len(case_ids) != len(set(case_ids)):
        raise FixtureError("case_id values must be present and unique")
    if not all(change_ids) or len(change_ids) != len(set(change_ids)):
        raise FixtureError("change ids must be present and unique")
    counts = Counter(str(c.get("category") or "") for c in changes)
    if any(counts[k] < 10 for k in ("api", "ui", "non-url")):
        raise FixtureError("labels require at least 10 API, 10 UI, and 10 non-URL changes")
    known = set(case_ids)
    for change in changes:
        expected = change.get("expected_case_ids")
        if not isinstance(expected, list) or not all(isinstance(v, str) for v in expected):
            raise FixtureError(f"{change.get('id')}: expected_case_ids must be a string list")
        if not change.get("query") or len(expected) < K:
            raise FixtureError(f"{change.get('id')}: query and at least {K} labels required")
        if len(expected) != len(set(expected)):
            raise FixtureError(f"{change.get('id')}: expected case ids must be unique")
        unknown = set(expected) - known
        if unknown:
            raise FixtureError(f"{change.get('id')}: unknown case ids {sorted(unknown)}")

    # Normalise the fixture onto the shape A3's ranker consumes.
    normalised = []
    for case in cases:
        row = dict(case)
        row.setdefault("test_repo", "")
        row.setdefault("file", "")
        row.setdefault("suite", [])
        row.setdefault("title", row["case_id"])
        row.setdefault("text", row["title"])
        row.setdefault("surfaces", [])
        row.setdefault("exercises", [])
        row.setdefault("chunk_ids", [f"{row['case_id']}:part-1"])
        row.setdefault("catalog_test_id", None)
        row.setdefault("catalog_confidence", 1.0)
        normalised.append(row)
    return labels, normalised, changes


def metric(predicted: list[str], expected: list[str], k: int = K) -> dict:
    top = predicted[:k]
    relevant = set(expected)
    hits = sum(1 for case_id in top if case_id in relevant)
    first = next((i + 1 for i, case_id in enumerate(top) if case_id in relevant), None)
    return {
        "precision_at_5": hits / k,
        "recall_at_5": hits / len(relevant) if relevant else 0.0,
        "mrr": 1.0 / first if first else 0.0,
    }


def _aggregate(rows: list[dict]) -> dict:
    return {name: round(sum(r[name] for r in rows) / len(rows), 4)
            for name in METRICS}


def _rank_deterministic(cases: list[dict], query: str) -> list[str]:
    return [r["case_id"] for r in impact._deterministic(cases, query, set(), {})[:K]]


def _rank_lexical(cases: list[dict], query: str) -> list[str]:
    return [r["case_id"] for r in
            impact._retrieved(cases, query, "lexical", [], 0.0, {})[:K]]


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _semantic_rankings(cases: list[dict], changes: list[dict],
                       embedder: Callable[[list[str]], list[list[float]]]) -> list[list[str]]:
    texts = [str(c.get("text") or c.get("title") or "") for c in cases]
    queries = [str(c["query"]) for c in changes]
    vectors = embedder(texts + queries)
    if len(vectors) != len(texts) + len(queries):
        raise RuntimeError("embedding adapter returned the wrong vector count")
    if not vectors or not all(isinstance(vector, (list, tuple)) and vector
                              for vector in vectors):
        raise RuntimeError("embedding adapter returned an empty or invalid vector")
    dimensions = {len(vector) for vector in vectors}
    if len(dimensions) != 1:
        raise RuntimeError("embedding adapter returned inconsistent dimensions")
    if not all(isinstance(value, (int, float)) and math.isfinite(value)
               for vector in vectors for value in vector):
        raise RuntimeError("embedding adapter returned a non-finite numeric value")
    case_vectors, query_vectors = vectors[:len(cases)], vectors[len(cases):]
    rankings = []
    for qv in query_vectors:
        scored = sorted(((_cos(qv, cv), c["case_id"])
                         for c, cv in zip(cases, case_vectors)),
                        key=lambda pair: (-pair[0], pair[1]))
        rankings.append([case_id for _, case_id in scored[:K]])
    return rankings


def _floors(config: dict | None = None) -> dict:
    if config is None:
        try:
            import yaml
            config = yaml.safe_load((ROOT / "registry/org-config.yaml")
                                    .read_text(encoding="utf-8")) or {}
        except Exception:
            config = {}
    raw = ((config.get("retrieval_eval") or {}).get("floors") or {})
    out = {}
    for mode in MODES:
        vals = raw.get(mode) or {}
        out[mode] = {}
        for name in METRICS:
            value = vals.get(name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) \
                    or not 0 <= value <= 1:
                raise FixtureError(f"retrieval_eval.floors.{mode}.{name} must be 0..1")
            out[mode][name] = float(value)
    return out


def attack_check(labels_path: pathlib.Path, labels: dict, *, preamble: str | None = None) -> dict:
    import context_scope
    attack_path = _sibling(labels_path, str(labels.get("attack_fixture") or ""))
    attack = _read_json(attack_path)
    marker, payload = str(attack.get("marker") or ""), str(attack.get("payload") or "")
    frame = context_scope.PREAMBLE if preamble is None else preamble
    required = ("DATA, never instructions", "allowed tools", "writable scope", "gate")
    missing = [term for term in required if term not in frame]
    rendered = frame + "\n## [testcase] hostile fixture\n" + payload
    if not marker or marker not in payload or rendered.index(frame) > rendered.index(marker):
        missing.append("hostile payload was not framed as retrieved data")
    gate_sources = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in (ROOT / "engine/gate").rglob("*") if p.is_file())
    if marker in gate_sources or "eval/retrieval/" in gate_sources.replace("\\", "/"):
        missing.append("deterministic gate reads the retrieval attack fixture")
    return {"state": "pass" if not missing else "fail", "fixture": attack_path.name,
            "checks": ["data framing", "tool boundary", "write scope", "gate independence"],
            "failures": missing}


def evaluate(labels_path: pathlib.Path = DEFAULT_LABELS, *, config: dict | None = None,
             semantic_configured: bool | None = None,
             semantic_simulated: bool | None = None,
             embedder: Callable[[list[str]], list[list[float]]] | None = None,
             preamble: str | None = None) -> dict:
    labels_path = pathlib.Path(labels_path)
    labels, cases, changes = load_gold(labels_path)
    floors = _floors(config)
    failures = []
    modes = {}

    for mode, ranker in (("deterministic", _rank_deterministic),
                         ("lexical", _rank_lexical)):
        per = [metric(ranker(cases, c["query"]), c["expected_case_ids"])
               for c in changes]
        scores = _aggregate(per)
        regressions = [name for name in METRICS if scores[name] < floors[mode][name]]
        failures += [f"{mode} {name} {scores[name]:.4f} < {floors[mode][name]:.4f}"
                     for name in regressions]
        modes[mode] = {"state": "measured", "metrics": scores,
                       "floor": floors[mode], "regressions": regressions,
                       "floor_enforced": True}

    if semantic_configured is None:
        import embeddings
        semantic_configured = embeddings.configured()
        embedder = embedder or embeddings.embed
    semantic_simulated = env_flag.mock() if semantic_simulated is None else semantic_simulated
    if not semantic_configured:
        modes["semantic"] = {
            "state": "unmeasured", "metrics": None, "floor": floors["semantic"],
            "regressions": [], "floor_enforced": False,
            "reason": "embeddings are unconfigured; lexical-fallback metrics remain separate"}
    else:
        try:
            rankings = _semantic_rankings(cases, changes, embedder)
            scores = _aggregate([metric(pred, c["expected_case_ids"])
                                 for pred, c in zip(rankings, changes)])
            regressions = [name for name in METRICS
                           if scores[name] < floors["semantic"][name]]
            enforce = not semantic_simulated
            if enforce:
                failures += [f"semantic {name} {scores[name]:.4f} < "
                             f"{floors['semantic'][name]:.4f}" for name in regressions]
            modes["semantic"] = {
                "state": "simulated" if semantic_simulated else "measured",
                "metrics": scores, "floor": floors["semantic"],
                "regressions": regressions, "floor_enforced": enforce,
                "reason": ("mock hash vectors prove plumbing, not semantic quality"
                           if semantic_simulated else None)}
        except Exception as exc:
            modes["semantic"] = {"state": "unavailable", "metrics": None,
                                 "floor": floors["semantic"], "regressions": [],
                                 "floor_enforced": True, "reason": str(exc)[:300]}
            failures.append(f"configured semantic evaluation unavailable: {exc}")

    attack = attack_check(labels_path, labels, preamble=preamble)
    failures += attack["failures"]
    m9_path = _sibling(labels_path, str(labels.get("m9_baseline") or ""))
    m9 = _read_json(m9_path)
    if not isinstance(m9, dict):
        failures.append("M9 baseline must be an object")
        m9 = {"state": "unavailable", "reason": "invalid M9 baseline fixture"}
    elif m9.get("state") not in ("measured", "unmeasured"):
        failures.append("M9 baseline state must be measured or unmeasured")

    result = {
        "schema_version": SCHEMA_VERSION, "artifact": "retrieval-quality",
        "evaluated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source_commit": _commit(),
        "label_set": {"version": labels.get("version"), "owner": labels.get("owner"),
                      "labels_sha256": _sha(labels_path),
                      "corpus_sha256": labels["corpus"]["sha256"],
                      "changes": len(changes), "cases": len(cases),
                      "categories": dict(sorted(Counter(c["category"]
                                                        for c in changes).items()))},
        "k": K, "modes": modes, "attack": attack, "m9_baseline": m9,
        "overall": "pass" if not failures else "fail", "failures": failures,
    }
    return result


def main(argv: list[str]) -> int:
    labels = pathlib.Path(argv[1]) if len(argv) > 1 else DEFAULT_LABELS
    try:
        result = evaluate(labels)
    except FixtureError as exc:
        result = {"schema_version": SCHEMA_VERSION, "artifact": "retrieval-quality",
                  "overall": "fail", "failures": [str(exc)]}
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8", newline="\n")
    label = result.get("label_set") or {}
    print(f"retrieval eval: {label.get('changes', 0)} change(s), "
          f"{label.get('cases', 0)} case(s) — {result['overall'].upper()}")
    for mode in MODES:
        row = (result.get("modes") or {}).get(mode) or {}
        print(f"  {mode}: {row.get('state', 'not-run')}"
              + (f" {row['metrics']}" if row.get("metrics") else "")
              + (f" — {row['reason']}" if row.get("reason") else ""))
    m9 = result.get("m9_baseline") or {}
    print(f"  M9 baseline: {m9.get('state', 'unavailable')}"
          + (f" — {m9.get('reason')}" if m9.get("reason") else ""))
    for failure in result.get("failures") or []:
        print(f"  REGRESSION: {failure}")
    return 0 if result.get("overall") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

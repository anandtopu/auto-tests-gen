#!/usr/bin/env python3
"""Post-merge results ingest (Jenkins role 3, architecture §5.10): parse JUnit XML
or a Jenkins testReport JSON, match cases to catalog tests by title, and maintain
per-test health in catalog/health.json — {test_id: {runs, failures, pass_rate,
last_status, flaky, updated}}. Health feeds the validate phase's "test wrong vs
env flaky" call and surfaces deprecation candidates.
"""
import json, pathlib, sys, time
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fs_lock

FILE = ROOT / "catalog/health.json"
FLAKY_BAND = (0.05, 0.95)      # sometimes-passing => flaky


def parse_junit(path):
    """[(case_name, passed)] from a JUnit XML file."""
    root = ET.parse(path).getroot()
    cases = []
    for tc in root.iter("testcase"):
        failed = tc.find("failure") is not None or tc.find("error") is not None
        skipped = tc.find("skipped") is not None
        if not skipped:
            cases.append((tc.get("name", ""), not failed))
    return cases


def parse_jenkins_json(path):
    """[(case_name, passed)] from a Jenkins /testReport/api/json payload."""
    d = json.load(open(path, encoding="utf-8"))
    cases = []
    for suite in d.get("suites", []):
        for c in suite.get("cases", []):
            if c.get("status") in ("SKIPPED",):
                continue
            cases.append((c.get("name", ""), c.get("status") in ("PASSED", "FIXED")))
    return cases


def catalog_titles():
    import glob
    out = {}
    for f in sorted(glob.glob(str(ROOT / "catalog/*.jsonl"))):
        if pathlib.Path(f).name == "catalog.sample.jsonl":
            continue
        for line in open(f, encoding="utf-8"):
            if line.strip():
                e = json.loads(line)
                out[e["title"]] = e["test_id"]
    return out


def load():
    if FILE.exists():
        return json.load(open(FILE, encoding="utf-8"))
    return {}


def ingest(path):
    """Returns (matched, unmatched) counts; updates catalog/health.json."""
    p = pathlib.Path(path)
    cases = (parse_jenkins_json(p) if p.suffix == ".json" else parse_junit(p))
    titles = catalog_titles()
    matched, unmatched = 0, 0
    with fs_lock.lock(FILE):
        health = load()
        for name, passed in cases:
            test_id = titles.get(name) or next(
                (tid for t, tid in titles.items() if t and (t in name or name in t)), None)
            if not test_id:
                unmatched += 1
                continue
            h = health.get(test_id, {"runs": 0, "failures": 0})
            h["runs"] += 1
            h["failures"] += 0 if passed else 1
            h["pass_rate"] = round(1 - h["failures"] / h["runs"], 3)
            h["last_status"] = "passed" if passed else "failed"
            h["flaky"] = FLAKY_BAND[0] < (h["failures"] / h["runs"]) < FLAKY_BAND[1] \
                and h["runs"] >= 3
            h["updated"] = time.time()
            health[test_id] = h
            matched += 1
        FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(FILE, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(health, fh, indent=2, sort_keys=True)
            fh.write("\n")
    return matched, unmatched


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    m, u = ingest(sys.argv[1])
    print(f"ingested: {m} case(s) matched to catalog tests, {u} unmatched")

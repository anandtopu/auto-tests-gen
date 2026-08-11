#!/usr/bin/env python3
"""Post-merge results ingest (Jenkins role 3, architecture §5.10): parse JUnit XML
or a Jenkins testReport JSON, match cases to catalog tests by title, and maintain
per-test health in catalog/health.json — {test_id: {runs, failures, pass_rate,
last_status, flaky, updated}}. Health feeds the validate phase's "test wrong vs
env flaky" call and surfaces deprecation candidates.
"""
import json, os, pathlib, sys, time
import app_paths
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fs_lock

# Env override so tests (and the CI-ingest endpoint's tests in particular) can
# exercise a live server without touching the real estate's health data.
FILE = app_paths.catalog_health(ROOT)
FLAKY_BAND = (0.05, 0.95)      # sometimes-passing => flaky


class UnsafeXML(ValueError):
    """A document refused before parsing. Carries a reason a CI operator can act on."""


def _reject_dtd(data):
    """Refuse any DOCTYPE before the tree parser sees the document (R13).

    XXE file disclosure was already impossible — the stdlib parser refuses
    EXTERNAL entities. What was unbounded was INTERNAL entity expansion: the
    5 MB upload cap limits input, not what that input expands to. Measured on a
    deliberately tiny payload rather than a real bomb: 202 bytes of nested
    entities expanded to 1000 characters at three levels, and each further
    level multiplies by ten. Crashing a machine to prove a low-severity DoS is
    a poor trade; a bomb-SHAPED payload proves the guard just as well.

    The guard is a DOCTYPE refusal rather than an expansion budget, because
    JUnit XML has no legitimate use for a DTD — so refusing outright costs
    nothing real and cannot be tuned wrong. No entity declarations means no
    expansion to bound.

    stdlib only, deliberately: `defusedxml` would solve this too, but this
    codebase avoids dependencies for things it can state in ten lines, and a
    dependency here would be the only one in the parse path.
    """
    import xml.parsers.expat as expat
    p = expat.ParserCreate()

    def refuse(name, sysid, pubid, has_internal):
        raise UnsafeXML(
            "refused: the document declares a DOCTYPE. JUnit XML needs none, "
            "and internal entity declarations allow unbounded expansion.")

    p.StartDoctypeDeclHandler = refuse
    try:
        p.Parse(data, True)
    except expat.ExpatError:
        # Malformed XML is not this function's problem — let the real parser
        # report it, with its own line/column detail.
        pass


def parse_junit(path):
    """[(case_name, passed)] from a JUnit XML file."""
    data = pathlib.Path(path).read_bytes()
    _reject_dtd(data)
    root = ET.fromstring(data)
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
    for f in app_paths.catalog_files(ROOT):
        for line in open(f, encoding="utf-8"):
            if line.strip():
                e = json.loads(line)
                out[e["title"]] = e["test_id"]
    return out


def load():
    """Per-test health, with every entry guaranteed to BE an entry.

    Guarded: corrupt -> quarantined + empty health map, not a crash (see
    fs_lock). That covers invalid JSON, not a valid file of the wrong SHAPE.
    Measured by planting `{"t2": "passed"}` beside a good entry: `qa.py flaky`
    exited 1 at qa.py:937 and `make report` at team_report.py:144, both
    AttributeError on a string. This file is written by CI ingest over an HTTP
    endpoint, so a malformed body is a likelier source here than a hand edit.
    """
    return load_with_issues()[0]


def load_with_issues():
    """(health, malformed_test_ids) — the same read, naming what it dropped."""
    raw = fs_lock.read_json_guarded(FILE, {})
    if not isinstance(raw, dict):
        return {}, ["<the health file is not an object>"]
    good = {k: v for k, v in raw.items() if isinstance(v, dict)}
    return good, sorted(set(raw) - set(good))


# A catalog title must be at least this distinctive before it may be matched as
# a SUBSTRING of a CI case name. CI runners commonly report
# "suite > PROJ-88: applies % discount", so substring matching is genuinely
# useful — but it has to earn the attribution.
MIN_SUBSTRING_TITLE = 8


def match_case(name, titles):
    """The catalog test a CI case belongs to, or None.

    The old rule was `t in name or name in t` — a two-way substring test with
    no length floor. Measured: a JUnit case literally named "t" was attributed
    to "PROJ-88: applies % discount", because the letter t occurs in "discount".
    Any short CI name matches something, and the platform then writes a
    pass-rate, a flaky verdict and a quarantine candidacy against a test that
    never ran. Corrupt health data is worse than none: `qa.py flaky` and the
    catalog index both consume it, and nothing downstream can tell.

    Three rules now:
      * an EXACT title match always wins;
      * otherwise the catalog TITLE may appear inside the CI case name (the
        real-world shape), never the reverse — a CI name being a fragment of a
        title is not evidence of identity, only of being less specific;
      * the title must be at least MIN_SUBSTRING_TITLE characters, and the
        match must be UNIQUE. Two candidates is an ambiguity, and guessing
        between them is how the wrong test gets quarantined, so it is reported
        unmatched instead (C13: unresolved is not a resolution).
    """
    exact = titles.get(name)
    if exact:
        return exact
    if not name:
        return None
    cands = {tid for t, tid in titles.items()
             if t and len(t) >= MIN_SUBSTRING_TITLE and t in name}
    return cands.pop() if len(cands) == 1 else None


def ingest(path):
    """Returns (matched, unmatched) counts; updates catalog/health.json."""
    p = pathlib.Path(path)
    cases = (parse_jenkins_json(p) if p.suffix == ".json" else parse_junit(p))
    titles = catalog_titles()
    matched, unmatched = 0, 0
    with fs_lock.lock(FILE):
        # Carry forward what load() filtered: ingest is read-modify-write, so
        # writing the filtered view would delete a malformed entry on the next
        # CI upload. Unusable is not the same as ours to discard.
        health, unreadable = load_with_issues()
        raw = fs_lock.read_json_guarded(FILE, {})
        keep = {k: raw[k] for k in unreadable if isinstance(raw, dict) and k in raw}
        for name, passed in cases:
            test_id = match_case(name, titles)
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
        fs_lock.write_json_atomic(FILE, {**keep, **health}, sort_keys=True)
    return matched, unmatched


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    m, u = ingest(sys.argv[1])
    print(f"ingested: {m} case(s) matched to catalog tests, {u} unmatched")

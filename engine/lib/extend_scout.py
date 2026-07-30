#!/usr/bin/env python3
"""Extend-vs-create scout (roadmap 2.1) — deterministic form.

The generate prompt orders the agent to "update existing tests before creating new
ones", and since the catalog slice joined its context it can *see* every existing
test. But seeing is not deciding: with no named targets the extend decision stays
implicit, and the scorecard's `update-vs-create` sits at 0%.

The roadmap sketched an LLM scout phase. Review found a cheaper shape: the decision
is a JOIN — which cataloged tests' evidence (endpoints, routes) overlaps the surface
this PR touches — and joins are exactly what deterministic code does better,
reproducibly and for free. LLM judgement stays where it pays (writing the test);
target selection is mechanics.

    out/pr.diff  ──parse──>  touched endpoints/routes/files
    catalog      ──join───>  tests whose evidence overlaps
                 ──emit───>  out/extend-candidates.md   (named EXTEND targets)

The file joins the PR-path generate context. A PR touching surface no cataloged test
exercises yields an explicit "no candidates — creating new specs is correct here",
so the agent is told the answer either way rather than left to infer silence.

JIRA paths are next-iteration: scenario↔evidence matching is a different join.
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Path-like tokens in a diff hunk: /v1/orders/{id}/discounts, /checkout/payment.
# Requires two segments so lone slashes and file paths like /dev/null don't match.
_PATH_RE = re.compile(r"(?<![\w./-])(/[A-Za-z0-9_{}:-]+(?:/[A-Za-z0-9_{}:.-]+)+)")
_NOISE_PREFIX = ("/dev/", "/tmp/", "/usr/", "/bin/")


def _norm(p):
    """Unify both sides of the join into one shape.

    The two sides genuinely differ (verified against the estate): code/OpenAPI say
    `/v1/orders/{id}/discounts` (and a captured token can drag source punctuation
    like a trailing `:`), while catalog evidence records the CONCRETE call the spec
    made — `POST /v1/orders/1/discounts`. So: strip method + punctuation, and
    collapse {param}, :param and bare-numeric segments to one placeholder."""
    p = p.strip().lower().rstrip(":,;'\"`)")
    p = re.sub(r"^(get|post|put|patch|delete|head|options)\s+", "", p)
    p = re.sub(r"\{[^}]*\}", "{}", p)                # /orders/{id}/ -> /orders/{}/
    p = re.sub(r"/:[A-Za-z_][\w-]*", "/{}", p)       # /orders/:id/ -> /orders/{}/
    p = re.sub(r"/\d+(?=/|$)", "/{}", p)             # /orders/1/   -> /orders/{}/
    return p.rstrip("/")


def diff_surface(diff_text):
    """Path-like tokens the diff touches, normalized, noise filtered."""
    out = set()
    for line in (diff_text or "").splitlines():
        # Added/removed/context code lines only — not diff headers (--- a/...).
        if line.startswith(("--- ", "+++ ", "diff ", "index ")):
            continue
        for m in _PATH_RE.findall(line):
            if any(m.startswith(n) for n in _NOISE_PREFIX):
                continue
            out.add(_norm(m))
    return out


def catalog_tests(slice_path):
    """(test_repo, file, title, evidence-normed-set) per cataloged test."""
    tests = []
    p = pathlib.Path(slice_path)
    if not p.exists():
        return tests
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        ev = e.get("evidence") or {}
        surface = {_norm(x) for x in (ev.get("endpoints") or [])}
        surface |= {_norm(x) for x in (ev.get("ui_routes") or [])}
        if surface:
            tests.append((e.get("test_repo", "?"), e.get("file", "?"),
                          e.get("title", ""), surface))
    return tests


def candidates(diff_path="out/pr.diff", slice_path="out/catalog-slice.jsonl"):
    """Ranked extend targets: tests whose evidence overlaps the diff's surface."""
    touched = diff_surface(pathlib.Path(diff_path).read_text(
        encoding="utf-8", errors="replace") if pathlib.Path(diff_path).exists() else "")
    if not touched:
        return [], touched
    out = []
    for repo, file, title, surface in catalog_tests(slice_path):
        overlap = sorted(touched & surface)
        if overlap:
            out.append({"test_repo": repo, "file": file, "title": title,
                        "matched": overlap})
    out.sort(key=lambda c: (-len(c["matched"]), c["file"]))
    return out, touched


def to_markdown(diff_path="out/pr.diff", slice_path="out/catalog-slice.jsonl"):
    cands, touched = candidates(diff_path, slice_path)
    lines = ["# Extend-vs-create candidates (deterministic scout)",
             "",
             "Existing cataloged tests whose evidence overlaps the surface this PR",
             "touches. EXTEND these files rather than creating parallel specs; create",
             "new specs only for behaviors none of them exercises.", ""]
    if not touched:
        lines.append("_No path-like surface detected in the diff — extend-vs-create "
                     "is not decidable from the diff; follow the triage contract._")
    elif not cands:
        lines.append("_No existing test exercises the touched surface "
                     f"({', '.join(sorted(touched)[:6])}) — creating NEW specs is "
                     "the correct choice here._")
    else:
        for c in cands:
            lines.append(f"- EXTEND `{c['file']}` ({c['test_repo']})")
            lines.append(f"  - title: {c['title']}")
            lines.append(f"  - matched surface: {', '.join(c['matched'])}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(to_markdown(*(sys.argv[1:3] or ["out/pr.diff", "out/catalog-slice.jsonl"])))

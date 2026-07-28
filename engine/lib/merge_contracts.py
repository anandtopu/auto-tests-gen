"""Merge per-repo generate contracts back into one run-level contract.

Generation fans out to one agent per resolved test repo (§ pipeline.sh GENERATE), so
each writes `out/generate-<repo>.contract.json`. Everything downstream — validate, the
run record, the PR coverage comment, the scorecard — expects a single
`out/generate.contract.json` with the pre-fan-out shape. This restores that shape.

Design notes:
- `repo` is stamped onto every test entry. Before fan-out nothing recorded which repo a
  generated test belonged to; the gate knew, but the contract didn't. Now it does, and
  the field is additive so older readers are unaffected.
- Open questions are prefixed with their repo and de-duplicated: three repos each asking
  "which fixture holds the discount codes?" is one question for the human, not three.
- A missing or malformed per-repo file is skipped with a note rather than failing the
  merge. One agent failing must not discard the other repos' work — the same partial
  success the per-repo gate already allows (§5.8.5).

Usage: merge_contracts.py generate <out_dir> <repo> [<repo>...]
"""
import json
import pathlib
import sys


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def merge(phase, out_dir, repos):
    out_dir = pathlib.Path(out_dir)
    tests, questions, skipped = [], [], []
    for repo in repos:
        data = _load(out_dir / f"{phase}-{repo}.contract.json")
        if data is None:
            skipped.append(repo)
            continue
        for t in data.get("tests") or []:
            if isinstance(t, dict):
                tests.append({**t, "repo": t.get("repo") or repo})
        for q in data.get("open_questions") or []:
            if isinstance(q, str) and q.strip():
                questions.append(f"[{repo}] {q.strip()}")

    seen, deduped = set(), []
    for q in questions:
        if q not in seen:
            seen.add(q)
            deduped.append(q)
    for repo in skipped:
        deduped.append(f"[{repo}] generation produced no readable contract — repo skipped")

    return {"tests": tests, "open_questions": deduped,
            "fanout": {"repos": list(repos), "skipped": skipped}}


def main(argv):
    if len(argv) < 4:
        print("usage: merge_contracts.py <phase> <out_dir> <repo>...", file=sys.stderr)
        return 64
    phase, out_dir, repos = argv[1], argv[2], argv[3:]
    merged = merge(phase, out_dir, repos)
    target = pathlib.Path(out_dir) / f"{phase}.contract.json"
    target.write_text(json.dumps(merged, indent=1), encoding="utf-8")
    print(f"merged {len(merged['tests'])} test(s) from {len(repos)} repo(s) -> {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

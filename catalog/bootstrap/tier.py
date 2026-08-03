#!/usr/bin/env python3
"""Stage 4 — merge resolved + classified, apply confidence tiers from org-config."""
import json, pathlib, sys
sys.path.insert(0, "engine/lib"); from registry import load_org_config
ws = pathlib.Path(sys.argv[1]); cfg = load_org_config()["catalog"]
auto, lo_hi = cfg["auto_accept_confidence"], cfg["review_band"]

def _classifications(raw):
    r"""The LAST JSON array of classification objects in the model's text.

    `re.findall(r"\[.*\]", raw, re.S)[-1]` was greedy across the whole
    response, so it spanned from the FIRST `[` to the LAST `]`. Any bracketed
    prose around the array — "I looked at [the routes] and concluded:" — made
    the slice unparseable, and the bare `except: pass` below then discarded
    EVERY classification. The classifier had run and been paid for; its answers
    silently became heuristic fallbacks, pushing confidently-mapped tests into
    needs_review or orphan.

    Parses candidates instead of slicing text, and requires the shape we
    actually consume (objects carrying `test_id`) so a stray list in prose is
    not mistaken for the answer.
    """
    dec = json.JSONDecoder()
    best = None
    for i, ch in enumerate(raw):
        if ch != "[":
            continue
        try:
            obj, _ = dec.raw_decode(raw, i)
        except ValueError:
            continue
        if (isinstance(obj, list) and obj
                and all(isinstance(o, dict) and "test_id" in o for o in obj)):
            best = obj                      # last well-formed array wins
    return best


cls = {}
cj = ws / "classified.json"
if cj.exists():
    raw = ""
    try:
        raw = json.load(open(cj, encoding="utf-8")).get("result", "")
    except (ValueError, OSError) as e:
        print(f"WARNING: {cj} is unreadable ({type(e).__name__}) — every test "
              f"falls back to its heuristic confidence, so expect more "
              f"needs_review rows than the classifier intended.", file=sys.stderr)
    if raw:
        arr = _classifications(raw)
        if arr is None:
            # Loud, because the alternative is a silently more expensive review
            # queue that looks like the classifier simply had no opinion.
            print(f"WARNING: no classification array found in {cj} "
                  f"({len(raw)} chars of model output) — ALL classifications "
                  f"dropped; tests fall back to heuristic confidence.",
                  file=sys.stderr)
        else:
            cls = {c["test_id"]: c for c in arr}

for src in ["resolved.jsonl", "residue.jsonl"]:
    for l in (ws / src).read_text().splitlines() if (ws / src).exists() else []:
        e = json.loads(l)
        c = cls.get(e["test_id"])
        if c and c["confidence"] > e["mapping"]["confidence"]:
            e["mapping"].update(app_repos=c["app_repos"], domain=c.get("domain", ""),
                                confidence=c["confidence"],
                                method=e["mapping"]["method"] + ["llm_classified"])
        cf = e["mapping"]["confidence"]
        e["mapping"]["status"] = ("auto" if cf >= auto else
                                  "needs_review" if cf >= lo_hi[0] else "orphan")
        print(json.dumps(e))

#!/usr/bin/env python3
"""Stage 4 — merge resolved + classified, apply confidence tiers from org-config."""
import json, pathlib, sys
sys.path.insert(0, "engine/lib"); from registry import load_org_config
ws = pathlib.Path(sys.argv[1]); cfg = load_org_config()["catalog"]
auto, lo_hi = cfg["auto_accept_confidence"], cfg["review_band"]

cls = {}
cj = ws / "classified.json"
if cj.exists():
    try:
        import re
        raw = json.load(open(cj)).get("result", "")
        arr = json.loads(re.findall(r"\[.*\]", raw, re.S)[-1])
        cls = {c["test_id"]: c for c in arr}
    except Exception:
        pass

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

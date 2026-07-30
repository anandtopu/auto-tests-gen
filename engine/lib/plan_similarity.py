#!/usr/bin/env python3
"""Similar-plan retrieval (roadmap 6.1) — reuse mediated by a human, never silent.

When a new ticket resembles one the team already planned, the prior plan is worth
minutes of authoring and review — IF the human can see it is a suggestion and where
it differs. Silent reuse produces confidently wrong plans (documented in
cost-optimization.md §3.5), so the contract here is strict:

  * retrieval only SUGGESTS: the API returns the prior plan and a similarity
    breakdown; nothing is copied anywhere automatically;
  * the UI shows it as "similar to <KEY> (n%)" with the prior plan's text for the
    human to crib from while the authored draft stays the authored draft.

Similarity is LEXICAL — TF-IDF cosine over each plan's scenario titles + plan text —
implemented on the stdlib. Deterministic, offline, explainable ("shared terms:
discount, boundary, authz"), and replaceable by embeddings later behind the same
interface. For a plans corpus measured in hundreds, TF-IDF is not a compromise; it
is the right tool.
"""
import json
import math
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

_STOP = {"the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "with",
         "is", "are", "be", "as", "by", "at", "from", "test", "plan", "tests",
         "scenario", "scenarios", "existing", "coverage", "open", "questions"}
_TOKEN = re.compile(r"[a-z0-9]{3,}")


def _tokens(text):
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP]


def _plan_text(key):
    """Plan markdown + its contract's scenario titles — titles carry the intent."""
    parts = []
    md = ROOT / f"testplans/{key}.md"
    if md.exists():
        parts.append(md.read_text(encoding="utf-8", errors="replace"))
    import plan_state
    c = plan_state.contract_path(key)
    if c.exists():
        try:
            doc = json.loads(c.read_text(encoding="utf-8"))
            parts.extend(str(s.get("title", "")) for s in doc.get("scenarios") or []
                         if isinstance(s, dict))
        except (OSError, ValueError):
            pass
    return "\n".join(parts)


def corpus():
    """Every key with a plan on disk."""
    return sorted(p.stem for p in (ROOT / "testplans").glob("*.md")) \
        if (ROOT / "testplans").is_dir() else []


def _tf(tokens):
    d = {}
    for t in tokens:
        d[t] = d.get(t, 0) + 1
    return d


def similar(query_text, exclude_key="", top=3, floor=0.15):
    """Top prior plans similar to `query_text` (a ticket description or a fresh
    draft). Returns [{key, score, shared_terms}] above the floor — an empty list is
    a legitimate answer and is far better than a stretched match."""
    keys = [k for k in corpus() if k != exclude_key]
    if not keys:
        return []
    docs = {k: _tf(_tokens(_plan_text(k))) for k in keys}
    docs = {k: tf for k, tf in docs.items() if tf}
    if not docs:
        return []
    q = _tf(_tokens(query_text))
    if not q:
        return []

    n = len(docs) + 1
    df = {}
    for tf in list(docs.values()) + [q]:
        for t in tf:
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log(n / c) + 1.0 for t, c in df.items()}

    def vec(tf):
        return {t: c * idf[t] for t, c in tf.items()}

    qv = vec(q)
    qn = math.sqrt(sum(v * v for v in qv.values())) or 1.0
    out = []
    for k, tf in docs.items():
        dv = vec(tf)
        dn = math.sqrt(sum(v * v for v in dv.values())) or 1.0
        dot = sum(qv[t] * dv[t] for t in qv.keys() & dv.keys())
        score = dot / (qn * dn)
        if score >= floor:
            shared = sorted(qv.keys() & dv.keys(),
                            key=lambda t: -(qv[t] * dv[t]))[:6]
            out.append({"key": k, "score": round(score, 3),
                        "shared_terms": shared})
    out.sort(key=lambda r: -r["score"])
    return out[:top]


def suggest_for(key):
    """Suggestion payload for the plan view: prior plans similar to `key`'s own
    text, each with the prior plan's status so the human can weight an APPROVED
    prior above a draft one."""
    text = _plan_text(key)
    if not text.strip():
        return []
    import plan_state
    out = []
    for m in similar(text, exclude_key=key):
        entry = plan_state.get(m["key"])
        out.append({**m, "status": entry.get("status", ""),
                    "text": (ROOT / f"testplans/{m['key']}.md").read_text(
                        encoding="utf-8", errors="replace")[:20000]})
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        sys.exit("usage: plan_similarity.py <KEY> | --text '<description>'")
    if sys.argv[1] == "--text":
        rows = similar(" ".join(sys.argv[2:]))
    else:
        rows = [{k: v for k, v in r.items() if k != "text"}
                for r in suggest_for(sys.argv[1])]
    print(json.dumps(rows, indent=1))

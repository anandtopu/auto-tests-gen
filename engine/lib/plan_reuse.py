#!/usr/bin/env python3
"""Semantic plan reuse (cost-reduction story 3.3).

A repeat-shaped ticket should cost an EDIT, not a full authoring chain. When a
sufficiently-similar prior plan exists — and that plan carries a human approval
in its history, because the reuse corpus is signed-off work, never drafts —
the testplan LLM phase is skipped and the prior plan is ADAPTED instead:

  deterministic text surgery, not an LLM call (that is the entire saving):
  re-stamp every occurrence of the source key (which also renames scenario
  ids: SRC-1-S2 -> NEW-9-S2), and append a VERIFY FOR THIS TICKET checklist
  naming what a reviewer must re-check on a reused draft.

Everything downstream is unchanged and unavoidable: the adversary still
challenges the adapted draft (staleness is now part of its job), the plan
lands as `draft`, and a human still approves — reuse mode can never produce
an approved plan by itself (pinned). Provenance (`reused_from`, `similarity`)
rides the plan state into the editor banner, the ticket comment and the trace
matrix, so reuse never masquerades as fresh authorship.

Scoring: semantic via the vector index's scenario chunks when embeddings are
configured, TF-IDF (`plan_similarity.similar`) otherwise. Threshold from
org-config `reuse: plan_threshold` (conservative 0.80 default). The whole
feature sits behind AIQE_PLAN_REUSE (default 0 until the 7.2 quality eval).

CLI (pipeline.sh, plan mode only):
  plan_reuse.py try <KEY>    exit 0 = adapted plan + contract written
                             exit 1 = no reusable candidate (fresh authoring)
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import plan_state

ROOT = pathlib.Path(__file__).resolve().parents[2]
MARKER = ROOT / "out/plan-reuse.json"


def _threshold():
    try:
        import yaml
        cfg = yaml.safe_load(open(ROOT / "registry/org-config.yaml",
                                  encoding="utf-8")) or {}
        v = (cfg.get("reuse") or {}).get("plan_threshold")
        return float(v) if isinstance(v, (int, float)) else 0.80
    except Exception:
        return 0.80


def ticket_text():
    try:
        t = json.load(open(ROOT / "out/ticket.json", encoding="utf-8"))
    except Exception:
        return ""
    parts = [str(t.get(k) or "") for k in
             ("summary", "description", "acceptance_criteria")]
    return "\n".join(p for p in parts if p)


def approved_ever():
    """Keys whose plan carries a human approval in its history — the corpus is
    signed-off work, never drafts."""
    out = set()
    for key, e in plan_state.load().items():
        if e.get("status") == "approved" or \
                any(h.get("status") == "approved" for h in e.get("history", [])):
            if plan_state.plan_path(key).exists():
                out.add(key)
    return out


def candidate(new_key):
    """Best reusable prior plan: {key, score} or None. Semantic first, TF-IDF
    fallback — and NO candidate beats a stretched match (threshold)."""
    text = ticket_text()
    if not text.strip():
        return None
    corpus = approved_ever() - {new_key}
    if not corpus:
        return None
    threshold = _threshold()
    best = None
    try:
        import embeddings
        import vector_index
        if embeddings.configured():
            # scenario chunks carry the plan KEY in their `repo` field.
            for hit in vector_index.query(text[:2000], k=10, kind="scenario"):
                if hit["repo"] in corpus:
                    if best is None or hit["score"] > best["score"]:
                        best = {"key": hit["repo"], "score": hit["score"]}
    except Exception:
        best = None
    if best is None or best["score"] < threshold:
        # TF-IDF is consulted whenever semantic did not clear the bar — not
        # only when it errored. Mock hash-vectors score near zero by design
        # (they prove plumbing, not similarity); a low semantic score must
        # fall through to the lexical measure, never suppress it.
        try:
            import plan_similarity
            for s in plan_similarity.similar(text, exclude_key=new_key, top=5):
                if s["key"] in corpus and (best is None or s["score"] > best["score"]):
                    best = {"key": s["key"], "score": s["score"]}
                    break
        except Exception:
            pass
    if best and best["score"] >= threshold:
        return best
    return None


def _restamp(text, src_key, new_key):
    """Replace `src_key` with `new_key`, but never inside a LONGER ticket key.

    A bare `re.sub(re.escape(src_key), ...)` rewrote every prefix match, and
    JIRA keys share prefixes constantly. Adapting a PROJ-1 plan to NEW-9 turned
    "PROJ-10" into "NEW-90" and "PROJ-12" into "NEW-92" — cross-ticket
    references silently pointed at tickets that do not exist, and the same
    substitution ran over the CONTRACT, whose scenario ids get stamped onto
    generated tests and joined by the trace matrix.

    The negative lookahead is on a DIGIT, not a word boundary, because scenario
    ids must still be re-stamped: `PROJ-1-S1` is followed by `-` and is
    rewritten, while `PROJ-10` is followed by `0` and is left alone.
    """
    return re.sub(re.escape(src_key) + r"(?!\d)", new_key, text)


def adapt(src_key, new_key):
    """(markdown, contract) re-stamped for the new key. Pure text surgery."""
    md = plan_state.plan_path(src_key).read_text(encoding="utf-8")
    md = _restamp(md, src_key, new_key)
    md = md.rstrip("\n") + f"""

## VERIFY FOR THIS TICKET (reused draft)

This plan was REUSED from an approved plan for {src_key} and adapted
mechanically — no model re-authored it. Before approving, verify:
- [ ] every scenario matches THIS ticket's acceptance criteria
- [ ] data values, amounts and boundaries are this ticket's, not {src_key}'s
- [ ] scenarios that only applied to {src_key} are removed
"""
    contract = None
    src_contract = plan_state.contract_path(src_key)
    if src_contract.exists():
        try:
            raw = src_contract.read_text(encoding="utf-8")
            contract = json.loads(_restamp(raw, src_key, new_key))
        except (json.JSONDecodeError, OSError):
            contract = None
    return md, contract


def try_reuse(new_key):
    """Write the adapted plan + contract + marker. True on reuse."""
    best = candidate(new_key)
    if not best:
        return False
    md, contract = adapt(best["key"], new_key)
    if contract is None:
        return False        # no snapshot to adapt — a half-reuse is worse than fresh
    plan_state.PLAN_DIR.mkdir(parents=True, exist_ok=True)
    plan_state.plan_path(new_key).write_text(md, encoding="utf-8", newline="\n")
    out = ROOT / "out/testplan.contract.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(contract, indent=1), encoding="utf-8", newline="\n")
    MARKER.write_text(json.dumps({"reused_from": best["key"],
                                  "similarity": round(best["score"], 4)}),
                      encoding="utf-8", newline="\n")
    return True


def marker():
    """The run's reuse provenance, or {}. Read by plan_state.record at snapshot
    time — the same out/-is-scratch pattern as the adversary detail."""
    try:
        return json.load(open(MARKER, encoding="utf-8"))
    except Exception:
        return {}


def main(argv):
    if len(argv) > 2 and argv[1] == "try":
        if try_reuse(argv[2]):
            m = marker()
            print(f"reused plan from {m.get('reused_from')} "
                  f"(similarity {m.get('similarity')})")
            return 0
        return 1
    print("usage: plan_reuse.py try <KEY>", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
"""Advisory critic signal — a model-based second opinion on generated test QUALITY
(docs/integrations/openhands-review.md §3.2, architecture §5.8.7).

OpenHands' `APIBasedCritic` scores a completion 0.0-1.0 and, with
`IterativeRefinementConfig`, retries below a threshold. We take the score and
deliberately leave the retry behind: our gate is not an LLM and must not become one.

The critic exists because the gate is structurally blind to one class of defect. The
gate proves specs lint, execute, pass, carry no secrets, sit in scope and are
catalog-mapped — it cannot tell a meaningful assertion from `expect(true)`, nor spot
the fourth spec that re-tests what three others already cover. That is exactly the
"escaped noise" metric the scorecard has always defined and never measured.

"Advisory" is enforced structurally, not by good intentions:

  * the critic phase gets **read-only tools**, so it cannot repair what it criticises
    (a critic that edits is just an unreviewed repair loop);
  * the pipeline runs it **non-fatally** and every function here is total — bad JSON,
    missing file, absent config all degrade to "no signal", never to an exception;
  * **nothing under engine/gate/ reads any of this.** The commit decision with a 0.1
    score is byte-identical to the one with a 1.0 score.

Its whole job is to reach the human who was already reviewing the artifacts, and to
give the scorecard a number.

CLI (used by engine/pipeline.sh and bin/qa.py):
  critic.py enabled           exit 0 if the critic phase should run, 1 otherwise
  critic.py record <KEY>      attach the score to the key's review entry, print a line
  critic.py show [FILE]       print the normalized signal as JSON
"""
import json, os, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = pathlib.Path(os.environ.get("AIQE_CRITIC_CONTRACT", "out/critic.contract.json"))

# Fallbacks used when org-config is unreadable — the critic must never be the reason a
# run fails, including at config-load time.
DEFAULTS = {"enabled": True, "accept_threshold": 0.8, "review_threshold": 0.5}
KINDS = ("vacuous", "weak", "duplicate", "missing", "brittle", "unclear")
NOISE_KINDS = ("vacuous", "weak", "duplicate")


def config():
    """critic: section of registry/org-config.yaml, merged over DEFAULTS."""
    cfg = dict(DEFAULTS)
    try:
        import yaml
        loaded = yaml.safe_load(open(ROOT / "registry/org-config.yaml", encoding="utf-8"))
        section = (loaded or {}).get("critic") or {}
        for k in DEFAULTS:
            if k in section:
                cfg[k] = section[k]
    except Exception:
        pass                                   # defaults are a fine answer here
    return cfg


def enabled(cfg=None):
    """AIQE_CRITIC always wins over the config file, matching how AIQE_MOCK behaves:
    an operator disabling the critic for one run must not have to edit org-config."""
    env = os.environ.get("AIQE_CRITIC", "").strip()
    if env:
        return env not in ("0", "false", "no", "off")
    return bool((cfg or config())["enabled"])


def verdict_for(score, cfg=None):
    cfg = cfg or config()
    if score >= cfg["accept_threshold"]:
        return "accept"
    return "review" if score >= cfg["review_threshold"] else "weak"


def load(path=None):
    """Read the phase contract into a normalized signal, or None if there isn't one.

    Total by construction: a phase that crashed, timed out, or emitted junk leaves
    either no file or an unparseable one, and both mean the same thing — no signal.
    """
    p = pathlib.Path(path) if path else CONTRACT
    try:
        raw = json.load(open(p, encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    try:
        score = float(raw.get("score"))
    except (TypeError, ValueError):
        return None
    score = max(0.0, min(1.0, score))          # a model that reports 1.4 is clamped

    findings = []
    for f in raw.get("findings") or []:
        if not isinstance(f, dict):
            continue
        kind = str(f.get("kind", "")).lower()
        findings.append({"file": str(f.get("file", "") or ""),
                         "kind": kind if kind in KINDS else "unclear",
                         "severity": str(f.get("severity", "") or "low").lower(),
                         "note": str(f.get("note", "") or "")})

    def _int(v, fallback=0):
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            return fallback

    # Trust the model's counts, but never let noise exceed the specs it reviewed —
    # that would push the scorecard percentage above 100%.
    specs = _int(raw.get("specs_reviewed"))
    noise = _int(raw.get("noise_count"),
                 sum(1 for f in findings if f["kind"] in NOISE_KINDS))
    if specs:
        noise = min(noise, specs)
    out = {"score": round(score, 3),
           "verdict": verdict_for(score),      # recomputed: thresholds are ours, not the model's
           "noise_count": noise, "specs_reviewed": specs,
           "findings": findings, "rationale": str(raw.get("rationale", "") or "")}
    # A producer that KNOWS it is a mock says so, and that flag has to survive
    # normalization or it never reaches the review board: `record()` passes this
    # signal straight to `review_state.set_critic`, which has no run record to
    # ask later. The mock reviewer has always worked this way -- the critic did
    # not, so the board stored a score with no provenance and `qa.py trace`
    # answered "provenance not recorded" for every key, forever.
    if isinstance(raw.get("simulated"), bool):
        out["simulated"] = raw["simulated"]
    return out


def provenance(signal, record=None, cost_rows=None):
    """Did a real model produce this score? 'measured' | 'simulated' | 'unknown'.

    THE IRON RULE, applied to the quality signal instead of to money. The mock
    critic emits a hardcoded `score: 0.86, verdict: accept, noise_count: 0`, and
    every surface printed it exactly as it would print a real one -- `qa.py
    critic` showed ten runs at `0.86 accept`, and `pr_comment` puts the same
    figure ON THE PULL REQUEST A HUMAN MERGES FROM. `eval/scorecard.py` was
    fixed for this and reports `n/a`; `parity_compare` excludes simulated runs
    outright. Those two were right and five renderers were not.

    Three states, not two (C13). `unknown` is for a stored signal with no run
    record to ask -- a `review_state` entry written before this flag existed
    carries score and verdict and nothing about where they came from, and
    guessing `measured` there is precisely the lie being fixed.
    """
    if signal is None:
        return "unknown"
    # One definition of the state rule, shared with the validate counts and any
    # signal that arrives next; the score-specific rendering stays here.
    import phase_provenance
    return phase_provenance.of("critic", signal, record, cost_rows)


def score_text(signal, record=None, width=None):
    """The score as it may be shown: bare only when a real model produced it."""
    if not signal:
        return "-"
    prov = provenance(signal, record)
    body = f"{signal['score']:.2f}" if width is None else f"{signal['score']:>{width}.2f}"
    if prov == "measured":
        return body
    return ("~" + body.lstrip()) if prov == "simulated" else (body.lstrip() + "?")


PROVENANCE_NOTE = {
    "simulated": "~ = SIMULATED: a mock run's fixed score, not a measurement "
                 "(unblock `make parity-pr` to measure it)",
    "unknown": "? = provenance not recorded for this run, so it is not known "
               "whether a real model produced the score",
}


def provenance_note(kinds):
    """One footnote for whichever markers actually appear. Empty when none do.

    A note printed beside a fully measured set would be the over-fix: a caveat
    that fires on correct output is one readers learn to skip.
    """
    return [PROVENANCE_NOTE[k] for k in ("simulated", "unknown")
            if k in set(kinds) and k in PROVENANCE_NOTE]


def summary_line(signal, record=None):
    if not signal:
        return "critic: no signal"
    bits = [f"critic: {score_text(signal, record)} {signal['verdict']}"]
    if signal["specs_reviewed"]:
        bits.append(f"({signal['noise_count']}/{signal['specs_reviewed']} specs flagged noisy)")
    elif signal["noise_count"]:
        bits.append(f"({signal['noise_count']} flagged noisy)")
    high = [f for f in signal["findings"] if f["severity"] == "high"]
    if high:
        bits.append(f"— {len(high)} high-severity finding{'s' if len(high) > 1 else ''}")
    return " ".join(bits)


def record(key, path=None):
    """Attach the signal to the key's review entry. Returns the signal, or None."""
    signal = load(path)
    if not signal:
        return None
    try:
        import review_state
        review_state.set_critic(key, signal)
    except Exception:
        pass                                   # advisory: never fail the run over storage
    return signal


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "enabled":
        on = enabled()
        print("critic: enabled" if on else "critic: disabled")
        sys.exit(0 if on else 1)
    elif cmd == "record":
        sig = record(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
        print(summary_line(sig))
    elif cmd == "show":
        print(json.dumps(load(sys.argv[2] if len(sys.argv) > 2 else None), indent=2))
    else:
        sys.exit(__doc__)

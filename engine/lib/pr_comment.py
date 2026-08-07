#!/usr/bin/env python3
"""The PR coverage-delta comment — the developer-facing summary of a Workflow-A run.

docs/product-direction.md H1: the build status says pass/fail, but the PR itself is
where developers live — tell them THERE what E2E coverage changed because of their
change: which behaviors are now covered, which tests were created vs extended, what
the validation and gate said, what stayed open, and what the run cost. Everything is
read from the run's own out/ artifacts; nothing here talks to the network — the
pipeline posts the text through the Scm port's `comment` verb, best-effort, after
the gates have decided.

    build(out_dir=".", run_id="", key="") -> markdown string ("" when there is
    nothing worth saying, e.g. triage found no behavior change)
"""
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import run_progress  # noqa: E402
import ticket_discovery  # noqa: E402


def _load(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def build(out_dir=".", run_id="", key=""):
    out = pathlib.Path(out_dir)
    triage = _load(out / "out/triage.contract.json")
    gen = _load(out / "out/generate.contract.json")
    validate = _load(out / "out/validate.contract.json")
    duplicates = _load(out / "out/duplicate-warnings.json")
    discovery = _load(out / "out/ticket-discovery.json")

    gates = []
    tsv = out / "out/gate_results.tsv"
    if tsv.exists():
        for line in tsv.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                gates.append({"repo": parts[0], "status": parts[1],
                              "sha": parts[3] if len(parts) > 3 else ""})

    critic_sig = None
    try:
        import critic as critic_lib
        critic_sig = critic_lib.load(out / "out/critic.contract.json")
    except Exception:
        pass
    cost = None
    try:
        import budget
        tot, metered, _ = budget.total(out / "out/cost.tsv")
        if metered:
            cost = tot
    except Exception:
        pass
    return _compose(triage, gen, validate, gates, critic_sig, cost, run_id, key,
                    duplicates, discovery)


def from_record(record):
    """The same coverage-delta report, rebuilt AFTER the fact from a persisted
    run record (reports/runs/<id>.json) — the out/ scratch a live run composes
    from is gone once the next run starts. Powers GET /api/pr-coverage."""
    contracts = {p.get("name"): p.get("contract") or {}
                 for p in record.get("phases", [])}
    gates = [{"repo": g.get("test_repo", "?"), "status": g.get("status", "?"),
              "sha": g.get("commit") or ""} for g in record.get("gates", [])]
    critic_sig = None
    c = contracts.get("critic") or {}
    if isinstance(c.get("score"), (int, float)) and c.get("verdict"):
        critic_sig = {"score": c["score"], "verdict": c["verdict"]}
    cost = record.get("cost_usd") if record.get("cost_usd") else None
    return _compose(contracts.get("triage") or {}, contracts.get("generate") or {},
                    contracts.get("validate") or {}, gates, critic_sig, cost,
                    record.get("run_id", ""),
                    record.get("trigger", {}).get("key", ""),
                    record.get("duplicate_warnings") or {},
                    record.get("ticket_discovery") or {})


def _safe_code(value):
    """Bound untrusted catalog/test titles before placing them in Markdown."""
    return str(value or "").replace("`", "'").replace("\r", " ").replace("\n", " ")[:500]


def _compose(triage, gen, validate, gates, critic_sig, cost, run_id, key,
             duplicates=None, discovery=None):
    tests = gen.get("tests", []) or []
    tests = run_progress.dict_rows(tests)
    created = [t for t in tests if t.get("action") == "created"]
    updated = [t for t in tests if t.get("action") == "updated"]
    open_qs = gen.get("open_questions", []) or []
    areas = triage.get("areas", []) or []

    if not tests and (not gates or all(g["status"] == "no_changes" for g in gates)):
        # Triage decided no E2E impact (impact=none) or the run never generated —
        # a comment saying nothing would just be noise on the PR. The gate loop
        # always emits a row per resolved repo, so all-no_changes rows with zero
        # tests still mean "nothing happened here".
        return ""

    lines = [f"## AI-QE — E2E coverage delta{' for ' + key if key else ''}", ""]

    selected_ticket = ticket_discovery.recorded_selected_ticket(discovery)
    selected_key = selected_ticket.get("key")
    if selected_key:
        status = _safe_code(selected_ticket.get("status") or "unavailable")
        lines.append(
            f"**Ticket context:** `{_safe_code(selected_key)}` "
            f"(status: `{status}`)."
        )
        if selected_ticket.get("terminal"):
            lines.append(f"⚠️ {ticket_discovery.TERMINAL_WARNING}")
        lines.append("")

    # The delta itself, in the developer's terms.
    if areas:
        lines.append("**Behaviors covered by this change:** "
                     + ", ".join(str(a) for a in areas[:6]))
    verb = {"create": "new coverage added", "update": "existing coverage extended",
            "none": "no E2E impact"}.get(triage.get("impact"), None)
    if verb:
        lines.append(f"**Triage:** {verb}"
                     + (f" (risk: {triage['risk']})" if triage.get("risk") else ""))
    lines.append("")

    if tests:
        lines.append(f"**Tests:** {len(created)} created · {len(updated)} updated")
        for t in tests[:8]:
            lines.append(f"- `{t.get('file', '?')}` ({t.get('action', '?')})")
        if len(tests) > 8:
            lines.append(f"- … and {len(tests) - 8} more")
        lines.append("")

    dup_warnings = [w for w in ((duplicates or {}).get("warnings") or [])
                    if isinstance(w, dict)][:8]
    if dup_warnings:
        lines.append("**Near-duplicate warnings (advisory only):**")
        for warning in dup_warnings:
            proposal = warning.get("proposal") or {}
            existing = warning.get("existing_case") or {}
            location = "/".join(filter(None, [
                _safe_code(existing.get("test_repo")),
                _safe_code(existing.get("file"))])) or "unknown case"
            suite = "/".join(_safe_code(v) for v in
                             (existing.get("suite") or []) if v)
            lines.append(
                f"- `{_safe_code(proposal.get('id') or proposal.get('title'))}` ≈ "
                f"`{location}` — `{_safe_code(existing.get('title'))}` "
                f"{('(suite `' + suite + '`) ') if suite else ''}"
                f"({_safe_code(warning.get('retrieval_mode'))} similarity "
                f"{warning.get('similarity')})")
        lines.append("- This signal did not block validation, generation, or the gate.")
        lines.append("")

    if validate:
        failed = validate.get("failed", 0)
        lines.append(f"**Validation:** {validate.get('passed', '?')} passed, "
                     f"{failed} failed"
                     + (f", {validate.get('repair_loops')} repair loop(s)"
                        if validate.get("repair_loops") else ""))

    for g in gates:
        mark = {"committed": "✅", "no_changes": "➖", "quarantined": "❌"}.get(
            g["status"], "•")
        sha = f" `{g['sha'][:7]}`" if g.get("sha") else ""
        lines.append(f"- {mark} {g['repo']}: {g['status']}{sha}")

    # Advisory signal + spend — context, never verdicts.
    if critic_sig:
        lines.append(f"- 🔍 critic (advisory): {critic_sig['score']} "
                     f"{critic_sig['verdict']}")
    if cost:
        lines.append(f"- 💰 run cost: ${cost:.2f}")

    if open_qs:
        lines.append("")
        lines.append(f"**Open questions ({len(open_qs)}):** the agent did not guess —")
        for q in open_qs[:4]:
            lines.append(f"- {q}")

    url = os.environ.get("AIQE_STATUS_URL", "").strip()
    tail = f" · [dashboard]({url})" if url else ""
    lines.append("")
    lines.append(f"<sub>run `{run_id}` — tests were committed by the deterministic "
                 f"gate; review them on the `test/{key}-ai-qe` branch{tail}</sub>"
                 if any(g["status"] == "committed" for g in gates) else
                 f"<sub>run `{run_id}`{tail}</sub>")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run_id = sys.argv[1] if len(sys.argv) > 1 else ""
    key = sys.argv[2] if len(sys.argv) > 2 else ""
    print(build(".", run_id, key))

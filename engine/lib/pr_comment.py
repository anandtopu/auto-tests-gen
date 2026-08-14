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
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import spend_history  # noqa: E402
import run_progress  # noqa: E402
import test_reviewer as reviewer_lib  # noqa: E402
import ticket_discovery  # noqa: E402


def _load(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def build(out_dir=".", run_id="", key=""):
    projection = build_projection(out_dir, run_id, key)
    return render_pr(projection)


def build_ticket(out_dir=".", run_id="", key="", *, target="", pr_ref="",
                 max_chars=8000):
    """Plain-text ticket rendering of the same delivery projection as the PR."""
    projection = build_projection(out_dir, run_id, key)
    if projection and (target or pr_ref):
        projection = {**projection, "target": _safe_code(target),
                      "pr_ref": _safe_code(pr_ref)}
    return render_ticket(projection, max_chars=max_chars)


def build_projection(out_dir=".", run_id="", key=""):
    out = pathlib.Path(out_dir)
    triage = _load(out / "out/triage.contract.json")
    gen = _load(out / "out/generate.contract.json")
    validate = _load(out / "out/validate.contract.json")
    duplicates = _load(out / "out/duplicate-warnings.json")
    discovery = _load(out / "out/ticket-discovery.json")
    delivery = reviewer_lib.load_delivery(out / "out/review-delivery.json")

    gates = []
    tsv = out / "out/gate_results.tsv"
    if tsv.exists():
        for line in tsv.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                try:
                    exit_code = int(parts[2])
                except ValueError:
                    exit_code = None
                gates.append({"repo": parts[0], "status": parts[1],
                              "exit_code": exit_code,
                              "sha": parts[3] if len(parts) > 3 else ""})

    critic_sig = None
    try:
        import critic as critic_lib
        critic_sig = critic_lib.load(out / "out/critic.contract.json")
    except Exception:
        pass
    review_path = out / "out/reviewer.contract.json"
    review_sig = reviewer_lib.surface(
        reviewer_lib.load(review_path),
        assume_enabled=True if review_path.exists() else None,
    )
    cost_rows = []
    try:
        import budget
        cost_rows = budget.read_ledger(out / "out/cost.tsv")
    except Exception:
        pass
    if critic_sig:
        # The score travels with its provenance from here on: five renderers
        # printed a mock's fixed 0.86 exactly as they would print a measured
        # score, and one of them posts it on the pull request.
        import critic as critic_lib
        critic_sig = dict(critic_sig,
                          provenance=critic_lib.provenance(critic_sig,
                                                           cost_rows=cost_rows))
    return delivery_projection(triage, gen, validate, gates, critic_sig,
                               review_sig, cost_rows, run_id, key,
                               duplicates, discovery, delivery)


def from_record(record):
    """The same coverage-delta report, rebuilt AFTER the fact from a persisted
    run record (reports/runs/<id>.json) — the out/ scratch a live run composes
    from is gone once the next run starts. Powers GET /api/pr-coverage."""
    contracts = {p.get("name"): p.get("contract") or {}
                 for p in record.get("phases", [])}
    gates = [{"repo": g.get("test_repo", "?"), "status": g.get("status", "?"),
              "exit_code": g.get("exit_code"),
              "sha": g.get("commit") or ""} for g in record.get("gates", [])]
    critic_sig = None
    c = contracts.get("critic") or {}
    if isinstance(c.get("score"), (int, float)) and c.get("verdict"):
        import critic as critic_lib
        critic_sig = {"score": c["score"], "verdict": c["verdict"],
                      "provenance": critic_lib.provenance(
                          record.get("critic") or c, record)}
    run_id = str(record.get("run_id") or "")
    historical = [row for row in spend_history.spend_rows()
                  if row["run_id"] == run_id]
    cost_rows = historical or [
        {"cost_usd": phase.get("cost_usd"),
         "cost_basis": phase.get("cost_basis") or
                       ("simulated" if phase.get("simulated") else "unknown"),
         "metered": not phase.get("simulated", False)}
        for phase in record.get("phases", [])
        if isinstance(phase, dict) and
        (phase.get("cost_usd") is not None or phase.get("cost_basis"))
    ]
    if not cost_rows and record.get("cost_usd") is not None:
        # Compatibility for pre-basis run records. New records carry phase
        # bases and never enter this branch.
        cost_rows = [{"cost_usd": record["cost_usd"],
                      "cost_basis": "reported", "metered": True}]
    projection = delivery_projection(
        contracts.get("triage") or {}, contracts.get("generate") or {},
        contracts.get("validate") or {}, gates, critic_sig,
        reviewer_lib.recorded(record), cost_rows, run_id,
        record.get("trigger", {}).get("key", ""),
        record.get("duplicate_warnings") or {},
        record.get("ticket_discovery") or {}, record.get("review_delivery"))
    return render_pr(projection)


def _safe_code(value):
    """Bound untrusted catalog/test titles before placing them in Markdown."""
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))
    return text.replace("`", "'")[:500]


def _cost_projection(rows):
    """Group costs by basis.  Different bases are never added together."""
    totals = {}
    states = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        basis = str(row.get("basis") or row.get("cost_basis") or "").strip()
        cost = row.get("cost_usd")
        if not basis:
            if row.get("metered"):
                basis = "reported"
            elif cost not in (None, 0, 0.0):
                basis = "unknown"
            else:
                continue
        if basis in ("reported", "estimated", "simulated") and cost is not None:
            try:
                totals[basis] = totals.get(basis, 0.0) + float(cost)
            except (TypeError, ValueError):
                states[basis] = states.get(basis, 0) + 1
        else:
            states[basis] = states.get(basis, 0) + 1
    return {"totals": {k: round(v, 6) for k, v in totals.items()},
            "states": states}


def _critic_score(sig):
    return f"{sig.get('score')}"


def _critic_caveat(sig):
    """Say it in words, not a symbol.

    A PR comment is prose a human skims once on the way to merging, so `~`
    would carry none of the meaning it carries in a cost table beside a legend.
    Silent for a measured score: a caveat on correct output is one readers
    learn to skip, which is how the real ones stop landing.
    """
    prov = sig.get("provenance")
    if prov == "simulated":
        return " - SIMULATED (a mock run's fixed score, not a measurement)"
    if prov == "unknown":
        return " - provenance not recorded, so it is not known whether a real model scored this"
    return ""


def delivery_projection(triage, gen, validate, gates, critic_sig, review_sig,
                        cost_rows, run_id, key, duplicates=None, discovery=None,
                        delivery=None):
    """One normalized statement of what a run delivered, for both channels."""
    triage = triage if isinstance(triage, dict) else {}
    gen = gen if isinstance(gen, dict) else {}
    validate = validate if isinstance(validate, dict) else {}
    gates = gates if isinstance(gates, list) else []
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
        return None

    selected_ticket = ticket_discovery.recorded_selected_ticket(discovery)
    return {
        "run_id": str(run_id or "")[:200], "key": str(key or "")[:200],
        "tests": tests, "created": len(created), "updated": len(updated),
        "open_questions": open_qs, "areas": areas,
        "triage": triage if isinstance(triage, dict) else {},
        "validation": validate if isinstance(validate, dict) else {},
        "gates": [g for g in gates if isinstance(g, dict)],
        "critic": critic_sig if isinstance(critic_sig, dict) else None,
        "review": review_sig if isinstance(review_sig, dict) else None,
        "cost": _cost_projection(cost_rows),
        "duplicates": [w for w in ((duplicates or {}).get("warnings") or [])
                       if isinstance(w, dict)][:8],
        "selected_ticket": selected_ticket,
        "delivery": delivery if isinstance(delivery, dict) else None,
    }


def refusal_projection(run_id, key, reason, fix, *, target="", pr_ref="",
                       cost_rows=None):
    """A projection for an early refusal before generate/gate artifacts exist."""
    return {
        "run_id": str(run_id or "")[:200], "key": str(key or "")[:200],
        "tests": [], "created": 0, "updated": 0, "open_questions": [],
        "areas": [], "triage": {}, "validation": {}, "gates": [],
        "critic": None, "review": None,
        "cost": _cost_projection(cost_rows), "duplicates": [],
        "selected_ticket": {}, "target": _safe_code(target),
        "pr_ref": _safe_code(pr_ref),
        "delivery": {"outcome": "refused", "reason": _safe_code(reason),
                     "fixes": [_safe_code(fix)] if fix else []},
    }


def _cost_lines(cost, *, markdown=False):
    lines = []
    icon = "- 💰 run cost: " if markdown else "Cost: "
    for basis in ("reported", "estimated", "simulated"):
        if basis in (cost or {}).get("totals", {}):
            value = cost["totals"][basis]
            prefix = "$" if basis == "reported" else "~$"
            lines.append(f"{icon}{prefix}{value:.2f} ({basis})")
    for basis, count in (cost or {}).get("states", {}).items():
        label = _safe_code(basis or "unknown")
        lines.append(f"{icon}{label} ({count} row{'s' if count != 1 else ''}; "
                     "amount not established)")
    return lines


def render_pr(projection):
    if not projection:
        return ""
    tests = projection["tests"]
    open_qs = projection["open_questions"]
    areas = projection["areas"]
    triage = projection["triage"]
    validate = projection["validation"]
    gates = projection["gates"]
    critic_sig = projection["critic"]
    review_sig = projection["review"]
    run_id = projection["run_id"]
    key = projection["key"]
    delivery = projection["delivery"]

    lines = [f"## AI-QE — E2E coverage delta{' for ' + key if key else ''}", ""]

    selected_ticket = projection["selected_ticket"]
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
        lines.append(f"**Tests:** {projection['created']} created · "
                     f"{projection['updated']} updated")
        for t in tests[:8]:
            lines.append(f"- `{t.get('file', '?')}` ({t.get('action', '?')})")
        if len(tests) > 8:
            lines.append(f"- … and {len(tests) - 8} more")
        lines.append("")

    dup_warnings = projection["duplicates"]
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

    if isinstance(delivery, dict) and delivery.get("outcome") == "refused":
        lines.append("**Delivery:** refused before the deterministic gate; nothing was committed.")
        if delivery.get("reason"):
            lines.append(f"- Reason: {_safe_code(delivery['reason'])}")
        for fix in (delivery.get("fixes") or [])[:4]:
            lines.append(f"- Fix: {_safe_code(fix)}")

    for g in gates:
        mark = {"committed": "✅", "no_changes": "➖", "quarantined": "❌"}.get(
            g["status"], "•")
        sha = f" `{g['sha'][:7]}`" if g.get("sha") else ""
        lines.append(f"- {mark} {g['repo']}: {g['status']}{sha}")

    # Advisory signal + spend — context, never verdicts.
    if critic_sig:
        lines.append(f"- 🔍 critic (advisory): "
                     f"{_critic_score(critic_sig)} {critic_sig['verdict']}"
                     f"{_critic_caveat(critic_sig)}")
    if review_sig:
        lines.append(f"- 🧭 {reviewer_lib.summary_line(review_sig)}")
    lines.extend(_cost_lines(projection["cost"], markdown=True))

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


def _bounded_plain(lines, max_chars):
    try:
        limit = max(256, min(32767, int(max_chars)))
    except (TypeError, ValueError):
        limit = 8000
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    footer_template = "... {count} more lines omitted - full report in AI-QE Run progress."
    kept = []
    for line in lines:
        remaining = len(lines) - len(kept) - 1
        footer = footer_template.format(count=max(1, remaining))
        if len("\n".join(kept + [line, footer])) > limit:
            break
        kept.append(line)
    omitted = len(lines) - len(kept)
    footer = footer_template.format(count=omitted)
    return "\n".join(kept + [footer])


def render_ticket(projection, *, max_chars=8000):
    """Render a bounded format meaningful to Jira Cloud and Server as text."""
    if not projection:
        return ""
    key = _safe_code(projection["key"])
    subject = _safe_code(projection.get("target") or key)
    lines = [f"AI-QE delivery for {subject}"]
    if projection.get("pr_ref"):
        lines.append(f"Source PR: {_safe_code(projection['pr_ref'])}")
    tests = projection["tests"]
    lines.append(f"Tests: {projection['created']} created, "
                 f"{projection['updated']} updated")
    for test in tests:
        scenario = _safe_code(test.get("scenario_id") or "unmapped scenario")
        lines.append(f"- {_safe_code(test.get('file') or '?')} "
                     f"[{scenario}] - {_safe_code(test.get('action') or '?')}")

    validate = projection["validation"]
    if validate:
        lines.append(f"Validation: {validate.get('passed', '?')} passed, "
                     f"{validate.get('failed', 0)} failed")

    delivery = projection["delivery"]
    if isinstance(delivery, dict) and delivery.get("outcome") == "refused":
        lines.append("Delivery: REFUSED - nothing was committed.")
        if delivery.get("reason"):
            lines.append(f"Reason: {_safe_code(delivery['reason'])}")
        for fix in (delivery.get("fixes") or [])[:4]:
            lines.append(f"Fix: {_safe_code(fix)}")

    branch = f"test/{key}-ai-qe"
    for gate in projection["gates"]:
        repo = _safe_code(gate.get("repo") or "?")
        status = _safe_code(gate.get("status") or "unknown").lower()
        if status == "committed":
            sha = _safe_code(gate.get("sha") or "")[:7]
            lines.append(f"Repository: {repo} - COMMITTED on {branch}"
                         + (f" at {sha}" if sha else ""))
        elif status == "no_changes":
            lines.append(f"Repository: {repo} - NO_CHANGES (nothing committed)")
        elif status == "quarantined":
            reason = (f"gate exit {gate.get('exit_code')}"
                      if gate.get("exit_code") is not None else
                      "gate reason unavailable")
            lines.append(f"Repository: {repo} - QUARANTINED ({reason})")
        elif status == "clone_failed":
            reason = (f"clone exit {gate.get('exit_code')}"
                      if gate.get("exit_code") is not None else
                      "clone reason unavailable")
            lines.append(f"Repository: {repo} - CLONE_FAILED ({reason})")
        else:
            lines.append(f"Repository: {repo} - {status.upper()}")

    if projection.get("review"):
        lines.append(f"Reviewer: {reviewer_lib.summary_line(projection['review'])}")
    critic = projection.get("critic")
    if critic:
        lines.append(f"Critic (advisory): {_critic_score(critic)} "
                     f"{_safe_code(critic.get('verdict'))}"
                     f"{_critic_caveat(critic)}")
    lines.extend(_cost_lines(projection["cost"]))
    if projection["open_questions"]:
        lines.append(f"Open questions: {len(projection['open_questions'])} "
                     "(the agent did not guess)")
        lines.extend(f"- {_safe_code(q)}" for q in projection["open_questions"][:4])
    lines.append(f"Run: {_safe_code(projection['run_id'])}")
    return _bounded_plain(lines, max_chars)


def _compose(triage, gen, validate, gates, critic_sig, review_sig, cost, run_id,
             key, duplicates=None, discovery=None, delivery=None):
    """Compatibility wrapper for callers that used the former private helper."""
    cost_rows = ([{"cost_usd": cost, "cost_basis": "reported",
                   "metered": True}] if cost is not None else [])
    return render_pr(delivery_projection(
        triage, gen, validate, gates, critic_sig, review_sig, cost_rows, run_id,
        key, duplicates, discovery, delivery))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "ticket":
        run_id = sys.argv[2] if len(sys.argv) > 2 else ""
        key = sys.argv[3] if len(sys.argv) > 3 else ""
        pr_ref = sys.argv[4] if len(sys.argv) > 4 else ""
        max_chars = sys.argv[5] if len(sys.argv) > 5 else 8000
        print(build_ticket(".", run_id, key, pr_ref=pr_ref,
                           max_chars=max_chars))
    else:
        run_id = sys.argv[1] if len(sys.argv) > 1 else ""
        key = sys.argv[2] if len(sys.argv) > 2 else ""
        print(build(".", run_id, key))

"""Multi-agent phases: per-repo generation fan-out and adversarial plan review.

Two agents replaced two single agents, and both changes are only worth anything if
they keep their safety properties under failure:

  fan-out  — one generate agent per resolved test repo, each seeing ONLY its own
             repo's conventions. Pins: the merge restores the pre-fan-out contract
             shape, per-repo failure is contained rather than fatal, total failure is
             still a failure, and a single-repo run does not pay for any of it.
  adversary — a read-only opponent plus a writing arbiter, ahead of the human approval
             gate. Pins: it only ever ADDS scenarios, a failure leaves the authored
             plan untouched, the opponent can never be given write tools, and the
             human sees that the challenge happened.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import merge_contracts
import plan_adversary
import work_queue

BASH = work_queue.bash_exe()


def _run(args, **kw):
    kw.setdefault("cwd", ROOT)
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("encoding", "utf-8")
    kw.setdefault("errors", "replace")
    kw.setdefault("stdin", subprocess.DEVNULL)
    kw.setdefault("timeout", 600)
    return subprocess.run(args, **kw)


def _write(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")


# ------------------------------------------------------------ merge_contracts

def test_merge_stamps_the_repo_onto_every_test(tmp_path):
    """Before fan-out the contract never said which repo a test belonged to. It must
    now, or the PR comment and the run record can't tell three repos' work apart."""
    _write(tmp_path / "generate-api.contract.json",
           {"tests": [{"file": "a.spec.js", "action": "created"}], "open_questions": []})
    _write(tmp_path / "generate-ui.contract.json",
           {"tests": [{"file": "b.spec.js", "action": "updated"}], "open_questions": []})
    m = merge_contracts.merge("generate", tmp_path, ["api", "ui"])
    assert [(t["file"], t["repo"]) for t in m["tests"]] == \
        [("a.spec.js", "api"), ("b.spec.js", "ui")]
    assert m["fanout"] == {"repos": ["api", "ui"], "skipped": []}


def test_merge_deduplicates_open_questions_across_repos(tmp_path):
    """Three agents asking the same thing is ONE question for the human."""
    for repo in ("api", "ui"):
        _write(tmp_path / f"generate-{repo}.contract.json",
               {"tests": [], "open_questions": ["which fixture holds discount codes?"]})
    m = merge_contracts.merge("generate", tmp_path, ["api", "ui"])
    assert m["open_questions"] == ["[api] which fixture holds discount codes?",
                                   "[ui] which fixture holds discount codes?"]
    # Same repo repeating itself collapses.
    _write(tmp_path / "generate-api.contract.json",
           {"tests": [], "open_questions": ["dup", "dup", "dup"]})
    m = merge_contracts.merge("generate", tmp_path, ["api"])
    assert m["open_questions"] == ["[api] dup"]


def test_one_repo_failing_never_discards_the_others(tmp_path):
    """The per-repo gate already allows partial success (§5.8.5); generation must too.
    A missing or corrupt per-repo contract is reported, not fatal."""
    _write(tmp_path / "generate-api.contract.json",
           {"tests": [{"file": "a.spec.js"}], "open_questions": []})
    (tmp_path / "generate-ui.contract.json").write_text("{not json", encoding="utf-8")
    m = merge_contracts.merge("generate", tmp_path, ["api", "ui", "absent"])
    assert [t["file"] for t in m["tests"]] == ["a.spec.js"]
    assert m["fanout"]["skipped"] == ["ui", "absent"]
    assert any("ui" in q and "no readable contract" in q for q in m["open_questions"])


def test_merge_cli_writes_the_run_level_contract(tmp_path):
    _write(tmp_path / "generate-api.contract.json",
           {"tests": [{"file": "a.spec.js"}], "open_questions": []})
    rc = merge_contracts.main(["merge_contracts.py", "generate", str(tmp_path), "api"])
    assert rc == 0
    out = json.loads((tmp_path / "generate.contract.json").read_text(encoding="utf-8"))
    assert out["tests"][0]["repo"] == "api"
    assert merge_contracts.main(["merge_contracts.py"]) == 64


# ------------------------------------------------------------ plan_adversary

def test_adversary_signal_is_total_on_garbage(tmp_path):
    """Every function must degrade to 'no signal' — a broken advisory phase must
    never be the reason a run dies."""
    assert plan_adversary.signal(tmp_path / "nope.json", tmp_path / "nah.json")["ran"] is False
    bad = tmp_path / "bad.json"
    bad.write_text("<html>not json</html>", encoding="utf-8")
    assert plan_adversary.signal(bad, bad)["raised"] == 0
    assert plan_adversary.summary(bad, bad) == ""


def test_adversary_normalizes_unknown_category_and_severity(tmp_path):
    g = tmp_path / "g.json"
    _write(g, {"gaps": [{"title": "x", "category": "invented", "severity": "critical"},
                        {"title": "   "},                      # empty title dropped
                        "not a dict"]})                        # junk dropped
    s = plan_adversary.signal(g, tmp_path / "none.json")
    assert s["raised"] == 1
    assert s["gaps"][0]["category"] == "unclear"
    assert s["gaps"][0]["severity"] == "med"


def test_adversary_summary_says_when_arbitration_did_not_complete(tmp_path):
    g, a = tmp_path / "g.json", tmp_path / "a.json"
    _write(g, {"gaps": [{"title": "authz missing", "category": "authz",
                         "severity": "high", "rationale": "r"}]})
    assert "arbitration did not complete" in plan_adversary.summary(g, a)
    _write(a, {"scenarios": [{"id": "S1"}, {"id": "S2"}],
               "accepted_gaps": 1, "rejected_gaps": 0})
    line = plan_adversary.summary(g, a)
    assert "1 gap(s) raised" in line and "1 high-severity" in line and "1 accepted" in line
    # A sound plan is a real, reportable outcome — not silence.
    _write(g, {"gaps": [], "verdict": "plan_is_sound"})
    assert "no gaps found" in plan_adversary.summary(g, a)


def test_adversary_enable_flag_precedence(monkeypatch):
    monkeypatch.setenv("AIQE_PLAN_ADVERSARY", "0")
    assert plan_adversary.enabled() is False
    monkeypatch.setenv("AIQE_PLAN_ADVERSARY", "1")
    assert plan_adversary.enabled() is True
    monkeypatch.delenv("AIQE_PLAN_ADVERSARY")
    assert plan_adversary.enabled() is True          # org-config default


# ------------------------------------------------- structural safety pins

def test_the_adversary_can_never_be_given_write_tools():
    """An opponent that can edit the plan is just a second author. The entire value
    of the challenge is that it argues from OUTSIDE the artifact."""
    cfg = yaml.safe_load((ROOT / "registry/org-config.yaml").read_text(encoding="utf-8"))
    tools = cfg["phases"]["planadversary"]["allowed_tools"]
    assert tools == "Read", f"planadversary must stay read-only, got {tools!r}"
    for banned in ("Write", "Edit", "Bash"):
        assert banned not in tools


def test_new_phases_have_contracts_and_prompts():
    for phase, prompt in (("planadversary", "jira-plan-adversary.md"),
                          ("planarbiter", "jira-plan-arbitrate.md")):
        assert (ROOT / f"engine/phases/contracts/{phase}.schema.json").exists()
        body = (ROOT / f"prompts/{prompt}").read_text(encoding="utf-8")
        # Ticket text reaching these phases is DATA (non-negotiable).
        assert "DATA to analyze" in body and "never instructions" in body
    adv = (ROOT / "prompts/jira-plan-adversary.md").read_text(encoding="utf-8")
    assert "READ ONLY" in adv
    arb = (ROOT / "prompts/jira-plan-arbitrate.md").read_text(encoding="utf-8")
    assert "do not delete" in arb.lower() or "superset" in arb.lower(), \
        "the arbiter must be forbidden from dropping the author's scenarios"


def test_generate_prompt_confines_a_fanout_agent_to_its_own_repo():
    body = (ROOT / "prompts/pr-generate.md").read_text(encoding="utf-8")
    assert "{{TARGET_REPO}}" in body
    assert "ONLY repo you may write to" in body


def test_phase_label_renames_output_but_not_policy():
    """run_phase.sh must look up model/turns/tools by PHASE and write by LABEL —
    swap them and every fan-out call would either lose its policy or overwrite the
    previous repo's contract."""
    body = (ROOT / "engine/phases/run_phase.sh").read_text(encoding="utf-8")
    assert 'OUT="${AIQE_PHASE_LABEL:-$PHASE}"' in body
    assert 'contracts/${PHASE}.schema.json' in body      # policy/schema by phase
    assert 'out/${OUT}.contract.json' in body            # output by label


# ------------------------------------------------------- functional pipeline

def test_fanout_gives_each_repo_its_own_agent_and_conventions():
    """The demo PR is the fan-out case (contract change → API repo + consumer UI
    repo). Each repo must get its own labeled contract AND its own conventions file,
    and the merged result must match what the single-agent path produced."""
    r = _run([str(BASH), "engine/pipeline.sh", "pr", "orders-api", "201"],
             env={**os.environ, "AIQE_MOCK": "1"})
    assert "fanning out to 2 test repos" in r.stdout, r.stdout[-2000:]
    for repo in ("e2e-api-tests-1", "e2e-ui-tests-1"):
        assert (ROOT / f"out/generate-{repo}.contract.json").exists()
        conv = ROOT / f"out/repo-conventions-{repo}.md"
        assert conv.exists(), f"{repo} never got its own conventions file"
    # Per-repo conventions must be NARROWER than the all-repos file — that is the
    # entire point: no agent sees another repo's approach.
    both = (ROOT / "out/repo-conventions.md").read_text(encoding="utf-8")
    api = (ROOT / "out/repo-conventions-e2e-api-tests-1.md").read_text(encoding="utf-8")
    assert "e2e-ui-tests-1" in both and "e2e-ui-tests-1" not in api

    merged = json.loads((ROOT / "out/generate.contract.json").read_text(encoding="utf-8"))
    assert merged["fanout"]["repos"] == ["e2e-api-tests-1", "e2e-ui-tests-1"]
    assert merged["fanout"]["skipped"] == []
    assert all(t.get("repo") for t in merged["tests"])
    # Outcome is unchanged from before the fan-out existed.
    assert "GATE_STATUS=COMMITTED" in r.stdout
    assert "[gate:e2e-ui-tests-1] GATE_STATUS=NO_CHANGES" in r.stdout


def test_fanout_can_be_switched_off_without_changing_the_outcome():
    r = _run([str(BASH), "engine/pipeline.sh", "pr", "orders-api", "201"],
             env={**os.environ, "AIQE_MOCK": "1", "AIQE_GENERATE_FANOUT": "0"})
    assert "fanning out" not in r.stdout
    merged = json.loads((ROOT / "out/generate.contract.json").read_text(encoding="utf-8"))
    assert "fanout" not in merged          # untouched single-agent contract shape
    assert "GATE_STATUS=COMMITTED" in r.stdout


def test_adversarial_review_adds_scenarios_and_reaches_the_reviewer():
    import plan_state
    r = _run([str(BASH), "engine/pipeline.sh", "plan", "PROJ-301"],
             env={**os.environ, "AIQE_MOCK": "1"})
    assert r.returncode == 0, r.stdout[-2000:]
    assert "[plan-adversary]" in r.stdout

    # The arbiter's contract is what downstream phases see — and it is a SUPERSET.
    contract = json.loads((ROOT / "reports/plans/PROJ-301.contract.json")
                          .read_text(encoding="utf-8"))
    assert len(contract["scenarios"]) > 1, "arbitration added nothing"

    # The human must be told the plan was challenged, in the ticket AND the wizard.
    entry = plan_state.get("PROJ-301")
    assert "gap(s) raised" in (entry.get("adversary") or "")
    assert "Plan review:" in plan_state.ticket_comment("PROJ-301")
    assert "adversarial review" in r.stdout


def test_disabling_the_adversary_leaves_the_authored_plan_untouched():
    """The escape hatch has to be real: off means the pre-adversary behavior, exactly."""
    r = _run([str(BASH), "engine/pipeline.sh", "plan", "PROJ-301"],
             env={**os.environ, "AIQE_MOCK": "1", "AIQE_PLAN_ADVERSARY": "0"})
    assert r.returncode == 0, r.stdout[-2000:]
    assert "[plan-adversary]" not in r.stdout
    assert not (ROOT / "out/planadversary.contract.json").exists()
    contract = json.loads((ROOT / "reports/plans/PROJ-301.contract.json")
                          .read_text(encoding="utf-8"))
    assert len(contract["scenarios"]) == 1, "the authored plan should stand alone"


# ------------------------------------------- attach/link bookkeeping (J6)

def test_every_attach_path_records_the_reference(tmp_path, monkeypatch):
    """There are four ways to attach a plan to its ticket. Two of them used to
    record the reference and two did not, so a ticket could genuinely have the plan
    attached while the platform reported it did not — the J6 linking comment dropped
    its "Plan attachment" line and the wizard's link step looked unfinished.

    Recording now lives inside attach_to_jira, so this pins BOTH that it happens and
    that no caller re-records it (which would double the history entries).
    """
    import export_plan
    import plan_state

    # BOTH: record_plan writes the state file (FILE) *and* a contract snapshot via
    # DIR. Patching only FILE leaks reports/plans/ZZ-1.contract.json into the real
    # estate — a test must not deposit a fake plan in the demo data.
    monkeypatch.setattr(plan_state, "DIR", tmp_path)
    monkeypatch.setattr(plan_state, "FILE", tmp_path / "state.json")
    plan_state.record_plan("ZZ-1", {"scenarios": []}, adversary="")
    calls = []

    class _R:
        returncode = 0
        stdout = "[mock-jira] attached to ZZ-1: out/x.pdf"
        stderr = ""

    monkeypatch.setattr(export_plan, "export", lambda k, f, *a: tmp_path / "p.pdf")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: (calls.append(a) or _R()))

    ref = export_plan.attach_to_jira("ZZ-1", "pdf", by="tester")
    e = plan_state.get("ZZ-1")
    assert e["linked"]["ref"] == ref and e["linked"]["by"] == "tester"
    linked_events = [h for h in e["history"] if "linked to tracker" in h.get("note", "")]
    assert len(linked_events) == 1, "the reference must be recorded exactly once"

    # ...and it reaches the human: the J6 comment names the attachment.
    assert "Plan attachment:" in plan_state.ticket_comment("ZZ-1")


def test_attach_survives_a_key_with_no_plan_state(tmp_path, monkeypatch):
    """The attach already succeeded — bookkeeping must never undo it. A key that
    never went through plan mode has nothing to annotate, and that is not an error."""
    import export_plan
    import plan_state

    monkeypatch.setattr(plan_state, "DIR", tmp_path)
    monkeypatch.setattr(plan_state, "FILE", tmp_path / "empty.json")

    class _R:
        returncode = 0
        stdout = "[mock-jira] attached to NO-STATE: out/x.pdf"
        stderr = ""

    monkeypatch.setattr(export_plan, "export", lambda k, f, *a: tmp_path / "p.pdf")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _R())
    assert "attached to NO-STATE" in export_plan.attach_to_jira("NO-STATE", "pdf")


def test_no_caller_double_records_the_link():
    """One recording site. A caller that also calls mark_linked would append a second
    history entry for a single attach."""
    for f in ("bin/qa.py", "bin/dashboard_server.py"):
        src = (ROOT / f).read_text(encoding="utf-8")
        assert "mark_linked" not in src, \
            f"{f} must let attach_to_jira record the reference, not do it itself"


# ------------------------------- OpenHands conversation tracking (user bug)

def test_a_launched_conversation_is_visible_without_any_webhook(tmp_path, monkeypatch):
    """Launching an agent created a real conversation in OpenHands and then lost it:
    /api/openhands is webhook-fed, and the webhook only arrives if OpenHands can
    reach a receiver we own. Users saw "conversation created" and had no way back
    to work they had started. The launch itself must be the first record."""
    import openhands_events as ev
    monkeypatch.setattr(ev, "FILE", tmp_path / "state.json")

    assert ev.summary() == []
    ev.record_launch("conv-abc", url="https://oh.example/c/conv-abc",
                     key="PROJ-301", title="AI-QE agent: test-plan PROJ-301",
                     source="agent:test-plan")
    rows = ev.summary()
    assert len(rows) == 1
    assert rows[0]["conversation_id"] == "conv-abc"
    assert rows[0]["status"] == "launched"
    # The URL is the point — an id the user cannot click through to is not tracking.
    assert rows[0]["url"] == "https://oh.example/c/conv-abc"
    assert rows[0]["key"] == "PROJ-301"


def test_webhook_events_enrich_the_launch_record_instead_of_duplicating(tmp_path, monkeypatch):
    import openhands_events as ev
    monkeypatch.setattr(ev, "FILE", tmp_path / "state.json")

    ev.record_launch("conv-1", url="https://oh.example/c/conv-1", key="PROJ-301")
    ev.record_conversation({"conversation_id": "conv-1", "status": "finished"})
    rows = ev.summary()
    assert len(rows) == 1, "the webhook must update the launch row, not add a second"
    assert rows[0]["status"] == "finished" and rows[0]["terminal"] is True
    assert rows[0]["url"] == "https://oh.example/c/conv-1", "URL survives enrichment"


def test_launch_never_regresses_a_status_the_webhooks_established(tmp_path, monkeypatch):
    """A retried or duplicate launch record must not un-finish a completed run."""
    import openhands_events as ev
    monkeypatch.setattr(ev, "FILE", tmp_path / "state.json")
    ev.record_conversation({"conversation_id": "c9", "status": "finished"})
    ev.record_launch("c9", url="https://oh.example/c/c9")
    assert ev.summary()[0]["status"] == "finished"
    assert ev.record_launch("") == {}, "a blank conversation id records nothing"


def test_every_launch_path_records_the_conversation():
    """Three entry points start conversations. All must record, or the bug returns
    on whichever one was missed."""
    for f in ("bin/dashboard_server.py", "bin/qa.py"):
        src = (ROOT / f).read_text(encoding="utf-8")
        starts = src.count("openhands_client.start(")
        records = src.count("openhands_events.record_launch(")
        assert records >= starts, (
            f"{f}: {starts} conversation launch(es) but only {records} recorded")


def test_the_tracker_card_is_not_confined_to_one_view():
    """The card was only in the Runs view, so an agent launched from Test plans left
    the user on a page that could not show it."""
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert src.count('class="card hidden oh-card"') >= 2, \
        "the OpenHands card must appear where agents are launched, not only in Runs"
    assert "document.querySelectorAll('.oh-card')" in src, \
        "refreshOpenHands must populate every card, not a single id"


# ------------------------- integration check after a factory reset (user bug)

def test_configured_scm_with_an_empty_registry_is_not_reported_unconfigured(monkeypatch):
    """A factory reset empties the registry and leaves .env untouched. The SCM check
    needed a repo to probe, found none, and returned `skipped` — which the UI renders
    as "not configured". Intact Stash credentials read as deleted, while JIRA and
    OpenHands (which never consult the registry) still reported connected."""
    import integration_check, registry as reg_mod
    for k, v in (("SCM_KIND", "stash"), ("STASH_TOKEN", "t"),
                 ("STASH_URL", "https://stash.example.com"), ("STASH_PROJECT", "QE")):
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(reg_mod, "load_registry",
                        lambda *a, **k: {"source_repositories": [],
                                         "test_repositories": []})
    r = integration_check.check_scm()
    assert r["status"] == "ok", "configured credentials must not read as 'not configured'"
    assert "credentials configured" in r["detail"]
    assert "no source repositories" in r["detail"]
    assert r["hint"], "tell the user how to get a full end-to-end verification"


def test_an_actually_unconfigured_scm_still_reports_not_configured(monkeypatch):
    """The fix must not turn a genuinely unconfigured SCM green."""
    import integration_check, registry as reg_mod
    monkeypatch.setenv("SCM_KIND", "stash")
    for k in ("STASH_TOKEN", "STASH_URL", "STASH_PROJECT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(reg_mod, "load_registry",
                        lambda *a, **k: {"source_repositories": []})
    r = integration_check.check_scm()
    assert r["status"] == "skipped" and "not set" in r["detail"]


def test_factory_reset_does_not_touch_env_credentials():
    """Pins the fact behind the bug report: the credentials were never deleted."""
    src = (ROOT / "engine/lib/demo_data.py").read_text(encoding="utf-8")
    assert '".env"' not in src and "'.env'" not in src, \
        "demo_data must never delete .env — credentials survive every reset"

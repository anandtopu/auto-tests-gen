"""JCTS-S4 rich plan/delivery comments and fused-ticket delivery."""
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import pr_comment
import spec_store
import ticket_comment_render
import work_queue


def _structured(count=3):
    return {"key": "PROJ-410", "scenarios": [
        {"id": f"PROJ-410-S{i}", "title": f"boundary case {i}",
         "layer": "api", "target_repo": "e2e-api-tests-1",
         "steps": {"given": "state", "when": "action", "then": "result"},
         "verification": ["status and body"]}
        for i in range(1, count + 1)
    ], "open_questions": []}


def test_spec_store_marks_arbiter_additions_and_renders_the_ticket_plan(tmp_path):
    original = tmp_path / "original.json"
    folded = tmp_path / "folded.json"
    source = _structured(1)
    original.write_text(json.dumps(source), encoding="utf-8")
    folded.write_text(json.dumps({"scenarios": [
        {"id": "PROJ-410-S1", "title": "boundary case 1", "layer": "api",
         "target_repo": "e2e-api-tests-1"},
        {"id": "PROJ-410-S2", "title": "authorization failure", "layer": "api",
         "target_repo": "e2e-api-tests-1"}], "open_questions": []}),
        encoding="utf-8")

    spec_store.merge_fold(original, folded)
    merged = json.loads(original.read_text(encoding="utf-8"))
    assert merged["scenarios"][0].get("adversary_added") is not True
    assert merged["scenarios"][1]["adversary_added"] is True
    text = spec_store.render_comment("PROJ-410", merged, target="PROJ-410")
    assert "PROJ-410-S1 - boundary case 1 (api -> e2e-api-tests-1)" in text
    assert "PROJ-410-S2 - authorization failure" in text
    assert "added by adversarial review" in text
    assert "make plan-approve KEY=PROJ-410" in text


def test_plan_comment_truncation_is_whole_line_and_honest():
    text = spec_store.render_comment("PROJ-410", _structured(20), max_chars=420)
    assert len(text) <= 420
    shown = len(re.findall(r"^- PROJ-410-S\d+ -", text, re.MULTILINE))
    omitted = re.search(r"^- (\d+) more scenarios", text, re.MULTILINE)
    assert omitted and shown + int(omitted.group(1)) == 20
    assert "full plan attached/linked" in text
    tiny = spec_store.render_comment("PROJ-410", _structured(20), max_chars=256)
    assert len(tiny) <= 256 and not tiny.endswith("make plan-app")
    assert "- 20 more scenarios" in tiny
    long_key = "K" * 200
    minimal = spec_store.render_comment(
        long_key, _structured(20), max_chars=256, target="T" * 128)
    assert len(minimal) <= 256 and "20 scenarios omitted" in minimal


def test_legacy_plan_and_flag_off_preserve_the_exact_summary(monkeypatch):
    fallback = "AI-QE authored a test plan - awaiting review/approval."
    monkeypatch.setenv("AIQE_TICKET_COMMENTS_RICH", "0")
    assert ticket_comment_render.plan_body("PROJ-410", "PROJ-410", fallback) == fallback


def test_rich_render_failure_falls_back_and_is_visible(monkeypatch, capsys):
    monkeypatch.setenv("AIQE_TICKET_COMMENTS_RICH", "1")
    monkeypatch.setattr(spec_store, "render_comment",
                        lambda *_args, **_kwargs: 1 / 0)
    fallback = "legacy summary"
    assert ticket_comment_render.plan_body("K-1", "K-1", fallback) == fallback
    assert "rich plan rendering degraded (ZeroDivisionError)" in capsys.readouterr().err
    monkeypatch.setenv("AIQE_TICKET_COMMENTS_RICH", "1")
    monkeypatch.setattr(spec_store, "load", lambda _key: {
        "scenarios": [{"id": "S1", "title": "free form", "layer": "api",
                       "target_repo": "repo"}]})
    assert ticket_comment_render.plan_body("PROJ-410", "PROJ-410", fallback) == fallback


def test_org_comment_bound_defaults_and_never_exceeds_jira_ceiling(tmp_path):
    cfg = tmp_path / "org.yaml"
    cfg.write_text("comments:\n  max_chars: 12000\n", encoding="utf-8")
    assert ticket_comment_render.max_chars(cfg) == 12000
    cfg.write_text("comments:\n  max_chars: 999999\n", encoding="utf-8")
    assert ticket_comment_render.max_chars(cfg) == 32767
    cfg.write_text("comments:\n  max_chars: false\n", encoding="utf-8")
    assert ticket_comment_render.max_chars(cfg) == 8000


def _projection():
    return pr_comment.delivery_projection(
        {"impact": "create", "areas": ["refund boundary"], "risk": "high"},
        {"tests": [
            {"file": "suites/refund-create.spec.js", "action": "created",
             "scenario_id": "PROJ-410-S1"},
            {"file": "suites/refund-update.spec.js", "action": "updated",
             "scenario_id": "PROJ-410-S2"}], "open_questions": []},
        {"passed": 2, "failed": 0},
        [{"repo": "e2e-api-tests-1", "status": "committed", "exit_code": 0,
          "sha": "abcdef123"},
         {"repo": "e2e-ui-tests-1", "status": "no_changes", "exit_code": 0,
          "sha": ""},
         {"repo": "e2e-api-tests-2", "status": "quarantined", "exit_code": 5,
          "sha": ""}],
        {"score": 0.88, "verdict": "accept"}, None,
        [{"cost_usd": 0.25, "cost_basis": "reported"},
         {"cost_usd": 0.10, "cost_basis": "estimated"},
         {"cost_usd": None, "cost_basis": "unknown"}],
        "run-410", "PROJ-410")


def test_pr_and_ticket_renderers_consume_one_projection_with_full_fidelity():
    projection = _projection()
    pr_text = pr_comment.render_pr(projection)
    ticket_text = pr_comment.render_ticket(projection)
    assert projection["created"] == 1 and projection["updated"] == 1
    assert "1 created · 1 updated" in pr_text
    assert "Tests: 1 created, 1 updated" in ticket_text
    assert "suites/refund-create.spec.js [PROJ-410-S1] - created" in ticket_text
    assert "COMMITTED on test/PROJ-410-ai-qe at abcdef1" in ticket_text
    assert "NO_CHANGES (nothing committed)" in ticket_text
    assert "QUARANTINED (gate exit 5)" in ticket_text
    for text in (pr_text, ticket_text):
        assert "$0.25 (reported)" in text
        assert "~$0.10 (estimated)" in text
        assert "unknown (1 row; amount not established)" in text
    assert "$0.35" not in pr_text + ticket_text, "different bases must never blend"


def test_ticket_refusal_names_reason_fix_source_pr_and_target():
    projection = pr_comment.refusal_projection(
        "run-r", "PR-orders-api-201", "budget envelope exceeded",
        "reduce scope, then retry", target="PROJ-301", pr_ref="orders-api#201",
        cost_rows=[{"cost_usd": 0.12, "cost_basis": "simulated"}])
    text = pr_comment.render_ticket(projection)
    assert text.startswith("AI-QE delivery for PROJ-301")
    assert "Source PR: orders-api#201" in text
    assert "Delivery: REFUSED" in text
    assert "Reason: budget envelope exceeded" in text
    assert "Fix: reduce scope, then retry" in text
    assert "Cost: ~$0.12 (simulated)" in text


def test_projection_defends_malformed_contracts_and_plain_text_controls():
    projection = pr_comment.delivery_projection(
        [], "bad", None,
        [{"repo": "repo\nspoof", "status": "clone_failed", "exit_code": 3}],
        None, None, [], "run\x00id", "K-1")
    text = pr_comment.render_ticket(projection)
    assert "repo spoof - CLONE_FAILED (clone exit 3)" in text
    assert "\x00" not in text and "run id" in text


def test_delivery_truncation_is_bounded_and_declares_omissions():
    projection = _projection()
    projection["tests"] = [
        {"file": f"suites/very-long-test-{i}-" + "x" * 80 + ".spec.js",
         "action": "created", "scenario_id": f"S{i}"}
        for i in range(30)]
    projection["created"] = 30
    text = pr_comment.render_ticket(projection, max_chars=500)
    assert len(text) <= 500
    assert re.search(r"\.\.\. \d+ more lines omitted", text)


def test_pipeline_wires_flagged_fused_delivery_before_run_record():
    source = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert "AIQE_TICKET_COMMENTS_RICH" in (
        ROOT / ".env.example").read_text(encoding="utf-8")
    assert "TICKET_RICH_ENABLED" in source
    assert 'if [ "$PR_TICKET_FUSED" = "1" ]' in source
    refusal = source[source.index('if [ "$REVIEW_POLICY_RC" -eq 78 ]'):]
    refusal = refusal[:refusal.index('elif [ "$REVIEW_POLICY_RC" -ne 0 ]')]
    assert refusal.index("TICKET_DELIVERY_COMMENT") < refusal.index("write_run_record")
    assert "delivery_projection" in pathlib.Path(pr_comment.__file__).read_text(
        encoding="utf-8")


def test_mock_fused_pr_posts_rich_delivery_to_the_selected_ticket():
    log = ROOT / "out/mock-comments.log"
    old = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    env = {**os.environ, "AIQE_MOCK": "1", "AIQE_PR_TICKET_CONTEXT": "1",
           "AIQE_TICKET_COMMENTS_RICH": "1", "AIQE_GENERATE_FANOUT": "0",
           "AIQE_PHASE_CACHE": "0"}
    result = subprocess.run(
        [work_queue.bash_exe(), "engine/pipeline.sh", "pr", "orders-api", "201"],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", stdin=subprocess.DEVNULL, timeout=600, check=False)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-1000:]
    added = log.read_text(encoding="utf-8", errors="replace")[len(old):]
    # The guarantee is that the rich delivery REACHED the ticket — not which
    # verb carried it. Once a delivery comment exists for this key, JCTS-S5
    # idempotency correctly UPDATES it instead of posting a duplicate, so
    # asserting "<-" (post) made this test pass only against a virgin out/ and
    # fail on any estate that had commented before — including this one, after
    # a single `make demo-pr`. That is an order-dependent test, not a defect in
    # the feature. Post-vs-update is pinned where it belongs, in the accounting
    # tests; here we assert delivery.
    assert ("[mock-jira] PROJ-301 <- AI-QE delivery for PROJ-301" in added
            or "[mock-jira] PROJ-301 updated" in added), (
        f"no rich delivery reached PROJ-301 by either verb; log tail: {added[-400:]}")
    assert "Source PR: orders-api#201" in added
    assert "[PR-orders-api-201-S1]" in added
    record = json.loads(max((ROOT / "reports/runs").glob("*.json"),
                            key=lambda path: path.stat().st_mtime)
                        .read_text(encoding="utf-8"))
    assert any(c["kind"] == "delivery" and c["target"] == "PROJ-301"
               for c in record["comments"])


def test_mock_plan_posts_structured_scenarios_and_adversary_marker(tmp_path):
    log = ROOT / "out/mock-comments.log"
    old = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    env = {**os.environ, "AIQE_MOCK": "1", "AIQE_TICKET_COMMENTS_RICH": "1",
           "AIQE_PHASE_CACHE": "0", "AIQE_PLAN_DIR": str(tmp_path / "plans"),
           "AIQE_TESTPLAN_DIR": str(tmp_path / "testplans"),
           "AIQE_SPEC_DIR": str(tmp_path / "specs"),
           "AIQE_TESTDATA_DIR": str(tmp_path / "testdata")}
    result = subprocess.run(
        [work_queue.bash_exe(), "engine/pipeline.sh", "plan", "PROJ-301"],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", stdin=subprocess.DEVNULL, timeout=600, check=False)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-1000:]
    added = log.read_text(encoding="utf-8", errors="replace")[len(old):]
    assert "[mock-jira] PROJ-301 <- AI-QE test plan for PROJ-301" in added
    assert "PROJ-301-S1 - boundary rejection" in added
    assert "PROJ-301-S3 - discount POST without orders:write scope" in added
    assert "added by adversarial review" in added
    assert "Approve with: make plan-approve KEY=PROJ-301" in added

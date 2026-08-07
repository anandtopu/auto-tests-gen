"""B3 reviewer delivery policy: consequence, refusal evidence, and boundaries."""
import json
import pathlib
import subprocess
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import pr_comment
import run_progress
import test_reviewer as reviewer
import work_queue


def _finding():
    return {
        "repo": "api-tests", "severity": "high",
        "category": "missing_coverage", "file": "suites/refund.spec.ts",
        "test": "refund ceiling", "finding": "No captured-total boundary.",
        "fix": "Add the equal and over-captured cases.",
    }


def _signal(verdict="needs_work"):
    findings = [_finding()] if verdict == "needs_work" else []
    local = [{k: v for k, v in finding.items() if k != "repo"}
             for finding in findings]
    return reviewer.normalize_merged_contract({
        "artifact": "test-reviewer", "schema": 1, "state": "reviewed",
        "verdict": verdict,
        "repos": [{"repo": "api-tests", "state": "reviewed",
                   "verdict": verdict, "findings": local, "simulated": False}],
        "findings": findings, "simulated": False,
    })


@pytest.mark.parametrize(("value", "expected"), [
    ("off", "off"), ("warn", "warn"), ("require", "require"),
    ("typo", "warn"), (None, "warn"),
])
def test_policy_is_closed_and_defaults_to_warn(value, expected):
    assert reviewer.policy({"agent_gate": value}) == expected


def test_off_and_require_outrank_the_per_run_rollout_flag(monkeypatch):
    monkeypatch.setenv("AIQE_TEST_REVIEWER", "1")
    assert reviewer.enabled({"agent_gate": "off", "enabled": True}) is False
    monkeypatch.setenv("AIQE_TEST_REVIEWER", "0")
    assert reviewer.enabled({"agent_gate": "require", "enabled": False}) is True
    assert reviewer.enabled({"agent_gate": "warn", "enabled": True}) is False


@pytest.mark.parametrize(("policy", "verdict", "outage", "outcome"), [
    ("off", "skipped", "proceed", "proceed"),
    ("warn", "needs_work", "proceed", "proceed"),
    ("require", "approve", "proceed", "proceed"),
    ("require", "needs_work", "proceed", "refused"),
    ("require", "unavailable", "proceed", "proceed"),
    ("require", "unavailable", "hold", "refused"),
])
def test_delivery_matrix(policy, verdict, outage, outcome):
    signal = None if verdict in {"skipped", "unavailable"} else _signal(verdict)
    value = reviewer.delivery(
        signal,
        cfg={"agent_gate": policy, "on_unavailable": outage,
             "enabled": verdict != "skipped"},
        assume_enabled=verdict != "skipped",
    )
    assert value["outcome"] == outcome
    assert value["policy"] == policy
    if outcome == "refused":
        assert value["fixes"] and "fix:" in reviewer.delivery_line(value)


def test_tampered_delivery_cannot_launder_a_required_refusal():
    value = reviewer.delivery(_signal(), cfg={"agent_gate": "require"})
    with pytest.raises(reviewer.ReviewInputError, match="inconsistent"):
        reviewer.normalize_delivery({**value, "outcome": "proceed"})


def test_final_surface_not_raw_verdict_decides_delivery(monkeypatch):
    monkeypatch.setattr(reviewer, "surface", lambda *args, **kwargs: {
        "verdict": "needs_work", "findings": [_finding()],
    })
    value = reviewer.delivery(_signal("approve"), cfg={"agent_gate": "require"})
    assert value["outcome"] == "refused"


def test_refusal_summary_is_single_line():
    value = reviewer.delivery(_signal(), cfg={"agent_gate": "require"})
    value["fixes"] = ["first line\nforged line"]
    line = reviewer.delivery_line(value)
    assert "\n" not in line and "first line forged line" in line


def test_required_refusal_is_before_the_gate():
    source = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    enforce = source.index("test_reviewer.py enforce")
    critic = source.index("# Critic (§5.8.7)", enforce)
    gate = source.index(": > out/gate_results.tsv", enforce)
    assert enforce < critic < gate
    refusal = source[source.index('if [ "$REVIEW_POLICY_RC" -eq 78 ]'):critic]
    assert "run_record.py" in refusal and "exit 78" in refusal
    assert "review_state.py auto" not in refusal


def test_default_disabled_reviewer_creates_no_delivery_sidecar():
    source = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    decision = source[source.index("rm -f out/review-delivery.json"):
                      source.index('if [ "$REVIEW_POLICY_RC" -eq 78 ]')]
    assert "if [ -f out/reviewer.contract.json ]; then" in decision
    assert decision.index("if [ -f out/reviewer.contract.json ]; then") < (
        decision.index("test_reviewer.py enforce"))


def test_the_gate_never_reads_reviewer_delivery():
    for path in (ROOT / "engine/gate").glob("*"):
        if path.is_file():
            source = path.read_text(encoding="utf-8", errors="replace").lower()
            assert "reviewer" not in source and "review-delivery" not in source


def test_reviewer_stays_read_only_and_never_moves_human_status():
    cfg = yaml.safe_load((ROOT / "registry/org-config.yaml").read_text(
        encoding="utf-8"))
    assert cfg["phases"]["reviewer"]["allowed_tools"] == "Read"
    source = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    refusal = source[source.index('if [ "$REVIEW_POLICY_RC" -eq 78 ]'):
                     source.index("# Critic (§5.8.7)")]
    assert "review_state.py" not in refusal


def test_critic_and_reviewer_constitution_rules_stay_distinct():
    clauses = yaml.safe_load((ROOT / "specs/platform/constitution.yaml").read_text(
        encoding="utf-8"))["clauses"]
    by_id = {clause["id"]: clause for clause in clauses}
    assert "critic is advisory" in by_id["C2"]["statement"]
    assert "reviewer may stop a run only before" in by_id["C14"]["statement"]
    assert by_id["C2"]["pins"] != by_id["C14"]["pins"]


def test_refusal_run_record_and_pr_comment_name_the_fix(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    signal = _signal()
    (out / "reviewer.contract.json").write_text(json.dumps(signal), encoding="utf-8")
    delivery = reviewer.delivery(signal, cfg={"agent_gate": "require"})
    (out / "review-delivery.json").write_text(json.dumps(delivery), encoding="utf-8")
    (out / "generate.contract.json").write_text(json.dumps({
        "tests": [{"file": "suites/refund.spec.ts", "action": "created"}],
        "open_questions": [],
    }), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "engine/lib/run_record.py"),
         "RID-B3", "pr", "PR-orders-3"],
        cwd=tmp_path, text=True, capture_output=True, stdin=subprocess.DEVNULL,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)
    assert record["overall"] == "review_refused"
    assert record["gates"] == []
    assert record["review_delivery"]["outcome"] == "refused"
    assert record["review"]["findings"] == [_finding()]
    markdown = pr_comment.from_record(record)
    assert "refused before the deterministic gate" in markdown
    assert "Fix: Add the equal and over-captured cases." in markdown
    steps = run_progress._steps_from_record(record, root=tmp_path)
    assert next(s for s in steps if s["id"] == "review")["state"] == "failed"
    assert next(s for s in steps if s["id"] == "gate")["state"] == "skipped"


def test_settings_explains_consequences_and_no_bypass():
    source = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8").lower()
    settings = source[source.index('<div data-view="settings">'):]
    for phrase in ("off", "warn (default rollout)", "require",
                   "nothing is committed", "not by a per-run bypass",
                   "roll out in two steps"):
        assert phrase in settings
    production = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (ROOT / "engine").rglob("*")
        if path.is_file() and path.suffix in {".py", ".sh"}
    )
    assert "AIQE_REVIEW_AGENT_GATE" not in production


def test_pipeline_shell_remains_valid():
    result = subprocess.run(
        [work_queue.bash_exe(), "-n", "engine/pipeline.sh"],
        cwd=ROOT, text=True, capture_output=True,
        stdin=subprocess.DEVNULL, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_exit_78_is_documented_for_progress():
    assert run_progress.explain_exit(78)[0] == "AGENT_REVIEW_REFUSED"

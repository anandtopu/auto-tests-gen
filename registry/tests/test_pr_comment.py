"""The PR coverage-delta comment (engine/lib/pr_comment.py + the pipeline wiring).

Developers live on the PR, not the dashboard: after a Workflow-A run the PR gets a
comment stating what E2E coverage changed — behaviors, created-vs-updated tests,
validation, gate outcome, open questions — composed purely from the run's out/
artifacts. A no-impact run must produce NO comment, so PRs never accumulate noise.
"""
import json, os, pathlib, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import pr_comment
import work_queue


@pytest.fixture
def rundir(tmp_path):
    (tmp_path / "out").mkdir()

    def write(name, obj):
        (tmp_path / "out" / name).write_text(json.dumps(obj) if not isinstance(obj, str)
                                             else obj, encoding="utf-8")
    write("triage.contract.json",
          {"impact": "create", "areas": ["orders discounts boundary"], "risk": "med",
           "existing_tests": []})
    write("generate.contract.json",
          {"tests": [{"file": "suites/orders/K-b.spec.js", "action": "created"},
                     {"file": "suites/orders/old.spec.js", "action": "updated"}],
           "open_questions": ["stacking behavior undefined"]})
    write("validate.contract.json", {"passed": 3, "failed": 0, "repair_loops": 1})
    write("gate_results.tsv",
          "e2e-api-tests-1\tcommitted\t0\tabc1234\ne2e-ui-tests-1\tno_changes\t0\t\n")
    return tmp_path


def test_comment_carries_the_whole_delta(rundir, monkeypatch):
    monkeypatch.setenv("AIQE_STATUS_URL", "https://qe.example.com")
    text = pr_comment.build(rundir, "42-1", "PR-x-1")
    for needle in ("E2E coverage delta", "orders discounts boundary",
                   "new coverage added (risk: med)",
                   "1 created · 1 updated",
                   "`suites/orders/K-b.spec.js` (created)",
                   "3 passed, 0 failed, 1 repair loop(s)",
                   "✅ e2e-api-tests-1: committed `abc1234`",
                   "➖ e2e-ui-tests-1: no_changes",
                   "stacking behavior undefined",
                   "test/PR-x-1-ai-qe", "run `42-1`",
                   "[dashboard](https://qe.example.com)"):
        assert needle in text, f"missing: {needle}\n{text}"


def test_no_impact_run_produces_no_comment(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out/triage.contract.json").write_text(
        json.dumps({"impact": "none", "areas": []}), encoding="utf-8")
    assert pr_comment.build(tmp_path, "42-2", "PR-x-2") == "", \
        "a run with nothing to say must not add PR noise"


def test_missing_artifacts_are_total(tmp_path):
    """A partially-aborted run (e.g. budget) must not crash the composer."""
    assert pr_comment.build(tmp_path, "42-3", "PR-x-3") == ""


def test_long_test_lists_are_truncated(rundir):
    tests = [{"file": f"suites/t{i}.spec.js", "action": "created"} for i in range(12)]
    (rundir / "out/generate.contract.json").write_text(
        json.dumps({"tests": tests, "open_questions": []}), encoding="utf-8")
    text = pr_comment.build(rundir, "42-4", "PR-x-4")
    assert "… and 4 more" in text
    assert text.count("- `suites/") == 8, "never more than 8 file bullets on a PR"


def test_near_duplicate_warning_names_case_and_remains_advisory(rundir):
    artifact = {"artifact": "duplicate-warnings", "warnings": [{
        "proposal": {"id": "BUG-9-S1"}, "retrieval_mode": "lexical",
        "similarity": 0.91, "existing_case": {
            "test_repo": "e2e-api-tests-1", "file": "suites/discount.spec.js",
            "suite": ["orders"], "title": "PROJ-1: expired discount"}}]}
    (rundir / "out/duplicate-warnings.json").write_text(
        json.dumps(artifact), encoding="utf-8")
    text = pr_comment.build(rundir, "42-dup", "PR-x-dup")
    assert "Near-duplicate warnings (advisory only)" in text
    assert "e2e-api-tests-1/suites/discount.spec.js" in text
    assert "PROJ-1: expired discount" in text
    assert "suite `orders`" in text
    assert "did not block validation, generation, or the gate" in text


def test_cost_line_appears_only_when_metered(rundir):
    (rundir / "out/cost.tsv").write_text("triage\t0.200000\t1\t0\n", encoding="utf-8")
    assert "💰 run cost: $0.20" in pr_comment.build(rundir, "1", "K")
    (rundir / "out/cost.tsv").write_text("triage\t0.000000\t0\t0\n", encoding="utf-8")
    assert "💰" not in pr_comment.build(rundir, "1", "K")


def test_quarantined_run_says_so_without_claiming_a_branch(rundir):
    (rundir / "out/gate_results.tsv").write_text(
        "e2e-api-tests-1\tquarantined\t5\t\n", encoding="utf-8")
    text = pr_comment.build(rundir, "42-5", "PR-x-5")
    assert "❌ e2e-api-tests-1: quarantined" in text
    assert "review them on the" not in text, \
        "must not point at a commit branch when nothing committed"


def test_pipeline_posts_the_comment_best_effort():
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert "pr_comment.py" in src
    i = src.index("pr_comment.py")
    assert 'SCM comment "$REPO" "$PR"' in src[i:i + 400]
    assert "|| true" in src[i:i + 400], "a failed comment must never fail the run"
    assert src.index("gate_results.tsv") < i, "comment must be composed AFTER the gates"


def test_demo_pr_end_to_end_posts_the_delta_comment():
    env = {**os.environ, "AIQE_MOCK": "1"}
    r = subprocess.run([work_queue.bash_exe(), "engine/pipeline.sh", "pr",
                        "orders-api", "201"], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, env=env, timeout=900)
    assert r.returncode == 0, r.stdout[-500:]
    assert "comment on orders-api#201" in r.stdout
    assert "E2E coverage delta" in r.stdout
    assert "critic (advisory)" in r.stdout

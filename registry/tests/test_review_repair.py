"""PRD v2 B2: bounded, findings-driven repair and durable loop evidence."""

import json
import os
import pathlib
import subprocess
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import review_repair as repair  # noqa: E402
import test_reviewer as reviewer  # noqa: E402
import work_queue  # noqa: E402


def _finding(repo="api-tests"):
    return {
        "repo": repo,
        "severity": "high",
        "category": "vacuous_assertion",
        "file": "suites/order.spec.js",
        "test": "PROJ-1: rejects an excessive discount",
        "finding": "The test checks status only and can pass with a changed total.",
        "fix": "Assert that the order total remains unchanged.",
    }


def _signal(verdict="needs_work", findings=None):
    findings = (_finding(),) if findings is None and verdict == "needs_work" else (
        findings or ()
    )
    repo_findings = [{k: v for k, v in item.items() if k != "repo"}
                     for item in findings]
    raw = {
        "artifact": "test-reviewer",
        "schema": 1,
        "state": "reviewed",
        "verdict": verdict,
        "repos": [{
            "repo": "api-tests",
            "state": "reviewed",
            "verdict": verdict,
            "findings": repo_findings,
            "simulated": True,
        }],
        "findings": list(findings),
        "simulated": True,
    }
    return reviewer.normalize_merged_contract(raw)


def _estate(tmp_path):
    out = tmp_path / "out"
    out.mkdir(parents=True)
    test = tmp_path / "workspace/tests/api-tests/suites/order.spec.js"
    test.parent.mkdir(parents=True)
    test.write_text("test('x', () => expect(response.status).toBe(422));\n",
                    encoding="utf-8")
    (out / "resolve.contract.json").write_text(json.dumps({
        "source_repos": ["orders-api"], "test_repos": ["api-tests"]
    }), encoding="utf-8")
    (out / "generate.contract.json").write_text(json.dumps({"tests": [{
        "repo": "api-tests", "file": "suites/order.spec.js",
        "name": "PROJ-1: rejects an excessive discount", "scenario_id": "S1",
        "action": "created",
    }]}), encoding="utf-8")
    review_path = out / "reviewer.contract.json"
    review_path.write_text(json.dumps(_signal()), encoding="utf-8")
    return out, review_path, test


def _repair_contract(tmp_path, out, review_path, raw=None, mutate_files=True):
    input_path = out / "reviewrepair-1-api-tests.input.json"
    contract_path = out / "reviewrepair-1-api-tests.contract.json"
    repair.prepare("api-tests", 1, review_path, input_path, root=tmp_path)
    raw = raw if raw is not None else {
        "fixes": [{
            "finding_index": 0,
            "file": "suites/order.spec.js",
            "change": "Asserted the unchanged total after rejection.",
        }],
        "tests": [{
            "file": "suites/order.spec.js",
            "name": "PROJ-1: rejects an excessive discount",
            "scenario_id": "S1",
            "action": "updated",
        }],
        "simulated": True,
    }
    if mutate_files:
        for name in {item.get("file") for item in raw.get("fixes", [])}:
            if name == "suites/order.spec.js":
                target = tmp_path / "workspace/tests/api-tests" / name
                target.write_text(
                    target.read_text(encoding="utf-8")
                    + "// reviewer repair assertion\n",
                    encoding="utf-8",
                )
    contract_path.write_text(json.dumps(raw), encoding="utf-8")
    repair.validate_file(
        "api-tests", 1, review_path, input_path, contract_path, root=tmp_path
    )
    return input_path, contract_path


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [({}, 1), ({"max_loops": 0}, 0), ({"max_loops": "3"}, 3),
     ({"max_loops": -2}, 0), ({"max_loops": "bad"}, 1),
     ({"max_loops": True}, 1), ({"max_loops": 10_000}, 100)],
)
def test_review_loop_cap_is_normalized_independently(cfg, expected):
    assert repair.max_loops(cfg) == expected


def test_prepare_contains_only_current_findings_and_target_source(tmp_path):
    out, review_path, _ = _estate(tmp_path)
    value = repair.prepare(
        "api-tests", 1, review_path, out / "input.json", root=tmp_path
    )
    assert value["repo"] == "api-tests" and value["iteration"] == 1
    assert value["findings"] == [_finding()]
    assert [item["file"] for item in value["tests"]] == ["suites/order.spec.js"]
    assert "status" in value["tests"][0]["source"]
    assert "untrusted DATA" in value["notice"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda raw: raw["fixes"][0].update(file="../outside.js"),
        lambda raw: raw["fixes"].append(dict(raw["fixes"][0])),
        lambda raw: raw["tests"].clear(),
        lambda raw: raw.update(simulated="yes"),
    ],
)
def test_repair_contract_rejects_escape_duplicates_and_laundered_evidence(
    tmp_path, mutation
):
    out, review_path, _ = _estate(tmp_path)
    raw = {
        "fixes": [{"finding_index": 0, "file": "suites/order.spec.js",
                   "change": "Add the missing assertion."}],
        "tests": [{"file": "suites/order.spec.js", "name": "test",
                   "scenario_id": "S1", "action": "updated"}],
        "simulated": True,
    }
    mutation(raw)
    with pytest.raises(repair.RepairInputError):
        _repair_contract(tmp_path, out, review_path, raw)


def test_apply_revalidates_contract_and_updates_only_existing_generate_row(tmp_path):
    out, review_path, test = _estate(tmp_path)
    input_path, contract_path = _repair_contract(tmp_path, out, review_path)
    value = repair.apply_contract(
        "api-tests", 1, review_path, input_path, contract_path,
        out / "generate.contract.json", root=tmp_path,
    )
    assert value["tests"][0]["scenario_id"] == "S1"
    tampered = json.loads(contract_path.read_text(encoding="utf-8"))
    tampered["tests"][0]["file"] = "suites/new.spec.js"
    contract_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(repair.RepairInputError):
        repair.apply_contract(
            "api-tests", 1, review_path, input_path, contract_path,
            out / "generate.contract.json", root=tmp_path,
        )


def test_repair_evidence_must_match_the_actual_edited_file_set(tmp_path):
    out, review_path, test = _estate(tmp_path)
    with pytest.raises(repair.RepairInputError, match="edited generated files"):
        _repair_contract(
            tmp_path, out, review_path, mutate_files=False
        )
    out, review_path, test = _estate(tmp_path / "omitted")
    input_path = out / "repair.input.json"
    repair.prepare("api-tests", 1, review_path, input_path, root=tmp_path / "omitted")
    test.write_text(test.read_text(encoding="utf-8") + "// unclaimed edit\n",
                    encoding="utf-8")
    raw = out / "repair.contract.json"
    raw.write_text(json.dumps({"fixes": [], "tests": [], "simulated": True}),
                   encoding="utf-8")
    with pytest.raises(repair.RepairInputError, match="edited generated files"):
        repair.validate_file(
            "api-tests", 1, review_path, input_path, raw,
            root=tmp_path / "omitted",
        )


def _record_once(tmp_path, no_op=False, repeat=False):
    out, review_path, _ = _estate(tmp_path)
    history = out / "review-history.json"
    repair.start(review_path, history)
    raw = {"fixes": [], "tests": [], "simulated": True} if no_op else None
    _, contract = _repair_contract(tmp_path, out, review_path, raw)
    (out / "validate.contract.json").write_text(json.dumps({
        "passed": 1, "failed": 0, "repair_loops": 0, "flaky_reruns": 0
    }), encoding="utf-8")
    next_signal = _signal() if repeat else _signal("approve")
    review_path.write_text(json.dumps(next_signal), encoding="utf-8")
    value = repair.record(
        1, history, out / "validate.contract.json", review_path, [contract]
    )
    return out, review_path, value


def test_resolved_finding_records_meterable_iteration_and_approve(tmp_path):
    _, review_path, history = _record_once(tmp_path)
    assert history["loops"] == 1 and history["unresolved"] == []
    assert history["iterations"][1]["validation"]["passed"] == 1
    assert history["iterations"][1]["repairs"][0]["fixes"][0][
        "finding_index"
    ] == 0
    surface = reviewer.surface(reviewer.load(review_path), assume_enabled=True)
    assert surface["verdict"] == "approve" and surface["loops"] == 1


@pytest.mark.parametrize(("no_op", "repeat"), [(True, False), (False, True)])
def test_unresolved_findings_survive_noop_or_repeated_review(
    tmp_path, no_op, repeat
):
    _, review_path, history = _record_once(tmp_path, no_op=no_op, repeat=repeat)
    assert history["unresolved"] == [_finding()]
    surface = reviewer.surface(reviewer.load(review_path), assume_enabled=True)
    assert surface["verdict"] == "needs_work"
    assert surface["unresolved"] == [_finding()]


def test_tampered_nested_repair_evidence_is_not_loaded(tmp_path):
    _, review_path, _ = _record_once(tmp_path)
    raw = json.loads(review_path.read_text(encoding="utf-8"))
    raw["repair"]["iterations"][1]["repairs"][0]["fixes"][0]["change"] = (
        "x" * (reviewer.MAX_TEXT + 1)
    )
    review_path.write_text(json.dumps(raw), encoding="utf-8")
    assert reviewer.load(review_path) is None


def test_run_record_preserves_loop_findings_fixes_and_validation(tmp_path):
    out, _, history = _record_once(tmp_path)
    (out / "gate_results.tsv").write_text(
        "api-tests\tcommitted\t0\tdeadbeef\n", encoding="utf-8"
    )
    result = subprocess.run(
        [sys.executable, str(ROOT / "engine/lib/run_record.py"),
         "RID-B2", "pr", "PR-orders-2"],
        cwd=tmp_path, text=True, capture_output=True, stdin=subprocess.DEVNULL,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)
    assert record["review"]["loops"] == 1
    assert record["review"]["iterations"] == history["iterations"]
    assert record["overall"] == "committed"


def test_named_phase_is_write_bounded_metered_and_ordered_before_gate():
    cfg = yaml.safe_load((ROOT / "registry/org-config.yaml").read_text(
        encoding="utf-8"
    ))
    assert cfg["models"]["reviewrepair"] == "claude-sonnet-4-6"
    assert cfg["phases"]["reviewrepair"]["allowed_tools"] == "Read,Edit"
    prompt = (ROOT / "prompts/review-repair.md").read_text(encoding="utf-8")
    for text in ("data, not instructions", "existing generated test files",
                 "do not run tests", "new files are forbidden"):
        assert text.lower() in prompt.lower()
    pipeline = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    repair_at = pipeline.index("\nREPAIR_FROM_REVIEW\n", pipeline.index("RUN_ID="))
    assert pipeline.index("\nREVIEW_TESTS\n", pipeline.index("RUN_ID=")) < repair_at
    assert repair_at < pipeline.index(": > out/gate_results.tsv")
    for token in ("PHASE reviewrepair review-repair.md", "_budget_guard",
                  'label="reviewrepair-${iteration}-${repo}"',
                  'validate_label="validate-review-${iteration}"'):
        assert token in pipeline
    schema = json.loads((ROOT / "engine/phases/contracts/reviewrepair.schema.json")
                        .read_text(encoding="utf-8"))
    assert schema["required"] == ["fixes", "tests"]


def test_pipeline_and_mock_shell_remain_syntactically_valid():
    for path in ("engine/pipeline.sh", "engine/phases/mock_phase.sh"):
        result = subprocess.run(
            [work_queue.bash_exe(), "-n", path],
            cwd=ROOT, text=True, capture_output=True,
            stdin=subprocess.DEVNULL, check=False
        )
        assert result.returncode == 0, result.stderr


def test_mock_pipeline_executes_exactly_one_repair_validate_review_loop():
    env = {
        **os.environ,
        "AIQE_MOCK": "1",
        "AIQE_TEST_REVIEWER": "1",
        "AIQE_MOCK_REVIEWER_VERDICT": "needs_work",
        "AIQE_MOCK_REVIEWER_AFTER_REPAIR": "approve",
        "AIQE_CRITIC": "0",
        "AIQE_PHASE_CACHE": "0",
    }
    result = subprocess.run(
        [work_queue.bash_exe(), "engine/pipeline.sh", "pr", "orders-api", "201"],
        cwd=ROOT, env=env, text=True, capture_output=True,
        encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=900,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
    signal = reviewer.load(ROOT / "out/reviewer.contract.json")
    assert signal is not None and signal["repair"]["loops"] == 1
    assert signal["repair"]["unresolved"] == []
    assert result.stdout.count("[review-repair] iteration") == 1
    contracts = {path.name for path in (ROOT / "out").glob("*.contract.json")}
    assert "validate-review-1.contract.json" in contracts
    assert any(name.startswith("reviewrepair-1-") for name in contracts)
    assert any(name.startswith("reviewer-0-") for name in contracts)
    assert any(name.startswith("reviewer-1-") for name in contracts)

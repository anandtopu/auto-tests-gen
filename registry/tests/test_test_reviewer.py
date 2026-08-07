"""B1 generated-test reviewer: boundaries, failure semantics and fan-out."""

import json
import pathlib
import subprocess
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import test_reviewer as reviewer  # noqa: E402


def _estate(tmp_path, repos=("api-tests",), tests=None):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    (out / "resolve.contract.json").write_text(
        json.dumps({"source_repos": ["orders-api"], "test_repos": list(repos)}),
        encoding="utf-8",
    )
    (out / "generate.contract.json").write_text(
        json.dumps({"tests": tests or []}), encoding="utf-8"
    )
    return out


def _test_file(
    tmp_path,
    repo="api-tests",
    name="suites/order.spec.js",
    source="test('x', () => expect(total).toBe(10));",
):
    path = tmp_path / "workspace/tests" / repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def _finding(**overrides):
    value = {
        "severity": "high",
        "category": "missing_coverage",
        "file": "<missing>",
        "test": "<missing>",
        "finding": "AC-2 has no generated test",
        "fix": "add a generated scenario for AC-2",
    }
    value.update(overrides)
    return value


def test_feature_is_off_by_default_and_environment_overrides(monkeypatch):
    monkeypatch.delenv("AIQE_TEST_REVIEWER", raising=False)
    assert reviewer.enabled({"enabled": False}) is False
    assert reviewer.enabled({"enabled": True}) is True
    monkeypatch.setenv("AIQE_TEST_REVIEWER", "true")
    assert reviewer.enabled({"enabled": False}) is True
    monkeypatch.setenv("AIQE_TEST_REVIEWER", "off")
    assert reviewer.enabled({"enabled": True}) is False
    monkeypatch.setenv("AIQE_TEST_REVIEWER", "garbage")
    assert reviewer.enabled({"enabled": True}) is False


def test_prepare_inlines_only_the_target_repositories_generated_source(tmp_path):
    tests = [
        {
            "repo": "api-tests",
            "file": "suites/a.spec.js",
            "name": "A",
            "scenario_id": "S1",
        },
        {
            "repo": "ui-tests",
            "file": "tests/u.spec.js",
            "name": "U",
            "scenario_id": "S2",
        },
    ]
    _estate(tmp_path, ("api-tests", "ui-tests"), tests)
    _test_file(tmp_path, "api-tests", "suites/a.spec.js", "assert(apiResult)")
    _test_file(tmp_path, "ui-tests", "tests/u.spec.js", "assert(uiResult)")
    value = reviewer.prepare("api-tests", tmp_path)
    assert value["repo"] == "api-tests"
    assert [t["file"] for t in value["tests"]] == ["suites/a.spec.js"]
    assert value["tests"][0]["source"] == "assert(apiResult)"
    assert "untrusted DATA" in value["notice"]


def test_single_repo_accepts_legacy_unstamped_generate_contract(tmp_path):
    _estate(tmp_path, tests=[{"file": "suites/a.spec.js"}])
    _test_file(tmp_path, name="suites/a.spec.js")
    assert (
        reviewer.prepare("api-tests", tmp_path)["tests"][0]["file"]
        == "suites/a.spec.js"
    )


def test_multi_repo_rejects_unstamped_test_instead_of_guessing(tmp_path):
    _estate(tmp_path, ("api-tests", "ui-tests"), [{"file": "suites/a.spec.js"}])
    _test_file(tmp_path, name="suites/a.spec.js")
    with pytest.raises(reviewer.ReviewInputError, match="unstamped"):
        reviewer.prepare("api-tests", tmp_path)


def test_repository_names_cannot_be_used_as_output_paths(tmp_path):
    _estate(tmp_path, ("../outside",), [])
    with pytest.raises(reviewer.ReviewInputError, match="repository name"):
        reviewer.prepare("../outside", tmp_path)
    result = reviewer.merge([("../outside", "reviewed", "")], tmp_path)
    assert result["state"] == "unavailable"
    assert result["repos"][0]["repo"] == "invalid"


@pytest.mark.parametrize("name", ["../secret.txt", "/absolute.spec.js"])
def test_prepare_rejects_paths_outside_target_repo(tmp_path, name):
    _estate(tmp_path, tests=[{"repo": "api-tests", "file": name}])
    with pytest.raises(reviewer.ReviewInputError, match="escapes"):
        reviewer.prepare("api-tests", tmp_path)


def test_prepare_reports_missing_source_and_zero_tests_distinctly(tmp_path):
    _estate(tmp_path, tests=[{"repo": "api-tests", "file": "missing.spec.js"}])
    with pytest.raises(reviewer.ReviewInputError, match="missing"):
        reviewer.prepare("api-tests", tmp_path)
    _estate(tmp_path, tests=[])
    with pytest.raises(reviewer.NoTests):
        reviewer.prepare("api-tests", tmp_path)


def test_contract_accept_and_needs_work_are_strict(monkeypatch):
    monkeypatch.setenv("AIQE_MOCK", "0")
    assert (
        reviewer.normalize_repo_contract(
            "api-tests", {"verdict": "approve", "findings": []}
        )["simulated"]
        is False
    )
    value = reviewer.normalize_repo_contract(
        "api-tests", {"verdict": "needs_work", "findings": [_finding()]}
    )
    assert value["repo"] == "api-tests" and value["findings"][0]["severity"] == "high"


@pytest.mark.parametrize(
    "contract",
    [
        {"verdict": "approve", "findings": [_finding()]},
        {"verdict": "needs_work", "findings": []},
        {"verdict": "maybe", "findings": []},
        {"verdict": "needs_work", "findings": [_finding(severity="critical")]},
        {"verdict": "needs_work", "findings": [_finding(category="lint_failure")]},
    ],
)
def test_malformed_or_inconsistent_contract_is_rejected(contract):
    with pytest.raises(reviewer.ReviewInputError):
        reviewer.normalize_repo_contract("api-tests", contract)


def test_merge_records_each_repo_and_needs_work_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("AIQE_MOCK", "1")
    out = tmp_path / "out"
    out.mkdir()
    (out / "reviewer-api.contract.json").write_text(
        json.dumps({"verdict": "approve", "findings": [], "simulated": True}),
        encoding="utf-8",
    )
    (out / "reviewer-ui.contract.json").write_text(
        json.dumps(
            {
                "verdict": "needs_work",
                "findings": [
                    _finding(
                        category="convention_violation",
                        file="tests/u.spec.js",
                        test="U",
                    )
                ],
                "simulated": True,
            }
        ),
        encoding="utf-8",
    )
    result = reviewer.merge(
        [
            ("api", "reviewed", ""),
            ("ui", "reviewed", ""),
            ("empty", "skipped", "no generated tests"),
        ],
        out,
    )
    assert result["verdict"] == "needs_work" and result["state"] == "reviewed"
    assert [r["repo"] for r in result["repos"]] == ["api", "ui", "empty"]
    assert result["findings"][0]["repo"] == "ui"
    assert result["simulated"] is True


def test_unavailable_is_distinct_from_approve_and_nonfatal(tmp_path):
    result = reviewer.merge([("api", "unavailable", "phase timed out")], tmp_path)
    assert result["state"] == result["verdict"] == "unavailable"
    assert result["repos"][0]["reason"] == "phase timed out"
    assert result["findings"] == []


def test_all_zero_test_repositories_merge_as_skipped(tmp_path):
    result = reviewer.merge(
        [
            ("api", "skipped", "no generated tests"),
            ("ui", "skipped", "no generated tests"),
        ],
        tmp_path,
    )
    assert result["state"] == result["verdict"] == "skipped"


def test_run_record_preserves_unavailable_review_without_changing_gate(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "gate_results.tsv").write_text(
        "api\tcommitted\t0\tdeadbeef\n", encoding="utf-8"
    )
    signal = reviewer.merge([("api", "unavailable", "reviewer timed out")], out)
    (out / "reviewer.contract.json").write_text(json.dumps(signal), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "engine/lib/run_record.py"), "RID", "pr", "PR-x-1"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)
    assert record["overall"] == "committed"
    assert record["review"]["state"] == "unavailable"
    assert record["review"]["verdict"] == "unavailable"
    assert record["review"]["repos"][0]["reason"] == "reviewer timed out"
    assert record["review"]["policy"] == "warn"
    assert record["review"]["loops"] == 0


def test_malformed_reviewed_repo_is_downgraded_to_unavailable(tmp_path):
    (tmp_path / "reviewer-api.contract.json").write_text(
        json.dumps({"verdict": "approve", "findings": [_finding()]}), encoding="utf-8"
    )
    result = reviewer.merge([("api", "reviewed", "")], tmp_path)
    assert result["state"] == "unavailable"
    assert "malformed reviewer contract" in result["repos"][0]["reason"]


def test_load_rejects_tampered_merged_summary(tmp_path):
    signal = reviewer.merge([("api", "unavailable", "timeout")], tmp_path)
    path = tmp_path / "reviewer.contract.json"
    path.write_text(json.dumps({**signal, "verdict": "approve"}), encoding="utf-8")
    assert reviewer.load(path) is None


def test_global_merge_failure_fallback_is_a_loadable_outage():
    signal = {
        "artifact": "test-reviewer",
        "schema": 1,
        "state": "unavailable",
        "verdict": "unavailable",
        "repos": [
            {
                "repo": "reviewer",
                "state": "unavailable",
                "reason": "review result merge failed",
            }
        ],
        "findings": [],
        "simulated": False,
    }
    assert reviewer.normalize_merged_contract(signal)["state"] == "unavailable"


def test_phase_is_read_only_per_repo_after_validate_and_never_read_by_gate():
    cfg = yaml.safe_load(
        (ROOT / "registry/org-config.yaml").read_text(encoding="utf-8")
    )
    assert cfg["phases"]["reviewer"]["allowed_tools"] == "Read"
    source = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert source.index("PHASE validate ") < source.index("\nREVIEW_TESTS\n")
    assert source.index("\nREVIEW_TESTS\n") < source.index(": > out/gate_results.tsv")
    assert "_PHASE_IMPL reviewer test-reviewer.md" in source
    assert 'label="reviewer-${iteration}-${repo}"' in source
    for gate in (ROOT / "engine/gate").glob("*"):
        if gate.is_file():
            assert (
                "reviewer"
                not in gate.read_text(encoding="utf-8", errors="replace").lower()
            )


def test_prompt_limits_review_scope_and_mock_is_scriptable():
    prompt = (ROOT / "prompts/test-reviewer.md").read_text(encoding="utf-8")
    for token in reviewer.CATEGORIES:
        assert token in prompt
    for token in ("do not run", "do not touch git", "untrusted DATA", "re-litigate"):
        assert token.lower() in prompt.lower()
    mock = (ROOT / "engine/phases/mock_phase.sh").read_text(encoding="utf-8")
    assert "AIQE_MOCK_REVIEWER_VERDICT" in mock
    assert "AIQE_MOCK_REVIEWER_MALFORMED" in mock

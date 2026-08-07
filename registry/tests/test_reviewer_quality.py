"""PRD v2 B6: attack-based reviewer quality evaluation."""
import copy
import hashlib
import json
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))
import reviewer_quality as rq  # noqa: E402


def _copy_gold(tmp_path):
    source = ROOT / "eval/reviewer/v1"
    for name in ("labels.json", "fixtures.json"):
        shutil.copyfile(source / name, tmp_path / name)
    return tmp_path / "labels.json"


def _repin(labels_path):
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    fixtures = labels_path.with_name("fixtures.json")
    labels["fixtures"]["sha256"] = hashlib.sha256(fixtures.read_bytes()).hexdigest()
    labels_path.write_text(json.dumps(labels), encoding="utf-8")


def _scripted_outputs(fixtures):
    return {row["id"]: row["scripted_contract"] for row in fixtures}


def _real_outputs(fixtures):
    outputs = copy.deepcopy(_scripted_outputs(fixtures))
    for output in outputs.values():
        output.pop("simulated", None)
    return outputs


def test_gold_set_is_owned_hashed_and_attacks_every_reviewer_class():
    labels, fixtures, expected = rq.load_gold()
    assert labels["owner"]["maintainer_role"] == "QE Lead"
    assert len(fixtures) == len(expected) == 5
    assert {row["defect_class"] for row in fixtures if row["defect_class"]} == \
        set(rq.reviewer.CATEGORIES)
    assert sum(row["defect_class"] is None for row in fixtures) == 1


def test_scripted_eval_reports_plumbing_not_model_judgement():
    result = rq.evaluate()
    simulated, real = result["simulated"], result["real_model"]
    assert result["overall"] == simulated["overall"] == "pass"
    assert result["measurement_state"] == "simulated"
    assert "do not measure" in result["measurement_reason"]
    assert simulated["catch_rate"] == 1.0
    assert simulated["caught"] == simulated["total"] == 4
    assert simulated["clean_control"]["passed"] is True
    assert all(row["catch_rate"] == 1.0
               for row in simulated["per_defect_class"].values())
    assert real["state"] == "blocked"
    assert real["measurement_state"] == "unmeasured"
    assert "parity authentication" in real["reason"]


def test_fixture_tampering_requires_qe_relabelling(tmp_path):
    labels_path = _copy_gold(tmp_path)
    fixtures_path = labels_path.with_name("fixtures.json")
    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
    fixtures["fixtures"][0]["context"]["generated_test"] = "changed"
    fixtures_path.write_text(json.dumps(fixtures), encoding="utf-8")
    with pytest.raises(rq.FixtureError, match="label drift"):
        rq.load_gold(labels_path)


def test_label_reference_cannot_escape_version_directory(tmp_path):
    labels_path = _copy_gold(tmp_path)
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    labels["fixtures"]["file"] = "../fixtures.json"
    labels_path.write_text(json.dumps(labels), encoding="utf-8")
    with pytest.raises(rq.FixtureError, match="must be a file name"):
        rq.load_gold(labels_path)


def test_labels_cannot_lower_m3_gate(tmp_path):
    labels_path = _copy_gold(tmp_path)
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    labels["m3_target_catch_rate"] = 0.5
    labels_path.write_text(json.dumps(labels), encoding="utf-8")
    with pytest.raises(rq.FixtureError, match="PRD threshold 1.0"):
        rq.load_gold(labels_path)


def test_wrong_defect_class_is_a_miss_and_fails_eval():
    labels, fixtures, expected = rq.load_gold()
    outputs = _scripted_outputs(fixtures)
    outputs["vacuous-status-only"] = {
        "verdict": "needs_work",
        "findings": [{
            "severity": "high", "category": "ticket_mismatch",
            "file": "test.js", "test": "test", "finding": "Wrong class.",
            "fix": "Use the expected class."
        }],
    }
    scored = rq._score(fixtures, expected, outputs)
    assert scored["overall"] == "fail"
    assert scored["per_defect_class"]["vacuous_assertion"]["catch_rate"] == 0
    assert any("vacuous-status-only" in message for message in scored["failures"])


def test_lucky_category_without_grounded_evidence_is_a_miss():
    _, fixtures, expected = rq.load_gold()
    outputs = _scripted_outputs(fixtures)
    outputs["ticket-contradiction"] = {
        "verdict": "needs_work",
        "findings": [{
            "severity": "high", "category": "ticket_mismatch",
            "file": "somewhere-else.js", "test": "unrelated",
            "finding": "Generic mismatch without the seeded evidence.",
            "fix": "Change something."
        }],
    }
    scored = rq._score(fixtures, expected, outputs)
    row = next(row for row in scored["fixtures"]
               if row["id"] == "ticket-contradiction")
    assert row["evidence_matched"] is False and row["caught"] is False
    assert "not grounded" in " ".join(scored["failures"])


def test_noisy_clean_control_is_rejected():
    _, fixtures, expected = rq.load_gold()
    outputs = _scripted_outputs(fixtures)
    outputs["clean-boundary-coverage"] = {
        "verdict": "needs_work",
        "findings": [{
            "severity": "low", "category": "missing_coverage",
            "file": "test.js", "test": "clean", "finding": "False positive.",
            "fix": "Approve the complete test."
        }],
    }
    scored = rq._score(fixtures, expected, outputs)
    assert scored["overall"] == "fail"
    assert scored["clean_control"]["passed"] is False


def test_injected_real_outputs_are_measured_separately():
    _, fixtures, _ = rq.load_gold()
    result = rq.evaluate(
        real_outputs=_real_outputs(fixtures),
        real_meta={"provider": "test-provider", "model": "test-model"},
    )
    assert result["measurement_state"] == "mixed"
    assert result["overall"] == "pass"
    assert result["real_model"]["measurement_state"] == "measured"
    assert result["real_model"]["catch_rate"] == 1.0
    assert result["real_model"]["provider"] == "test-provider"


def test_measured_real_miss_fails_the_combined_artifact():
    _, fixtures, _ = rq.load_gold()
    outputs = _real_outputs(fixtures)
    outputs["ticket-contradiction"] = {"verdict": "approve", "findings": []}
    result = rq.evaluate(real_outputs=outputs)
    assert result["simulated"]["overall"] == "pass"
    assert result["real_model"]["overall"] == "fail"
    assert result["overall"] == "fail"
    assert any("ticket-contradiction" in message for message in result["failures"])


def test_simulated_outputs_cannot_be_laundered_as_real_measurement():
    _, fixtures, _ = rq.load_gold()
    result = rq.evaluate(real_outputs=_scripted_outputs(fixtures))
    assert result["real_model"]["overall"] == "fail"
    assert result["real_model"]["catch_rate"] == 0
    assert all("marked simulated" in message
               for message in result["real_model"]["failures"])


def test_malformed_scripted_contract_fails_closed(tmp_path):
    labels_path = _copy_gold(tmp_path)
    fixtures_path = labels_path.with_name("fixtures.json")
    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
    fixtures["fixtures"][0]["scripted_contract"]["verdict"] = "maybe"
    fixtures_path.write_text(json.dumps(fixtures), encoding="utf-8")
    _repin(labels_path)
    with pytest.raises(rq.FixtureError, match="invalid scripted contract"):
        rq.load_gold(labels_path)


def test_cli_writes_both_measurement_states(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rq, "RESULT", tmp_path / "reviewer-quality.json")
    assert rq.main([]) == 0
    result = json.loads(rq.RESULT.read_text(encoding="utf-8"))
    assert result["artifact"] == "reviewer-quality"
    output = capsys.readouterr().out
    assert "SIMULATED" in output and "REAL MODEL" in output and "BLOCKED" in output


def test_explicit_real_failure_is_recorded_and_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "RESULT", tmp_path / "reviewer-quality.json")

    def unavailable():
        raise rq.RealEvaluationError("authentication expired")

    monkeypatch.setattr(rq, "run_real", unavailable)
    assert rq.main(["--real"]) == 1
    result = json.loads(rq.RESULT.read_text(encoding="utf-8"))
    assert result["overall"] == "fail"
    assert result["real_model"]["state"] == "unavailable"
    assert "authentication expired" in result["real_model"]["reason"]


def test_real_evaluator_refuses_a_writable_reviewer_policy(tmp_path, monkeypatch):
    (tmp_path / "registry").mkdir()
    (tmp_path / "registry/org-config.yaml").write_text(
        "models:\n  reviewer: model\nphases:\n"
        "  reviewer: {max_turns: 1, allowed_tools: 'Read,Write'}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(rq, "ROOT", tmp_path)
    monkeypatch.setattr(rq.llm_runner, "provider_for", lambda *args: "claude")
    monkeypatch.setattr(rq.llm_runner, "check_assignment", lambda *args: None)
    monkeypatch.setattr(rq.llm_runner, "check_model_mapping", lambda *args: None)
    with pytest.raises(rq.RealEvaluationError, match="allowed_tools=Read"):
        rq.run_real()


def test_make_and_scorecard_render_simulated_and_real_states():
    make = (ROOT / "Makefile").read_text(encoding="utf-8")
    score = (ROOT / "eval/scorecard.py").read_text(encoding="utf-8")
    assert make.count("python3 eval/reviewer_quality.py") == 4
    assert "reviewer-eval-real:" in make
    assert "Reviewer quality (SIMULATED)" in score
    assert "Reviewer quality (REAL MODEL)" in score

"""PRD v2 A4: labelled, validation-aware discovery evaluation."""
import hashlib
import json
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))
import discovery_quality as dq  # noqa: E402


def _copy_gold(tmp_path):
    source = ROOT / "eval/discovery/v1"
    for name in ("labels.json", "fixtures.json"):
        shutil.copyfile(source / name, tmp_path / name)
    return tmp_path / "labels.json"


def _repin(labels_path):
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    fixtures = labels_path.with_name("fixtures.json")
    labels["fixtures"]["sha256"] = hashlib.sha256(fixtures.read_bytes()).hexdigest()
    labels_path.write_text(json.dumps(labels), encoding="utf-8")


def test_gold_set_is_owned_hashed_and_covers_every_required_case():
    labels, fixtures, expected = dq.load_gold()
    assert labels["owner"]["maintainer_role"] == "QE Lead"
    assert len(fixtures) == len(expected) == 7
    assert dq.REQUIRED_SCENARIOS <= {row["scenario"] for row in expected}
    assert set(expected[0]["expected_signal_keys"]) == set(dq.discovery.SIGNALS)


def test_metric_math_counts_wrong_guess_as_false_positive_and_false_negative():
    got = dq.metric(
        ["ticket:RIGHT-1", "ticket:WRONG-9"],
        ["ticket:RIGHT-1", "ticket:EXPECTED-2"],
    )
    assert got == {
        "true_positive": 1,
        "false_positive": 1,
        "false_negative": 1,
        "precision": 0.5,
        "recall": 0.5,
    }
    refusal = dq.refusal_token("conflict")
    guessed = dq.metric(["ticket:GUESS-1"], [refusal])
    assert guessed["true_positive"] == 0
    assert guessed["false_positive"] == guessed["false_negative"] == 1


def test_metric_identity_keeps_repeated_ticket_decisions_per_fixture():
    decisions = [
        ("pr-one", "ticket:SHARED-1"),
        ("pr-two", "ticket:SHARED-1"),
    ]
    got = dq.metric(decisions, decisions)
    assert got["true_positive"] == 2
    assert got["precision"] == got["recall"] == 1.0


def test_evaluation_reports_each_signal_and_rewards_the_conflict_refusal():
    result = dq.evaluate()
    assert result["overall"] == "pass"
    assert result["measurement_state"] == "simulated"
    assert result["m1"]["precision"] == result["m1"]["recall"] == 1.0
    assert result["m1"]["target_precision"] == 0.95
    assert result["correct_refusal"] == {"correct": 1, "total": 1, "rate": 1.0}
    assert result["exact_outcomes"] == {"correct": 7, "total": 7, "rate": 1.0}
    assert all(
        row["precision"] == row["recall"] == 1.0
        for row in result["per_signal"].values()
    )
    conflict = next(
        row for row in result["fixtures"] if row["id"] == "conflicting-keys"
    )
    invalid = next(row for row in result["fixtures"] if row["id"] == "invalid-key")
    assert conflict["outcome"] == "ambiguous" and conflict["selected_key"] is None
    assert invalid["outcome"] == "discovered_invalid"
    assert all(not keys for keys in invalid["signals"].values())


def test_fixture_tampering_is_rejected_until_labels_are_re_reviewed(tmp_path):
    labels_path = _copy_gold(tmp_path)
    fixtures_path = tmp_path / "fixtures.json"
    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
    fixtures["fixtures"][0]["explicit"] = "OTHER-999"
    fixtures_path.write_text(json.dumps(fixtures), encoding="utf-8")
    with pytest.raises(dq.FixtureError, match="label drift"):
        dq.load_gold(labels_path)


def test_unlabelled_validation_candidate_fails_closed(tmp_path):
    labels_path = _copy_gold(tmp_path)
    fixtures_path = tmp_path / "fixtures.json"
    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
    branch = next(row for row in fixtures["fixtures"] if row["id"] == "branch-only")
    branch["validations"] = {}
    fixtures_path.write_text(json.dumps(fixtures), encoding="utf-8")
    _repin(labels_path)
    result = dq.evaluate(labels_path)
    assert result["overall"] == "fail"
    assert any(
        "do not match labelled validations" in message
        for message in result["failures"]
    )


def test_label_references_cannot_escape_the_version_directory(tmp_path):
    labels_path = _copy_gold(tmp_path)
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    labels["fixtures"]["file"] = "../fixtures.json"
    labels_path.write_text(json.dumps(labels), encoding="utf-8")
    with pytest.raises(dq.FixtureError, match="must be a file name"):
        dq.load_gold(labels_path)


def test_labels_cannot_lower_the_prd_precision_gate(tmp_path):
    labels_path = _copy_gold(tmp_path)
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    labels["m1_target_precision"] = 0.50
    labels_path.write_text(json.dumps(labels), encoding="utf-8")
    with pytest.raises(dq.FixtureError, match="must remain the PRD threshold 0.95"):
        dq.load_gold(labels_path)


def test_cli_writes_a_labelled_result_artifact(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(dq, "RESULT", tmp_path / "discovery-quality.json")
    assert dq.main(["discovery_quality.py"]) == 0
    result = json.loads(dq.RESULT.read_text(encoding="utf-8"))
    assert result["artifact"] == "ticket-discovery-quality"
    assert result["measurement_state"] == "simulated"
    output = capsys.readouterr().out
    assert "SIMULATED" in output
    assert "correct refusal: 1/1" in output

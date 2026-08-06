"""The run record: the only durable copy of what a run did.

`reports/runs/<id>.json` is what every monitoring surface reads — gate outcomes,
spend, diffs, review state. The workspace is ephemeral, so if this assembly
fails there is no second source. It was the one module with no direct tests.

Run as a script (it reads sys.argv at import), so these drive it the way the
pipeline does: a real subprocess with a real `out/` directory.
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "engine/lib/run_record.py"


def _record(tmp_path, tsv, run_id="RUN-1", mode="jira", key="K-1"):
    (tmp_path / "out").mkdir(exist_ok=True)
    (tmp_path / "reports" / "runs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out" / "gate_results.tsv").write_text(tsv, encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), run_id, mode, key],
                       cwd=tmp_path, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_a_torn_line_does_not_destroy_the_whole_record(tmp_path):
    """`int("")` raised, and because this assembles the ONLY durable copy of a
    run, one partial write threw away every gate result in the file — including
    repos that had already committed. The work happened and the evidence went
    missing, which is the worst trade this file can make.
    """
    rec = _record(tmp_path,
                  "e2e-api-tests-1\tcommitted\t0\tabc1234\n"
                  "e2e-ui-tests-1\tno_changes\n")
    repos = {g["test_repo"]: g for g in rec["gates"]}
    assert "e2e-api-tests-1" in repos, "a successful gate was lost to a torn line"
    assert repos["e2e-api-tests-1"]["commit"] == "abc1234"
    assert rec["overall"] == "committed"


def test_a_missing_exit_code_is_unknown_rather_than_zero(tmp_path):
    """`exit_code: 0` would read as a clean run. A missing code is not a
    successful one — the same rule the cost stack applies to unpriced spend."""
    rec = _record(tmp_path, "e2e-ui-tests-1\tno_changes\n")
    assert rec["gates"][0]["exit_code"] is None


def test_unreadable_lines_are_counted_not_silently_dropped(tmp_path):
    """A record showing three gates when the file held five must SAY it is
    short, or `gates: [...]` reads as the complete set."""
    rec = _record(tmp_path,
                  "e2e-api-tests-1\tcommitted\t0\tabc1234\n"
                  "garbage-with-no-status\n"
                  "\te2e-ui\tno_changes\t0\n")
    assert len(rec["gates"]) == 1
    assert rec["malformed_gate_lines"] == 2


def test_the_key_is_absent_when_nothing_was_lost(tmp_path):
    """A permanent `malformed_gate_lines: 0` teaches people to stop reading the
    field — the same reason the Overview tiles are conditional."""
    rec = _record(tmp_path, "e2e-api-tests-1\tcommitted\t0\tabc1234\n")
    assert "malformed_gate_lines" not in rec


def test_overall_comes_only_from_gate_outcomes(tmp_path):
    """A quarantined repo dominates a committed one: a run that half-failed must
    not report success. The critic never contributes (openhands-review 3.2)."""
    rec = _record(tmp_path,
                  "a\tcommitted\t0\tsha1\n"
                  "b\tquarantined\t8\t\n")
    assert rec["overall"] == "quarantined"

    rec = _record(tmp_path, "a\tno_changes\t0\t\n")
    assert rec["overall"] == "no_changes"

    rec = _record(tmp_path, "a\tcommitted\t0\tsha1\n")
    assert rec["overall"] == "committed"


def test_a_blank_file_still_produces_a_record(tmp_path):
    """An empty result file must not crash the assembly; a run with no gate
    results still needs its phases and spend recorded."""
    rec = _record(tmp_path, "\n\n")
    assert rec["gates"] == []
    assert rec["run_id"] == "RUN-1" and rec["trigger"]["key"] == "K-1"


def test_impact_artifact_is_archived_but_corruption_is_nonfatal(tmp_path):
    (tmp_path / "out").mkdir()
    artifact = {"schema_version": 1, "artifact": "impact-candidates",
                "retrieval_mode": "deterministic", "candidates": []}
    (tmp_path / "out/impact-candidates.json").write_text(
        json.dumps(artifact), encoding="utf-8")
    rec = _record(tmp_path, "")
    assert rec["impact_candidates"] == artifact

    (tmp_path / "out/impact-candidates.json").write_text("{torn", encoding="utf-8")
    rec = _record(tmp_path, "")
    assert "impact_candidates" not in rec and rec["run_id"] == "RUN-1"

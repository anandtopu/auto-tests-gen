"""TCA-A1 durable spend-ledger and exit-path integrity pins."""
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import app_paths
import budget
import spend_ledger as sl
import work_queue


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    live = tmp_path / "cost.tsv"
    starts = tmp_path / "starts.jsonl"
    costs = tmp_path / "costs"
    monkeypatch.setattr(budget, "LEDGER", live)
    monkeypatch.setenv("AIQE_PHASE_STARTS_FILE", str(starts))
    monkeypatch.setenv("AIQE_COSTS_DIR", str(costs))
    monkeypatch.setenv("AIQE_SPEND_LEDGER", "1")
    monkeypatch.delenv("AIQE_MOCK", raising=False)
    monkeypatch.delenv("AIQE_MOCK_PHASE_COST", raising=False)
    monkeypatch.delenv("AIQE_COST_ATTRIBUTION", raising=False)
    return live, starts, costs


def _completed(live, phase="analyze", basis="reported", cost="0.125000"):
    live.write_text(
        f"{phase}\t{cost}\t1\t1700000000\tmodel-x\t100\t20\t30\t4\t2\tclaude\t{basis}\n",
        encoding="utf-8",
    )


def test_completed_call_flushes_the_full_auditable_row(isolated):
    live, starts, costs = isolated
    assert sl.mark_start("1700000000-7", "requirements", "PROJ-1", "analyze",
                         "claude", "model-x", starts)
    _completed(live)
    target = sl.flush("1700000000-7", "requirements", "PROJ-1",
                      live, starts, costs)
    doc = json.loads(target.read_text(encoding="utf-8"))
    assert doc["schema"] == 1 and doc["attribution"] == "user"
    row = doc["rows"][0]
    assert (row["run_id"], row["mode"], row["key"], row["phase"]) == (
        "1700000000-7", "requirements", "PROJ-1", "analyze")
    assert row["provider"] == "claude" and row["model"] == "model-x"
    assert row["basis"] == "reported" and row["cost_usd"] == pytest.approx(.125)
    assert row["input_tokens"] == 100 and row["output_tokens"] == 20
    assert row["cache_read_tokens"] == 30 and row["cache_creation_tokens"] == 4
    assert row["turns"] == 2 and row["attempts"] == 1


def test_started_without_result_is_unrecorded_never_zero(isolated):
    live, starts, costs = isolated
    sl.mark_start("1700000001-8", "jira", "PROJ-2", "generate",
                  "claude", "sonnet", starts)
    live.write_text("generate\t0.000000\t0\t10\t\t0\t0\t0\t0\t0\t\t\n",
                    encoding="utf-8")
    target = sl.flush("1700000001-8", "jira", "PROJ-2", live, starts, costs)
    row = json.loads(target.read_text(encoding="utf-8"))["rows"][0]
    assert row["basis"] == "unrecorded"
    for field in ("cost_usd", "input_tokens", "output_tokens",
                  "cache_read_tokens", "cache_creation_tokens", "turns"):
        assert row[field] is None, f"{field} must be unknown, never zero"


def test_never_started_has_no_row_and_no_file(isolated):
    live, starts, costs = isolated
    live.write_text("generate\t0.000000\t0\t10\t\t0\t0\t0\t0\t0\t\t\n",
                    encoding="utf-8")
    assert sl.flush("1700000002-9", "jira", "PROJ-3", live, starts, costs) is None
    assert not costs.exists()


def test_same_phase_attempts_are_counted_once_without_losing_spend(isolated):
    live, starts, costs = isolated
    live.write_text(
        "analyze\t0.100000\t1\t10\tm\t10\t2\t3\t4\t1\tclaude\treported\n"
        "analyze\t0.200000\t1\t11\tm\t20\t3\t4\t5\t2\tclaude\treported\n",
        encoding="utf-8",
    )
    target = sl.flush("1700000003-10", "jira", "PROJ-4", live, starts, costs)
    rows = json.loads(target.read_text(encoding="utf-8"))["rows"]
    assert len(rows) == 1 and rows[0]["attempts"] == 2
    assert rows[0]["cost_usd"] == pytest.approx(.3)
    assert rows[0]["input_tokens"] == 30 and rows[0]["turns"] == 3


def test_incompatible_attempt_bases_are_incomplete_not_blended(isolated):
    live, starts, costs = isolated
    live.write_text(
        "analyze\t0.100000\t1\t10\tm\t1\t1\t0\t0\t1\tclaude\treported\n"
        "analyze\t0.200000\t1\t11\tm\t1\t1\t0\t0\t1\tclaude\testimated\n",
        encoding="utf-8",
    )
    row = json.loads(sl.flush("1700000004-11", "jira", "PROJ-5", live,
                              starts, costs).read_text(encoding="utf-8"))["rows"][0]
    assert row["basis"] == "unknown" and row["cost_usd"] is None


def test_knob_attribution_and_run_id_are_safe(isolated, monkeypatch):
    live, starts, costs = isolated
    _completed(live)
    monkeypatch.setenv("AIQE_COST_ATTRIBUTION", "probe")
    target = sl.flush("1700000005-12", "jira", "PROJ-6", live, starts, costs)
    assert json.loads(target.read_text(encoding="utf-8"))["rows"][0]["attribution"] == "probe"
    assert sl.flush("../../escape", "jira", "K", live, starts, costs) is None
    monkeypatch.setenv("AIQE_SPEND_LEDGER", "0")
    assert sl.flush("1700000006-13", "jira", "K", live, starts, costs) is None


def test_cost_path_precedence_bundle_reset_prune_and_git_hygiene(tmp_path, monkeypatch):
    monkeypatch.setenv("AIQE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("AIQE_COSTS_DIR", raising=False)
    assert app_paths.costs_dir() == tmp_path / "state/reports/costs"
    monkeypatch.setenv("AIQE_COSTS_DIR", str(tmp_path / "isolated"))
    assert app_paths.costs_dir() == tmp_path / "isolated"

    import demo_data
    import state_bundle
    assert "reports/costs" in state_bundle.INCLUDE_DIRS
    assert "reports/costs" in demo_data.CLEAR_DIRS
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "reports/costs/[0-9]*.json" in ignored
    assert "reports/costs/*.json" not in ignored

    costs = tmp_path / "prune-costs"
    for i in range(3):
        costs.mkdir(exist_ok=True)
        (costs / f"170000000{i}-{i}.json").write_text(json.dumps({
            "run_id": f"170000000{i}-{i}", "flushed_at": i, "rows": []}),
            encoding="utf-8")
    assert sl.prune(2, costs) == {"kept": 2, "removed": 1}
    assert sorted(p.stem for p in costs.glob("*.json")) == ["1700000001-1", "1700000002-2"]

    relocated = tmp_path / "isolated"
    relocated.mkdir()
    entry = relocated / "1700000099-9.json"
    entry.write_text(json.dumps({"run_id": entry.stem, "flushed_at": 99,
                                 "rows": []}), encoding="utf-8")
    assert pathlib.Path("reports/costs/1700000099-9.json") in state_bundle.collect()
    assert state_bundle.source_of("reports/costs/1700000099-9.json") == entry

    clear_root = tmp_path / "clear-estate"
    generated = clear_root / "reports/costs/1700000100-10.json"
    generated.parent.mkdir(parents=True)
    generated.write_text("{}", encoding="utf-8")
    result = demo_data.clear(clear_root)
    assert not generated.exists() and "reports/costs/" in result["targets"]


def test_suite_redirects_cost_history_away_from_the_estate():
    configured = pathlib.Path(os.environ["AIQE_COSTS_DIR"]).resolve()
    assert configured != (ROOT / "reports/costs").resolve()


def test_pipeline_has_one_chained_exit_handler_and_exact_start_boundaries():
    pipeline = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    traps = re.findall(r"^[^#\n]*trap\s+[^\n]*\bEXIT\b", pipeline, re.MULTILINE)
    assert len(traps) == 1, "a second EXIT trap replaces lock release"
    handler = pipeline[pipeline.index("_pipeline_exit() {"):pipeline.index("trap '", pipeline.index("_pipeline_exit() {"))]
    assert handler.index("spend_ledger.py flush") < handler.index('rmdir "$LOCK"')
    assert 'local rc="${1:-0}"' in handler and 'return "$rc"' in handler

    real = (ROOT / "engine/phases/run_phase.sh").read_text(encoding="utf-8")
    assert real.index("spend_ledger.py mark-start") < real.index('bash "$RUNNER" run_phase')
    assert real.index("spend_ledger.py mark-start") > real.index("artifact_reuse.py restore")
    mock = (ROOT / "engine/phases/mock_phase.sh").read_text(encoding="utf-8")
    assert '"$OUT" mock mock' in mock

    import inspect
    flush = inspect.getsource(sl.flush)
    assert flush.index("fs_lock.lock") < flush.index("fs_lock.write_json_atomic")


def test_requirements_draft_stop_writes_ledger_not_run_record(tmp_path):
    costs = tmp_path / "costs"
    env = {**os.environ, "AIQE_MOCK": "1", "AIQE_MOCK_PHASE_COST": "0.01",
           "AIQE_SPEND_LEDGER": "1", "AIQE_COSTS_DIR": str(costs),
           "AIQE_SPEC_DIR": str(tmp_path / "specs"),
           "AIQE_PLAN_DIR": str(tmp_path / "plans"),
           "AIQE_TESTPLAN_DIR": str(tmp_path / "testplans"),
           "AIQE_TESTDATA_DIR": str(tmp_path / "testdata")}
    result = subprocess.run(
        [work_queue.bash_exe(), "engine/pipeline.sh", "requirements", "PROJ-301"],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", stdin=subprocess.DEVNULL, timeout=900, check=False)
    assert result.returncode == 0, result.stdout[-1000:] + result.stderr[-500:]
    entries = list(costs.glob("*.json"))
    assert len(entries) == 1
    doc = json.loads(entries[0].read_text(encoding="utf-8"))
    assert doc["mode"] == "requirements" and doc["rows"]
    assert not (ROOT / "reports/runs" / entries[0].name).exists()
    assert not (ROOT / "out/.pipeline.lock").exists()

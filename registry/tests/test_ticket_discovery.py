"""Successor PRD A1: deterministic, validated PR ticket discovery."""
import json
import os
import pathlib
import subprocess
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import explain  # noqa: E402
import ticket_discovery as td  # noqa: E402
import work_queue  # noqa: E402


def _meta(branch="", title="", description="", commits=()):
    return {"state": "available", "source_branch": branch, "title": title,
            "description": description, "commit_messages": list(commits)}


def _resolve(artifact, valid=(), invalid=(), unavailable=()):
    verdicts = {k: {"state": "valid"} for k in valid}
    verdicts.update({k: {"state": "invalid"} for k in invalid})
    verdicts.update({k: {"state": "unavailable"} for k in unavailable})
    return td.resolve(artifact, verdicts)


def test_extraction_reuses_the_correlator_earned_key_grammar():
    artifact = td.extract(_meta(
        branch="feature/PROJ-301-fix-HTTP-2",
        title="UTF-8 support for SHOP-7",
        commits=["RFC-2616 docs and PAY9-22 behavior"]), "")
    assert td.candidate_keys(artifact) == ["PAY9-22", "PROJ-301", "SHOP-7"]
    assert td.normalize_explicit("PROJ-301") == "PROJ-301"
    assert td.normalize_explicit("use PROJ-301") is None


def test_valid_explicit_key_beats_every_inference():
    artifact = td.extract(_meta(branch="feature/BRANCH-2",
                                title="TITLE-3", commits=["COMMIT-4"]), "EXPL-1")
    result = _resolve(artifact, valid=("EXPL-1", "BRANCH-2", "TITLE-3", "COMMIT-4"))
    assert result["outcome"] == "selected"
    assert result["selected_key"] == "EXPL-1"
    assert result["reason"] == "validated explicit intake key"


def test_unique_branch_key_wins_a_multi_key_validation():
    artifact = td.extract(_meta(branch="feature/BRANCH-2",
                                title="TITLE-3", commits=["COMMIT-4"]), "")
    result = _resolve(artifact, valid=("BRANCH-2", "TITLE-3", "COMMIT-4"))
    assert result["selected_key"] == "BRANCH-2"
    assert result["reason"] == "unique validated branch-name key"


def test_multiple_valid_non_branch_keys_refuse_to_guess():
    artifact = td.extract(_meta(title="TITLE-3", commits=["COMMIT-4"]), "")
    result = _resolve(artifact, valid=("TITLE-3", "COMMIT-4"))
    assert result["outcome"] == "ambiguous"
    assert result["selected_key"] is None
    assert "TITLE-3" in td.context_text(result) and "COMMIT-4" in td.context_text(result)


def test_invalid_unavailable_and_not_found_are_distinct():
    found = td.extract(_meta(title="DEAD-1"), "")
    invalid = _resolve(found, invalid=("DEAD-1",))
    unavailable = _resolve(found, unavailable=("DEAD-1",))
    missing = _resolve(td.extract(_meta(), ""))
    assert invalid["outcome"] == "discovered_invalid"
    assert unavailable["outcome"] == "validation_unavailable"
    assert missing["outcome"] == "not_found"
    assert td.context_text(missing).endswith("No ticket discovered.\n")


def test_validation_tsv_is_closed_and_missing_rows_are_unavailable():
    parsed = td.parse_validations(
        "AA-1\tvalid\tresolved\nBB-2\tbogus\tignored\nCC-3\tinvalid\tnot found\n")
    assert set(parsed) == {"AA-1", "CC-3"}
    artifact = td.extract(_meta(title="AA-1 and BB-2"), "")
    result = td.resolve(artifact, parsed)
    rows = {r["key"]: r["validation"] for r in result["candidates"]}
    assert rows == {"AA-1": "valid", "BB-2": "unavailable"}


def test_queue_carries_validated_explicit_intake_to_the_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(work_queue, "FILE", tmp_path / "queue.json")
    item, fresh = work_queue.add("pr", "orders-api", pr="201", ticket="PROJ-301")
    assert fresh and item["ticket"] == "PROJ-301"
    with pytest.raises(SystemExit, match="one bare JIRA key"):
        work_queue.add("pr", "orders-api", pr="202", ticket="ticket PROJ-301")
    with pytest.raises(SystemExit, match="only be supplied in pr mode"):
        work_queue.add("jira", "PROJ-301", ticket="PROJ-301")

    calls = []
    monkeypatch.setattr(work_queue.subprocess, "run", lambda cmd, **kw: (
        calls.append((cmd, kw)) or types.SimpleNamespace(returncode=0, stdout="", stderr="")))
    work_queue.run_all()
    assert calls[0][1]["env"]["AIQE_PR_TICKET"] == "PROJ-301"
    assert calls[0][0][-3:] == ["pr", "orders-api", "201"]


def test_queue_dedupe_does_not_hide_a_new_explicit_link(tmp_path, monkeypatch):
    monkeypatch.setattr(work_queue, "FILE", tmp_path / "queue.json")
    _, first = work_queue.add("pr", "orders-api", pr="201", ticket="PROJ-301")
    _, second = work_queue.add("pr", "orders-api", pr="201", ticket="OTHER-9")
    assert first and second
    assert len(work_queue.load()) == 2


def test_mock_ports_supply_metadata_and_not_found_validation():
    bash = work_queue.bash_exe()
    context = subprocess.run(
        [bash, "adapters/mock/scm.sh", "pr_context", "orders-api", "201"],
        cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert context.returncode == 0
    assert json.loads(context.stdout)["source_branch"] == "feature/PROJ-301-discounts"
    missing = subprocess.run(
        [bash, "adapters/mock/tracker.sh", "get_item", "DOESNOTEXIST-999"],
        cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert missing.returncode == 3


@pytest.mark.parametrize(("status", "expected"), (("404", 3), ("503", 1)))
def test_real_tracker_separates_not_found_from_unavailable(tmp_path, status, expected):
    stub = tmp_path / "bin"
    stub.mkdir()
    curl = stub / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        "out=''\n"
        "while [ $# -gt 0 ]; do\n"
        "  case \"$1\" in -o) out=$2; shift 2;; -w) shift 2;; *) shift;; esac\n"
        "done\n"
        "printf '%s\\n' '{}' > \"$out\"\n"
        f"printf '%s' '{status}'\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)
    command, env = work_queue.git_bash_command(
        ROOT / "adapters/tracker/jira.sh", "get_item", "MISSING-9",
        prepend=[stub], JIRA_URL="https://jira.example.com",
        ATLASSIAN_MCP_TOKEN="synthetic-test-token",
    )
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", errors="replace",
                            stdin=subprocess.DEVNULL, env=env)
    assert result.returncode == expected


def test_run_record_persists_discovery_and_explain_answers_why(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    discovery = _resolve(td.extract(_meta(branch="feature/PROJ-301"), ""),
                         valid=("PROJ-301",))
    (out / "ticket-discovery.json").write_text(json.dumps(discovery), encoding="utf-8")
    env = {**os.environ, "AIQE_MOCK": "1",
           "AIQE_ARTIFACTS_DIR": str(tmp_path / "artifacts")}
    result = subprocess.run(
        [sys.executable, str(ROOT / "engine/lib/run_record.py"), "run-a1", "pr",
         "PR-orders-api-201"], cwd=tmp_path, env=env, capture_output=True,
        text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)
    assert record["ticket_discovery"]["selected_key"] == "PROJ-301"

    runs = tmp_path / "reports/runs"
    runs.mkdir(parents=True)
    (runs / "run-a1.json").write_text(json.dumps(record), encoding="utf-8")
    answer = explain.explain(run_id="run-a1", root=tmp_path)
    decision = next(d for d in answer["decisions"] if d["id"] == "ticket-discovery")
    assert decision["answer"] == "PROJ-301"
    assert any("signals=branch" in why for why in decision["because"])


def test_pipeline_and_ui_keep_a1_default_off_and_single_path():
    pipeline = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert "${AIQE_PR_TICKET_CONTEXT:-0}" in pipeline
    assert pipeline.index("SCM pr_context") < pipeline.index("SCM changed_files")
    assert "TRACKER get_item \"$candidate\"" in pipeline
    assert pipeline.count("out/pr-ticket-context.md") >= 2
    assert 'PR_TICKET_CONTEXT=()' in pipeline
    dashboard = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    server = (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")
    assert 'id="wz-pr-ticket"' in dashboard
    assert "ticket: ticket" in dashboard
    assert 'ticket=p.get("ticket") or None' in server


def test_real_scm_adapters_expose_one_pr_context_port():
    for name in ("github.sh", "bitbucket.sh", "stash.sh"):
        source = (ROOT / "adapters/scm" / name).read_text(encoding="utf-8")
        assert "pr_context)" in source
        assert "source_branch" in source and "commit_messages" in source

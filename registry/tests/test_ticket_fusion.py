"""A2 pipeline wiring and selected-ticket integration tests."""
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import work_queue  # noqa: E402


def test_pipeline_wires_only_selected_tickets_at_the_run_specific_tail():
    source = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert source.count('TRACKER get_item "$candidate"') == 1
    assert 'TRACKER get_item "$DISCOVERED_TICKET"' not in source
    assert 'mv out/.ticket.json.tmp out/ticket.json' in source
    assert source.count("engine/lib/ticket_fields.py out/ticket.json") == 2
    assert 'if [ "${PR_TICKET_FUSED:-0}" = "1" ]' in source
    triage = next(line for line in source.splitlines()
                   if line.strip().startswith("PHASE triage pr-triage.md"))
    generate = next(line for line in source.splitlines()
                    if line.strip().startswith("GENERATE ") and "out/pr.diff" in line)
    assert "$(CTX triage)" in triage
    assert triage.endswith('"${PR_TRIAGE_FUSION_CONTEXT[@]}"')
    assert "$(CTX generate)" in generate
    assert generate.endswith('"${PR_GENERATE_FUSION_CONTEXT[@]}"')
    assert source.count("out/pr-ticket-fused-triage.md") >= 2
    assert source.count("out/pr-ticket-fused-generate.md") >= 2


def test_mock_pr_selected_ticket_is_canonical_guided_and_fused():
    env = {**os.environ, "AIQE_MOCK": "1", "AIQE_PR_TICKET_CONTEXT": "1",
           "AIQE_GENERATE_FANOUT": "0"}
    result = subprocess.run(
        [work_queue.bash_exe(), "engine/pipeline.sh", "pr", "orders-api", "201"],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", stdin=subprocess.DEVNULL, timeout=300)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-1000:]
    ticket = json.loads((ROOT / "out/ticket.json").read_text(encoding="utf-8"))
    assert ticket["key"] == "PROJ-301"
    guidance = (ROOT / "out/issue-guidance.md").read_text(encoding="utf-8")
    assert "Extend existing mapped tests" in guidance
    for phase in ("triage", "generate"):
        text = (ROOT / f"out/pr-ticket-fused-{phase}.md").read_text(encoding="utf-8")
        manifest = json.loads((ROOT / f"out/pr-ticket-fused-{phase}.json")
                              .read_text(encoding="utf-8"))
        assert "AC-1: 1-90% accepted" in text
        assert manifest["selected_key"] == "PROJ-301"


def test_flag_off_source_path_keeps_context_arrays_empty():
    source = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert "PR_TICKET_CONTEXT=()" in source and "PR_TICKET_FUSED=0" in source
    assert "PR_TRIAGE_FUSION_CONTEXT=()" in source
    assert "PR_GENERATE_FUSION_CONTEXT=()" in source
    assert source.index('case "$(printf \'%s\' "${AIQE_PR_TICKET_CONTEXT:-0}"') \
        < source.index('if [ "$PR_TICKET_ENABLED" = "1" ]')


def test_flag_off_cannot_inherit_stale_fusion_artifacts():
    out = ROOT / "out"
    stale = [out / "ticket.json", out / "issue-guidance.md",
             out / "ticket-discovery.json", out / "pr-ticket-fused-triage.md",
             out / "pr-ticket-fused-generate.json"]
    for path in stale:
        path.write_text("stale", encoding="utf-8")
    env = {**os.environ, "AIQE_MOCK": "1", "AIQE_PR_TICKET_CONTEXT": "0",
           "AIQE_GENERATE_FANOUT": "0"}
    result = subprocess.run(
        [work_queue.bash_exe(), "engine/pipeline.sh", "pr", "orders-api", "201"],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", stdin=subprocess.DEVNULL, timeout=300)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-1000:]
    assert all(not path.exists() for path in stale)

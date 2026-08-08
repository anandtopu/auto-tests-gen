"""S1: structured ticket search, safe JQL, and truthful page counts."""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import ticket_fields
import ticket_search
import work_queue

INJECTION = 'x" OR key in (SEC-1)//'


def _adapter(path, verb, *args, prepend=(), **env_extra):
    command, env = work_queue.git_bash_command(
        ROOT / path, verb, *args, prepend=prepend, **env_extra)
    return subprocess.run(
        command, cwd=ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
        timeout=60, check=False,
    )


def test_search_vocabulary_is_closed_superset_of_processed_fields():
    assert set(ticket_fields.PROCESSED_FIELD_VOCABULARY) < set(
        ticket_search.SEARCH_FIELD_VOCABULARY)
    assert set(ticket_search.SEARCH_FIELD_VOCABULARY) == {
        "fixversion", "issue_type", "component", "label", "status", "text"
    }
    with pytest.raises(ticket_search.SearchInputError, match="unsupported"):
        ticket_search.normalize_filters({"raw_jql": "project = SEC"})
    with pytest.raises(ticket_search.SearchInputError, match="must be a string"):
        ticket_search.normalize_filters({"label": ["security"]})


def test_jql_builder_escapes_injection_and_control_characters():
    jql = ticket_search.build_jql({
        "fixversion": INJECTION,
        "text": "line1\nline2\\tail",
    })
    assert jql == (
        'fixVersion = "x\\" OR key in (SEC-1)//" AND '
        'text ~ "line1\\nline2\\\\tail"'
    )
    assert ticket_search.build_jql({}) == "key is not EMPTY"


@pytest.mark.parametrize("filters", [
    {"fixversion": "2026.08"},
    {"issue_type": "story"},
    {"component": "checkout"},
    {"label": "api-only"},
    {"status": "in progress"},
    {"text": "rounding"},
    {"fixversion": "2026.08", "issue_type": "Story", "component": "Checkout",
     "label": "api-only", "status": "In Progress", "text": "discount"},
])
def test_mock_adapter_round_trips_each_filter_and_anded_combination(filters):
    result = _adapter("adapters/mock/tracker.sh", "search", json.dumps(filters))
    assert result.returncode == 0, result.stderr
    envelope = json.loads(result.stdout.strip().splitlines()[-1])
    assert envelope["returned"] == envelope["total"] == 1
    item = envelope["items"][0]
    assert item == {
        "key": "PROJ-301",
        "summary": "Discount validation hardening",
        "issue_type": "Story",
        "components": ["Checkout"],
        "labels": ["api-only"],
        "fix_versions": ["2026.08"],
        "status": "In Progress",
    }


def test_mock_adapter_treats_injection_release_as_literal():
    result = _adapter("adapters/mock/tracker.sh", "search_release", INJECTION)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == []


def test_real_adapter_passes_only_escaped_literal_jql_to_curl(tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    capture = tmp_path / "jql.txt"
    curl = stub / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--data-urlencode\" ]; then\n"
        "    shift\n"
        f"    case \"$1\" in jql=*) printf '%s' \"${{1#jql=}}\" > "
        f"\"{capture.as_posix()}\";; esac\n"
        "  fi\n"
        "  shift\n"
        "done\n"
        "printf '%s' '{\"issues\":[],\"total\":0}'\n",
        encoding="utf-8",
    )
    os.chmod(curl, 0o755)
    result = _adapter(
        "adapters/tracker/jira.sh", "search_release", INJECTION,
        prepend=[stub], JIRA_URL="https://jira.example.test",
        ATLASSIAN_MCP_TOKEN="synthetic-test-token",
    )
    assert result.returncode == 0, result.stderr
    assert capture.read_text(encoding="utf-8") == ticket_search.build_jql(
        {"fixversion": INJECTION})
    assert json.loads(result.stdout.strip().splitlines()[-1]) == []


def test_jira_projection_states_returned_total_and_all_attributes():
    envelope = ticket_search.project_jira_response({
        "total": 140,
        "issues": [{"key": "PROJ-9", "fields": {
            "summary": "Synthetic result",
            "issuetype": {"name": "Bug"},
            "components": [{"name": "Checkout"}],
            "labels": ["api-only"],
            "fixVersions": [{"name": "2.14"}],
            "status": {"name": "Open"},
        }}],
    })
    assert envelope["returned"] == 1
    assert envelope["total"] == 140
    assert envelope["items"][0]["issue_type"] == "Bug"
    assert envelope["items"][0]["components"] == ["Checkout"]


def test_mock_pagination_count_is_population_not_page_length(tmp_path):
    for number in range(3):
        (tmp_path / f".item-PROJ-{number}.json").write_text(json.dumps({
            "key": f"PROJ-{number}", "summary": "match", "labels": ["bulk"]
        }), encoding="utf-8")
    envelope = ticket_search.search_fixture_dir(
        {"label": "bulk"}, str(tmp_path / ".item-*.json"), page_size=2)
    assert envelope["returned"] == 2
    assert envelope["total"] == 3


def test_search_release_remains_list_shaped_and_uses_shared_search():
    result = _adapter("adapters/mock/tracker.sh", "search_release", "2026.08")
    assert result.returncode == 0, result.stderr
    legacy = json.loads(result.stdout.strip().splitlines()[-1])
    assert isinstance(legacy, list) and legacy[0]["key"] == "PROJ-301"
    for adapter in ("adapters/mock/tracker.sh", "adapters/tracker/jira.sh"):
        source = (ROOT / adapter).read_text(encoding="utf-8")
        release_case = source.split("search_release)", 1)[1].split(";;", 1)[0]
        assert "run_search" in release_case
        assert 'JQL="fixVersion' not in source

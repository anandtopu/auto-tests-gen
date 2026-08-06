"""Regression pins for truthful HTTP delivery and Splunk TLS handling."""
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("adapter,minimum", [
    ("adapters/notify/slack.sh", 1),
    ("adapters/tracker/jira.sh", 1),
    ("adapters/scm/bitbucket.sh", 2),
    ("adapters/scm/stash.sh", 2),
    ("adapters/telemetry/splunk.sh", 1),
])
def test_http_write_verbs_fail_on_rejected_status(adapter, minimum):
    source = (ROOT / adapter).read_text()
    assert source.count("--fail-with-body") >= minimum


def test_splunk_tls_verification_is_default_with_explicit_opt_out():
    source = (ROOT / "adapters/telemetry/splunk.sh").read_text()
    assert "curl -s -k" not in source
    assert 'AIQE_SSL_VERIFY:-1' in source
    assert 'CURL_FLAGS+=(-k)' in source

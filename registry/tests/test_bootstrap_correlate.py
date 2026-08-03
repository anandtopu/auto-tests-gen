"""Correlation: the deterministic joins that attribute a test to an app repo.

`correlate.py` turns harvested evidence into a mapping and a confidence, and
that confidence decides whether the mapping is auto-accepted, reviewed, or sent
to the LLM classifier. Its comments record two bugs already paid for — JIRA-key
false positives, and git_history voting on attribution it cannot support — so
these pin the behaviour those fixes established, plus the path normaliser.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = (ROOT / "catalog/bootstrap/correlate.py").read_text(encoding="utf-8")


def _fns():
    """Load the pure helpers; the script's body runs on import."""
    ns = {"re": re}
    exec(SRC[SRC.index("ATTRIBUTING ="):SRC.index("\nfor e in entries")], ns)  # noqa: S102
    return ns


def test_a_path_segment_that_merely_starts_with_digits_is_not_an_id():
    """`/\\d+` matched the digits at the START of a segment, so
    `/api/2fa/verify` normalised to `/api/{id}fa/verify` — a path that then
    matches nothing in the contract index, silently costing the test its
    attribution.

    It fails in the CONSERVATIVE direction (no mapping rather than a wrong
    one), which is exactly why it sat unnoticed: the test lands in the review
    queue looking like something the correlator simply had no opinion about.
    """
    norm = _fns()["norm"]
    assert norm("/api/2fa/verify") == "/api/2fa/verify"
    assert norm("/a/1b/c") == "/a/1b/c"
    assert norm("/v1/oauth2/token") == "/v1/oauth2/token"


def test_whole_numeric_segments_still_normalise():
    norm = _fns()["norm"]
    assert norm("/v1/orders/123/discounts") == "/v1/orders/{id}/discounts"
    assert norm("/orders/123/items/456") == "/orders/{id}/items/{id}"
    assert norm("/users/42") == "/users/{id}"
    assert norm("/users/42/") == "/users/{id}/"


def test_technical_tokens_are_not_mistaken_for_jira_keys():
    """`[A-Z][A-Z0-9]+-\\d+` matched UTF-8, HTTP-2, SHA-1 and RFC-2616 in
    ordinary commit messages. Each invented a `feature` value and added a
    method, pushing single-signal mappings over the auto-accept line."""
    jira_keys = _fns()["jira_keys"]
    noise = ("Fix UTF-8 handling, bump to HTTP-2, verify SHA-1 per RFC-2616 "
             "and TLS-1 with AES-256")
    assert jira_keys(noise) == []
    assert jira_keys("PROJ-301: add discount boundary test") == ["PROJ-301"]
    assert jira_keys("PROJ-301 and AB-7 both touched this") == ["AB-7", "PROJ-301"]


def test_git_history_does_not_vote_on_confidence():
    """A commit message says which TICKET touched a file. It contributes no app
    repo, so it cannot support a claim about WHICH REPO the test covers — it
    used to take a single-signal mapping from 0.75 to 0.95, over the 0.85 auto
    line, so a mapping skipped human review on the strength of a commit
    message. `covers:` regenerates from these mappings and decides routing.
    """
    ns = _fns()
    assert ns["ATTRIBUTING"] == ("contract_match", "route_match")
    assert "git_history" not in ns["ATTRIBUTING"]
    # And the formula counts only attributing methods.
    body = SRC[SRC.index("attributing = {m for m"):SRC.index("e[\"mapping\"] =")]
    assert "if m in ATTRIBUTING" in body
    assert "len(attributing)" in body, "confidence counts every method again"


def test_the_confidence_ladder_lands_where_the_tiers_expect():
    """0.65 base, +0.2 per attributing match, capped 0.99 — and no attribution
    at all scores 0.0 so it falls below split_residue's 0.55 line and reaches
    the classifier, which is what the review band exists for."""
    def conf(n_attributing, has_repos=True):
        return round(min(0.99, 0.65 + 0.2 * n_attributing), 2) if has_repos else 0.0
    assert conf(0, has_repos=False) == 0.0
    assert conf(1) == 0.85, "one deterministic match must reach the auto line"
    assert conf(2) == 0.99, "two matches must cap, not exceed"
    # Mirror of the source, so a change to the constants breaks this.
    assert "0.65 + 0.2 * len(attributing)" in SRC
    assert "min(0.99," in SRC


def test_evidence_is_still_recorded_even_when_it_does_not_vote():
    """git_jira_keys stays on the entry and `feature` is read from it — the
    signal is useful for traceability, it just no longer buys confidence."""
    assert 'e["evidence"]["git_jira_keys"] = keys' in SRC
    assert '"feature": (e["evidence"]["git_jira_keys"] or [""])[0]' in SRC
    assert '"git_history"' in SRC, "the method is no longer recorded at all"

"""A conversation link must point at OpenHands, not at the dashboard.

REPORTED: "Author via OpenHands" on the Test plans tab produced a link to
http://localhost:4999/... — the dashboard's own origin — and the browser said
there is no OpenHands there. OPENHANDS_URL was configured correctly the whole
time.

The cause is not a hardcoded localhost. There is none: openhands_client reads
OPENHANDS_URL from the environment and refuses with "OPENHANDS_URL is not set —
configure it in Settings" when it is missing (verified by running the endpoint
with it unset — HTTP 502 with exactly that message). The URL is settable through
Settings, .env.example, and deploy/openshift/secret.example.yaml, which is where
the other endpoint URLs live too (JIRA_URL, STASH_URL, CONFLUENCE_URL) because an
endpoint belongs with its credential.

What went wrong is one step later. The Agent Server may return `url` as a PATH
(`/conversations/abc`) rather than an absolute URL, and it was stored verbatim.
bin/dashboard.py renders it straight into `<a href=...>`, so the browser resolved
that path against the page's own origin — the dashboard. Any relative URL from
any deployment lands on the dashboard host, whatever OPENHANDS_URL says.

So the fix resolves what the server returns against the configured base, and
leaves an already-absolute URL exactly as the server gave it.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import openhands_client as oh  # noqa: E402

BASE = "https://openhands.corp.example"


def test_a_relative_conversation_url_is_resolved_against_the_configured_base():
    """The reported bug: this used to stay relative and the browser resolved it
    against http://localhost:4999."""
    assert oh._absolute(BASE, "/conversations/abc") == f"{BASE}/conversations/abc"
    assert oh._absolute(BASE, "conversations/abc") == f"{BASE}/conversations/abc"


def test_an_absolute_url_is_left_exactly_as_the_server_gave_it():
    """A deployment whose server returns a full URL — possibly on a different
    host than the API base — must not have it rewritten."""
    for url in ("https://other.example/x", "http://oh.internal:3000/conversations/1"):
        assert oh._absolute(BASE, url) == url


def test_a_trailing_slash_on_the_base_does_not_double_up():
    assert oh._absolute(BASE + "/", "/conversations/abc") == f"{BASE}/conversations/abc"


def test_no_url_stays_empty_rather_than_becoming_the_base():
    """Returning the bare base would render a link that looks like a
    conversation and is not one."""
    assert oh._absolute(BASE, "") == ""
    assert oh._absolute(BASE, None) == ""


def test_without_a_base_the_value_is_passed_through_unchanged():
    """Nothing to resolve against. Inventing a host here would be guessing, and
    the caller already refuses when OPENHANDS_URL is unset."""
    assert oh._absolute("", "/conversations/abc") == "/conversations/abc"


def test_both_call_sites_resolve_and_neither_stores_the_raw_field():
    """The helper only helps where it is used, and there are two places that
    take a `url` from the server: start() and the conversation lookup."""
    src = (ROOT / "engine/lib/openhands_client.py").read_text(encoding="utf-8")
    assert src.count("_absolute(base, resp.get(\"url\"))") == 2, \
        "a call site takes the server's url without resolving it"
    assert 'url = resp.get("url") or (f"{base}' not in src, \
        "start() stores the raw url again"
    assert 'conv_url = resp.get("url") or' not in src, \
        "the conversation lookup stores the raw url again"


def test_the_client_has_no_hardcoded_host():
    """The reported symptom sounded like a hardcoded localhost. It was not, and
    this keeps it that way — the base must always come from configuration."""
    import ast
    src = (ROOT / "engine/lib/openhands_client.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    # Parse rather than grep. The first version scanned raw lines and flagged
    # this file's OWN docstring, which merely DESCRIBES the localhost:4999
    # symptom — prose about a bug is not the bug. Docstrings are excluded here;
    # comments never reach the AST at all.
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)

    offenders = [n.value for n in ast.walk(tree)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and n.value not in docstrings
                 and ("localhost" in n.value or "127.0.0.1" in n.value)]
    assert not offenders, f"hardcoded host in the OpenHands client: {offenders}"
    assert "OPENHANDS_URL" in src, "the base is no longer read from configuration"

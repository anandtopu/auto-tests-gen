"""Discovering that an OPTIONAL system is absent must be cheap.

`openhands_client.health()` tries four candidate paths in sequence. Measured
against a closed local port, all four returned the identical WinError 10061
(connection refused) and the probe took 8.3s -- 4 x 2.07s of asking a host that
had already refused the connection whether a DIFFERENT path might answer. On an
unroutable host each attempt costs the full TIMEOUT (15s), so the same loop is
up to 60s.

Found by profiling the test suite, not by reading the code: five tests in
test_standalone.py stood out (53s / 17s / 17s / 8.6s / 8.3s) and every one of
them was an "OpenHands unreachable" case. The fixture even carries the comment
"closed port: connect fails fast, no network wait" -- the assumption was
reasonable and simply not true.

Who pays it: `make check-integrations` (whose EXIT CODE is a CI contract), the
dashboard's /api/openhands/health endpoint (which blocks a request thread), and
the LLM provider adapter. OpenHands is optional by design and its outage is
`degraded`, never fatal -- so the cost of establishing that must not be four
timeouts.

The candidate list exists for a server that ANSWERS on a different route, and
that case already returns on any code < 500. The only remaining reason to keep
walking the list is a 5xx, which proves the server is there.
"""
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine" / "lib"))
import openhands_client as oc


def _fake_request(calls, outcomes):
    """Record every attempt; return queued (code, body, err) triples."""
    def _req(method, url, headers, body=None, timeout=None):
        calls.append(url)
        return outcomes[min(len(calls) - 1, len(outcomes) - 1)]
    return _req


def _configured(monkeypatch, base="http://oh.invalid"):
    monkeypatch.setattr(oc, "_configured", lambda: (base, "key"))
    monkeypatch.delenv("OPENHANDS_HEALTH_PATH", raising=False)


def test_a_refused_connection_is_not_retried_on_other_paths(monkeypatch):
    """THE DEFECT. A path cannot answer when the connection never happened."""
    calls = []
    _configured(monkeypatch)
    monkeypatch.setattr(oc, "_request", _fake_request(
        calls, [(None, None, "[WinError 10061] connection refused")]))

    out = oc.health()
    assert out["reachable"] is False
    assert len(calls) == 1, (
        f"asked {len(calls)} paths of a host that refused the connection; "
        f"each one costs a full connect attempt: {calls}")


def test_the_error_names_what_actually_happened(monkeypatch):
    """It used to report "no response from <base> on any health path". That was
    already misleading (all four failures were identical) and would be simply
    untrue now that only one is tried."""
    _configured(monkeypatch)
    monkeypatch.setattr(oc, "_request", _fake_request(
        [], [(None, None, "[WinError 10061] connection refused")]))
    out = oc.health()
    assert "10061" in out["error"], \
        f"the real cause was replaced by a summary: {out['error']!r}"
    assert "any health path" not in out["error"]


def test_a_5xx_still_walks_the_candidate_list(monkeypatch):
    """The list earns its keep here and ONLY here: the server answered, so a
    different route may be the healthy one. Breaking on 5xx too would turn this
    fix into a regression."""
    calls = []
    _configured(monkeypatch)
    monkeypatch.setattr(oc, "_request", _fake_request(calls, [
        (503, None, "HTTP 503"),          # answered, unhealthy -> keep looking
        (200, "{}", None),                # ...found it
    ]))
    out = oc.health()
    assert out["reachable"] is True
    assert len(calls) == 2, f"stopped walking the list on a 5xx: {calls}"


def test_an_answering_server_still_returns_on_the_first_path(monkeypatch):
    calls = []
    _configured(monkeypatch)
    monkeypatch.setattr(oc, "_request", _fake_request(calls, [(200, "{}", None)]))
    assert oc.health()["reachable"] is True
    assert len(calls) == 1


def test_a_rejected_key_is_still_reachable_with_a_hint(monkeypatch):
    """401/403 means the server is THERE -- reporting it unreachable would send
    an operator to check the network instead of the credential."""
    _configured(monkeypatch)
    monkeypatch.setattr(oc, "_request", _fake_request([], [(401, None, None)]))
    out = oc.health()
    assert out["reachable"] is True and out["http_code"] == 401
    assert "API key" in out["hint"]


def test_an_explicit_health_path_is_the_only_one_tried(monkeypatch):
    """OPENHANDS_HEALTH_PATH is the escape hatch for the trade-off this fix
    makes: a deployment whose health route is unusual pins it."""
    calls = []
    _configured(monkeypatch)
    monkeypatch.setenv("OPENHANDS_HEALTH_PATH", "/custom/health")
    monkeypatch.setattr(oc, "_request", _fake_request(
        calls, [(None, None, "refused")]))
    oc.health()
    assert calls == ["http://oh.invalid/custom/health"]


def test_the_probe_is_cheap_against_a_really_closed_port():
    """The measurement, not a mock: a real connect to a closed local port. The
    old loop took ~8.3s here. The bound is deliberately loose (one connect
    attempt plus slack) so this pins the SHAPE -- one attempt, not four -- and
    does not become a flaky timing assertion on a loaded machine.
    """
    import os
    prev = {k: os.environ.get(k) for k in ("OPENHANDS_URL", "OPENHANDS_API_KEY")}
    os.environ["OPENHANDS_URL"] = "http://127.0.0.1:1"
    os.environ["OPENHANDS_API_KEY"] = "x"
    try:
        start = time.time()
        out = oc.health()
        elapsed = time.time() - start
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert out["reachable"] is False
    assert elapsed < 6.0, (
        f"a refused connection took {elapsed:.1f}s; the four-path walk is back "
        f"(it measured 8.3s before this fix)")

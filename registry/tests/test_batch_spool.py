"""The batch spool: many requests, one batch, results back on the right keys.

The spool exists for WALL CLOCK, not for a bigger discount -- all Batches API
usage is 50% off, so slice 1's one-request batches already get the full saving.
Forty tickets as forty sequential batches is forty waits; as one batch it is
one. That framing matters here because it is what the tests defend: throughput
without losing track of whose result is whose.

Two failure modes are specific to N-in-one and are pinned hard:

  * correlation. Results may return in ANY order. With one request a positional
    read is merely sloppy; with forty it silently files ticket A's test plan
    under ticket B's key, and nothing downstream can detect that.
  * partial outcomes. One batch can end with some succeeded, some errored and
    some expired. Failing the whole drain throws away work already paid for;
    dropping the failures under-delivers silently. Every request gets an entry.
"""
import json
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

STATE = {"ended": True, "outcomes": {}, "submitted": None}


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _j(self, o, code=200):
        b = json.dumps(o).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        STATE["submitted"] = json.loads(self.rfile.read(n) or b"{}")
        self._j({"id": "msgbatch_spool", "processing_status": "in_progress"})

    def do_GET(self):
        if self.path.endswith("/results"):
            rows = []
            for cid, kind in STATE["outcomes"].items():
                if kind == "succeeded":
                    r = {"type": "succeeded",
                         "message": {"model": "m",
                                     "content": [{"type": "text",
                                                  "text": f"PLAN FOR {cid}"}],
                                     "usage": {"input_tokens": 10,
                                               "output_tokens": 5}}}
                elif kind in ("expired", "canceled"):
                    r = {"type": kind}
                else:
                    r = {"type": "errored", "error": {"type": "overloaded_error"}}
                rows.append({"custom_id": cid, "result": r})
            # REVERSED on purpose: results may come back in any order.
            body = "\n".join(json.dumps(r) for r in reversed(rows)).encode()
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._j({"id": "msgbatch_spool",
                 "processing_status": "ended" if STATE["ended"] else "in_progress",
                 "results_url": (f"http://{self.headers['host']}"
                                 f"/v1/messages/batches/msgbatch_spool/results")
                 if STATE["ended"] else None})


@pytest.fixture()
def spool(tmp_path, monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setenv("AIQE_BATCH_DIR", str(tmp_path / "batch"))
    monkeypatch.setenv("ANTHROPIC_BASE_URL", url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    for m in list(sys.modules):
        if m == "batch_spool":
            del sys.modules[m]
    import batch_spool
    STATE.update(ended=True, outcomes={}, submitted=None)
    yield batch_spool
    srv.shutdown()


def _spool_three(bs):
    ids = [bs.add(k, "testplan", "m", f"prompt for {k}")
           for k in ("PROJ-1", "PROJ-2", "PROJ-3")]
    return ids


def test_many_requests_go_out_as_one_batch(spool):
    """The whole point: one submission, not one per ticket."""
    _spool_three(spool)
    assert len(spool.pending()) == 3
    rec = spool.submit()
    assert len(STATE["submitted"]["requests"]) == 3, \
        "the spool did not submit them together"
    assert rec["id"] == "msgbatch_spool"
    assert spool.pending() == [], "the spool was not cleared after submission"


def test_every_request_carries_max_tokens(spool):
    _spool_three(spool)
    spool.submit()
    for r in STATE["submitted"]["requests"]:
        assert int(r["params"]["max_tokens"]) >= 1


def test_results_are_routed_by_custom_id_not_position(spool):
    """The stub returns results REVERSED. A positional read would file PROJ-1's
    plan under PROJ-3 -- wrong in a way nothing downstream can detect."""
    ids = _spool_three(spool)
    spool.submit()
    STATE["outcomes"] = {c: "succeeded" for c in ids}
    got = {r["key"]: r["text"] for r in spool.drain() if r.get("state") == "succeeded"}
    assert len(got) == 3
    for cid in ids:
        key = cid.split(":")[0]
        assert got[key] == f"PLAN FOR {cid}", \
            f"{key} received another request's result"


def test_a_partial_batch_keeps_the_good_results_and_names_the_bad(spool):
    """Failing the whole drain would discard work already paid for; dropping
    the failures would under-deliver in silence."""
    ids = _spool_three(spool)
    spool.submit()
    STATE["outcomes"] = {ids[0]: "succeeded", ids[1]: "expired",
                         ids[2]: "errored"}
    res = spool.drain()
    counts = spool.summarize(res)
    assert counts == {"succeeded": 1, "expired": 1, "errored": 1}, counts
    assert len(res) == 3, "a request vanished from the drain report"


def test_expired_says_not_billed_and_that_nothing_is_known(spool):
    ids = _spool_three(spool)
    spool.submit()
    STATE["outcomes"] = {c: "expired" for c in ids}
    for r in spool.drain():
        assert r["state"] == "expired"
        assert r["billed"] is False, "an expired request was reported as billed"
        assert "nothing is known" in r["detail"].lower(), (
            "expired reads as a verdict about the phase -- the model never saw "
            "the request")


def test_an_errored_request_is_billed_and_distinct_from_expired(spool):
    ids = _spool_three(spool)
    spool.submit()
    STATE["outcomes"] = {ids[0]: "errored", ids[1]: "expired", ids[2]: "succeeded"}
    by = {r["state"]: r for r in spool.drain()}
    assert by["errored"]["billed"] is True
    assert by["expired"]["billed"] is False


def test_a_batch_still_running_is_not_reported_as_producing_nothing(spool):
    _spool_three(spool)
    spool.submit()
    STATE["ended"] = False
    try:
        res = spool.drain()
    finally:
        STATE["ended"] = True
    assert len(res) == 1 and res[0]["state"] == "still_processing"
    assert "still billed" in res[0]["detail"] or "billed" in res[0]["detail"]
    assert not any(r.get("state") == "expired" for r in res)


def test_draining_twice_does_not_reprocess(spool):
    ids = _spool_three(spool)
    spool.submit()
    STATE["outcomes"] = {c: "succeeded" for c in ids}
    assert len(spool.drain()) == 3
    assert spool.drain() == [], "an already-drained batch was drained again"


def test_the_routing_table_survives_the_spool_being_cleared(spool):
    """submit() clears the spool, so key/phase must be recorded on the BATCH.
    A result whose key cannot be recovered is a plan nobody can find."""
    _spool_three(spool)
    spool.submit()
    assert spool.pending() == []
    rec = spool.batches()[0]
    assert {r["key"] for r in rec["requests"]} == {"PROJ-1", "PROJ-2", "PROJ-3"}
    assert all(r["phase"] == "testplan" for r in rec["requests"])


def test_submitting_an_empty_spool_is_not_an_error(spool):
    out = spool.submit()
    assert out["state"] == "empty" and "nothing spooled" in out["message"]
    assert STATE["submitted"] is None, "an empty batch was sent to the API"


def test_an_unreachable_api_leaves_the_spool_intact(spool, monkeypatch):
    """Losing spooled prompts because the network blipped would mean rebuilding
    them all; the operator must be able to just retry."""
    _spool_three(spool)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    import importlib
    importlib.reload(spool)
    with pytest.raises(RuntimeError, match="PROVIDER_UNREACHABLE"):
        spool.submit()
    assert len(spool.pending()) == 3, "the spool was emptied by a failed submit"


def test_a_missing_api_key_refuses_rather_than_falling_back(spool, monkeypatch):
    _spool_three(spool)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        spool.submit()
    assert len(spool.pending()) == 3


def test_status_reports_unknown_when_the_api_cannot_be_asked(spool, monkeypatch):
    """C13: 'we could not ask' is neither 'still running' nor 'done'."""
    _spool_three(spool)
    spool.submit()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    import importlib
    importlib.reload(spool)
    st = spool.status()
    assert st and st[0]["state"] == "unknown"
    assert "could not reach" in st[0]["detail"]

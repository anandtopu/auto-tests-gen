"""A network-facing handler must not trust the Content-Length it is sent.

Both servers read the body as
`self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))`, and the
receiver binds 0.0.0.0 in the Dockerfile AND in deploy/openshift/configmap.yaml.
Measured against it running:

  * `Content-Length: abc` raised ValueError out of the handler — no response
    line at all, traceback in the log.
  * `Content-Length: 10000000` with a 30-byte body BLOCKED the handler waiting
    for bytes that never came. Each such connection holds a worker thread, so a
    handful stop the trigger ingress accepting PR and JIRA events — silently,
    because nothing failed, it just never answered.
  * the 5 MB cap was applied AFTER the read and only on the results route, so
    the check that exists to prevent a huge allocation ran once the allocation
    had happened. 3 MB was accepted on /hooks/taskevent.

These are unit tests over a fake handler so they are fast and deterministic;
test_taskevent_receiver-style end-to-end coverage would need a live socket per
case. The last two assert the wiring, since a correct helper nothing calls
fixes nothing.
"""
import io
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import http_body  # noqa: E402


class _Handler:
    """Minimal stand-in: headers + rfile is all read_body touches."""

    def __init__(self, declared, body=b"", explode=False):
        self.headers = {} if declared is None else {"Content-Length": declared}
        self.rfile = _Rfile(body, explode)


class _Rfile:
    def __init__(self, body, explode):
        self.buf = io.BytesIO(body)
        self.explode = explode
        self.read_calls = 0

    def read(self, n):
        self.read_calls += 1
        # The SIZE ASKED FOR is the thing under test. Asserting on bytes consumed
        # cannot tell `read(5_000_000_000)` from `read(1024)` when the fake body
        # is 100 bytes — the first version of this file made exactly that mistake
        # and a mutation restoring the unbounded read passed it.
        self.max_requested = max(getattr(self, "max_requested", 0), n)
        if self.explode:
            raise TimeoutError("socket timeout")
        return self.buf.read(n)


def test_a_malformed_content_length_is_a_400_not_an_exception():
    raw, err = http_body.read_body(_Handler("abc"))
    assert raw is None and err[0] == 400
    assert "Content-Length" in err[1]["error"]


def test_a_negative_content_length_is_refused():
    raw, err = http_body.read_body(_Handler("-5"))
    assert raw is None and err[0] == 400


def test_an_oversize_declaration_is_refused_without_reading_it_all():
    """The whole point of the ordering change: refusing after allocating is not
    refusing. A declared 5 GB must cost nothing."""
    h = _Handler(str(5_000_000_000), b"x" * 100)
    raw, err = http_body.read_body(h, limit=1024)
    assert raw is None and err[0] == 413
    # The bounded drain may read, but no single read may ASK for more than the
    # limit we were already willing to accept — never the 5 GB declared.
    assert getattr(h.rfile, "max_requested", 0) <= 1024,         f"asked the socket for {h.rfile.max_requested} bytes on a refused request"


def test_an_oversize_body_is_drained_so_the_error_can_be_read():
    """A CI job posting a 6 MB results file should get the 413, not a transport
    reset, so a modest overage is drained first (bounded)."""
    h = _Handler(str(1500), b"x" * 1500)
    raw, err = http_body.read_body(h, limit=1024)
    assert err[0] == 413
    assert h.rfile.buf.tell() > 0, "nothing was drained; the client cannot read the 413"


def test_a_body_shorter_than_declared_is_answered_not_awaited():
    """The slowloris case at sub-limit sizes: answer rather than wait."""
    raw, err = http_body.read_body(_Handler("500", b"{}"), limit=1024)
    assert raw is None and err[0] == 400
    assert "shorter than Content-Length" in err[1]["error"]


def test_a_dead_peer_produces_no_response_and_no_traceback():
    """read raising means nobody is left to answer. Returning an error would
    make the caller write to a dead socket and log the traceback this change
    exists to remove."""
    raw, err = http_body.read_body(_Handler("10", explode=True))
    assert raw is None and err is None


def test_a_normal_body_is_returned_unchanged():
    raw, err = http_body.read_body(_Handler("7", b"payload"), limit=1024)
    assert err is None and raw == b"payload"


def test_a_missing_content_length_reads_nothing():
    raw, err = http_body.read_body(_Handler(None, b"ignored"))
    assert err is None and raw == b""


# --- wiring: a guard nothing calls guards nothing ---------------------------

def test_both_servers_use_the_guard_and_not_the_raw_read():
    for rel in ("bin/taskevent_receiver.py", "bin/dashboard_server.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "http_body.read_body(" in src, f"{rel} does not use the guard"
        assert 'self.rfile.read(int(' not in src, \
            f"{rel} still reads the body on the client's word"


def test_both_handlers_set_a_socket_timeout():
    """Without a class-level timeout the socket read has no deadline, and the
    blocking case measured above comes straight back."""
    import re
    for rel in ("bin/taskevent_receiver.py", "bin/dashboard_server.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        cls = src.split("class Handler(BaseHTTPRequestHandler):", 1)[1][:600]
        assert re.search(r"^\s+timeout\s*=\s*\d+", cls, re.M), \
            f"{rel}'s Handler sets no timeout"


def test_the_results_route_keeps_its_larger_limit():
    """A JUnit XML upload is legitimately bigger than a TaskEvent; collapsing
    them to one limit would start rejecting real CI posts."""
    src = (ROOT / "bin/taskevent_receiver.py").read_text(encoding="utf-8")
    assert "5 * 1024 * 1024 if path ==" in src, \
        "the results route no longer gets its own limit"

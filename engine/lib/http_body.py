#!/usr/bin/env python3
"""Read an HTTP request body without trusting the client that sent it.

Both servers did this:

    raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))

and both have the same three defects, measured against a running receiver that
binds 0.0.0.0 in the Dockerfile AND in deploy/openshift/configmap.yaml:

  1. `Content-Length: abc` raises ValueError out of the handler. The client gets
     no response line at all and a traceback lands in the log — an unparseable
     header is a 400, not a crash.

  2. `Content-Length: 10000000` with a short body BLOCKS the handler waiting for
     bytes that never arrive. Measured: no response, connection held open. Each
     such connection ties up a worker thread, so a handful of them stop the
     trigger ingress accepting PR and JIRA events — silently, because nothing
     failed, it just never answered.

  3. The size cap was applied AFTER the read and only on one path, so the check
     that exists to prevent a huge allocation ran once the allocation had already
     happened. A 3 MB body on /hooks/taskevent was accepted; a declared 5 GB one
     would have been read before the 413 could fire.

The fix is ordering: decide the limit from the ROUTE, refuse an oversize
declaration BEFORE reading, and bound the read itself so a lying header cannot
hold a thread. Handlers must also set a class-level `timeout`, or the socket
read has no deadline to hit.

Measured after the fix, against the same running receiver:

    malformed Content-Length      400   (was: no response + traceback)
    negative Content-Length       400
    declared 10 MB, sent 30 B     413   (was: blocked, no response)
    declared 5 GB, sent 100 B     413   instantly, nothing allocated
    declared 900 KB, sent 1 B     connection closed after 30 s, no traceback
                                        (was: held forever)
    3 MB on /hooks/ci/results     200   (under that route's 5 MB limit)

An oversize declaration is drained first ONLY when the overage is plausible
(<= DRAIN_MAX_MULTIPLE x limit), so a real CI job that already sent a slightly
too-big body can read the 413 instead of being reset. Beyond that the refusal is
immediate and undrained: waiting on a claim is the thread-holding this guard
exists to stop. Measured against the deployed receiver — a declared 10 MB with a
25-byte body answered 413 at once, where draining it had stopped answering at all.

Two limits stated rather than glossed. A gross overage sees a connection reset
rather than a readable 413 — that is the trade for not waiting. And a SUB-limit
lie is still bounded by the socket timeout, not refused instantly: 30 seconds of
one thread, not forever.
"""

DEFAULT_MAX = 1 * 1024 * 1024          # a TaskEvent is a few hundred bytes
# Above this multiple of the limit, a declaration is not a body in flight —
# it is a claim we refuse without waiting for.
DRAIN_MAX_MULTIPLE = 2
_CHUNK = 64 * 1024


def _drain(handler, upto):
    """Read and discard at most `upto` bytes so an error response can be read by
    a client that is still sending. Chunked, so memory stays flat."""
    left = upto
    try:
        while left > 0:
            got = handler.rfile.read(min(_CHUNK, left))
            if not got:
                break
            left -= len(got)
    except Exception:                                        # noqa: BLE001
        pass


def read_body(handler, limit=DEFAULT_MAX):
    """Return (raw_bytes, error) where error is (code, {"error": ...}) or None.

    error == None with raw == None means the connection is gone and there is
    nobody left to answer — the caller should simply stop, not try to respond.
    """
    declared = handler.headers.get("Content-Length")
    if declared is None:
        return b"", None
    try:
        n = int(str(declared).strip())
    except (TypeError, ValueError):
        return None, (400, {"error": "invalid Content-Length header"})
    if n < 0:
        return None, (400, {"error": "negative Content-Length"})
    if n > limit:
        # Refuse BEFORE reading it all — refusing after allocating is not
        # refusing. Two failure modes pull in opposite directions here, and the
        # size of the OVERAGE is what separates them:
        #
        #   a MODEST overage (a 6 MB JUnit post against a 5 MB cap) is a real CI
        #   job that has already sent its body. Answering without reading it
        #   resets the connection and it never sees the 413 — measured as an
        #   intermittent failure in test_oversize_payload_is_refused. So drain
        #   it, bounded and discarded, then answer.
        #
        #   a GROSS overage (10 MB declared against a 1 MB cap) is the lying
        #   Content-Length this guard exists for. Draining there means waiting
        #   for bytes that will never arrive, holding a worker thread until the
        #   socket timeout — measured against the DEPLOYED receiver, where a
        #   declared 10 MB with a 25-byte body stopped answering entirely where
        #   it had previously returned 413 at once.
        #
        # So drain only when the client plausibly has the body in flight.
        if n <= DRAIN_MAX_MULTIPLE * limit:
            _drain(handler, n)
        return None, (413, {"error": f"request body over {limit // 1024} KB"})
    if n == 0:
        return b"", None
    try:
        raw = handler.rfile.read(n)
    except Exception:                                        # noqa: BLE001
        # Socket timeout (the handler's `timeout` fired), reset, or any other
        # transport failure. The peer is not listening; saying so would raise
        # again inside the error path.
        return None, None
    if len(raw) < n:
        # The client declared more than it sent and then stopped. Answer rather
        # than wait: an honest 400 costs one round trip, waiting costs a thread.
        return None, (400, {"error": "request body shorter than Content-Length"})
    return raw, None

"""The Message Batches adapter, driven against a stub of the real API.

Batch trades latency for a 50% discount, which makes its FAILURE modes the
interesting part: a request that expired was never sent to the model and was
never billed, and reporting that as "the phase produced nothing" would be an
established negative we have no basis for (C13). Likewise an in-flight batch is
not a completed phase that cost nothing.

Most of these run the adapter end to end against a local HTTP stub rather than
grepping the source, because the things worth pinning -- custom_id correlation,
status handling, what gets written to the result JSON -- are behaviour.

Correlation is pinned deliberately: the API documents that results may come
back in ANY order, and its own example returns the second request before the
first. The stub therefore always answers with a decoy row FIRST.
"""
import json
import pathlib
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "adapters/llm/batch.sh"

sys.path.insert(0, str(ROOT / "engine/lib"))
import budget  # noqa: E402
import llm_runner  # noqa: E402
import work_queue  # noqa: E402

BASH = work_queue.bash_exe()          # plain "bash" is WSL's stub on Windows

# What the stub should say the single request's result was.
STATE = {"result_type": "succeeded", "ended": True}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):        # keep pytest output clean
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        ln = int(self.headers.get("content-length") or 0)
        req = json.loads(self.rfile.read(ln) or b"{}")
        STATE["custom_id"] = req["requests"][0]["custom_id"]
        STATE["params"] = req["requests"][0]["params"]
        self._json({"id": "msgbatch_stub", "processing_status": "in_progress",
                    "results_url": None})

    def do_GET(self):
        if self.path.endswith("/results"):
            ours = {"custom_id": STATE["custom_id"],
                    "result": _result_for(STATE["result_type"])}
            decoy = {"custom_id": "some-other-request",
                     "result": _result_for("succeeded", text="DECOY - wrong row")}
            # Decoy FIRST: results may arrive in any order.
            body = (json.dumps(decoy) + "\n" + json.dumps(ours) + "\n").encode()
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if "/v1/messages/batches" in self.path:
            ended = STATE["ended"]
            self._json({"id": "msgbatch_stub",
                        "processing_status": "ended" if ended else "in_progress",
                        "results_url": (f"http://{self.headers['host']}"
                                        f"/v1/messages/batches/msgbatch_stub/results")
                        if ended else None,
                        "data": []})
            return
        self._json({"error": "unexpected path"}, 404)


def _result_for(kind, text="PLAN OK"):
    if kind == "succeeded":
        return {"type": "succeeded",
                "message": {"model": "claude-sonnet-4-6",
                            "content": [{"type": "text", "text": text}],
                            "usage": {"input_tokens": 1200, "output_tokens": 340}}}
    if kind in ("expired", "canceled"):
        return {"type": kind}
    return {"type": "errored", "error": {"type": "invalid_request_error"}}


@pytest.fixture()
def stub():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _run(stub_url, out_json, prompt="write a plan", **env):
    e = {"ANTHROPIC_BASE_URL": stub_url, "ANTHROPIC_API_KEY": "sk-test",
         "AIQE_BATCH_POLL_SECONDS": "1", "AIQE_BATCH_MAX_WAIT_MIN": "1",
         "PATH": __import__("os").environ.get("PATH", ""),
         "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")}
    e.update(env)
    return subprocess.run(
        [BASH, str(ADAPTER), "run_phase", "claude-sonnet-4-6", "1", "", str(out_json)],
        input=prompt, capture_output=True, text=True, env=e, cwd=str(ROOT))


# --- capability / policy (the config-time guarantees) ------------------------

def test_batch_is_completion_class_so_agentic_phases_are_refused():
    r = subprocess.run([BASH, str(ADAPTER), "capabilities"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.stdout.strip() == "completion"
    err = llm_runner.check_assignment("generate", "batch")
    assert err and "cannot run agentic phase" in err, (
        "an agentic phase on batch is not refused -- every turn needing a client "
        "tool result would be another ~1h batch submission")
    assert "llm.phase_providers" in err, "the refusal does not name the fix"


def test_a_completion_phase_is_accepted():
    """Control: a guard that refuses everything would pass the test above."""
    assert llm_runner.check_assignment("testplan", "batch") is None


def test_tool_policy_is_never_wider_than_asked():
    r = subprocess.run([BASH, str(ADAPTER), "tool_policy", "Read,Write,Edit"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.stdout.split()[0] == "none", (
        "a batch request has no client tool loop; granting tools here would let "
        "'advisory' phases appear to have write access")


def test_unknown_verb_exits_64():
    r = subprocess.run([BASH, str(ADAPTER), "no_such_verb"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 64


def test_a_bare_switch_to_batch_is_not_blocked_by_model_mapping():
    """batch IS Anthropic, so the tier ids are already its ids. Demanding a
    mapping would refuse the switch while asking for claude->claude."""
    assert llm_runner.check_model_mapping("testplan", "batch") is None
    assert "batch" in llm_runner.PROVIDERS


# --- behaviour against the stub ---------------------------------------------

def test_a_succeeded_batch_writes_the_normalized_result(tmp_path, stub):
    out = tmp_path / "r.json"
    r = _run(stub, out)
    assert r.returncode == 0, r.stderr
    got = json.loads(out.read_text(encoding="utf-8"))
    assert got["result"] == "PLAN OK", "the decoy row was picked up"
    assert got["provider"] == "batch"
    assert got["usage"]["input_tokens"] == 1200
    assert "total_cost_usd" not in got, (
        "the Batch API reports TOKENS, not dollars -- emitting a cost would "
        "claim a precision we do not have")


def test_the_result_is_matched_by_custom_id_not_by_position(tmp_path, stub):
    """The stub always returns a decoy FIRST, because the API documents that
    results may be returned in any order."""
    out = tmp_path / "r.json"
    assert _run(stub, out).returncode == 0
    assert "DECOY" not in out.read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", ["expired", "canceled"])
def test_expired_and_canceled_say_nothing_is_known_and_nothing_was_billed(
        kind, tmp_path, stub):
    STATE["result_type"] = kind
    try:
        out = tmp_path / "r.json"
        r = _run(stub, out)
    finally:
        STATE["result_type"] = "succeeded"
    assert r.returncode != 0, f"a {kind} request was treated as a usable result"
    assert not out.exists(), "a result file was written for a request that never ran"
    low = r.stderr.lower()
    assert "not billed" in low, f"{kind} does not say it was not billed"
    assert "nothing is known" in low, (
        f"{kind} reads as a verdict about the phase -- it is not one, the model "
        "never saw the request")


def test_an_errored_request_is_distinct_from_an_expired_one(tmp_path, stub):
    STATE["result_type"] = "errored"
    try:
        r = _run(stub, tmp_path / "r.json")
    finally:
        STATE["result_type"] = "succeeded"
    assert r.returncode != 0
    assert "BATCH_ERRORED" in r.stderr
    assert "not billed" not in r.stderr.lower(), (
        "an errored request is not the same as one that never ran")


def test_a_batch_that_never_ends_is_not_reported_as_a_failed_phase(tmp_path, stub):
    STATE["ended"] = False
    try:
        r = _run(stub, tmp_path / "r.json", AIQE_BATCH_MAX_WAIT_MIN="1",
                 AIQE_BATCH_POLL_SECONDS="1")
    finally:
        STATE["ended"] = True
    assert r.returncode != 0
    assert "BATCH_STILL_PROCESSING" in r.stderr
    assert "still" in r.stderr.lower() and "billed" in r.stderr.lower(), (
        "giving up on the wait does not cancel the batch -- it keeps running and "
        "is still billed, which the operator must be told")
    assert "msgbatch_stub" in r.stderr, (
        "the batch id is not named, so the operator cannot retrieve or cancel it")


def test_the_submitted_request_carries_max_tokens(tmp_path, stub):
    """The API rejects a request without it, and the failure would surface as an
    opaque 400 from inside the adapter."""
    out = tmp_path / "r.json"
    assert _run(stub, out).returncode == 0
    assert int(STATE["params"]["max_tokens"]) >= 1


# --- auth and cost ----------------------------------------------------------

def test_a_missing_api_key_refuses_and_names_the_fix(tmp_path):
    r = subprocess.run(
        [BASH, str(ADAPTER), "run_phase", "m", "1", "", str(tmp_path / "r.json")],
        input="x", capture_output=True, text=True,
        env={"PATH": __import__("os").environ.get("PATH", ""),
             "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})
    assert r.returncode != 0
    assert "ANTHROPIC_API_KEY" in r.stderr
    assert "no silent fallback" in r.stderr.lower(), (
        "C12: falling back to the paid synchronous provider would charge full "
        "price to someone who switched to batch to spend less")
    assert "cli" in r.stderr.lower() or "subscription" in r.stderr.lower(), (
        "the CLI-login-does-not-work-here trap is the whole reason this fails")


def test_unpriced_batch_spend_is_unknown_never_zero():
    cost, basis = budget.priced("batch", "claude-sonnet-4-6",
                                {"input_tokens": 10_000_000, "output_tokens": 1_000_000})
    assert basis == "unknown" and cost is None, (
        "an unpriced provider reporting 0 understates a real bill -- the R1 defect")


# --- batch inside the generic spend-control stack ---------------------------
#
# No batch-specific cost code was written, and that is the finding rather than
# an omission: budget.py became provider-agnostic in multi-LLM slice 3, so batch
# already flows through priced() / unpriced() / enforceability() correctly. What
# was missing is proof that it KEEPS doing so, which is what these pin.

def test_unpriced_batch_makes_the_ceiling_report_itself_unenforceable(tmp_path):
    """The R1 defect, one provider along: an unpriced provider records cost 0,
    so the exit-77 ceiling never fires and the run reports "within budget"
    while burning tokens. Cost cannot be invented -- but the INABILITY to
    enforce has to be visible and has to name the provider."""
    led = tmp_path / "cost.tsv"
    led.write_text(
        "testplan\t0\t0\t100\tclaude-sonnet-4-6\t1200000\t340000\t0\t0\t1\tbatch\tunknown\n",
        encoding="utf-8")
    calls, provs = budget.unpriced(str(led))
    assert calls == 1 and provs == ["batch"]

    import os
    old = os.environ.get("MAX_COST_USD_PER_RUN")
    os.environ["MAX_COST_USD_PER_RUN"] = "5"
    try:
        state, msg = budget.enforceability(str(led))
    finally:
        if old is None:
            os.environ.pop("MAX_COST_USD_PER_RUN", None)
        else:
            os.environ["MAX_COST_USD_PER_RUN"] = old
    assert state in ("partial", "unenforceable"), \
        f"unpriced batch spend reported as {state!r} -- i.e. as enforced"
    assert "batch" in msg, "the message does not name the provider that cannot be priced"
    assert "pricing" in msg.lower(), "the message does not name the fix"


def test_priced_batch_is_estimated_never_reported(monkeypatch):
    """The Batch API returns tokens, not dollars. `reported` means the provider
    gave us a dollar figure; claiming it here would dress an estimate up as a
    billed number."""
    monkeypatch.setattr(budget, "_pricing",
                        lambda: {"batch": {"m": {"in": 1.50, "out": 7.50}}})
    cost, basis = budget.priced("batch", "m",
                                {"input_tokens": 1_000_000, "output_tokens": 200_000})
    assert basis == "estimated", f"batch spend claimed basis {basis!r}"
    assert cost == pytest.approx(3.0)


def test_the_50_percent_discount_is_not_applied_twice(monkeypatch):
    """org-config asks for rates ALREADY HALVED, so the discount lives in one
    place. If a later change also multiplies by 0.5 inside priced(), every batch
    bill is reported at a quarter of its real size -- an understatement, the
    direction this whole stack exists to prevent."""
    monkeypatch.setattr(budget, "_pricing",
                        lambda: {"batch": {"m": {"in": 2.0, "out": 0.0}}})
    cost, _ = budget.priced("batch", "m",
                            {"input_tokens": 1_000_000, "output_tokens": 0})
    assert cost == pytest.approx(2.0), (
        "priced() no longer returns tokens x the configured rate -- a second "
        f"discount is being applied somewhere (got {cost}, expected 2.0)")

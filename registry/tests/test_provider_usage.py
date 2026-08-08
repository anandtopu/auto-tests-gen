"""TCA-C2 provider usage port, credential and adapter conformance pins."""
import json
import os
import pathlib
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import provider_usage
import work_queue

BASH = work_queue.bash_exe()


def run_adapter(name, days="7", env=None):
    clean = dict(os.environ)
    clean.pop("ANTHROPIC_ADMIN_KEY", None)
    clean.update(env or {})
    return subprocess.run(
        [BASH, str(ROOT / "adapters" / name), "usage", days],
        cwd=ROOT, env=clean, text=True, capture_output=True, encoding="utf-8",
    )


def test_unconfigured_and_unsupported_adapters_never_invent_zero():
    for adapter in ("llm/claude.sh", "llm/codex.sh", "llm/ollama.sh",
                    "llm/openhands.sh"):
        result = run_adapter(adapter)
        assert result.returncode == 0, result.stderr
        value = json.loads(result.stdout)
        assert value["state"] == "unavailable"
        assert "cost" not in value and "cost_usd" not in value


def test_mock_usage_is_deterministic_provider_reported_fixture():
    first = json.loads(run_adapter("mock/llm.sh", "1").stdout)
    second = json.loads(run_adapter("mock/llm.sh", "30").stdout)
    assert first == second
    assert first["state"] == "available"
    assert first["cost"] == {"amount_usd": "12.34", "currency": "USD",
                             "basis": "provider-reported"}


def test_every_usage_adapter_rejects_malformed_windows():
    for adapter in ("llm/claude.sh", "llm/codex.sh", "llm/ollama.sh",
                    "llm/openhands.sh", "mock/llm.sh"):
        result = run_adapter(adapter, "not-days")
        assert result.returncode == 64, adapter


def test_engine_calls_port_and_validates_contract(monkeypatch):
    monkeypatch.setattr(provider_usage.settings_store, "load_env_into", lambda _target: None)
    value = provider_usage.retrieve(7, "mock")
    assert value["provider"] == "mock" and value["schema"] == 1
    for bad in (0, 366, True):
        try:
            provider_usage.retrieve(bad, "mock")
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid days {bad!r}")


def test_engine_contains_no_vendor_endpoint_or_credential_name():
    source = (ROOT / "engine/lib/provider_usage.py").read_text(encoding="utf-8")
    assert "api.anthropic.com" not in source
    assert "ANTHROPIC_ADMIN_KEY" not in source
    assert "cost_report" not in source


def test_engine_rejects_malicious_available_amounts_and_windows():
    base = {"schema": 1, "state": "available", "provider": "mock",
            "window": {"starting_at": "2026-01-01T00:00:00Z",
                       "ending_at": "2026-01-02T00:00:00Z"},
            "cost": {"amount_usd": "1.25", "currency": "USD",
                     "basis": "provider-reported"}}
    for amount in ("NaN", "Infinity", "-0.01", "not-money"):
        value = json.loads(json.dumps(base))
        value["cost"]["amount_usd"] = amount
        try:
            provider_usage._validate(value, "mock")
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted unsafe provider amount {amount}")
    value = json.loads(json.dumps(base))
    value["window"]["ending_at"] = value["window"]["starting_at"]
    try:
        provider_usage._validate(value, "mock")
    except ValueError:
        pass
    else:
        raise AssertionError("accepted an empty provider window")


def test_claude_cost_report_paginates_and_converts_fractional_cents():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            seen.append((parsed.path, query,
                         {key.lower(): value for key, value in self.headers.items()}))
            page = query.get("page", [""])[0]
            if page:
                body = {"data": [{"results": [{"amount": "0.55", "currency": "USD"}]}],
                        "has_more": False, "next_page": None}
            else:
                body = {"data": [{"results": [{"amount": "123.45", "currency": "USD"}]}],
                        "has_more": True, "next_page": "next-secret-cursor"}
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    key = "synthetic-admin-key-never-print"
    try:
        result = run_adapter("llm/claude.sh", "7", {
            "ANTHROPIC_ADMIN_KEY": key,
            "ANTHROPIC_ADMIN_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            "AIQE_USAGE_NOW": "2026-01-08T18:45:00Z",
        })
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert result.returncode == 0, result.stderr
    assert key not in result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["cost"]["amount_usd"] == "1.24"
    assert value["window"] == {"starting_at": "2026-01-01T00:00:00Z",
                               "ending_at": "2026-01-08T00:00:00Z",
                               "bucket_width": "1d"}
    assert len(seen) == 2
    assert seen[0][0] == "/v1/organizations/cost_report"
    assert seen[0][1]["starting_at"] == ["2026-01-01T00:00:00Z"]
    assert seen[1][1]["page"] == ["next-secret-cursor"]
    assert seen[0][2]["x-api-key"] == key
    assert seen[0][2]["anthropic-version"] == "2023-06-01"


def test_malformed_provider_response_is_unavailable_not_zero():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            raw = b'{"unexpected":true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = run_adapter("llm/claude.sh", "1", {
            "ANTHROPIC_ADMIN_KEY": "synthetic",
            "ANTHROPIC_ADMIN_BASE_URL": f"http://127.0.0.1:{server.server_port}",
        })
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
    value = json.loads(result.stdout)
    assert value["state"] == "unavailable"
    assert "cost" not in value

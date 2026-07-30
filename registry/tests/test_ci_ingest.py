"""CI results auto-ingest (roadmap 1.1) — POST /hooks/ci/results on the receiver.

The scorecard's "test health" read n/a forever because ingestion was a manual make
target nobody ran. This endpoint lets a CI job post raw JUnit XML at the end of a
run. Pins: token gating, raw-XML body (not JSON-wrapped), matched/unmatched counts
in the response, health.json actually updated, oversize/garbage rejected.
"""
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

JUNIT = """<?xml version="1.0"?>
<testsuite tests="3">
  <testcase classname="orders" name="PROJ-88: applies % discount"/>
  <testcase classname="orders" name="PROJ-61: gets an order by id">
    <failure message="500 != 200"/>
  </testcase>
  <testcase classname="misc" name="totally unknown test nobody cataloged"/>
</testsuite>
"""


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _post(url, data, token=None, ctype="application/xml"):
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", ctype)
    if token:
        req.add_header("X-AIQE-Token", token)
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read().decode())


@pytest.fixture
def receiver(tmp_path):
    procs = []

    def start(**env_extra):
        port = _free_port()
        env = {**os.environ, "AIQE_HOOK_PORT": str(port), "AIQE_MOCK": "1",
               "AIQE_HEALTH_FILE": str(tmp_path / "health.json"),
               "AIQE_QUEUE_FILE": str(tmp_path / "queue.json"),
               "AIQE_OPENHANDS_DIR": str(tmp_path / "oh")}
        env.pop("AIQE_HOOK_TOKEN", None)
        env.update(env_extra)
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "bin/taskevent_receiver.py")],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, env=env)
        procs.append(proc)
        base = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/healthz", timeout=2)
                break
            except (ConnectionError, urllib.error.URLError, OSError):
                if proc.poll() is not None:
                    raise RuntimeError("receiver died on startup")
                time.sleep(0.25)
        return base

    yield start
    for p in procs:
        p.terminate()


def test_junit_xml_posts_straight_in_and_updates_health(receiver, tmp_path):
    base = receiver()
    code, body = _post(base + "/hooks/ci/results", JUNIT.encode())
    assert code == 200 and body["ok"] is True
    # Two cataloged titles matched (one pass, one fail); the unknown one reported —
    # a silent 200 would hide mapping rot from the CI job's log.
    assert body["matched"] == 2
    assert body["unmatched"] == 1

    health = json.loads((tmp_path / "health.json").read_text(encoding="utf-8"))
    assert len(health) == 2
    failed = [h for h in health.values() if h["failures"] == 1]
    assert len(failed) == 1 and failed[0]["last_status"] == "failed"


def test_token_gating_matches_the_other_hooks(receiver):
    base = receiver(AIQE_HOOK_TOKEN="s3cret")
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base + "/hooks/ci/results", JUNIT.encode())
    assert e.value.code == 401
    code, body = _post(base + "/hooks/ci/results", JUNIT.encode(), token="s3cret")
    assert code == 200 and body["matched"] == 2


def test_garbage_and_empty_bodies_are_rejected_not_ingested(receiver, tmp_path):
    base = receiver()
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base + "/hooks/ci/results", b"this is not xml at all <<<")
    assert e.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as e2:
        _post(base + "/hooks/ci/results", b"   ")
    assert e2.value.code == 400
    assert not (tmp_path / "health.json").exists(), "nothing must be written"


def test_oversize_payload_is_refused(receiver):
    base = receiver()
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base + "/hooks/ci/results", b"<x>" + b"a" * (5 * 1024 * 1024) + b"</x>")
    assert e.value.code == 413

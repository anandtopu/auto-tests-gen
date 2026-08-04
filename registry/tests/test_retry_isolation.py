"""The retry limiter's counters must not be fillable by anything but retries.

`make review` failed on a rate limit nobody had hit: the suite's own fixture
attempts were being written to the ESTATE's reports/retries.json, because
retry_policy had no isolation knob. Measured at the point of failure, the
fixture key PROJ-9 held three genuine attempts, so the limiter was refusing a
test on evidence the test itself had manufactured — and in a deployment it
would refuse an OPERATOR on the same kind of evidence.

This is the third instance of one shape this session (the audit log, run
history, and now retry counters): a shared store with no test-side redirect,
where the damage lands somewhere nobody looks until it denies something.
"""
import importlib
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import retry_policy  # noqa: E402


def test_the_suite_is_not_writing_to_the_estates_counters():
    assert (os.environ.get("AIQE_RETRIES_FILE") or "").strip(), \
        "conftest no longer redirects the retry store"
    assert retry_policy.FILE.resolve() != (ROOT / "reports/retries.json").resolve(), \
        "retry attempts from tests land in the estate's real rate limits"


def test_recording_an_attempt_does_not_touch_the_estate_file():
    """A probe, not just a path comparison — isolation that is 'proven' by a
    record() that quietly does nothing would pass the assertion above."""
    estate = ROOT / "reports/retries.json"
    before = estate.read_text(encoding="utf-8") if estate.exists() else None
    retry_policy.record("ZZ-ISOLATION-PROBE")
    assert retry_policy.attempts("ZZ-ISOLATION-PROBE"), \
        "record() wrote nothing — isolation cannot be proven by a no-op"
    after = estate.read_text(encoding="utf-8") if estate.exists() else None
    assert after == before, "the estate's retry counters were modified by a test"


def test_the_knob_actually_redirects(tmp_path, monkeypatch):
    target = tmp_path / "elsewhere/retries.json"
    monkeypatch.setenv("AIQE_RETRIES_FILE", str(target))
    try:
        importlib.reload(retry_policy)
        assert retry_policy.FILE == target
        retry_policy.record("ZZ-KNOB")
        assert target.exists(), "the knob was read but not honoured on write"
    finally:
        monkeypatch.undo()
        importlib.reload(retry_policy)

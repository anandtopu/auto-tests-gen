"""Regression tests for the email notification feature (engine/lib/email_notify.py,
adapters/notify/email.sh, and the notify-port wiring)."""
import email as emaillib
import pathlib, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import email_notify, work_queue

BASH = work_queue.bash_exe()


@pytest.fixture
def mock_email(tmp_path, monkeypatch):
    """Force mock mode and redirect the .eml output under a temp ROOT."""
    monkeypatch.setenv("AIQE_MOCK", "1")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    d = tmp_path / "out" / "mock-email"
    monkeypatch.setattr(email_notify, "MOCK_DIR", d)
    return d


def _read_eml(d):
    files = list(d.glob("*.eml"))
    assert len(files) == 1, files
    return emaillib.message_from_bytes(files[0].read_bytes())


def test_send_mock_writes_eml_with_mime_parts(mock_email):
    status = email_notify.send("Hello subject", "plain body",
                               "<h1>html body</h1>", to="a@x.com,b@y.com")
    assert "[mock-email]" in status and "AIQE_MOCK=1" in status
    msg = _read_eml(mock_email)
    assert msg["Subject"] == "Hello subject"
    assert msg["To"] == "a@x.com, b@y.com"
    assert msg["From"] and msg["Message-ID"]
    assert msg.is_multipart()                          # text + html alternative
    types = {p.get_content_type() for p in msg.walk()}
    assert "text/plain" in types and "text/html" in types


def test_send_requires_recipients(mock_email, monkeypatch):
    monkeypatch.delenv("SMTP_TO", raising=False)
    with pytest.raises(SystemExit, match="no recipients"):
        email_notify.send("s", "b")


def test_smtp_to_default_used_when_no_explicit_to(mock_email, monkeypatch):
    monkeypatch.setenv("SMTP_TO", "team@x.com; lead@x.com")
    email_notify.send("s", "b")
    msg = _read_eml(mock_email)
    assert msg["To"] == "team@x.com, lead@x.com"       # ';' normalized to ', '


def test_no_smtp_host_falls_back_to_mock_even_if_not_mock_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("AIQE_MOCK", "0")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    d = tmp_path / "mock"; monkeypatch.setattr(email_notify, "MOCK_DIR", d)
    status = email_notify.send("s", "b", to="x@y.com")
    assert "no SMTP_HOST" in status and d.glob("*.eml")


def test_generators_produce_subject_and_bodies():
    subj, text, html = email_notify.team_report_email(days=30)
    assert subj.startswith("[AI QE] Team status report") and "last 30d" in subj
    assert "QA Team Report" in text and html and "<html" in html.lower()
    subj, text, html = email_notify.review_digest()
    assert subj.startswith("[AI QE]") and "review" in subj.lower()
    assert "<table" in html or "clear" in html.lower()


def test_run_summary_from_a_real_run_record():
    import glob, json
    recs = [f for f in glob.glob(str(ROOT / "reports/runs/*.json"))
            if pathlib.Path(f).name not in ("reviews.json", "queue.json", "hooks-seen.json")]
    if not recs:
        pytest.skip("no run records present")
    run_id = json.load(open(recs[0], encoding="utf-8"))["run_id"]
    subj, text, html = email_notify.run_summary(run_id)
    assert run_id in text and subj.startswith("[AI QE]")
    assert "gate" in text.lower()


def test_run_summary_unknown_id_errors():
    with pytest.raises(SystemExit, match="no run record"):
        email_notify.run_summary("nope-000")


def test_notify_email_adapter_post_verb(tmp_path, monkeypatch):
    # the adapter runs from ROOT; AIQE_MOCK=1 makes it write to out/mock-email/
    env = {**__import__("os").environ, "AIQE_MOCK": "1", "SMTP_TO": "x@y.com"}
    env.pop("SMTP_HOST", None)
    r = subprocess.run([BASH, "adapters/notify/email.sh", "post",
                        "Subject line\nbody line two"],
                       cwd=ROOT, capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    assert "[mock-email]" in r.stdout


def test_email_adapter_unknown_verb_exits_64():
    r = subprocess.run([BASH, "adapters/notify/email.sh", "bogus"],
                       cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 64


def test_smtp_keys_documented_and_in_settings_spec():
    import settings_store
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    documented = {l.split("=", 1)[0].strip() for l in example.splitlines()
                  if "=" in l and not l.lstrip().startswith("#")}
    for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "SMTP_USER",
              "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TO", "NOTIFY_KIND"):
        assert k in documented, f"{k} missing from .env.example"
        assert k in settings_store.ALL_KEYS, f"{k} missing from Settings SPEC"


def test_qa_cli_email_subcommand():
    import os
    env = {**os.environ, "AIQE_MOCK": "1", "SMTP_TO": "x@y.com"}
    env.pop("SMTP_HOST", None)
    r = subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), "email", "digest"],
                       cwd=ROOT, capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    assert "[mock-email]" in r.stdout

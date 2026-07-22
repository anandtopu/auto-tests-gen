#!/usr/bin/env python3
"""Email notifications — SMTP integration + generators for platform events.

Composes MIME emails (plain text + optional HTML) and sends them via an SMTP
server configured through SMTP_* settings (Settings view / .env). Generators turn
platform state into ready-to-send emails: a per-run gate summary, a pending-review
digest, and the full team status report.

Mock-safe: when AIQE_MOCK=1 or no SMTP_HOST is configured, emails are written to
`out/mock-email/*.eml` instead of being sent — so the feature is demoable and
testable without a real server (same pattern as the Confluence/JIRA mocks).

CLI:
  email_notify.py send "<subject>" "<body>" [--html f.html] [--to a@b,c@d]
  email_notify.py run  <RUN_ID> [--to ...]         # gate summary for one run
  email_notify.py digest [--to ...]                # pending-review backlog
  email_notify.py report [--days N] [--release X] [--to ...]   # team report
"""
import os, pathlib, smtplib, ssl, sys, time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
MOCK_DIR = ROOT / "out/mock-email"


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def smtp_config():
    """Read SMTP settings from the environment (honouring .env via settings_store)."""
    try:
        import settings_store
        settings_store.load_env_into()
    except Exception:                                   # settings_store optional
        pass
    return {
        "host": _env("SMTP_HOST"),
        "port": int(_env("SMTP_PORT") or "587"),
        "user": _env("SMTP_USER"),
        "password": _env("SMTP_PASSWORD"),
        "from": _env("SMTP_FROM") or _env("SMTP_USER") or "ai-qe@platform.local",
        "security": (_env("SMTP_SECURITY") or "starttls").lower(),   # starttls|ssl|none
        "to": [a.strip() for a in _env("SMTP_TO").replace(";", ",").split(",") if a.strip()],
    }


def _recipients(to, cfg):
    if isinstance(to, str):
        to = [a.strip() for a in to.replace(";", ",").split(",") if a.strip()]
    return list(to) if to else list(cfg["to"])


def _build(subject, text, html, sender, to):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="ai-qe.platform.local")
    msg.set_content(text or "")
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def send(subject, text, html=None, to=None):
    """Compose and deliver an email. Returns a human-readable status string.

    Falls back to writing an .eml file under out/mock-email/ when AIQE_MOCK=1 or no
    SMTP_HOST is configured — never raises just because SMTP is unset."""
    cfg = smtp_config()
    rcpts = _recipients(to, cfg)
    if not rcpts:
        raise SystemExit("no recipients: pass --to or set SMTP_TO")
    msg = _build(subject, text, html, cfg["from"], rcpts)

    mock = os.environ.get("AIQE_MOCK", "1") == "1" or not cfg["host"]
    if mock:
        MOCK_DIR.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in subject)[:60]
        # int(time()) only — deterministic filename base; index avoids same-second clobber
        stamp = int(time.time())
        p = MOCK_DIR / f"{stamp}-{safe}.eml"
        i = 1
        while p.exists():
            p = MOCK_DIR / f"{stamp}-{safe}-{i}.eml"; i += 1
        p.write_bytes(bytes(msg))
        reason = "AIQE_MOCK=1" if os.environ.get("AIQE_MOCK", "1") == "1" else "no SMTP_HOST"
        try:
            shown = p.relative_to(ROOT).as_posix()
        except ValueError:
            shown = p.as_posix()
        return f"[mock-email] ({reason}) wrote {shown} -> {', '.join(rcpts)}"

    sec = cfg["security"]
    if sec == "ssl":
        server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=30,
                                  context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=30)
    try:
        server.ehlo()
        if sec == "starttls":
            server.starttls(context=ssl.create_default_context()); server.ehlo()
        if cfg["user"]:
            server.login(cfg["user"], cfg["password"])
        server.send_message(msg)
    finally:
        try: server.quit()
        except Exception: pass
    return f"sent '{subject}' to {', '.join(rcpts)} via {cfg['host']}:{cfg['port']}"


# --------------------------------------------------------------- generators

def _run_record(run_id):
    import json
    p = ROOT / f"reports/runs/{run_id}.json"
    if not p.exists():
        raise SystemExit(f"no run record: {run_id}")
    return json.load(open(p, encoding="utf-8"))


def _html_doc(title, body_html):
    """Self-contained, email-client-safe HTML (inline-ish styles, no external refs)."""
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>"
            "<style>body{font:14px/1.5 -apple-system,Segoe UI,Arial,sans-serif;color:#1a1a19;"
            "max-width:680px;margin:0 auto;padding:20px}"
            "table{border-collapse:collapse;width:100%;margin:10px 0}"
            "th,td{text-align:left;padding:6px 10px;border-bottom:1px solid #e4e2de}"
            "th{color:#5f5e5b}code{background:#f4f3f0;padding:1px 5px;border-radius:4px}"
            "h1{font-size:19px}</style></head><body>" + body_html +
            "<hr><p style='color:#8a8880;font-size:12px'>Sent by the AI QE Platform.</p>"
            "</body></html>")


def run_summary(run_id):
    """(subject, text, html) summarising one pipeline run's gate outcomes."""
    import review_state
    r = _run_record(run_id)
    key = r["trigger"]["key"]
    overall = r.get("overall", "?")
    rel = review_state.load().get(key, {}).get("release", "")
    subject = f"[AI QE] {key}: {overall}" + (f" ({rel})" if rel else "")
    lines = [f"AI QE run {run_id} for {key}", f"Trigger: {r['trigger']['type']}",
             f"Overall: {overall}" + (f"   Release: {rel}" if rel else ""), "", "Gate results:"]
    rows = ""
    for g in r.get("gates", []):
        sha = (g.get("commit") or "")[:7]
        lines.append(f"  - {g['test_repo']}: {g['status']}" + (f" @{sha}" if sha else ""))
        rows += (f"<tr><td><code>{g['test_repo']}</code></td><td>{g['status']}</td>"
                 f"<td><code>{sha or '—'}</code></td></tr>")
    html = _html_doc(subject,
                     f"<h1>{key} — {overall}</h1>"
                     f"<p>Run <code>{run_id}</code> · {r['trigger']['type']}"
                     + (f" · release {rel}" if rel else "") + "</p>"
                     "<table><tr><th>test repo</th><th>gate</th><th>commit</th></tr>"
                     + (rows or "<tr><td colspan=3>no gates</td></tr>") + "</table>")
    return subject, "\n".join(lines), html


def review_digest():
    """(subject, text, html) listing keys awaiting team review, oldest first."""
    import review_state
    now = time.time()
    reviews = review_state.load()
    pending = sorted(
        ((k, e, (now - e.get("updated", now)) / 86400) for k, e in reviews.items()
         if e.get("status") in ("pending_review", "in_review")),
        key=lambda x: -x[2])
    subject = f"[AI QE] {len(pending)} item(s) awaiting review"
    if not pending:
        return subject, "The review board is clear.", _html_doc(subject,
            "<h1>Review board is clear</h1><p>Nothing is awaiting team review.</p>")
    lines = ["Keys awaiting team review (oldest first):", ""]
    rows = ""
    for k, e, age in pending:
        rel = e.get("release", "") or "—"
        lines.append(f"  - {k}  [{e['status']}]  release {rel}  waiting {age:.1f}d")
        rows += (f"<tr><td><b>{k}</b></td><td>{e['status']}</td><td>{rel}</td>"
                 f"<td>{age:.1f} day(s)</td></tr>")
    html = _html_doc(subject, f"<h1>{len(pending)} awaiting review</h1>"
                     "<table><tr><th>key</th><th>status</th><th>release</th>"
                     f"<th>waiting</th></tr>{rows}</table>")
    return subject, "\n".join(lines), html


def team_report_email(days=None, release=None):
    """(subject, text, html) — the full team status report as an email."""
    import team_report
    md = team_report.to_markdown(days, release)
    scope = (f"last {days}d" if days else "all time") + (f" · {release}" if release else "")
    subject = f"[AI QE] Team status report ({scope})"
    try:
        import export_plan
        html = export_plan.md_to_html_doc(md, subject)
    except Exception:
        html = None
    return subject, md, html


# --------------------------------------------------------------- CLI

def _main(argv):
    sys.stdout.reconfigure(encoding="utf-8")
    if not argv:
        sys.exit(__doc__)
    cmd, rest = argv[0], argv[1:]

    def opt(name, default=None):
        return rest[rest.index(name) + 1] if name in rest else default
    to = opt("--to")

    if cmd == "send":
        pos = [a for a in rest if not a.startswith("--")]
        subject, body = (pos + ["", ""])[:2]
        html = None
        if opt("--html"):
            html = pathlib.Path(opt("--html")).read_text(encoding="utf-8")
        print(send(subject, body, html, to))
    elif cmd == "run":
        rid = [a for a in rest if not a.startswith("--")][0]
        print(send(*run_summary(rid), to=to))
    elif cmd == "digest":
        print(send(*review_digest(), to=to))
    elif cmd == "report":
        days = opt("--days")
        print(send(*team_report_email(int(days) if days else None, opt("--release")), to=to))
    else:
        sys.exit(f"unknown command: {cmd}\n{__doc__}")


if __name__ == "__main__":
    _main(sys.argv[1:])

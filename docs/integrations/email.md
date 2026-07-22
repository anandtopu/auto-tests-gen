# Email (SMTP) integration

The platform sends email via any standard SMTP server — for run notifications
(Notify port), review digests, and the team status report. It is stdlib-only
(`smtplib`), so there is nothing to install.

## Configure

Set these in the dashboard **Settings** view (Email section) or `.env`:

| Setting | Example | Notes |
|---|---|---|
| `SMTP_HOST` | `smtp.gmail.com` | Unset ⇒ emails are written to `out/mock-email/` instead of sent |
| `SMTP_PORT` | `587` | `587` for STARTTLS, `465` for SSL, `25` for plain relay |
| `SMTP_SECURITY` | `starttls` | `starttls` \| `ssl` \| `none` |
| `SMTP_USER` | `ai-qe@example.com` | Omit for an unauthenticated internal relay |
| `SMTP_PASSWORD` | *(secret)* | App password / token; write-only in the Settings UI |
| `SMTP_FROM` | `ai-qe@example.com` | Defaults to `SMTP_USER` |
| `SMTP_TO` | `qa-team@example.com,lead@example.com` | Default recipients (CSV) |

## Provider quick-reference

| Provider | Host | Port | Security | Auth |
|---|---|---|---|---|
| Gmail / Google Workspace | `smtp.gmail.com` | 587 | starttls | account + [App Password](https://support.google.com/accounts/answer/185833) |
| Office 365 | `smtp.office365.com` | 587 | starttls | mailbox user + password |
| Amazon SES | `email-smtp.<region>.amazonaws.com` | 587 | starttls | SES SMTP credentials |
| Internal relay | your relay host | 25 | none | usually none |

## Use it

```bash
# Send on demand (mock-writes to out/mock-email/ until SMTP_HOST is set)
make email KIND=report DAYS=7 TO=qa-team@example.com
python3 bin/qa.py email run <RUN_ID>          # one run's gate summary
python3 bin/qa.py email digest                # pending-review backlog

# Make every pipeline run email its summary (first line = subject)
export NOTIFY_KIND=email        # or 'both' for Slack + email
```

The dashboard Overview **Team report** card also has an **Email** button.

## Verify

```bash
# Mock: confirm an .eml is produced with the right subject/recipients
python3 bin/qa.py email digest --to you@example.com
ls out/mock-email/

# Real: after setting SMTP_* for a live server
python3 bin/qa.py email report --to you@example.com   # -> "sent '...' to ... via host:port"
```

If a real send fails (auth, TLS, connection), the CLI surfaces the SMTP error and the
dashboard endpoint returns HTTP 502 — a run's best-effort notification never aborts the
pipeline.

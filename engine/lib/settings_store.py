#!/usr/bin/env python3
"""Integration settings store — the dashboard Settings view's backend.

Reads and writes the gitignored `.env` (the same file every real adapter loads
its credentials from) so integrations can be configured from the UI instead of
a text editor. Secrets are WRITE-ONLY through this API: reads report whether a
secret is set, never its value, so the dashboard can be served or snapshotted
without leaking credentials. Unknown keys are rejected — the editable surface
is exactly SPEC, which is conformance-tested against `.env.example`.

Path override for tests: AIQE_ENV_FILE.
"""
import os, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fs_lock


def env_file():
    return pathlib.Path(os.environ.get("AIQE_ENV_FILE") or ROOT / ".env")


# Sections mirror the supported integrations (docs/integrations/). Field keys:
# env (the .env variable), label, secret (write-only), options (select),
# default (effective value when unset), help (placeholder text).
SPEC = [
    {"section": "General", "hint": "Adapter mode and the LLM credential.",
     "fields": [
        {"env": "AIQE_MOCK", "label": "Adapter mode",
         "options": [["1", "mock adapters (demo)"], ["0", "real adapters"]],
         "default": "1"},
        {"env": "SCM_KIND", "label": "SCM adapter",
         "options": [["github", "GitHub"], ["bitbucket", "Bitbucket Cloud"],
                     ["stash", "Bitbucket Server / DC (Stash)"]],
         "default": "github"},
        {"env": "ANTHROPIC_API_KEY", "label": "Anthropic API key", "secret": True},
     ]},
    {"section": "GitHub", "hint": "Used when SCM adapter is GitHub.",
     "fields": [
        {"env": "GITHUB_TOKEN", "label": "GitHub token", "secret": True,
         "help": "fine-grained: contents RW on feature branches"},
     ]},
    {"section": "Bitbucket Cloud", "hint": "Used when SCM adapter is Bitbucket Cloud.",
     "fields": [
        {"env": "BITBUCKET_TOKEN", "label": "App password / access token", "secret": True},
     ]},
    {"section": "Bitbucket Server / Stash",
     "hint": "Used when SCM adapter is Stash (Bitbucket Server/DC).",
     "fields": [
        {"env": "STASH_URL", "label": "Base URL", "help": "https://stash.company.com"},
        {"env": "STASH_PROJECT", "label": "Project key", "help": "ENG"},
        {"env": "STASH_TOKEN", "label": "HTTP access token", "secret": True},
     ]},
    {"section": "JIRA", "hint": "Tracker port: tickets, comments, attachments.",
     "fields": [
        {"env": "JIRA_URL", "label": "JIRA base URL",
         "help": "https://your-domain.atlassian.net"},
        {"env": "ATLASSIAN_MCP_TOKEN", "label": "Atlassian API token", "secret": True,
         "help": "service account token (shared with Confluence)"},
     ]},
    {"section": "Confluence", "hint": "Knowledge port: linked docs + test-plan publishing.",
     "fields": [
        {"env": "CONFLUENCE_URL", "label": "Confluence base URL",
         "help": "https://your-domain.atlassian.net/wiki"},
        {"env": "CONFLUENCE_SPACE", "label": "Default space", "default": "QA"},
     ]},
    {"section": "OpenHands", "hint": "Orchestrator (Path 1) + make smoke-openhands.",
     "fields": [
        {"env": "OPENHANDS_URL", "label": "Agent Server URL"},
        {"env": "OPENHANDS_API_KEY", "label": "API key", "secret": True},
        {"env": "AIQE_SANDBOX_IMAGE", "label": "Sandbox image",
         "default": "ai-qe-sandbox:latest"},
        {"env": "AIQE_CONTROL_REPO", "label": "Control repo", "help": "org/ai-qe-control"},
        {"env": "AIQE_SMOKE_TICKET", "label": "Smoke-test ticket", "help": "PROJ-123"},
        {"env": "AIQE_SMOKE_REPO", "label": "Smoke-test repo"},
        {"env": "AIQE_SMOKE_PR", "label": "Smoke-test PR number"},
     ]},
    {"section": "CI/CD (Jenkins)", "hint": "CICD port: result ingestion triggers.",
     "fields": [
        {"env": "JENKINS_URL", "label": "Jenkins URL"},
        {"env": "JENKINS_USER", "label": "Jenkins user"},
        {"env": "JENKINS_API_TOKEN", "label": "API token", "secret": True},
     ]},
    {"section": "Notify & telemetry",
     "hint": "Slack notifications and Splunk telemetry.",
     "fields": [
        {"env": "SLACK_WEBHOOK_URL", "label": "Slack webhook URL", "secret": True},
        {"env": "SPLUNK_HEC_URL", "label": "Splunk HEC URL"},
        {"env": "SPLUNK_HEC_TOKEN", "label": "Splunk HEC token", "secret": True},
     ]},
    {"section": "Budgets", "hint": "Per-run cost and wall-clock guardrails.",
     "fields": [
        {"env": "MAX_COST_USD_PER_RUN", "label": "Max cost per run (USD)",
         "default": "4.00"},
        {"env": "MAX_WALLCLOCK_MIN", "label": "Max wall-clock (min)", "default": "25"},
     ]},
]

ALL_KEYS = {f["env"]: f for s in SPEC for f in s["fields"]}


def _parse(text):
    vals = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        v = v.split(" #", 1)[0].strip().strip('"').strip("'")
        vals[k.strip()] = v
    return vals


def load():
    f = env_file()
    return _parse(f.read_text(encoding="utf-8")) if f.exists() else {}


def get_settings():
    """SPEC with current values; secret values are masked to a boolean."""
    vals = load()
    out = []
    for sec in SPEC:
        fields = []
        for f in sec["fields"]:
            v = vals.get(f["env"], "")
            fields.append({**f, "set": bool(v),
                           "value": "" if f.get("secret") else (v or f.get("default", ""))})
        out.append({"section": sec["section"], "hint": sec.get("hint", ""),
                    "fields": fields})
    return out


def save(updates):
    """Merge `updates` ({ENV: value}) into .env, preserving unrelated lines and
    comments. Empty value clears the key's value in place."""
    unknown = sorted(k for k in updates if k not in ALL_KEYS)
    if unknown:
        raise SystemExit(f"unknown setting(s): {', '.join(unknown)}")
    for k, v in updates.items():
        if not isinstance(v, str) or "\n" in v or "\r" in v:
            raise SystemExit(f"{k}: value must be a single-line string")
        opts = ALL_KEYS[k].get("options")
        if opts and v and v not in [o[0] for o in opts]:
            raise SystemExit(f"{k}: must be one of {', '.join(o[0] for o in opts)}")
    path = env_file()
    with fs_lock.lock(path):
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        pending = dict(updates)
        for i, line in enumerate(lines):
            s = line.strip()
            if s.startswith("#") or "=" not in s:
                continue
            k = s.split("=", 1)[0].strip()
            if k in pending:
                lines[i] = f"{k}={pending.pop(k)}"
        lines += [f"{k}={v}" for k, v in pending.items()]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return {"updated": sorted(updates)}


if __name__ == "__main__":
    import json
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "get":
        print(json.dumps(get_settings(), indent=2))
    elif len(sys.argv) > 3 and sys.argv[1] == "set":
        print(json.dumps(save({sys.argv[2]: sys.argv[3]})))
    else:
        sys.exit("usage: settings_store.py get | set <ENV> <value>")

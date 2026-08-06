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
import os, pathlib, re, sys

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
        {"env": "AIQE_STATUS_URL", "label": "Build-status link URL",
         "help": "URL the ai-qe PR build status links to (e.g. the dashboard)"},
        {"env": "ANTHROPIC_API_KEY", "label": "Anthropic API key", "secret": True},
        {"env": "AIQE_SSO_HEADER", "label": "SSO identity header",
         "help": "e.g. X-Forwarded-User — set ONLY behind a reverse proxy that "
                 "terminates auth and overwrites this header; fails closed (401) "
                 "when the header is missing. The value signs approvals."},
        {"env": "AIQE_SSL_VERIFY", "label": "SSL verification",
         "options": [["1", "enabled (default)"], ["0", "disabled (corporate CA / self-signed)"]],
         "default": "1"},
        {"env": "AIQE_HTTPS_PROXY", "label": "HTTPS proxy",
         "help": "e.g. http://desktop.proxy.vzwcorp.com:5150 — used for all outbound "
                 "HTTPS calls (OpenHands, JIRA, Stash, Anthropic). Leave blank for "
                 "direct connections."},
        {"env": "AIQE_NO_PROXY", "label": "No-proxy bypass",
         "help": "comma-separated hostnames/domains that should bypass the proxy, "
                 "e.g. .verizon.com,localhost,127.0.0.1 — internal Stash/JIRA hosts "
                 "typically don't need the proxy and should be listed here."},
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
        {"env": "STASH_PROJECT", "label": "Default project key",
         "help": "fallback only — repos can live under different projects; set each "
                 "repo's URL to PROJECT/slug in the Repositories view"},
        {"env": "STASH_TOKEN", "label": "HTTP access token", "secret": True},
     ]},
    {"section": "JIRA", "hint": "Tracker port: tickets, comments, attachments.",
     "fields": [
        {"env": "JIRA_URL", "label": "JIRA base URL",
         "help": "https://your-domain.atlassian.net"},
        {"env": "ATLASSIAN_MCP_TOKEN", "label": "Atlassian API token", "secret": True,
         "help": "service account token (shared with Confluence)"},
        {"env": "ATLASSIAN_MCP_URL", "label": "Atlassian MCP URL",
         "default": "https://mcp.atlassian.com/v1/mcp"},
     ]},
    {"section": "Confluence", "hint": "Knowledge port: linked docs + test-plan publishing.",
     "fields": [
        {"env": "CONFLUENCE_URL", "label": "Confluence base URL",
         "help": "https://your-domain.atlassian.net/wiki"},
        {"env": "CONFLUENCE_SPACE", "label": "Default space", "default": "QA"},
     ]},
    {"section": "LLM provider",
     "hint": "Which model runs the pipeline's phases. Agentic phases "
             "(generate, validate) need claude or codex — they edit files in "
             "our workspace; completion providers (Ollama, delegated "
             "OpenHands) serve every other phase. Per-phase routing lives in "
             "org-config llm.phase_providers.",
     "fields": [
        {"env": "AIQE_LLM_PROVIDER", "label": "Provider",
         "options": [["", "org-config default (claude)"],
                     ["claude", "Claude Code (agentic)"],
                     ["ollama", "Ollama / local (completion phases only)"],
                     ["codex", "OpenAI Codex CLI (agentic)"],
                     ["openhands", "OpenHands (delegated, experimental)"]],
         "help": "empty = follow registry/org-config.yaml llm.provider"},
        {"env": "OLLAMA_URL", "label": "Ollama base URL",
         "help": "OpenAI-compatible /v1 (default http://localhost:11434/v1); "
                 "also serves LM Studio / vLLM / llama.cpp"},
        {"env": "OLLAMA_API_KEY", "label": "Ollama API key (optional)",
         "secret": True, "help": "local daemons need none"},
        {"env": "CODEX_BIN", "label": "Codex CLI binary",
         "help": "default `codex` on PATH; set an absolute path if it is "
                 "installed elsewhere"},
        {"env": "AIQE_OPENHANDS_PROVIDER",
         "label": "OpenHands as an LLM provider (experimental)",
         "options": [["", "off (default)"], ["1", "on — delegate phases"]],
         "help": "a phase becomes a conversation (minutes, not seconds) and "
                 "its spend lands on the OpenHands account, so it is tracked "
                 "as `unknown` cost. Completion phases only"},
        {"env": "OPENHANDS_PHASE_TIMEOUT",
         "label": "OpenHands phase timeout (seconds)",
         "help": "default 900; on timeout the phase fails and names the "
                 "conversation URL — nothing is written"},
     ]},
    {"section": "Cost levers",
     "hint": "Each cost-reduction mechanism has its own kill switch — any "
             "regression is one save away from off, per deployment.",
     "fields": [
        {"env": "AIQE_PHASE_CACHE", "label": "Content-addressed phase cache",
         "options": [["1", "on (default)"], ["0", "off"]], "default": "1"},
        {"env": "AIQE_CONTEXT_SCOPE", "label": "Retrieval-scoped context",
         "options": [["1", "on for org-config phases (default)"], ["0", "off — full AGENTS.md everywhere"]],
         "default": "1",
         "help": "per-phase policy lives in org-config context_scope:"},
        {"env": "AIQE_CONTEXT_RETRY", "label": "Missing-context retry",
         "options": [["1", "on (default)"], ["0", "off"]], "default": "1",
         "help": "a scoped phase reporting missing_context re-runs once on the full estate"},
        {"env": "AIQE_PLAN_REUSE", "label": "Semantic plan reuse",
         "options": [["0", "off (default until the quality eval)"], ["1", "on — plan mode only"]],
         "default": "0",
         "help": "adapts a similar HUMAN-APPROVED prior plan instead of authoring; always lands as draft"},
        {"env": "AIQE_TESTCASE_INDEX", "label": "Test-case knowledge index",
         "options": [["0", "off (default)"], ["1", "on — index individual test cases"]],
         "default": "0",
         "help": "S1 preview: JS/TS Playwright and node:test; inspect coverage with make index-stats"},
        {"env": "AIQE_TESTCASE_CHUNK_CHARS", "label": "Test-case chunk size",
         "default": "2000", "help": "characters per physical testcase chunk (512–6000)"},
        {"env": "AIQE_ARTIFACT_STORE", "label": "Durable agent artifact store",
         "options": [["0", "off (default)"], ["1", "on — persist addressed artifacts"]],
         "default": "0", "help": "B1: immutable blobs plus append-only provenance"},
        {"env": "AIQE_ARTIFACT_KEEP_RUNS", "label": "Artifact retention (runs)",
         "default": "200", "help": "newest producing runs retained by make maintain"},
        {"env": "AIQE_ARTIFACT_MAX_BYTES", "label": "Artifact size ceiling (bytes)",
         "default": "1048576", "help": "oversize artifacts are rejected before storage"},
        {"env": "AIQE_IMPACT_ANALYSIS", "label": "Change-to-test impact analysis",
         "options": [["0", "off (default)"], ["1", "on — propose impacted existing cases"]],
         "default": "0",
         "help": "A3 preview: deterministic-first; proposal only, never edits tests"},
        {"env": "AIQE_IMPACT_DETERMINISTIC_THRESHOLD", "label": "Impact deterministic threshold",
         "default": "0.70", "help": "endpoint/route and testcase identifier signals"},
        {"env": "AIQE_IMPACT_SEMANTIC_THRESHOLD", "label": "Impact semantic threshold",
         "default": "0.78", "help": "cosine threshold when testcase embeddings are available"},
        {"env": "AIQE_IMPACT_LEXICAL_THRESHOLD", "label": "Impact lexical threshold",
         "default": "0.30", "help": "separate threshold for zero-embedding lexical fallback"},
        {"env": "AIQE_ARTIFACT_REUSE", "label": "Artifact reuse and learning",
         "options": [["0", "off (default)"], ["1", "on — durable reuse plus advisory learning"]],
         "default": "0",
         "help": "B3 durable pure-phase reuse; also enables A4 duplicate advice and A6 outcome tie-breaks. Never replays generate/validate"},
        {"env": "AIQE_DUPLICATE_SEMANTIC_THRESHOLD", "label": "Duplicate semantic threshold",
         "default": "0.90", "help": "cosine threshold when testcase embeddings are available"},
        {"env": "AIQE_DUPLICATE_LEXICAL_THRESHOLD", "label": "Duplicate lexical threshold",
         "default": "0.55", "help": "separate threshold for zero-embedding lexical fallback"},
     ]},
    {"section": "Embeddings",
     "hint": "Optional semantic index (vector search over plans, specs, estate "
             "knowledge). Unset = lexical TF-IDF fallback, silently.",
     "fields": [
        {"env": "EMBED_URL", "label": "Embeddings API base URL",
         "help": "OpenAI-compatible /v1 base (Voyage, OpenAI, Azure, local "
                 "TEI/Ollama); /embeddings is appended"},
        {"env": "EMBED_API_KEY", "label": "Embeddings API key", "secret": True},
        {"env": "EMBED_MODEL", "label": "Embedding model",
         "help": "e.g. voyage-3-lite / text-embedding-3-small"},
        {"env": "EMBED_DIMS", "label": "Vector dimensions (optional)",
         "help": "passed through when the provider supports it"},
     ]},
    {"section": "OpenHands",
     "hint": "Optional orchestrator (Path 1). The pipeline never calls it — runs also "
             "trigger from CI, the TaskEvent receiver, or the work queue.",
     "fields": [
        {"env": "AIQE_OPENHANDS", "label": "Dependency mode",
         "help": "off = standalone · auto = hybrid, outage is non-fatal (default) · "
                 "required = an outage fails the connectivity check"},
        {"env": "OPENHANDS_URL", "label": "Agent Server URL"},
        {"env": "OPENHANDS_API_KEY", "label": "API key", "secret": True},
        {"env": "OPENHANDS_CONVERSATIONS_PATH", "label": "Conversations path",
         "help": "Leave blank to auto-detect (self-hosted /api/conversations, "
                 "falling back to Cloud /api/v1/app-conversations on 404/405). "
                 "Set it only to pin a non-standard endpoint."},
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
     "hint": "Notification channel(s) and Splunk telemetry.",
     "fields": [
        {"env": "NOTIFY_KIND", "label": "Notify channel",
         "options": [["slack", "Slack"], ["email", "Email (SMTP)"],
                     ["both", "Slack + Email"]], "default": "slack"},
        {"env": "SLACK_WEBHOOK_URL", "label": "Slack webhook URL", "secret": True},
        {"env": "SPLUNK_HEC_URL", "label": "Splunk HEC URL"},
        {"env": "SPLUNK_HEC_TOKEN", "label": "Splunk HEC token", "secret": True},
     ]},
    {"section": "Email (SMTP)",
     "hint": "Outbound email for run summaries, review digests and team reports. "
             "With no host set, emails are written to out/mock-email/ instead of sent.",
     "fields": [
        {"env": "SMTP_HOST", "label": "SMTP host", "help": "smtp.example.com"},
        {"env": "SMTP_PORT", "label": "SMTP port", "default": "587"},
        {"env": "SMTP_SECURITY", "label": "Security",
         "options": [["starttls", "STARTTLS"], ["ssl", "SSL/TLS"], ["none", "None"]],
         "default": "starttls"},
        {"env": "SMTP_USER", "label": "SMTP username"},
        {"env": "SMTP_PASSWORD", "label": "SMTP password", "secret": True},
        {"env": "SMTP_FROM", "label": "From address", "help": "ai-qe@example.com"},
        {"env": "SMTP_TO", "label": "Default recipients (csv)",
         "help": "qa-team@example.com, lead@example.com"},
     ]},
    {"section": "Budgets",
     "hint": "ENFORCED by the pipeline before every phase: over either limit the "
             "run aborts (exit 77) and notifies before the gate is reached. Cost "
             "comes from each claude phase's reported spend; mock/demo runs meter "
             "nothing and only the wall-clock limit applies to them.",
     "fields": [
        {"env": "MAX_COST_USD_PER_RUN", "label": "Max cost per run (USD)",
         "default": "4.00"},
        {"env": "MAX_WALLCLOCK_MIN", "label": "Max wall-clock (min)", "default": "25"},
     ]},
    # SDD adoption S3 (gap G1). These knobs existed only in org-config.yaml — a
    # file that ships INSIDE the image and cannot be written under
    # readOnlyRootFilesystem, so a deployed estate had no way to turn
    # spec-driven governance on at all. As env settings they land in .env,
    # which the Settings page already writes and R12 already relocates.
    #
    # The hint states the CONSEQUENCE, not the mechanism: someone deciding
    # whether to enable this needs to know what starts FAILING, not which YAML
    # key moves. Empty means "use org-config".
    {"section": "Spec-driven governance",
     "hint": "Off by default. Turning these on changes what the platform "
             "REFUSES. Roll out in two steps — warn until the signal is clean, "
             "then strict; enabling strict first only teaches people to route "
             "around the gate. Current state is shown in the Spec workflow view.",
     "fields": [
        {"env": "AIQE_REQUIREMENTS_GATE", "label": "Requirements gate",
         "options": [["", "use org-config (default: off)"],
                     ["0", "off — planning proceeds without approved requirements"],
                     ["1", "on — planning REFUSES until requirements are approved"]],
         "help": "When on, plan/jira runs stop with exit 65 until a human has "
                 "approved the EARS requirements for that ticket."},
        {"env": "AIQE_SPEC_ENFORCE", "label": "Spec satisfaction (gate)",
         "options": [["", "use org-config (default: off)"],
                     ["off", "off — the gate ignores uncovered scenarios"],
                     ["warn", "warn — uncovered scenarios reported, commit proceeds"],
                     ["strict", "strict — the gate REFUSES (exit 8) on an "
                                "uncovered, unwaived scenario"]],
         "help": "Start at warn. Strict blocks a commit when an approved "
                 "scenario has no test and no unexpired waiver."},
     ]},
]

ALL_KEYS = {f["env"]: f for s in SPEC for f in s["fields"]}

# Track which env-var keys were loaded from .env by load_env_into() so that
# refresh=True can update them on a subsequent call without touching keys that
# the user's shell environment set explicitly.
_env_file_keys: set = set()


def _parse(text):
    vals = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        v = v.strip()
        # Quoted values must round-trip what save() writes: strip the outer quotes
        # and undo bash single-quote escaping ('\''), and never comment-split
        # inside them — `SMTP_PASSWORD='pass #word'` is the whole value. Only an
        # UNQUOTED value can carry an inline ` #` comment.
        if len(v) >= 2 and v[0] == v[-1] == "'":
            v = v[1:-1].replace("'\\''", "'")
        elif len(v) >= 2 and v[0] == v[-1] == '"':
            v = v[1:-1]
        else:
            v = v.split(" #", 1)[0].strip()
        vals[k.strip()] = v
    return vals


def load():
    f = env_file()
    return _parse(f.read_text(encoding="utf-8")) if f.exists() else {}


def load_env_into(environ=None, refresh=False):
    """Apply configured settings as process-env DEFAULTS — for entry points that
    spawn adapters without going through pipeline.sh's `source .env` (dashboard
    server, publish/attach CLI paths, integration checks).

    Precedence, lowest to highest:

        aiqe.properties  <  .env  <  explicit environment

    .env is applied last of the two files so it wins over the properties baseline:
    .env is what the Settings page writes, and a UI save that appeared to do nothing
    because a properties file outranked it would be a miserable bug to diagnose.
    Anything already exported wins over both.

    refresh=True: also update keys that were previously set by an earlier call to
    this function (i.e. came from .env, not from the user's shell environment).
    Use this when re-checking settings after a UI save, so stale values from the
    startup load don't shadow newly-saved .env values.
    """
    environ = os.environ if environ is None else environ
    applied = []
    # ORDER MATTERS: each layer only fills keys that are still unset, so the layer
    # applied FIRST wins. .env therefore goes before properties, not after.
    current_env = load()
    for k, v in current_env.items():
        if v and (k not in environ or (refresh and k in _env_file_keys)):
            environ[k] = v
            applied.append(k)
            _env_file_keys.add(k)
    try:
        import props_file
        applied += props_file.apply_to(environ)     # baseline; may be absent
    except Exception:
        pass                                        # config file must never break startup
    # Map AIQE_* proxy vars to standard env vars so curl and urllib's default
    # ProxyHandler both pick them up automatically. Standard vars are only set
    # when the AIQE_ var is present — we never clear pre-existing values.
    proxy = environ.get("AIQE_HTTPS_PROXY", "").strip()
    if proxy:
        for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            if k not in environ:
                environ[k] = proxy
    no_proxy = environ.get("AIQE_NO_PROXY", "").strip()
    if no_proxy:
        for k in ("NO_PROXY", "no_proxy"):
            if k not in environ:
                environ[k] = no_proxy
    return applied


def get_settings():
    """SPEC with current values; secret values are masked to a boolean.

    Each field also reports `source`: "env-file" when .env supplies it, "properties"
    when only the properties baseline does. Without that an operator seeing a value
    they cannot find in .env has no way to learn where it came from.
    """
    vals = load()
    try:
        import props_file
        props = props_file.load()
    except Exception:
        props = {}
    out = []
    for sec in SPEC:
        fields = []
        for f in sec["fields"]:
            v = vals.get(f["env"], "")
            pv = props.get(f["env"], "")
            source = "env-file" if v else ("properties" if pv else "")
            effective = v or pv
            fields.append({**f, "set": bool(effective), "source": source,
                           "value": "" if f.get("secret")
                           else (effective or f.get("default", ""))})
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
        replaced = set()
        for i, line in enumerate(lines):
            s = line.strip()
            if s.startswith("#") or "=" not in s:
                continue
            k = s.split("=", 1)[0].strip()
            if k in updates:                # replace EVERY occurrence — bash source
                lines[i] = f"{k}={_shell_quote(updates[k])}"     # is last-wins
                replaced.add(k)
        lines += [f"{k}={_shell_quote(v)}" for k, v in updates.items()
                  if k not in replaced]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return {"updated": sorted(updates)}


def _shell_quote(v):
    """.env is `source`d by pipeline.sh — a value with spaces, $(), or backticks
    must be single-quoted so bash can never word-split or execute it."""
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]*", v):
        return v
    return "'" + v.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    import json
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "get":
        print(json.dumps(get_settings(), indent=2))
    elif len(sys.argv) > 3 and sys.argv[1] == "set":
        print(json.dumps(save({sys.argv[2]: sys.argv[3]})))
    else:
        sys.exit("usage: settings_store.py get | set <ENV> <value>")

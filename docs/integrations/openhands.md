# Integrating OpenHands Agents (Trigger Path 1 — primary)

OpenHands owns **orchestration and the sandbox**: trigger intake (PR labels, mentions,
webhooks), provisioning an ephemeral Docker runtime per run, and posting results back.
Claude Code runs *inside* that sandbox as the agent runtime; the platform's
`engine/pipeline.sh` is what the OpenHands conversation executes (architecture §4.3, §5.6).

## Prerequisites

- OpenHands (Cloud, or self-hosted Agent Server ≥ the resolver-enabled release) with
  Docker runtime available.
- This control repo reachable by the sandbox (e.g. `org/ai-qe-control`).
- The sandbox image built from [sandbox/Dockerfile](../../sandbox/Dockerfile) and pushed
  to a registry the runtime can pull (`ai-qe-sandbox:latest`).
- Credentials from the [credential matrix](README.md#credential-matrix) stored as
  OpenHands secrets (never baked into the image).

## Step 1 — Install the OpenHands integration on your repos

**GitHub estates:** install the OpenHands GitHub App on every source repo and test repo
you registered in `registry/repo-registry.yaml`. **Bitbucket estates:** connect the
Bitbucket workspace (OpenHands supports Bitbucket Cloud natively; for Stash/Server use
Path 3 — see [bitbucket-stash.md](bitbucket-stash.md)).

## Step 2 — Ship the microagent

Copy [triggers/openhands/microagents/ai-qe.md](../../triggers/openhands/microagents/ai-qe.md)
into each **source repo** at `.openhands/microagents/ai-qe.md`. It makes any OpenHands
conversation triggered on that repo:

1. clone this control repo,
2. register the Atlassian MCP (`sandbox/mcp-setup.sh`),
3. run `engine/pipeline.sh pr <repo> <pr>` (or `jira <KEY>`) exactly as triggered,
4. post the pipeline summary back — and *only* the summary; the gate owns all pushes.

The microagent's frontmatter `triggers: [ai-tests, ai-test-gen]` is what binds the
labels to the flow.

## Step 3 — Configure the runtime

In OpenHands settings (per repo or global):

| Setting | Value |
|---|---|
| Runtime image | `ai-qe-sandbox:latest` (from `sandbox/Dockerfile`) |
| Secrets | `ANTHROPIC_API_KEY`, `ATLASSIAN_MCP_TOKEN`, SCM token(s), `SLACK_WEBHOOK_URL` |
| Max iterations / budget | align with `budgets:` in `registry/org-config.yaml` |

The sandbox is ephemeral and network-restricted; `--dangerously-skip-permissions` inside
`engine/phases/run_phase.sh` is acceptable **only** under that isolation (§5.3) — never
run the pipeline this way on a shared host.

## Step 4 — Wire the triggers

- **Workflow A (PR):** label a PR `ai-tests`, or comment `@openhands-agent`. The
  resolver starts a conversation; the microagent routes it into the pipeline.
- **Workflow B (JIRA):** a Jira Automation rule fires a webhook to the OpenHands Agent
  Server REST API on label `ai-test-gen` — body per
  [triggers/jira-automation/webhook-setup.md](../../triggers/jira-automation/webhook-setup.md):
  `{"mode":"jira","key":"{{issue.key}}","updated":"{{issue.updated}}"}`.
  (OpenHands Cloud's native Jira integration is an alternative to the custom webhook.)

Idempotency: the receiver dedupes on `sha256(key + updated + workflow_version)` for
tickets and `(repo, PR#, head SHA)` for PRs — webhook redeliveries are no-ops.

The platform now ships that receiver as code: `make hook-server` runs
[bin/taskevent_receiver.py](../../bin/taskevent_receiver.py) (`POST /hooks/taskevent`,
schema in [triggers/task-event-schema.json](../../triggers/task-event-schema.json),
`X-AIQE-Token` auth, dedupe + enqueue + optional auto-drain). Point the Jira
Automation web request — or any webhook source — at it directly when you are not
using the OpenHands-native trigger path.

## Step 4b — Stream agent events back (recommended)

Without this, a long OpenHands conversation is opaque: the platform learns nothing
until the pipeline writes its own run record, and a conversation that dies never
reports at all. The Agent Server can POST its event stream to our TaskEvent receiver
instead — buffered, retried, and authenticated with a header you choose.

Add a `webhooks` entry to the Agent Server config (default
`workspace/openhands_agent_server_config.json`, or point
`OPENHANDS_AGENT_SERVER_CONFIG_PATH` at your own):

```json
{
  "webhooks": [
    {
      "base_url": "https://ai-qe-receiver.example.com/hooks/openhands",
      "headers": { "Authorization": "Bearer ${AIQE_HOOK_TOKEN}" },
      "event_buffer_size": 20,
      "flush_delay": 5,
      "num_retries": 3,
      "retry_delay": 5
    }
  ]
}
```

OpenHands appends the two paths itself, so the receiver sees:

| Path | Carries |
|---|---|
| `POST /hooks/openhands/events` | batches of agent events (state changes, actions, errors) |
| `POST /hooks/openhands/conversations` | conversation lifecycle: repo, status, failure reason |

Notes that matter in practice:

- **Auth**: `WebhookSpec` can only send arbitrary headers, so use
  `Authorization: Bearer <AIQE_HOOK_TOKEN>`. The receiver accepts that or the usual
  `X-AIQE-Token`.
- **These routes never enqueue work.** They are observability only — an agent cannot
  start pipeline runs by emitting events. Triggers still go to `/hooks/taskevent`.
- The receiver answers `200` even on a malformed batch, deliberately: a 5xx would just
  make OpenHands retry the same bad payload forever.
- Storage is bounded (recent conversations, capped event trail per conversation) and
  lives in `reports/openhands/state.json`. `python3 engine/lib/openhands_events.py prune`
  drops finished conversations older than a day.

View what arrived:

```bash
python3 bin/qa.py openhands       # conversations, status, event counts, errors
```

The dashboard's **Runs & reviews** view shows the same as an *OpenHands agent runs*
card whenever any conversation has been recorded.

For a definitive end-of-run answer you can also pull
`GET /api/conversations/{id}/agent_final_response` from the Agent Server rather than
polling conversation status.

## Step 4c — Block completion on a failing gate (recommended)

By default an agent decides for itself when a task is done. `.openhands/hooks.json`
binds OpenHands' blocking **`stop`** event to `.openhands/hooks/gate-check.sh`, so the
agent cannot declare success on work the quality gate would reject:

```json
{ "stop": [ { "matcher": "*", "hooks": [
    { "type": "command", "command": ".openhands/hooks/gate-check.sh",
      "timeout": 360, "async": false } ] } ] }
```

Ship both files in the control repo (they are already in this one). On finish, the
hook runs the gate in **check-only mode** against every writable test repo in
`workspace/tests/` and either allows completion or returns

```json
{"decision":"deny","reason":"The AI-QE quality gate would reject this work …"}
```

with exit 2, which blocks the agent and hands back the rule and the offending file
("a new spec has no catalog sidecar entry — every test must be born-mapped").

What it deliberately does **not** do:

- **It never commits or pushes.** Check-only mode (`AIQE_GATE_CHECK_ONLY=1`) runs all
  the checks and stops before writing, printing `GATE_STATUS=WOULD_COMMIT`.
  `engine/gate/gate.sh` remains the only component that writes, and the pipeline still
  runs it for real afterwards — the hook only moves the verdict earlier.
- **It never blocks on its own malfunction.** No workspace, no python, unreadable
  repo → it allows completion. The authoritative gate still runs later, so nothing
  escapes unchecked; it is simply caught further along.

Check it by hand the same way the hook does:

```bash
cd workspace/tests/<repo> && AIQE_GATE_CHECK_ONLY=1 bash ../../../engine/gate/gate.sh KEY <repo>
```

## Step 4d — Per-discipline conventions (UI vs API)

`AGENTS.md` is always-on, so every phase receives every convention — an agent writing
an API suite also gets UI page-object rules. OpenHands' **path-triggered** skills fix
that: conventions injected only when the agent actually touches a matching file.

`.agents/skills/e2e-{api,ui}-conventions/SKILL.md` are generated by
`bin/gen_path_skills.py` (`make skills`, also run by `make agents` and every
`bin/repos.py` change):

```yaml
---
name: e2e-api-conventions
paths:
  - "workspace/tests/e2e-api-tests-1/suites/**"
  - "suites/**/*.spec.js"
---
```

The globs are **derived from the registry** — each test repo declares `layer`
(api|ui) and `layout` (specs/fixtures/pages) — so adding a repo or renaming a specs
directory keeps the triggers correct with nothing hand-edited. The convention text
itself is single-sourced in `skills/e2e-<layer>-conventions/`.

Two things to know:

- **Path triggers do not fire in ACP-backed conversations** (the ACP server owns tool
  execution). `AGENTS.md` stays always-on, so the conventions still reach the agent
  there — just without the per-discipline split. This is an enhancement, not the only
  delivery path.
- Repo-relative spec globs are scoped to `*.spec.{js,ts}` on purpose: a bare
  `tests/**` would also match the control repo's own `tests/` directory and inject UI
  rules when an agent touched the gate harness.

## Step 5 — Verify

Start with the staged smoke test — it tells you exactly what is missing and validates
each layer of the chain with the credentials you have:

```bash
make smoke-openhands                       # all checks that have credentials
bash bin/smoke-openhands.sh --dry          # plumbing only, no network
AIQE_SMOKE_TRIGGER=1 make smoke-openhands  # ALSO starts a real conversation ($)
```

Stages: credentials → Agent Server reachability → sandbox image → live Jira read
(`AIQE_SMOKE_TICKET`) → live PR read (`AIQE_SMOKE_REPO`/`AIQE_SMOKE_PR`, per
`SCM_KIND`) → Confluence context → trigger config sanity → optional real conversation
(endpoint path configurable via `OPENHANDS_CONVERSATIONS_PATH` for your OpenHands
version). After a green Stage 8, confirm the PR/ticket comment, `make status`, and the
gate commit.

Then the end-to-end trigger path:

1. Open a trivial PR against a registered source repo touching a `testable_path`; label
   it `ai-tests`.
2. Watch the OpenHands conversation: it should clone the control repo, run the pipeline,
   and comment the per-test-repo summary (committed / no changes / quarantined).
3. Confirm the run record landed: `make status` in the control repo shows the run;
   `reports/runs/<RUN_ID>-<repo>.diff` holds the generated test code.
4. Human feedback loop: reply `@openhands use <repos>` on a clarification comment to
   re-trigger with pinned routing.

## Troubleshooting

| Symptom | Check |
|---|---|
| Conversation starts but no pipeline run | Microagent file present in the *source repo*? Frontmatter triggers match the label? |
| `GATE_REFUSED` (exit 6) | The sandbox cloned test repos without `.git` — clone through the SCM adapter, never `cp` |
| Clarification comment instead of tests | Routing confidence < threshold — expected for unmapped repos; fix registry/`covers` or reply with pinned routing |
| MCP tools missing in-session | `sandbox/mcp-setup.sh` ran? `ATLASSIAN_MCP_TOKEN` secret present? |
| Budget exceeded / runaway loops | Per-phase `--max-turns` + `budgets:` in `org-config.yaml`; OpenHands max-iterations as backstop |

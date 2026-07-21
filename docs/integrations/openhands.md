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

## Step 5 — Verify

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

# Integrating Jira (Tracker port) + Confluence context

Jira drives **Workflow B** (ticket → test plan → data → tests) and receives the run
summary as a comment. Confluence pages linked from the ticket are pulled as requirement
context during the analyze phase (Knowledge port). One Atlassian connection covers
Jira + Confluence + Bitbucket Cloud (§5.7, §5.10).

## Prerequisites

- Jira Cloud (Atlassian Remote MCP Server available). For Jira **Server/DC**, skip the
  MCP and use the REST adapter path below with your server base URL.
- A **service account** (not a personal account) with: read on the target project(s),
  comment write. Nothing else (NFR-5).

## Step 1 — Credentials

1. Create an API token for the service account (Atlassian account settings → Security →
   API tokens).
2. Fill `.env`:
   ```bash
   ATLASSIAN_MCP_URL=https://mcp.atlassian.com/v1/mcp
   ATLASSIAN_MCP_TOKEN=<service-account API token>
   ```
   Use the `/mcp` endpoint — the legacy `/sse` endpoint is unsupported after
   June 30, 2026 (§3.3). Admins can allowlist which MCP clients may connect; access
   respects existing Jira permissions.

## Step 2 — Two access paths (both are wired; keep both)

| Path | Used by | Setup |
|---|---|---|
| **Atlassian Remote MCP** (primary) | Claude Code phases in-session (read ticket, linked Confluence pages, comment) | `sandbox/mcp-setup.sh` registers it: `claude mcp add atlassian --transport http $ATLASSIAN_MCP_URL --header "Authorization: Bearer $ATLASSIAN_MCP_TOKEN"` |
| **REST adapter** (pipeline-side) | `engine/pipeline.sh` itself (`TRACKER get_item/comment`) | Edit [adapters/tracker/jira.sh](../../adapters/tracker/jira.sh): set `J=` to your site, e.g. `https://your-domain.atlassian.net/rest/api/3` (Server/DC: `https://jira.company.com/rest/api/2`) |

The adapter's verbs are the port contract: `get_item` (returns key, summary,
description, components, labels, linked_repos) and `comment`. `make conformance`
guards them.

## Step 3 — Routing configuration

Workflow B routes tickets to repos via `registry/repo-registry.yaml`:

```yaml
routing_hints:
  jira_component_map:        # Jira Component -> source repos
    Checkout: [web-storefront-ui, orders-api]
  jira_label_map:
    api-only: { restrict_layers: [api] }
    ui-only:  { restrict_layers: [ui] }
```

Map every component your teams actually use; unmapped tickets below the confidence
threshold get a clarifying comment instead of guessed tests (that is by design).
Dev-panel linked branches/PRs, when present, are the strongest routing evidence.

## Step 4 — The automation rule (trigger)

Per [triggers/jira-automation/webhook-setup.md](../../triggers/jira-automation/webhook-setup.md):

1. Project settings → Automation → Create rule.
2. **Trigger:** Issue labeled, label = `ai-test-gen`.
3. **Action:** Send web request →
   - Path 1: OpenHands Agent Server endpoint (see [openhands.md](openhands.md))
   - Path 3: Jenkins generic-webhook (see the Jenkinsfile in `triggers/jenkins/`)
   - Body: `{"mode":"jira","key":"{{issue.key}}","updated":"{{issue.updated}}"}`
4. Re-trigger loop: remove + re-apply the label after addressing comments.

## Step 5 — Confluence context (Knowledge port)

No extra credential needed — the same Atlassian connection covers it. During analyze,
the pipeline pulls Confluence pages linked from the ticket
(`adapters/knowledge/confluence.sh get_linked_docs`), budgeted by
`knowledge.confluence_max_pages` / `confluence_max_tokens` in `org-config.yaml`.
Page text is treated as untrusted data under the same prompt-injection framing as
ticket text. Optional outbound mirroring of test plans to a Confluence space is
one-way (repo → Confluence) to avoid two-master drift (§5.10).

## Step 6 — Verify

```bash
# 1. Adapter reads a real ticket:
bash adapters/tracker/jira.sh get_item PROJ-123   # expect the JSON summary

# 2. Adapter comments (use a sandbox ticket):
bash adapters/tracker/jira.sh comment PROJ-123 "AI-QE integration check"

# 3. End-to-end: label a well-formed story `ai-test-gen`, then in the control repo:
make status          # run appears with per-repo gate outcomes
python3 bin/qa.py artifacts PROJ-123   # plan, data, generated tests, commit
```

A well-formed story = clear acceptance criteria + a mapped Component (or dev-panel
link). Ambiguous ACs produce `test.fixme()` skeletons + open questions on the ticket —
that's the never-guess contract, not a failure.

## Troubleshooting

| Symptom | Check |
|---|---|
| 401/403 from adapter | Token is the *service account's*; Bearer header (Cloud API v3); account has project read |
| Comment posted but no tests | Look for the clarification comment — routing confidence below threshold; fix `jira_component_map` |
| Confluence pages not in analyze context | Links must be on the ticket (remote links or in description); check page-count/token budgets |
| Webhook fires twice | Expected on Jira retries — receiver dedupe on `(key, updated)` makes it a no-op |
| Rule doesn't fire | Automation rule scope includes the project? Label spelled `ai-test-gen`? |

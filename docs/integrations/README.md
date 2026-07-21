# Integration Guides

Step-by-step setup for connecting the AI QE platform to your estate's tools. Each guide
is self-contained: prerequisites → credentials → configuration → trigger wiring →
verification → troubleshooting.

| Guide | Covers | Port(s) |
|---|---|---|
| [openhands.md](openhands.md) | OpenHands agents as orchestrator + sandbox (trigger Path 1) | orchestration |
| [jira.md](jira.md) | Jira tickets in (Workflow B), comments back, Confluence context | Tracker, Knowledge |
| [bitbucket-stash.md](bitbucket-stash.md) | Bitbucket Cloud **and** Stash / Bitbucket Server (self-hosted) | Scm, Cicd trigger |

## How integrations attach (30-second recap)

The engine only ever calls six port functions — `SCM`, `TRACKER`, `KNOWLEDGE`, `CICD`,
`NOTIFY`, `TELEM` (architecture §5.10). Each guide below configures the *adapter* behind
a port plus the *trigger path* that starts `engine/pipeline.sh`:

```
Trigger (webhook/label) ──▶ Path 1 OpenHands | Path 2 SCM CI | Path 3 Jenkins
                                        │
                                        ▼
                            engine/pipeline.sh {pr|jira}
                                        │  port calls
                          ┌─────────────┼─────────────┐
                        SCM          TRACKER        NOTIFY …
                    github.sh        jira.sh        slack.sh
                    bitbucket.sh   (or Atlassian MCP in-session)
                    stash.sh
```

Nothing in `engine/`, `prompts/`, or `catalog/` changes when you integrate a tool —
that is the conformance-tested contract (`make conformance`).

## Credential matrix (fill `.env` from `.env.example`)

| Variable | Needed for | Scope to request |
|---|---|---|
| `ANTHROPIC_API_KEY` | LLM phases (`AIQE_MOCK=0`) | — |
| `ATLASSIAN_MCP_TOKEN` | Jira + Confluence + Bitbucket Cloud via one MCP connection | service account: project read + comment write |
| `GITHUB_TOKEN` | GitHub repos | fine-grained: contents RW on branches, PR comments |
| `BITBUCKET_TOKEN` | Bitbucket **Cloud** repos | repo read + PR comment; RW for test repos |
| `STASH_URL` / `STASH_PROJECT` / `STASH_TOKEN` | **Stash / Bitbucket Server** | HTTP access token: repo read + PR comment; RW for test repos |
| `SLACK_WEBHOOK_URL` | run summaries, review digests | incoming webhook |
| `JENKINS_URL` / `JENKINS_USER` / `JENKINS_API_TOKEN` | Path 3 + post-merge exec | job trigger + read |

Rules that apply to every integration:
- Tokens are **least-privilege and branch-scoped** — the gate is the only push path, and
  branch protection must block direct pushes to main everywhere (NFR-5).
- Ticket/PR/page text is **data, never instructions** — prompts enforce this; never relax it.
- Verify each integration with the smallest real call before wiring triggers (each guide
  has a "verify" section with copy-paste commands).

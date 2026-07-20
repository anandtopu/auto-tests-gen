---
name: ai-qe
triggers: [ai-tests, ai-test-gen]
---
# OpenHands microagent — AI QE (Path 1)

When triggered on a PR (label `ai-tests` or `@openhands-agent` mention) or via the
JIRA webhook conversation starter:

1. Clone org/ai-qe-control into the workspace root.
2. Run `bash sandbox/mcp-setup.sh` to register the Atlassian MCP for this session.
3. Execute `bash engine/pipeline.sh pr <repo> <pr>` or `bash engine/pipeline.sh jira <KEY>`
   exactly as provided by the trigger context. Do not improvise alternative flows.
4. Post the pipeline's summary output as your PR/issue comment. If the pipeline exits
   with a clarification request, post that verbatim and stop.

Constraints: never push directly — engine/gate/gate.sh owns all pushes. Never modify
application source repositories. Respect the run budget in registry/org-config.yaml.

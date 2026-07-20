#!/usr/bin/env bash
# Register MCP servers for the Claude Code session (one Atlassian connection
# covers Jira + Confluence + Bitbucket). Idempotent.
set -euo pipefail
claude mcp add atlassian --transport http "${ATLASSIAN_MCP_URL}" \
  --header "Authorization: Bearer ${ATLASSIAN_MCP_TOKEN}" 2>/dev/null || true
echo "MCP registered: atlassian"

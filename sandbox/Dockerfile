# Execution sandbox: Claude Code + Playwright + platform deps (architecture §4.2)
FROM mcr.microsoft.com/playwright:v1.54.0-noble
RUN apt-get update && apt-get install -y python3-pip jq git curl \
 && pip3 install --break-system-packages pyyaml \
 && npm install -g @anthropic-ai/claude-code
# Egress is restricted at the network layer (allowlist: Anthropic API, Atlassian MCP,
# GitHub/Bitbucket, npm/pypi). No host mounts. Ephemeral per run.
WORKDIR /workspace

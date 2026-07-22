# AI QE Platform — service image (dashboard + TaskEvent receiver + pipeline).
#
# Runs the platform in MOCK mode out of the box (no credentials, no external calls):
# demo estate, mock adapters, node --test gate. For REAL mode (AIQE_MOCK=0) mount an
# ANTHROPIC_API_KEY and set INSTALL_REAL_TOOLS=1 at build time to add the Claude CLI
# and Playwright browsers (see docs/deployment.md).
#
# OpenShift-compatible: the app tree is owned by group 0 and group-writable, and the
# process runs fine as an arbitrary non-root UID (the platform never needs root).
FROM node:20-bookworm-slim

ARG INSTALL_REAL_TOOLS=0

# Runtime deps: python3 + pyyaml (engine/CLIs), bash + git + curl + jq (pipeline/gate),
# make (the documented entry point for every workflow: make demo-pr, report, prune…),
# tini (PID 1 signal handling). node comes from the base image (demo estate + node --test).
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-yaml bash git curl jq make ca-certificates tini \
    && if [ "$INSTALL_REAL_TOOLS" = "1" ]; then \
         npm install -g @anthropic-ai/claude-code && npx --yes playwright install --with-deps chromium; \
       fi \
    && rm -rf /var/lib/apt/lists/*

ENV APP_HOME=/app \
    AIQE_MOCK=1 \
    AIQE_UI_HOST=0.0.0.0 AIQE_UI_PORT=4999 \
    AIQE_HOOK_HOST=0.0.0.0 AIQE_HOOK_PORT=4998 \
    PYTHONUNBUFFERED=1 GIT_TERMINAL_PROMPT=0
WORKDIR ${APP_HOME}

# App source (respecting .dockerignore — scratch/state dirs are excluded)
COPY . ${APP_HOME}

# git identity for the gate's commits inside workspace clones; make the tree writable
# by GID 0 so an arbitrary-UID OpenShift run can write state, and pre-create the
# scratch/state dirs that volumes will normally back.
RUN git config --system user.email "ai-qe@platform.local" \
    && git config --system user.name  "AI QE Platform" \
    && git config --system --add safe.directory '*' \
    && mkdir -p ${APP_HOME}/reports/runs ${APP_HOME}/workspace ${APP_HOME}/out \
    && chgrp -R 0 ${APP_HOME} && chmod -R g=u ${APP_HOME}

EXPOSE 4999 4998
USER 1001

# Default command runs the dashboard; the receiver container overrides it (see the
# compose file / k8s Deployment). tini reaps the pipeline subprocesses cleanly.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "bin/dashboard_server.py"]

# Onboarding a New SDLC Tool (the "and more" contract, architecture §5.10)
1. Classify against the six ports: Scm | Tracker | Knowledge | Cicd | Notify | Telemetry.
2. MCP server exists? Register in sandbox/mcp-setup.sh + map tool names in org-config.
   Otherwise: write a thin CLI adapter implementing ONLY that port's verbs
   (copy an existing adapter; unknown verbs must exit 64).
3. Credentials → .env / secret store; events → webhook-to-TaskEvent translation at intake.
4. Add the adapter + its verbs to adapters/conformance/test_adapters.sh.
Nothing in engine/, prompts/, or catalog/ changes.

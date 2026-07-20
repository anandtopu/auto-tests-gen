# JIRA Automation → AI QE (Workflow B trigger)

1. JIRA project → Automation → Create rule:
   - **Trigger:** Issue labeled → label = `ai-test-gen`
   - **Action:** Send web request
     - URL: OpenHands Agent Server endpoint (Path 1) or Jenkins generic-webhook (Path 3)
     - Body (custom JSON): `{"mode":"jira","key":"{{issue.key}}","updated":"{{issue.updated}}"}`
2. Idempotency: the receiver dedupes on sha256(key + updated + workflow_version).
3. Re-trigger loop: QE comments on the ticket, re-applies the label after removing it.
4. Clarification path: when routing confidence < threshold the agent comments on the
   ticket with candidate repos and posts to Slack; a reply of
   `@openhands use orders-api,e2e-api-tests-1` re-triggers with pinned routing.

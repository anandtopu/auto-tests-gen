---
name: e2e-ui-conventions
description: UI E2E authoring conventions for Playwright test repos in this estate.
---
# UI E2E Conventions
- Selectors: data-testid only. If a needed testid is missing in the app, do NOT invent
  a CSS chain — record it in open_questions (app change needed).
- Page objects live in pages/; one class per screen; tests never call page.locator directly.
- Waits: web-first assertions (expect(...).toBeVisible()); never hard sleeps.
- One user journey per spec file; independent tests (no ordering deps).
- Tag every test with @<JIRA-KEY> and the domain tag (@checkout, @catalog...).

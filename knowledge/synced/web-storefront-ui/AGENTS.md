# web-storefront-ui â€” conventions for AI-generated E2E tests

Repo-local guidance: ships with the UI app and is synced into the estate AGENTS.md,
so every generation phase sees it.

- Select elements by `data-testid` only â€” never by CSS class or visible text
  (both are localised and change with design updates).
- Routes live in `src/routes.tsx`; a route added there needs UI coverage in
  `e2e-ui-tests-1`.
- Cart state persists in `localStorage` under `cart.v2` â€” clear it in `beforeEach`
  or tests leak state into each other.
- The checkout flow requires a seeded session; use the `loginAs(user)` fixture rather
  than driving the login form in every spec.

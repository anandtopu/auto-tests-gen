# Issue-type guidance: Story / Enhancement
- Extend existing mapped tests before creating new ones (the coverage section in
  AGENTS.md is the authority) — enhancements usually change behavior a suite
  already exercises.
- Cover every unambiguous acceptance criterion with at least one scenario; add
  boundary and negative cases around each AC's limits.
- New user-visible behavior with no existing coverage => new spec, born-mapped.

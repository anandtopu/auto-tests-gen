# Cross-file integration checks: SDD-S1

Date: 2026-08-08

| Flow / contract | Status | Evidence | Finding / disposition |
| --- | --- | --- | --- |
| `spec_workflow` state -> label -> dashboard | Pass | Six real state branches and exact label map; API still returns six machine states | No state inference or mutation added |
| Marked term -> glossary -> rendered HTML | Pass | Both-direction coverage, unknown-id refusal, HTML escaping, tooltip/internal text | One closed presentation boundary |
| Journey nav -> title -> docs | Pass after fix | Dashboard server/client titles and four live user-facing docs agree | Stale user-guide/Settings references corrected |
| Structured/prose approval vocabulary | Pass | Three newcomer docs and glossary pin both explicit labels | Signed guarantees are not claimed for prose |
| Default/off governance behavior | Pass | Existing spec gate/event/UI suites green; S1 changes only visible words | No knob or enforcement semantic changed |
| Standalone pipeline -> idempotent comment | Pass after fix | Mock full pipeline plus SDD set 78/78 | Stale posted-only assertion corrected |
| State bundle hostile import | Pass after fix | Adversarial script accepts only checksum preflight refusal | Fixture aligned with hardened manifest contract |
| Deployment/browser | Partial | Real render and local HTTP/API pass | Browser runtime asset path missing; no visual-click claim |

Security review found no raw HTML injection path: arbitrary marked copy is
escaped and only definitions from the closed in-process table become HTML.

# A4 Near-Duplicate Detection — Per-File Review

Date: 2026-08-06
Scope: PRD A4 only

| File | Review result | Evidence / action |
| --- | --- | --- |
| `engine/lib/duplicate_detector.py` | Correct after fix | Bounded proposals/matches, separate thresholds, hashed query provenance, no writes outside `out/`. Embedding-call failure originally removed all advice; fixed to lexical fallback. |
| `engine/pipeline.sh` | Correct | JIRA runs after final arbitration; PR runs after the first proposal exists and before validation/reporting. Helper handles failure without returning a gate-affecting status and removes stale partial output. |
| `engine/lib/plan_state.py` | Correct | Snapshots the bounded artifact while `out/` is live; every new draft clears stale warnings; no approval transition is coupled to warnings. |
| `bin/dashboard.py` | Correct after fix | Renders an explicitly advisory card using `escHtml` for every untrusted field. Added missing suite provenance. No warning state is connected to save/approve/generate controls. |
| `engine/lib/pr_comment.py` | Correct after fix | Live and archived composition share one renderer, truncate to eight warnings, sanitize Markdown code text, and name repo/file/suite/title. |
| `engine/lib/run_record.py` | Correct | Archives only the named/versioned artifact after `overall` is computed exclusively from gates; corrupt optional JSON is non-fatal. |
| `engine/lib/selection.py` | Correct after fixes | Typed duplicate exclusions require a testcase reference, preserve it in finalized manifests, and expose M6 numerator/denominator. Fixed object-key counting and corrupt-count totality. |
| `bin/dashboard_server.py` | Correct | Passes typed reason/reference through the authenticated selection endpoint; existing validation and actor attribution remain in force. |
| Settings/examples | Correct | Flag defaults off; semantic and lexical distributions have separate controls and are present in both supported example formats. |
| Focused tests | Adequate | Cover both workflows, fallback modes, bounds, stale cleanup, nonblocking placement, lifecycle, M6 evidence, archival, PR rendering, UI presence, and settings conformance. |

No unresolved per-file correctness or security finding remains in A4 scope.

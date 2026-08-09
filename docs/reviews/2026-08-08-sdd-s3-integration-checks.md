# Cross-file integration checks: SDD-S3 adoption levels

Date: 2026-08-08

| Flow | Status | Evidence |
| --- | --- | --- |
| Existing controls → engine resolvers → `governance()` → named level | Pass | Five tuple round trips; invalid values for all three controls remain Custom on repeated reads |
| Definitions → Settings choices and consequence copy | Pass | `/api/adoption` returns the single definition module; browser renders returned names/copy rather than re-declaring them |
| Authenticated apply → atomic `.env` save → refresh → effective GET | Pass | Live isolated server wrote exactly three keys, returned Enforced/warn, then restored Reviewed plans |
| Exported environment precedence → post-apply truth | Pass by design | `load_env_into(refresh=True)` preserves explicit values; effective state derives after save and may remain Custom |
| Effective level → Start here / Governance / downloadable markdown | Pass | All consume `governance().adoption`; generated HTML and governance JSON/markdown checks pass |
| Warn versus strict | Pass | Warn exact badge says reporting, not refusing; strict exact badge says enforcing/refused |
| Custom advanced raw edit → live presentation | Pass | Generic settings route refreshes only when one of the three mapped keys changed; UI reloads all governance surfaces |
| Security and audit | Pass | Existing handler auth protects POST; closed vocabulary rejects malformed input; event records updated key names, never values |
| Deployment / read-only root | Pass | Writes continue through relocatable `AIQE_ENV_FILE`; no packaged org-config mutation introduced |
| Test isolation | Pass after cleanup | API uses a temporary env file; two timeout-aborted broad runs altered a tracked PROJ-301 fixture, which was restored exactly and rechecked |

## Residual checks

- Browser visual click-through was not available in this runner; generated HTML,
  served API behavior, and JavaScript/source pins cover the functional contract.
- `make review` exceeded 15 minutes and the direct complete Python suite exceeded
  10 minutes without a result. Neither is claimed as passed. The completed
  cross-file set and post-review set are the release evidence for this iteration.

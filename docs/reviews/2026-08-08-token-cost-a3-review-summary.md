# TCA-A3 Review Summary

Date: 2026-08-08
Decision: **Ready after fixes**

TCA-A3 now has one basis-aware historical spend reader. Every enumerated spend
surface either consumes that union directly or, for queue/team/dashboard and
baseline/regression views, consumes the shared cost report built on it. Live
budget enforcement is intentionally unchanged.

The first broad run found two P1 normalization/compatibility defects: missing
`turns_used` mapping and malformed spend records entering the run count. Both
were fixed and targeted before a clean full rerun. Review also corrected CLI
artifact precedence and removed obsolete normalization code.

Acceptance result:

- A1.2 collision dedupe and retry aggregation: pass.
- A1.2a enumerated consumer migration: pass.
- Explicit incomplete/simulation states and no blended bases: pass.
- No direct-source consumer pin: pass.
- Full compatibility: 1,720 passed.

Next eligible item: TCA-C1, per-task cost statement.

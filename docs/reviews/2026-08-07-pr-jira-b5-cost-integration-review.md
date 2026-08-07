# PR/JIRA B5 Cost Containment - Cross-File Integration Review

Date: 2026-08-07

## Pass 2 - integration, security, reliability, deployment, coverage

### Correctness

- Runtime enforcement and queue intake call the same effective-envelope
  function, eliminating config-interpretation drift.
- `warn` receives headroom only when the reviewer is enabled; `require`
  receives it despite a per-run disable; `off` receives none despite a
  per-run enable. Plan-only receives none because it runs no test reviewer.
- Base envelopes stay PR $1.50, JIRA $4.00, plan $1.00, tests $3.00. Active
  generated-test review adds the configured provisional $0.75.
- `MAX_COST_USD_PER_RUN` remains the highest-precedence cap.

### Security and reliability

- Mode is used only as a bounded mapping key; no command, path, or provider
  selection is derived from it.
- Invalid, negative, or boolean money values cannot silently become a cap.
- Config/read failures retain the existing best-effort fallback in enforcement
  and produce no deceptive queue warning.
- The queue warning remains informational and uses measured history; the
  uplift is labelled planning allowance, not measured spend.
- Git and Git-Bash test subprocesses explicitly detach stdin, so coverage runs
  launched by system Python 3.14 do not inherit an invalid Windows handle.

### Deployment

- No new secret, network call, state store, port, volume, manifest, or phase is
  introduced.
- Default disabled review retains historical cap behavior.
- Existing providers already map the capable tier; reviewer capability remains
  read-only and repair capability remains the existing agentic path.

### Panel boundary

- Panel status is declarative and deferred; no model/phase entry exists.
- Adoption requires an agreed E4 threshold exceeded over a complete 90-day
  real-evidence window. Until human findings support that metric, it remains
  unmeasured rather than treated as below threshold.

### Coverage

- Unit: effective cap matrix, bad numeric values, tier mapping.
- Integration: queue history boundary and explicit-cap precedence.
- Adversarial: policy/environment precedence and mutation tests.
- Compatibility: 1,566 registry tests passed with 70.15% branch coverage, then
  every adapter/adversarial/smoke/replay/evaluation/scorecard stage passed.

## Conclusion

No open P0-P2 correctness, security, reliability, deployment, or coverage
finding remains. Residual operational risk is that $0.75 is a provisional
single-default-loop allowance; real traffic and any higher loop/fan-out policy
must drive recalibration.

# TCA-C2 integration checks

| Boundary | Evidence | Result |
|---|---|---|
| Settings → environment → adapter | write-only metadata tests and settings/example parity | Pass |
| Engine → LLM adapter port | mock Make journey plus engine contract tests | Pass |
| Adapter → Anthropic Admin API | local HTTP reproduction checked path, headers, timestamps, pagination | Pass |
| Provider response → normalized dollars | `123.45 + 0.55` fractional cents becomes exact `$1.24` | Pass |
| Unconfigured/unsupported/down/malformed → honesty | explicit unavailable state and absent cost field | Pass |
| Secret → logs/output | synthetic admin key absent from stdout/stderr; errors expose type only | Pass |
| Windows → Git Bash adapter launch | engine uses `work_queue.bash_exe()` | Pass |
| Deployment → credential declaration | `.env`, properties, Settings, OpenShift example | Pass after finding C2-04 |
| Test execution → tracked estate | standalone full-run paths redirected and hash-pinned unchanged | Pass after finding C2-05 |
| TCA-C2 → TCA-C3 boundary | no durable comparison, auto-correction, alert, or badge added | Pass |

Validation: 165 focused tests passed; adapter conformance passed; 343 broad
cost/adapter/settings/state compatibility tests passed; the documented mock Make
journey passed; shell and Python syntax checks passed. The full registry command
was also attempted but hit its 600-second runner timeout without an exit result,
so it is not reported as passing.

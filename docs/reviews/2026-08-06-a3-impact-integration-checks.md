# A3 Impact Analysis — Pass 2 Integration Review

Date: 2026-08-06

| Check | Evidence | Result |
| --- | --- | --- |
| PR diff → catalog/testcase join → generation | `RUN_IMPACT pr`; deterministic endpoint and identifier tests | Pass |
| JIRA acceptance criteria + final scenarios → generation | Hook follows plan arbitration; JIRA and approved-plan-resume source assertions | Pass |
| Deterministic cost short-circuit | embedding availability/search bombs remain uncalled on deterministic winners | Pass |
| Semantic outage/unconfigured provider | empty semantic result takes lexical path with its own threshold and recorded mode | Pass |
| Explicit absence | versioned artifact always carries the create-new message when no candidate clears | Pass |
| Bug regression question | ranked direct-surface candidates persist independently of fallback; explicit absence covered | Pass |
| Persistence/explain | run record archives artifact; explain uses archived evidence and rejects cross-run live state | Pass |
| Security/authority | raw query omitted from artifact, SHA/provenance retained, retrieved fields marked untrusted, proposal cannot write/commit | Pass |
| Reliability/deployment | default off; atomic artifact write; malformed catalog/chunk/health data degrades; settings documented | Pass |
| Test coverage | 12 A3 acceptance/adversarial cases and 82 focused adjacent compatibility tests passed | Pass |
| Broad compatibility | all 1,306 registry tests passed across three bounded tranches after the relocation/config findings were fixed | Pass |
| Shell pipeline smoke | Windows exposes only a nonfunctional WSL launcher; no Git Bash/WSL `/bin/bash` exists | Environment-limited; recorded, not treated as pass |

Residual risk: A5 must establish labelled precision/recall/MRR before thresholds
are tuned or A3 is enabled by default. The feature remains default-off.

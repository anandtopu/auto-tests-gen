# Exploratory E2E Review — Iteration 015

## Scope

This iteration completed Feature 14, Settings and integrations, through the
served browser UI, authenticated HTTP API and focused fault injection. It
covered settings persistence and removal, write-only credential metadata,
read-only connection checks, token/SSO boundaries, malformed requests and
configuration-write durability.

## Findings

| ID | Severity | Files | Finding | Impact | Fix |
| --- | --- | --- | --- | --- | --- |
| E2E-EXP-035 | P1 | `engine/lib/settings_store.py` | Refresh never removed a file-owned setting that was cleared. | A long-lived server could continue using a removed credential, URL or proxy. | Remove cleared file-owned values and safely retire loader-owned proxy aliases. |
| E2E-EXP-036 | P1 | `engine/lib/settings_store.py` | `.env` was rewritten in place. | Interruption could truncate all integration configuration and credentials. | Write a same-directory temporary file, preserve its prior mode and atomically replace under the existing lock. |
| E2E-EXP-037 | P2 | `bin/dashboard_server.py` | Settings/check handlers trusted every parsed JSON shape. | Arrays, scalars and null reset the connection instead of returning a bounded client error. | Require object bodies and an object-valued `updates` member, returning 400. |
| E2E-EXP-038 | P2 | `bin/dashboard_server.py` | Invalid check selectors reached a helper whose fallback means all checks. | A typo or wrong JSON type could unexpectedly probe every configured external system. | Accept only lists of known string check identifiers before dispatch. |

## Reproduction and retest evidence

- Before E2E-EXP-035, save JIRA/proxy values, load them into a long-lived
  environment, save both empty, and refresh: the old values and standard proxy
  aliases remained. Afterward they are absent; reconfiguration still applies.
- Before E2E-EXP-036, replacing `fs_lock.replace_atomic` with a synthetic disk
  failure was never reached because save wrote the destination directly.
  Afterward the fault is raised, the original bytes remain complete, and the
  temporary file is removed.
- Before E2E-EXP-037, eight non-object endpoint cases and two wrong-shaped
  update cases closed the connection. They now return 400 and the immediately
  following authenticated settings request remains healthy.
- Before E2E-EXP-038, a scalar selector or unknown-only list ran all checks.
  String, number, object, non-string-list and unknown-name inputs now return
  400 without invoking integration code.
- The browser saved `https://jira.synthetic.invalid`, persisted a synthetic
  SMTP placeholder as write-only metadata, reloaded both states, ran the safe
  check table, then cleared JIRA to `JIRA_URL=`. The API returned no secret
  bytes; unauthenticated access returned 401.

## Pass 1 — per-file review

- `engine/lib/settings_store.py`: settings validation precedes I/O; locking
  still covers read/merge/replace; the temporary file is same-volume and is
  mode-preserving and cleaned on success or failure. Refresh mutates only keys previously loaded
  from `.env`, while proxy aliases are changed only when their current value
  matches the loader-owned value.
- `bin/dashboard_server.py`: shape and selector validation precede field access
  and checker dispatch. Existing valid `{}` and known-list contracts remain
  compatible; JSON/type errors produce bounded 400 responses.
- `registry/tests/test_settings.py`: tests cover clear, proxy removal,
  reconfiguration and fault-injected atomicity without touching the checkout
  `.env`.
- `registry/tests/test_api_adversarial.py`: the server owns an isolated `.env`,
  strips inherited integration credentials and verifies connection health
  after malformed Settings traffic.

## Pass 2 — cross-file review

- Correctness: the UI, API, `.env` bytes and refreshed process environment now
  agree for set, secret-retain, clear and reconfigure transitions.
- Security: read APIs expose only secret set/unset metadata; invalid selector
  input cannot broaden external checks; test processes cannot inherit known
  production integration credentials.
- Reliability: atomic replacement preserves the previous complete `.env` on a
  failed commit, and malformed requests do not kill the server connection.
- Deployment: default `.env`, token and SSO behavior are backward compatible;
  no schema, dependency, manifest or external integration changed.
- Coverage: 17 focused regressions passed after fixes; 245 broad settings, UI,
  integrations, auth and adversarial checks passed. The post-review ownership
  refinement passed 80 settings/properties/integration checks and 16 live-API
  regressions, plus compilation and high-signal Ruff.

## Seed and cleanup review

All Feature 14 writes lived under ignored `out/exploratory-e2e-iter15`. The
dashboard bound only to `127.0.0.1`, required a synthetic token, used mock mode
and had all known external credentials removed. No PII, customer data or real
secret was used.

## Residual risk

- Real vendor endpoints were intentionally not probed; this iteration validates
  safe dispatch and not-configured behavior, not third-party availability.
- Reverse-proxy header overwriting cannot be proven without a deployed proxy;
  app-boundary SSO fail-closed/trusted-header tests passed.
- Danger-zone clearing was covered in the Settings backend during earlier
  exploratory iterations and was not repeated against the real checkout.
- No blocker remains for Feature 14. Feature 15, API and CLI parity, is next.

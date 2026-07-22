# e2e-api-tests-1 â€” conventions for this E2E suite

Repo-local guidance for the API E2E repo itself: how tests here must be written.

- Specs live in `suites/`, fixtures in `data/`. One spec per behaviour, not per endpoint.
- Titles start with the ticket key (`PROJ-123: ...`) and carry a `@PROJ-123` tag â€”
  the catalog sidecar is matched on it.
- Use the shared `apiClient` from `data/client.js`; never construct raw URLs, so the
  base URL injected by the harness is always honoured.
- Assert on status code **and** body shape. A test that only asserts 2xx is rejected
  in review.
- New specs must add their catalog sidecar line in the same commit (the gate enforces
  this â€” exit 4).

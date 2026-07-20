# AI QE Agent Policy — E2E Test Repository
- You are updating E2E tests only. Never modify application source (read-only clones).
- Every test title starts with the JIRA key when known: "PROJ-123: ...". Tag @PROJ-123.
- Conventions: load the matching skill (e2e-ui-conventions or e2e-api-conventions).
- Reuse page objects / shared clients; extend, don't duplicate.
- Test data via factories/fixtures; synthetic only; no PII, no real customer data.
- Ambiguous acceptance criteria → test.fixme() skeleton + open question. Never guess.
- Every new spec gets a catalog sidecar line in catalog/generated.jsonl (born-mapped);
  the gate rejects unmapped tests.
- Commit format: "test(PROJ-123): <summary>" + Co-Authored-By: ai-qe-agent trailer.

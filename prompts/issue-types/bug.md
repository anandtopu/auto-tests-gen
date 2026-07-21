# Issue-type guidance: Bug fix
- The primary deliverable is a REGRESSION test that reproduces the reported
  defect: it must encode the exact reproduction path from the ticket and assert
  the CORRECT (fixed) behavior — it would have failed before the fix.
- Add boundary cases immediately around the defect (off-by-one, empty, null,
  type mismatches) — bugs cluster.
- If reproduction steps are missing or ambiguous, raise an open question and emit
  a test.fixme() skeleton naming the missing detail; never invent the repro.
- Prefer extending the suite that should have caught this bug over a new file.

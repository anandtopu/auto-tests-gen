# PR ticket discovery label set v1

This QE Platform-owned set exercises the production PR ticket discovery policy
without calling an SCM or Tracker. `fixtures.json` contains bounded PR metadata
and synthetic Tracker validation outcomes. `labels.json` contains the expected
post-validation signal keys and final decisions.

The labels file pins the exact SHA-256 of `fixtures.json`. Any fixture edit must
therefore be reviewed by the QE Lead together with its labels before the
evaluation can run. The minimum PRD cases are branch-only, commit-only, absent,
invalid, and conflicting keys; explicit and title/description cases keep every
production signal measurable.

Results are always labelled `simulated`: the fixtures prove extraction,
validation, selection, and refusal plumbing. They do not establish accuracy for
an estate's real branch and commit conventions, so the runtime discovery flag
remains default off until separately labelled real-estate evidence clears the
same M1 threshold.

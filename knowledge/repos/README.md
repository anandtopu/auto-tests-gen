# knowledge/repos/ — per-repo agent guidance

One `<repo-name>.md` per registered repository (app or E2E test repo),
team-authored: conventions, selectors, auth flows, data setup, known pitfalls.
Edit from the dashboard **Repositories** view, or:

    bin/repos.py notes <repo> --set "..."     # or --file guidance.md

`bin/gen_agents_md.py` merges these files — together with any `AGENTS.md` /
`CLAUDE.md` found inside the repo's own checkout — into the estate `AGENTS.md`,
which is injected into every LLM phase (test plans, test generation, coverage
gap fixes). This README is not merged; only files named after a registered repo.

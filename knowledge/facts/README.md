# knowledge/facts/ — structured per-repo facts

One `<repo-name>.yaml` per participating repository, holding what humans assert
about it. E2E test repositories retain the original facts behavior;
application repositories opt in by adding this authored file. See
`docs/knowledge-base-proposal.md` for the design and the tier deliberately not
built yet.

    knowledge/facts/<repo>.yaml           AUTHORED — tracked, never regenerated
    knowledge/facts/derived/<repo>.yaml   HARVESTED — gitignored, rebuilt

Two files rather than one so a rebuild never dirties git: a human's assertions
and a generator's output do not belong in the same document.

## Why this exists

A repo's operational knowledge — "auth goes through `loginAs()`, never the
form", "don't assert on `updatedAt`, it drifts" — used to live only as free
prose in `knowledge/repos/<repo>.md`. Prose cannot be ranked, filtered by
severity, or attributed. These files make the load-bearing parts structured so
retrieval can treat a `must` differently from a stylistic aside, and so a
reader can tell what a human asserted from what a generator derived.

**The prose is not replaced.** `knowledge/repos/<repo>.md` stays exactly as it
was; some knowledge is genuinely narrative. This is for the parts that are not.

## Editing

Hand-edit the YAML, or:

    python3 engine/lib/repo_facts.py show <repo>      # merged view + provenance
    python3 engine/lib/repo_facts.py rebuild          # harvested tier only

`show` also reports schema problems (an unknown `severity`, a convention with
no `rule`). Only the authored tier is validated — validating this module's own
derived output would tell you nothing.

## Scope and application-repository opt-in

E2E repositories are rebuilt as before. An application repository participates
only when `knowledge/facts/<app-repo>.yaml` exists. Its harvested tier is a
deterministic reshaping of registry ownership/dependencies, the configured
contract or route table, and Test Catalog evidence. An unavailable contract or
route table is recorded as `status: unavailable`; it is never presented as an
available-but-empty surface.

Opted-in authored conventions are rendered through the existing generated
`AGENTS.md` path. Repo-owned guidance still wins, followed by curated guidance
and then generated scratch; B4 does not add a second guidance generator.

## Precedence

    repo_owned (the repo's own AGENTS.md)  >  authored  >  harvested

Nothing generated ever outranks something a human wrote — constitution C6.

## Absence is normal

No file means no facts. Every accessor returns empty and the pipeline behaves
exactly as it did before this existed, so adopting it is per-repo and optional.

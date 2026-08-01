# Per-repo knowledge base — proposal

**Status: PROPOSAL. Nothing here is built.** It is written to be reacted to.

## 1. What a repo already knows about itself

Grounded in the current code, not aspiration. Every registered repo already has:

| source | shape | who owns it | regenerated? |
|---|---|---|---|
| registry entry | `name, type, layer, url, testable_paths, covers/scope` | operator (`bin/repos.py`) | `covers:` yes, rest no |
| harvested surface | endpoints from the OpenAPI contract, routes from the route table, `[NO TEST]` annotations | derived | yes, every run |
| catalog evidence | per test: endpoints, ui_routes, page_objects, fixtures, jira keys + confidence | derived from code + git | yes, on bootstrap |
| guidance prose | `knowledge/{repos,curated,synced,generated}/<repo>.md` | team / repo / generator | only `generated/` |
| health | `catalog/health.json` — flake, quarantine | CI ingest | yes |
| chunks + vectors | retrieval substrate | derived | yes |

That is a real knowledge base. The plumbing is not the gap.

## 2. The actual gap

**Everything qualitative is one undifferentiated blob of prose.** A repo's hard-won
operational knowledge — "auth goes through `loginAs()`, never the UI form",
"the orders search endpoint is flaky under parallelism", "don't assert on
`updatedAt`, it drifts" — lives as free text in one `.md`, which means:

* it cannot be **queried** — retrieval either injects the whole file or none of it;
* it cannot be **ranked** — a critical pitfall and a stylistic aside weigh the same;
* it cannot be **attributed** — you cannot tell what a human asserted from what a
  generator guessed, so nothing can be trusted differently;
* it cannot be **learned** — the platform runs hundreds of times, gets critic
  scores, gets reviewer edits, gets CI results, and forgets all of it. Nothing
  from run N informs run N+1 except the artefacts themselves.

The last one is the real cost. The platform accumulates evidence and discards it.

## 3. Proposed schema

`knowledge/facts/<repo>.yaml` — one structured record per registered repo,
sitting *beside* the existing prose rather than replacing it.

```yaml
repo: e2e-api-tests-1
schema: 1

# ---- AUTHORED: humans assert these. Never regenerated, never overwritten. ----
authored:
  ownership:
    team: payments-qe
    contact: "#payments-qe"           # channel, not a person — people move
  conventions:
    - id: auth-helper
      rule: "Authenticate with helpers/auth.loginAs(role); never drive the login form."
      applies_to: ["suites/**"]
      severity: must                   # must | should | avoid
    - id: no-volatile-assertions
      rule: "Never assert on updatedAt/createdAt — they drift between runs."
      severity: must
  data_setup:
    - "Orders are seeded by fixtures/orders.seed.js; do not POST them inline."
  pitfalls:
    - id: parallel-search
      note: "The /v1/orders/search endpoint is not safe under --parallel."
      severity: avoid

# ---- HARVESTED: derived from the repo. Regenerated; hand edits are lost. ----
harvested:
  generated_at: 2026-08-01T00:00:00Z
  framework: playwright-api
  layout:
    specs: "suites/**/*.spec.js"
    helpers: "helpers/"
    fixtures: "fixtures/"
  surface_covered: ["POST /v1/orders/{id}/discounts", "GET /v1/orders/{id}"]
  shared_helpers: ["helpers/auth.js", "helpers/orders.js"]

# ---- OBSERVED: learned from run history + CI. Regenerated; evidence-backed. --
observed:
  generated_at: 2026-08-01T00:00:00Z
  window_days: 30
  flaky:
    - test_id: "e2e-api-tests-1::suites/orders/search.spec.js::search paginates"
      failure_rate: 0.22
      runs: 41                          # never a rate without its denominator
  churn:                                # surfaces that keep needing new tests
    - surface: "POST /v1/orders/{id}/discounts"
      tests_added: 4
      last: 2026-07-28
  review_signal:
    critic_avg: 0.86
    runs_scored: 12
    common_flags: ["new-approach", "weak-assertion"]
  reviewer_edits:                       # what humans CHANGED about generated tests
    - pattern: "replaced inline POST with fixtures/orders.seed.js"
      seen: 3
```

### Provenance is the point

Four tiers, and the precedence rule already in the constitution extends cleanly:

```
repo_owned (the repo's own AGENTS.md)  >  authored  >  observed  >  harvested
```

`observed` outranks `harvested` because a measured flake rate beats a static
guess about the same surface; `authored` outranks both because a human asserting
"never drive the login form" is a decision, not an observation. Nothing
generated ever outranks something a human wrote — constitution C6, unchanged.

Every fact carries where it came from, so a prompt can say *"the team asserts"*
versus *"observed across 41 runs"*, and a reviewer can tell which is which.

## 4. What this changes about generation

Today `generate` receives the whole `AGENTS.md` plus exemplar code. With facts:

* **Retrieval gets sharper.** `must`-severity conventions for the target repo are
  MUST-KEEP chunks — they survive any context budget. Stylistic notes compete for
  the remainder. Today both are the same undifferentiated prose.
* **The critic gets a rubric.** `new-approach` is currently judged against
  exemplar code. With `authored.conventions` it can name the rule that was
  broken — a reviewable finding rather than a vibe.
* **Flake feeds planning.** A scenario touching a surface with a known flaky test
  can be planned differently, or flagged for the human.
* **Reviewer edits close the loop.** `reviewer_edits` is the one field that makes
  the platform learn: what humans keep changing about generated output is exactly
  what the next generation should do differently.

## 5. What regenerates, what persists

| tier | regenerated by | survives `clear-demo`? | in the state bundle? |
|---|---|---|---|
| `authored` | never | yes — it is somebody's work | yes |
| `harvested` | `make agents` / bootstrap | rebuilt | no (derived) |
| `observed` | `make maintain` | rebuilt | no (derived) |

`authored` is tracked in git. `harvested`/`observed` are derived and gitignored,
rebuilt on demand — the same rule `knowledge/generated/` already follows, so a
fresh clone self-heals rather than shipping stale facts.

## 6. Migration — no big bang

1. Ship the schema + loader; `facts/<repo>.yaml` absent ⇒ everything behaves as
   it does today. No behaviour change until a file exists.
2. Generate `harvested` from what `gen_agents_md.py` already computes. Zero new
   analysis; it is a re-shaping of existing output.
3. Generate `observed` from run records + `health.json` in `make maintain`.
4. `authored` starts empty. The Repositories view gets a structured editor; the
   existing prose stays and is merged as it is today.
5. Only then teach retrieval to rank by severity and tier.

Each step is independently useful and independently revertable.

## 7. What I am NOT proposing

* **Not replacing the prose.** `knowledge/repos/<repo>.md` stays. Some knowledge
  is genuinely narrative and forcing it into fields would lose it.
* **Not an LLM-authored knowledge base.** Every field above is either
  human-asserted or mechanically derived from evidence. A model summarising a
  repo into "facts" would produce confident, unfalsifiable, unattributable
  claims — the opposite of what this is for.
* **Not pushed to the repo.** Constitution C6 holds: the gate stays the only
  writer of repo content.

## 8. Open questions for you

1. **Is `observed` worth it?** It is the highest-value tier and the most work —
   it needs run-record mining and a real CI feed (`health.json` is currently
   absent until someone runs `make ingest-results`). Ship 1–2 first and defer it?
2. **Severity vocabulary** — is `must / should / avoid` the right axis, or do you
   want it tied to something existing (the critic's flag taxonomy)?
3. **App repos too, or E2E repos only?** The schema fits both, but an app repo's
   useful facts are mostly surface + ownership; the conventions and pitfalls are
   test-repo concepts. Modelling both identically may be a false symmetry.
4. **Who writes `authored`?** A structured editor in the Repositories view is
   more work than a YAML file in git, but a YAML file in git means only people
   comfortable with a PR contribute — which is probably the wrong set of people
   for "here is the pitfall that cost me a day".

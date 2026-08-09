# The dashboard, view by view

`make serve` → <http://localhost:4999>. Fifteen views. This page says what each
one answers, what you can do in it, and — where it matters — what it will
deliberately refuse to tell you. The last section describes the shell they all
sit in.

Two conventions hold everywhere and are worth learning once:

**A number that was not measured is never shown as if it were.** Simulated spend
is prefixed `~`, a local model's cost renders `$0 (local)` with tokens still
counted, and an unpriced provider reads `unknown` rather than `$0`. If a figure
would have to be invented to fill the box, the box says so instead.

**An empty table means "nothing matched", and a failure says it failed.** They
used to look identical: a loader that could not reach the server left a blank
table, which reads as "there is nothing here" — the opposite of the truth. Any
view that could not load now says so and tells you to hit Refresh.

Every view re-loads when you enter it, so navigating back to Activity or Alerts
shows the current state, not a snapshot from when the page opened.

---

## Overview

**Answers:** what is the state of the estate, and what should I look at first?

The *Needs attention* card is the one to read first: failed runs, review debt,
expiring waivers, and alert rules currently firing. The team report card is the
same content as `make report`, and the LLM-spend tile is the same figure as the
Cost view.

The "est. avoided spend" tile only exists when a measured median can price it.
Its absence is not a bug — it means nothing on this estate has been measured
yet, and the tile refuses to show a number derived from mock runs.

A **Start here** panel appears while the estate is still being set up: register
repositories, generate tests from a PR or ticket, review what came out. Each
step reports what is actually TRUE (repo counts, run counts, cataloged tests),
so it doubles as a status check for a half-built estate rather than a splash
screen, and the whole panel disappears once the three are done.

That panel exists because an estate with no repos, no runs and no catalog used
to be told "Nothing needs attention — all clear". Everything *was* clear;
nothing was set up. An absence of data was being reported as a healthy state,
which is the same mistake constitution C13 forbids elsewhere — here it landed
on the one user least able to spot it.

**Navigation** is grouped: **Start** (orient and launch), **Work** (the things
you do to a request), **Insight** (what the work produced), **Configure** (how
the platform behaves). Fifteen flat entries gave a newcomer no way to tell the
three they need from the twelve they do not.

## Guided run

**Answers:** I have a PR or a ticket and I do not know the command.

Two journeys — PR sync and ticket-to-tests — sequenced step by step. It drives
the *same* endpoints as every other view; it only orders them and polls for
progress. Generation is asynchronous (a run is minutes, an OpenHands
conversation longer), so starting it and coming back is the intended usage.

A failing step shows the run's actual error, not just "run failed" — the queue
runner is a background process whose console nobody reads, so the reason is
captured and surfaced here.

## Run progress

**Answers:** where is my request right now, and if it failed — which step, why,
and where do I look?

Guided run answers the *journey* question and deliberately collapses the run
itself into one step ("the agent is analyzing and writing tests"). That is the
right grain for a wizard and useless for tracing a failure. This view is the
inside of that step: the pipeline's actual stages, each with what it is FOR, in
the order the engine runs them for that mode.

While a run is live the view polls; once it finishes the run record is the
source of truth. Entering a key that never ran says so, rather than showing a
ladder of pending steps that reads as "queued and starting soon".

**What it refuses to tell you.** It will not claim a step succeeded that it
could not observe. A step shows `unknown` — visually distinct from pending and
done — when the run holding this checkout is gone (a lock older than 90 minutes
is presumed dead), or when a finished record has no contract for a phase and no
skip reason. A record with no gate block does not report the gate as passed: a
run aborted on budget never reached it, and saying "done" there would tell you
tests were committed when nothing was.

**Debugging a failure.** A failed gate names the repo, the exit code AND what
that code means in words, the log path, and the tail of that log. An exit code
this pipeline does not document is labelled `UNRECOGNIZED` rather than given an
invented meaning — a wrong explanation sends you somewhere that is not the
problem. A log that could not be read says so; that is not the same as a log
that was empty.

## Queue

**Answers:** what work is pending, and how do I add some?

Fetch tickets by release or fixVersion (free text), paste a PR URL, or paste raw
JIRA context for a ticket that does not exist yet. A pasted Stash/Bitbucket/
GitHub PR URL is parsed into its project and slug — on Stash the URL carries the
project key, which is otherwise a per-repo registry field you would have to know.

Queue intake **warns** when a key's measured history exceeds its effective
budget envelope. When generated-test review is active, the warning spells out
the base cap plus provisional agent-review uplift; plan-only and disabled/off
review retain the base. It does not refuse: the envelope is a planning number,
not a permission.

## Test plans

**Answers:** what did the platform propose, and do I agree?

The plan editor is where the human gate lives. Approving **signs** the plan
against a content hash; editing an approved plan revokes the approval, so
"approved" always refers to text somebody actually read.

Also here: the diff since last approval (so re-approval reviews the *change*,
not the document on faith), the adversarial review summary, ambiguities raised
during analysis, and — when a similar prior plan was adapted — a banner naming
what it was reused from and how similar it was.

## Plan → tests journey

**Answers:** where is each ticket between an approved plan and delivered tests, and what is
blocking it?

Six states render as plain-language milestones: acceptance criteria awaiting
validation → test plan being authored → plan awaiting your approval → tests
awaiting generation → tests awaiting delivery → delivered and running in CI.
The greppable machine name remains behind ⓘ. Each row names the *specific*
blocker, who owns it, and exactly one next-action button. The equivalent CLI
command is printed directly below the button. Both values come from
`spec_workflow.py`; browser code does not infer a second workflow from the
machine-state name.

The **How this works** card teaches the loop in five sentences and owns the
in-product glossary. Its important distinction is explicit: an **approved test
plan (signed)** has a structured signature, while an **approved test plan
(prose — not signed)** may proceed without scenario-level drift and enforcement
guarantees. **Acceptance criteria (EARS)** are the formalized ticket behaviors,
not another name for the test plan.

Read the header first. It states whether any of this is **enforced** in your
estate, because "blocked" means different things under different configuration —
with the gates off, every step here is advisory and the platform will not stop a
run that skips one. A workflow view that quietly reflects configuration teaches
a rule nobody applies.

It also states the effective adoption level from the engine's resolved
controls: **Off**, **Reviewed plans**, **Validated criteria**, or **Enforced
coverage**. Enforced coverage always shows its sub-state: **warn** is a dry run
(reporting, not refusing), while **strict** refuses uncovered, unwaived
scenarios. An unmatched or unusable hand configuration is **Custom** and shows
the raw resolved values instead of being forced into a friendly label.

Three more cards live here:

- **Acceptance criteria (EARS)** — the testable statements formalized from the ticket, and the
  ambiguities found in it. Approving signs the file. A *blocking* ambiguity
  stops planning with a question on the ticket rather than a guess.
- **Waivers** — an approved scenario shipping without a test. Every waiver needs
  a reason (at least a sentence — it must be answerable by someone reading it in
  six months), an owner, and an expiry capped at 90 days, so "temporarily"
  cannot quietly become "forever". Expired ones stay listed: a lapsed exception
  is the row worth reading. Expired waivers and stale scenarios render the same
  refusal contract the CLI emits: what refused, why, and one next action.
- **Work this test plan makes unnecessary** — approved scenarios a cataloged test
  already covers, so they need no authoring call. It counts them and refuses to
  price them: converting a skipped scenario into money needs a measured
  authoring cost, and until `make parity-*` can run, this estate has none. It is
  advisory — nothing skips authoring automatically, because a wrong join would
  silently drop coverage, the one failure this platform cannot see.
- **The rules** — generated from `specs/platform/constitution.yaml`, with each
  rule showing the test that holds it. A clause whose pin has been deleted is
  reported as undefended rather than printed as though it still held. Download
  it as markdown to share with people who will never open the dashboard.

## Runs & team reviews

**Answers:** what ran, what did the gate decide, and who still needs to look?

Per-run gate status and exit code per test repo, the critic's advisory score
(which never gates a commit), release, and review state. Filter by release or
review status. The OpenHands conversations card sits here and in Test plans —
where agents are launched.

## Activity

**Answers:** who did what, and what happened because of it?

The append-only transaction log. Filter by kind, actor, target or outcome; export
to CSV. Secrets are redacted at write time by a key denylist plus a length
ceiling.

If the log could not be fully written, this view says the history is
**incomplete** rather than showing a convincing partial list.

## Alerts

**Answers:** what should page someone, and is it firing now?

Rules over the transaction log: match a kind/outcome/target, a threshold, a
window, a cooldown, and a channel. Firing is a *state* — it resolves when the
condition clears. The cooldown gates the message, not the state, so a flapping
condition does not spam.

**Set `to` for any email rule.** An email or `both` rule with no recipients
delivers nowhere, and the row will tell you so. A configured alert that silently
reaches no one is worse than no alert, because it is believed.

If the log cannot be read, a rule reports `unevaluable` and names what was lost.
It never reports `ok`, which would mean "checked, and fine".

`Test` sends through the **real** channel — deliberately without retries, so a
test tells you about the channel as it is right now.

## Trace

**Answers:** for this requirement, show me the whole chain.

One row per plan scenario: ticket → scenario → generated spec → gate commit → CI
health. An approved scenario with **no** test still gets a row — that is the
loudest line on an audit. PR-path tests with no plan appear too. CSV export for
auditors who want it in a spreadsheet.

## Cost

**Answers:** what did the LLM work cost, and where?

By workflow, key, phase and model tier, with turn calibration and cache hit
rates. The provider card splits local from cloud tokens — the figure that
actually justifies moving a phase off a paid provider.

The four cost bases never mix: `reported` (the provider returned a figure),
`estimated` (`~`, tokens priced from config), `local` (`$0`, tokens tracked), and
`simulated` (mock). A provider with no price entry stays `unknown`, because a
zero would understate a real bill. When any spend is unpriced the total is
labelled **incomplete** — and the budget ceiling says plainly that it cannot
enforce on spend it cannot price.

## Artifacts

**Answers:** what code was actually generated?

Per key: the phase contracts and the gate commit diff. The workspace is
ephemeral, so this diff is the durable copy of the generated test code.

## Catalog

**Answers:** which tests exist, what do they cover, and how confident are we?

Every test mapped to app repos with evidence and a confidence tier. Filter by
repo, mapping status, or free text. CI health per test comes from ingested JUnit
results; quarantining a flaky test tags it here — the printed exclusion line is a
*proposal* for the repo owner's own CI, because the platform never edits a test
repo's configuration.

## Repositories

**Answers:** what is in the estate, and what does each test repo cover?

Add and edit app and test repos, set a test repo's declared scope, edit per-repo
team notes, and sync `AGENTS.md`/`CLAUDE.md` from the SCM without cloning.

`covers:` is **generated** from catalog evidence ∪ declared scope — do not try to
hand-edit it. Curated guidance is durable and user-edited; a file the repo itself
owns always outranks both curated and generated copies.

## Settings

**Answers:** how is this connected, and does it work?

Edits `.env` (secrets are write-only — they are never read back to the page).
"Validate connections" probes every configured external system read-only: it
never posts, pushes or sends.

Test-plan adoption lives here too. The normal path is one of four named levels:

- **Off** — Plans remain prose; nothing is signed or enforced.
- **Reviewed plans** — Plans are structured and signed by a human before generation.
- **Validated criteria** — Acceptance criteria are formalized and approved before planning.
- **Enforced coverage** — The gate checks signed plans' approved scenarios;
  prose plans remain exempt. Choose **warn** for a visible dry run, then
  **strict** when the signal is ready to refuse delivery.

The effective level is derived from what the engine actually resolves, not the
last button clicked. Applying one writes only `AIQE_SPEC_MODE`,
`AIQE_REQUIREMENTS_GATE`, and `AIQE_SPEC_ENFORCE`. The same raw controls remain
below for diagnosis and deliberate Custom estates:

- **Requirements gate** — off: planning proceeds without approved requirements.
  On: planning refuses until they are approved.
- **Spec enforcement** — `off` ignores uncovered scenarios, `warn` reports them
  and still commits, `strict` makes the gate refuse (exit 8) on an uncovered,
  unwaived scenario. Roll out in that order. Turning on `strict` first just
  teaches people to bypass the gate.
- **Agent review delivery** — `off` does not run the reviewer; `warn` records
  and surfaces findings while the deterministic gate still runs; `require`
  refuses before the gate on final needs-work findings (exit 78), so nothing is
  committed. Measure under `warn` before changing the estate-wide org-config
  policy to `require`; there is no per-run bypass.

The Danger zone clears generated demo data; factory reset additionally empties
the registry and team notes. Both refuse while a pipeline lock looks live.

---

## The shell every view sits in

Implemented from the "QA Dashboard" Claude Design. The whole page is generated by
`bin/dashboard.py` as a single self-contained HTML file — no build step, no CDN,
so the layout and styling are the same whether it is served (`make serve`) or
opened from disk. What differs is behaviour, not appearance: opened as a file it
is a snapshot, and the views that act on the estate need the server.

**Geometry comes from tokens, not literals.** Radii, control heights, the sidebar
width, the topbar height and the content max-width are all `--sr-*` custom
properties declared once:

| Token | Value | Governs |
|---|---|---|
| `--sr-sidebar-w` | 240px | the nav column |
| `--sr-topbar-h` | 56px | topbar and logo row (they must agree, or the header steps) |
| `--sr-control-h` / `-sm` | 36 / 32px | buttons, inputs, selects |
| `--sr-radius-sm/-/-lg/-full` | 4 / 8 / 12 / 9999px | corners, including pills and status dots |
| `--sr-content-max` | 1240px | reading width on a wide monitor |
| `--sr-ring` | — | the keyboard focus outline |

The colour half of the token set was already here; the geometry half was written
as a literal at each use site, so the design could not be adjusted in one place.
`--sr-ring` was defined by neither side, which is why keyboard focus fell back to
the browser default and was invisible against the dark primary. Every interactive
element now takes a 2px `:focus-visible` outline — the dashboard is fully
keyboard-navigable and that is the only thing that shows you where you are.

**Layout** is a two-column grid (`--sr-sidebar-w 1fr`). Below 900px the grid
restates itself as one column and the sidebar stacks above the content. That rule
is load-bearing and easy to lose: the shell was converted from flex to grid, and
`flex-direction: column` is a *no-op* on a grid — the sidebar would have stayed a
240px column on a phone with nothing reporting a problem. `test_design_tokens.py`
pins it, along with the rule that no `var(--sr-…)` is used without being defined
(an undefined token silently resolves to nothing, which is how an invisible focus
ring happens).

**Dark mode** follows `prefers-color-scheme`; both palettes are declared, so there
is no toggle to get out of sync with the OS.

**The breadcrumb** in the topbar reads `ai-qe / <view>` and is updated by `go()`
on every navigation. It is rendered once server-side and updated client-side, so
both have to agree — otherwise the topbar names a view you are not on.

# Architecture Diagrams

Rendered (Mermaid) views of the system described in [architecture.md](architecture.md).
Section references (§) point there. GitHub and most IDEs render these natively.

Contents: 1 system overview · 2 ports & adapters · 3–4 Workflows A/B · 5 the gate ·
6 resolution · 7 catalog bootstrap · 8 workspace · 9 estate knowledge & repo config ·
10 QA monitoring/review/release · 11 sharing the test plan · 12 team report ·
13 configuration & estate management.

## 1. System overview (§4.2)

```mermaid
flowchart TB
    subgraph TRIGGERS["Trigger layer — all paths normalize to one TaskEvent"]
        GH["SCM webhook (GitHub /<br/>Bitbucket / Stash)<br/>PR labeled 'ai-tests'"]
        JIRA["JIRA Automation webhook<br/>(ticket labeled 'ai-test-gen')"]
        UI["Dashboard (make serve)<br/>fetch by release · queue ·<br/>pasted JIRA text (inline)"]
        HOOK["TaskEvent receiver :4998<br/>validate · dedupe (idempotent) ·<br/>enqueue"]
    end

    subgraph ORCH["Orchestration"]
        OH["OpenHands Agent Server<br/>(sandbox per run)"]
        WQ["Work queue<br/>(reports/runs/queue.json,<br/>locked, re-queue/remove)"]
    end

    subgraph EXEC["Execution — engine/pipeline.sh (per-checkout run lock)"]
        R0["Phase 0: Resolve<br/>(rules-first, registry)"]
        CLONE["Workspace clone<br/>src/ read-only · tests/ writable"]
        CTX["Context refresh: AGENTS.md ·<br/>PR diff · coverage gaps ·<br/>issue-type guidance · Confluence bodies"]
        PHASES["LLM phase chain<br/>(claude -p, per-phase allowedTools)"]
        GATE["Deterministic gates — PARALLEL<br/>per test repo (the ONLY git push)"]
    end

    subgraph OUT["Outputs"]
        BR["test/&lt;KEY&gt;-ai-qe branches<br/>+ born-mapped catalog entries"]
        CMT["PR / JIRA comments +<br/>ai-qe build status on the PR head"]
        REC["Run records + archived diffs ·<br/>review/release tracking ·<br/>Splunk / Slack"]
    end

    GH --> HOOK
    JIRA --> HOOK
    GH -.-> OH
    JIRA -.-> OH
    UI --> WQ
    HOOK --> WQ
    OH --> R0
    WQ --> R0
    R0 --> CLONE --> CTX --> PHASES --> GATE
    GATE --> BR
    GATE --> CMT
    GATE --> REC
```

## 2. Ports & adapters — the reusable platform (§5.10)

```mermaid
flowchart LR
    subgraph ENGINE["Core engine (vendor-free)"]
        P["Trigger normalizer → Resolver →<br/>Phase pipeline → Gate → Reporter"]
    end

    P -- Scm --> SCM["GitHub · Bitbucket<br/><i>mock: adapters/mock/scm.sh</i>"]
    P -- Tracker --> TR["Jira (Atlassian MCP)<br/><i>mock: tracker.sh</i>"]
    P -- Knowledge --> KN["Confluence<br/>(linked PRDs → analyze context)"]
    P -- Cicd --> CI["Jenkins · GH Actions ·<br/>Bitbucket Pipelines"]
    P -- Notify --> NO["Slack<br/><i>mock: notify.sh</i>"]
    P -- Telemetry --> TE["Splunk HEC<br/><i>mock: telemetry.sh</i>"]
```

Every adapter — real or mock — answers the same verbs; unknown verbs exit 64
(`make conformance` enforces this). `AIQE_MOCK=1` swaps the whole right-hand column
for mocks without touching the engine.

## 3. Workflow A — PR-triggered test sync (§5.1)

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant SCM as SCM (GitHub/BB)
    participant P as pipeline.sh
    participant RES as resolve.py
    participant LLM as Phases (triage→generate→validate)
    participant ENV as with-env.sh
    participant G as gate.sh

    Dev->>SCM: open PR (label ai-tests)
    SCM->>P: webhook → pr <repo> <number>
    P->>SCM: changed_files + diff (the real patch hunks)
    P->>RES: changed files + registry
    RES-->>P: source_repos, test_repos, cross_repo_impact, confidence
    alt confidence below threshold
        P->>SCM: clarifying comment (candidates) — run ends
    end
    P->>P: clone src/ (read-only), tests/ (branch test/KEY-ai-qe)
    P->>P: refresh AGENTS.md + coverage gaps (surface with NO test)
    P->>LLM: triage (diff + catalog slice + gaps) → generate specs + sidecar → validate
    par one gate per test repo (parallel)
        P->>G: gate.sh KEY repo
        G->>ENV: boot app-under-test (OS-assigned free port)
        ENV-->>G: BASE_URL exported
        G->>G: scope ✓ born-mapped ✓ lint ✓ run new specs ✓ secret scan ✓
        ENV-->>ENV: teardown (trap — guaranteed)
        G-->>P: GATE_STATUS=COMMITTED sha
    end
    P->>SCM: aggregated summary comment
    P->>SCM: set_status: ai-qe success|failure on the PR head commit
    Note over P: diff archived to reports/runs/ · review state → pending_review
```

## 4. Workflow B — JIRA-triggered test authoring (§5.2)

```mermaid
sequenceDiagram
    participant QE as QE Lead
    participant J as JIRA (Tracker port)
    participant C as Confluence (Knowledge port)
    participant P as pipeline.sh
    participant LLM as Phases
    participant G as gate.sh

    QE->>J: label ticket ai-test-gen
    J->>P: webhook → jira PROJ-301
    alt pasted JIRA context (no ticket)
        Note over P: AIQE_INLINE_FILE — qa.py run-inline /<br/>dashboard textarea synthesizes the ticket
    end
    P->>J: get_item → components, labels, linked repos, fixVersions, issue type
    P->>P: release captured (fixVersions) · issue-type guidance selected<br/>(story | bug regression | security negative-tests)
    P->>C: get_linked_docs (PRD page BODIES, budgeted, untrusted data)
    P->>P: resolve (component map + label restrictions, e.g. api-only)
    P->>LLM: analyze (guidance + ticket + Confluence)
    P->>LLM: testplan (+ coverage gaps) → testdata → generate → validate
    P->>G: parallel gates (same as Workflow A)
    G-->>P: GATE_STATUS per repo
    P->>J: comment: plan link, tests, per-repo status
    Note over P: + Slack summary · Splunk run record ·<br/>review state → pending_review · plan exportable<br/>(pdf/docx/Confluence/JIRA attach)
```

## 5. The deterministic gate (§5.5)

```mermaid
flowchart TD
    S([gate.sh KEY test_repo]) --> T6{cwd is a standalone<br/>test repo?}
    T6 -- no --> E6["exit 6 GATE_REFUSED"]
    T6 -- yes --> CH{any changes?}
    CH -- no --> OK0(["exit 0 · GATE_STATUS=NO_CHANGES"])
    CH -- yes --> T2{safe filenames +<br/>only test-repo paths?}
    T2 -- no --> E2["exit 2 SCOPE_VIOLATION<br/>(unsafe charset OR out of scope)"]
    T2 -- yes --> T4{every new spec has a<br/>catalog sidecar entry?}
    T4 -- no --> E4["exit 4 UNMAPPED_TEST"]
    T4 -- yes --> L["lint (commands.lint from .ai-qe/config.yaml)"]
    L --> RUN["with-env.sh: boot app (fail if not ready) →<br/>run changed specs → teardown"]
    RUN -- fail --> E5["exit 5 TESTS_FAILED<br/>(log → reports/, NOT committed)"]
    RUN -- pass --> T3{secret / PII patterns<br/>in new content?}
    T3 -- yes --> E3["exit 3 SECRET_PATTERN"]
    T3 -- no --> CP["git commit"]
    CP --> PUSH{remote configured?}
    PUSH -- yes, push ok --> OK(["exit 0 · GATE_STATUS=COMMITTED sha"])
    PUSH -- no remote (demo) --> OK
    PUSH -- push failed --> E7["exit 7 PUSH_FAILED<br/>(auth / protection / network —<br/>never reported as success)"]
```

All red paths quarantine the run for human inspection — never auto-retried. The
scope check rejects filenames outside a safe charset **before** any spec name is
interpolated into a shell command (the gate is the deterministic safety boundary).
Codes 2–5 are permanently regression-tested by `make test-gate`.

## 6. Repo resolution — Phase 0 (§5.8.2)

```mermaid
flowchart TD
    E[TaskEvent: PR or JIRA] --> RULES["Deterministic rules<br/>PR: registry lookup + contract fan-out to consumers<br/>JIRA: component map + label map + dev-panel links"]
    RULES --> CONF{confidence ≥ 0.8?}
    CONF -- yes --> GO["proceed with resolved set<br/>(rationale in run record)"]
    CONF -- no --> LLMR["LLM resolver (Haiku)<br/>ticket/PR text + registry"]
    LLMR --> CONF2{confidence ≥ 0.8?}
    CONF2 -- yes --> GO
    CONF2 -- no --> ASK["post clarifying comment to JIRA/PR<br/>human replies '@openhands use &lt;repos&gt;'"]
```

## 7. Catalog bootstrap (§5.9.2)

```mermaid
flowchart TD
    A["Stage 1 EXTRACT (deterministic)<br/>titles · tags · endpoints · routes · fixtures"]
    --> B["Stage 2 CORRELATE (deterministic joins)<br/>endpoints ↔ OpenAPI contracts<br/>routes ↔ frontend route tables<br/>JIRA keys ← git history of each spec"]
    --> C["Stage 3 CLASSIFY (LLM, residue only)"]
    --> D{"Stage 4 tier by confidence"}
    D -- "≥ 0.85" --> AUTO["auto-accepted"]
    D -- "0.5–0.85" --> REV["review queue<br/>catalog/review/*.csv → QE"]
    D -- "< 0.5" --> ORPH["orphan<br/>(dead-test candidates)"]
    AUTO --> PUB["Stage 5 PUBLISH<br/>catalog/&lt;repo&gt;.jsonl committed<br/>registry covers[] regenerated"]
    REV --> PUB
```

## 8. Workspace layout per run (§5.8.3)

```mermaid
flowchart LR
    subgraph WS["workspace/ (ephemeral, gitignored)"]
        SRC["src/&lt;source-repo&gt;<br/>read-only clone @ PR head"]
        TST["tests/&lt;test-repo&gt;<br/>writable · branch test/KEY-ai-qe<br/>own .git — commits land HERE"]
    end
    OUT2["out/ — phase JSON contracts"]
    REP["reports/ — gate logs"]
    SRC -.->|app-under-test source| TST
    TST --> REP
```

The gate refuses (exit 6) to operate in any directory that resolves to the scaffold's
own repository — workspace clones are always independent git repos.

## 9. Estate knowledge & repository configuration

Every path that changes estate truth regenerates `AGENTS.md`, so LLM phases always
plan and generate against current facts:

```mermaid
flowchart TD
    subgraph SOURCES["Sources of truth"]
        REG["registry/repo-registry.yaml<br/>(repo config · scope · routing hints)"]
        CAT["catalog/*.jsonl<br/>(test knowledge + mappings)"]
        ART["contracts & route tables<br/>(workspace/src/ fresh, demo/ fallback)"]
        GUI["Per-repo guidance:<br/>knowledge/repos/&lt;name&gt;.md (team notes)<br/>+ repo-local AGENTS.md / CLAUDE.md"]
    end

    subgraph WRITERS["What changes them"]
        RP["bin/repos.py / repo_admin.py<br/>add-app · add-test · set · link ·<br/>scope · notes · remove<br/>(+ dashboard Repositories view)"]
        OB["bin/onboard.sh<br/>register new repo"]
        QA["bin/qa.py<br/>map · apply-review"]
        BS["catalog bootstrap"]
    end

    CI["CI results ingest<br/>(JUnit / Jenkins testReport)"] --> HL["catalog/health.json<br/>(pass rate · flakiness)"]

    RP --> REG
    RP -- "notes" --> GUI
    OB --> REG
    QA --> CAT
    BS --> CAT
    CAT -- "regen_coverage.py:<br/>covers = evidence ∪ scope" --> REG

    REG --> GAPS["coverage_gaps.py<br/>surface vs evidence"]
    CAT --> GAPS
    ART --> GAPS
    REG --> GEN["bin/gen_agents_md.py"]
    CAT --> GEN
    ART --> GEN
    GUI -- "'Repository guidance' section" --> GEN
    GAPS -- "[NO TEST] annotations" --> GEN
    GEN --> AG["AGENTS.md<br/>(estate knowledge)"]
    AG --> PH["LLM phases: triage · analyze ·<br/>testplan · testdata · generate"]
    GAPS -- "out/coverage-gaps.md" --> PH
    CAT --> DB["index_db.py →<br/>reports/catalog.db<br/>(SQLite query index)"]
    HL --> DB
    RP -. "re-runs routing goldens" .-> GT["registry/tests goldens"]
```

Each E2E test repo carries a hand-managed **scope** (the app repos it is responsible
for — many app repos map to one test repo). `covers[]` stays generated as *catalog
evidence ∪ scope*, so a newly-mapped repo routes immediately, before any test evidence
exists, without ever hand-editing coverage. **Per-repo guidance** — team notes plus any
`AGENTS.md`/`CLAUDE.md` committed inside a repo's own checkout — is merged into
`AGENTS.md` and therefore steers every generation, test-plan, and coverage-gap phase.

## 10. QA monitoring, review & release tracking

```mermaid
flowchart LR
    subgraph RUNTIME["Every pipeline run"]
        P["pipeline.sh"] --> RR["reports/runs/&lt;RUN_ID&gt;.json<br/>+ archived gate-commit .diff"]
        P --> RS["reviews.json (locked):<br/>team review + release per key<br/>(commit resets approval → pending)"]
        P --> TEL["Telemetry port → Splunk"]
    end

    subgraph SURFACES["QA surfaces"]
        ST["make status / reviews<br/>(review + release columns)"]
        DB["make serve — authed dashboard (7 views):<br/>Overview · Intake &amp; queue · Runs &amp; reviews ·<br/>Artifacts · Test catalog · Repositories · Settings"]
        AR["qa.py artifacts &lt;KEY&gt;<br/>plan · data · tests · diffs"]
        REP["make report / qa.py report<br/>(md·html·docx·pdf): completed work ·<br/>queue · throughput · estate health"]
        SC["eval/scorecard.py: commit rate ·<br/>repair loops · update-vs-create ·<br/>acceptance · flakiness"]
    end

    RR --> ST
    RR --> DB
    RR --> AR
    RR --> REP
    RR --> SC
    RS --> ST
    RS --> DB
    RS --> REP
    RS --> SC
    TEAM["QE: qa.py mark / release<br/>(or dashboard Approve button)"] --> RS

    subgraph REVIEW["Mapping review loop"]
        Q1["catalog/review/&lt;repo&gt;-queue.csv"] --> Q2["QE fills decision column"]
        Q2 --> Q3["bin/qa.py apply-review / map"]
        Q3 --> Q4["catalog updated → covers[] +<br/>AGENTS.md + catalog.db regenerated"]
    end

    Q4 --> DB
```

## 11. Sharing the test plan

```mermaid
flowchart LR
    PLAN["testplans/&lt;KEY&gt;.md<br/>(source of truth, in Git)"] --> X["export_plan.py<br/>+ run metadata: release · review ·<br/>scenarios · data · tests · validation · commits"]
    X --> MD["Markdown / standalone HTML"]
    X --> DOCX["Word .docx<br/>(stdlib OOXML writer)"]
    X --> PDF["PDF<br/>(stdlib native writer, searchable)"]
    X --> CONF["Confluence page<br/>(publish_doc: create-or-update,<br/>one-way mirror + do-not-edit note)"]
    X --> ATT["JIRA issue attachment<br/>(Tracker attach verb)"]
    UI2["Dashboard artifact card:<br/>export links + publish/attach buttons"] -.-> X
    CLI["make export-plan / publish-plan /<br/>attach-plan · qa.py"] -.-> X
```

## 12. Team status report

One shareable document — for standups and release readouts — aggregated from state
the platform already keeps. Same stdlib renderers as the test-plan export.

```mermaid
flowchart LR
    subgraph STATE["Existing platform state"]
        RR["run records<br/>reports/runs/*.json"]
        RVS["review board + release<br/>reviews.json"]
        Q2["work queue<br/>queue.json"]
        CT["catalog + coverage gaps"]
        HL2["CI health<br/>catalog/health.json"]
    end
    TR["team_report.py<br/>build(days, release)"]
    RR --> TR
    RVS --> TR
    Q2 --> TR
    CT --> TR
    HL2 --> TR
    TR --> SEC["Sections: summary (commit rate ·<br/>new vs extended · repair loops) ·<br/>completed work · quarantined ·<br/>awaiting review · queue · by-release ·<br/>throughput · estate health"]
    SEC --> OUT3["md · html · docx · pdf"]
    UI3["Dashboard Overview card:<br/>period + release pickers"] -.-> TR
    CLI2["make report / qa.py report ·<br/>GET /api/report"] -.-> TR
```

## 13. Configuration & estate management (dashboard)

Everything a QA lead configures lives in two dashboard views (plus CLI parity), so no
YAML or `.env` editing is required.

```mermaid
flowchart TD
    subgraph REPOSV["Repositories view — repo_admin.py"]
        A1["Application repos:<br/>add/edit ui &amp; service repos ·<br/>domains · contract/routes · consumes"]
        A2["E2E test repos + mapping:<br/>add/edit · set scope (many app → one test)"]
        A3["Per-repo guidance editor<br/>(knowledge/repos/&lt;name&gt;.md)"]
    end
    subgraph SETV["Settings view"]
        B1["Integrations → .env<br/>(GitHub/Bitbucket/Stash · JIRA ·<br/>Confluence · OpenHands · Jenkins ·<br/>Slack/Splunk · budgets · adapter mode)<br/>secrets are write-only"]
        B2["Danger zone: Clear demo data<br/>(generated state only; estate kept)"]
    end
    A1 --> REG2["registry (validated · goldens re-run)"]
    A2 --> REG2
    A2 -- "covers = evidence ∪ scope" --> REG2
    A3 --> AG2["AGENTS.md regenerated"]
    REG2 --> AG2
    B1 --> ENV[".env (secrets masked on read;<br/>loaded by pipeline + server + exports)"]
    B2 --> DEMO["demo_data.clear()<br/>(locked · refuses during a run)"]
    AG2 --> PH2["injected into every LLM phase"]
```

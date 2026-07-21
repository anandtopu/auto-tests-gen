# Architecture Diagrams

Rendered (Mermaid) views of the system described in [architecture.md](architecture.md).
Section references (§) point there. GitHub and most IDEs render these natively.

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
    CH -- yes --> T2{only test-repo paths<br/>touched?}
    T2 -- no --> E2["exit 2 SCOPE_VIOLATION"]
    T2 -- yes --> T4{every new spec has a<br/>catalog sidecar entry?}
    T4 -- no --> E4["exit 4 UNMAPPED_TEST"]
    T4 -- yes --> L["lint (commands.lint from .ai-qe/config.yaml)"]
    L --> RUN["with-env.sh: boot app → run changed specs → teardown"]
    RUN -- fail --> E5["exit 5 TESTS_FAILED<br/>(log → reports/, NOT committed)"]
    RUN -- pass --> T3{secret / PII patterns<br/>in new content?}
    T3 -- yes --> E3["exit 3 SECRET_PATTERN"]
    T3 -- no --> CP["git commit + push<br/>(the only push in the platform)"]
    CP --> OK(["exit 0 · GATE_STATUS=COMMITTED sha"])
```

All red paths quarantine the run for human inspection — never auto-retried. Codes 2–5
are permanently regression-tested by `make test-gate`.

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
        REG["registry/repo-registry.yaml<br/>(repo config + routing hints)"]
        CAT["catalog/*.jsonl<br/>(test knowledge + mappings)"]
        ART["contracts & route tables<br/>(workspace/src/ fresh, demo/ fallback)"]
    end

    subgraph WRITERS["What changes them"]
        RP["bin/repos.py<br/>set · link · unlink · remove"]
        OB["bin/onboard.sh<br/>register new repo"]
        QA["bin/qa.py<br/>map · apply-review"]
        BS["catalog bootstrap"]
    end

    CI["CI results ingest<br/>(JUnit / Jenkins testReport)"] --> HL["catalog/health.json<br/>(pass rate · flakiness)"]

    RP --> REG
    OB --> REG
    QA --> CAT
    BS --> CAT
    CAT -- "regen_coverage.py" --> REG

    REG --> GAPS["coverage_gaps.py<br/>surface vs evidence"]
    CAT --> GAPS
    ART --> GAPS
    REG --> GEN["bin/gen_agents_md.py"]
    CAT --> GEN
    ART --> GEN
    GAPS -- "[NO TEST] annotations" --> GEN
    GEN --> AG["AGENTS.md<br/>(estate knowledge)"]
    AG --> PH["LLM phases: triage · analyze ·<br/>testplan · testdata · generate"]
    GAPS -- "out/coverage-gaps.md" --> PH
    CAT --> DB["index_db.py →<br/>reports/catalog.db<br/>(SQLite query index)"]
    HL --> DB
    RP -. "re-runs routing goldens" .-> GT["registry/tests goldens"]
```

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
        DB["make serve — authed dashboard:<br/>runs per E2E repo · release filter ·<br/>artifact cards · CI health · queue"]
        AR["qa.py artifacts &lt;KEY&gt;<br/>plan · data · tests · diffs"]
        SC["eval/scorecard.py: commit rate ·<br/>repair loops · update-vs-create ·<br/>acceptance · flakiness"]
    end

    RR --> ST
    RR --> DB
    RR --> AR
    RR --> SC
    RS --> ST
    RS --> DB
    RS --> SC
    TEAM["QE: qa.py mark / release"] --> RS

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

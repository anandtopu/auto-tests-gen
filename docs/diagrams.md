# Architecture Diagrams

Rendered (Mermaid) views of the system described in [architecture.md](architecture.md).
Section references (§) point there. GitHub and most IDEs render these natively.

## 1. System overview (§4.2)

```mermaid
flowchart TB
    subgraph TRIGGERS["Trigger layer — all paths normalize to one TaskEvent"]
        GH["GitHub / Bitbucket webhook<br/>(PR labeled 'ai-tests')"]
        JIRA["JIRA Automation webhook<br/>(ticket labeled 'ai-test-gen')"]
        JENK["Jenkins generic webhook"]
    end

    subgraph ORCH["Orchestration — OpenHands Agent Server"]
        Q["Task queue + dedup<br/>(idempotency keys)"]
        SB["Sandbox provisioning<br/>(ephemeral Docker)"]
    end

    subgraph EXEC["Execution — engine/pipeline.sh inside sandbox"]
        R0["Phase 0: Resolve<br/>(rules-first, registry)"]
        CLONE["Workspace clone<br/>src/ read-only · tests/ writable"]
        KB["AGENTS.md regenerated<br/>(estate knowledge from fresh clones)"]
        PHASES["LLM phase chain<br/>(claude -p, per-phase allowedTools,<br/>AGENTS.md as context)"]
        GATE["Deterministic gate<br/>(the ONLY git push)"]
    end

    subgraph OUT["Outputs"]
        BR["test/&lt;KEY&gt;-ai-qe branches<br/>+ born-mapped catalog entries"]
        CMT["PR / JIRA comments"]
        TEL["Run records → Splunk<br/>Slack notifications"]
    end

    GH --> Q
    JIRA --> Q
    JENK --> Q
    Q --> SB --> R0 --> CLONE --> KB --> PHASES --> GATE
    GATE --> BR
    GATE --> CMT
    GATE --> TEL
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
    P->>RES: changed files + registry
    RES-->>P: source_repos, test_repos, cross_repo_impact, confidence
    alt confidence below threshold
        P->>SCM: clarifying comment (candidates) — run ends
    end
    P->>P: clone src/ (read-only), tests/ (branch test/KEY-ai-qe)
    P->>LLM: triage → generate specs + catalog sidecar → validate
    P->>G: per test repo
    G->>ENV: boot app-under-test (random port)
    ENV-->>G: BASE_URL exported
    G->>G: scope ✓ born-mapped ✓ lint ✓ run new specs ✓ secret scan ✓
    ENV-->>ENV: teardown (trap — guaranteed)
    G-->>P: GATE_STATUS=COMMITTED sha
    P->>SCM: aggregated summary comment
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
    P->>J: get_item → components, labels, linked repos
    P->>C: get_linked_docs (PRD, budgeted, untrusted data)
    P->>P: resolve (component map + label restrictions, e.g. api-only)
    P->>LLM: analyze → testplan (testplans/KEY.md)
    P->>LLM: testdata (canonical cases, testdata/KEY/)
    P->>LLM: generate specs per test repo → validate
    P->>G: per test repo (same gate as Workflow A)
    G-->>P: GATE_STATUS per repo
    P->>J: comment: plan link, tests, per-repo status
    Note over P: + Slack summary, Splunk run record
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

    RP --> REG
    OB --> REG
    QA --> CAT
    BS --> CAT
    CAT -- "regen_coverage.py" --> REG

    REG --> GEN["bin/gen_agents_md.py"]
    CAT --> GEN
    ART --> GEN
    GEN --> AG["AGENTS.md<br/>(estate knowledge)"]
    AG --> PH["LLM phases: triage · analyze ·<br/>testplan · testdata · generate"]
    RP -. "re-runs routing goldens" .-> GT["registry/tests goldens"]
```

## 10. QA monitoring & mapping-review loop

```mermaid
flowchart LR
    subgraph RUNTIME["Every pipeline run"]
        P["pipeline.sh"] --> RR["reports/runs/&lt;RUN_ID&gt;.json<br/>(phases + per-repo gate outcomes)"]
        P --> TEL["Telemetry port → Splunk"]
        P --> LOGS["reports/&lt;KEY&gt;-&lt;repo&gt;.log"]
    end

    subgraph SURFACES["QA surfaces"]
        ST["make status<br/>(recent runs CLI)"]
        DB["make dashboard<br/>reports/dashboard.html"]
        CV["make coverage<br/>(matrix + gap warnings)"]
    end

    RR --> ST
    RR --> DB
    LOGS --> DB

    subgraph REVIEW["Mapping review loop"]
        Q1["catalog/review/&lt;repo&gt;-queue.csv"] --> Q2["QE fills decision column"]
        Q2 --> Q3["bin/qa.py apply-review"]
        Q3 --> Q4["catalog updated<br/>covers[] + AGENTS.md regenerated"]
    end

    Q4 --> CV
    Q4 --> DB
```

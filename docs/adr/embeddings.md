# ADR: Embedding source and vector store for semantic retrieval

**Status:** accepted (2026-07-30) · **Stories:** cost-reduction 3.1/3.2 ·
**Owners:** platform

## Context

The cost-reduction backlog needs semantic retrieval over a small corpus
(hundreds to low thousands of knowledge chunks: repo surfaces, guidance,
exemplar specs, catalog mappings, plans, testdata) to power context scoping
(2.2) and artifact reuse (3.3–3.5). Constraints that decide everything:

- The platform is **stdlib-only Python** (+ pyyaml/pytest in tooling), runs on
  Windows Git Bash, in a container, and from `make serve` — no native wheels,
  no companion services.
- Every external system sits behind a **port with a mock**; the engine never
  imports a vendor SDK. Demos and tests run with `AIQE_MOCK=1`, offline.
- An unconfigured estate must **degrade silently to the existing TF-IDF**
  similarity, not break.

## Decision

**Store: SQLite, vectors as big-endian float32 BLOBs, brute-force pure-Python
cosine** (`engine/lib/vector_index.py`, `reports/knowledge-index/vectors.db`,
gitignored derived data).

**Embedding source: any OpenAI-compatible `/v1/embeddings` endpoint over
stdlib HTTP** (`adapters/embed/http.sh` — covers Voyage, OpenAI, Azure, local
TEI/Ollama), configured via `EMBED_URL` / `EMBED_API_KEY` / `EMBED_MODEL` /
`EMBED_DIMS`. **Mock: deterministic sha256-expanded vectors**
(`adapters/mock/embed.sh`) — stable across platforms, proves plumbing, never
retrieval quality.

## Alternatives rejected

| Option | Why not |
|---|---|
| sqlite-vec / FAISS / numpy | native wheels — breaks the no-native-deps rule on Windows/CI and bloats the image for a corpus where brute force is ~10 ms |
| Chroma / Qdrant / Weaviate | a server (or a heavyweight client) to deploy, monitor, back up — pure operational weight at this scale, and a hard dependency the mock posture forbids |
| Provider SDKs (voyageai, openai) | the engine never imports a vendor; stdlib HTTP through a port keeps conformance, mocks and credential handling uniform |
| Embedding inside the phase LLM calls | couples retrieval freshness to expensive authoring calls; the index refresh is a cheap, capped, nightly-or-on-change job |

## Consequences

- Query cost is O(corpus) per lookup — fine to ~50k chunks. **Revisit trigger:
  corpus > 50k chunks or p95 query > 200 ms**, at which point sqlite-vec (if
  native deps become acceptable) or a served index is the upgrade path; the
  port boundary means consumers do not change.
- Refresh embeds only changed chunks (sha-skip) and stops at
  `budgets.max_embed_usd_per_day` — the cost-saving layer cannot become its
  own runaway bill. Queries fall back to TF-IDF wherever vectors are missing.
- A corrupt db is quarantined (`.corrupt-<ts>`) and rebuilt from chunks —
  derived data is regenerated, never repaired.
- The index is excluded from state bundles; a new deployment rebuilds with
  `make index-rebuild`.

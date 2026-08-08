# TCA-C2 per-file analysis

Scope: PRD C2.1/C2.1a, provider usage port only. TCA-C3 owns ledger comparison;
TCA-C4 owns maintenance, persistence, alerting, and UI state.

| File/surface | Review result |
|---|---|
| `engine/lib/provider_usage.py`, `llm_runner.py` | Pass after review. Provider selection is phase-independent; the engine invokes and validates the adapter contract and contains no vendor endpoint or credential name. |
| `adapters/llm/claude.sh` | Pass after review. Official Admin cost endpoint, scoped credential, bounded/repeat-safe pagination, exact Decimal fractional-cent conversion, UTC daily window, redacted failure states. |
| Codex/Ollama/OpenHands adapters | Pass. Unsupported billing is explicit `unavailable`; no zero-like cost field is emitted. |
| Mock adapter and fixture | Pass. Deterministic, synthetic, credential-free provider-reported fixture. |
| Adapter conformance | Pass. Every LLM family member declares and exercises `usage`; unavailable states cannot carry a cost. |
| Settings and environment examples | Pass after review. Admin key is write-only and separated from the LLM API key. Local, properties, and OpenShift declarations agree. |
| Make entry point and user/architecture docs | Pass. The command says it exposes provider evidence only and does not claim C3 reconciliation. |
| Provider/settings/LLM tests | Pass after review. Pagination, headers, decimal units, UTC window, redaction, malformed input/response, deterministic mock, and engine/vendor isolation are pinned. |
| `registry/tests/test_standalone.py` | Fixed during broad verification. Its real pipeline run wrote tracked PROJ-301 plans; all authored state now points at `tmp_path`. |

No production dependency, schema migration, vendor SDK, or direct engine vendor
import was introduced.

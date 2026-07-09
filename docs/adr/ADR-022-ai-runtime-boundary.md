# ADR-022: AI Runtime Boundary — EOS Orchestrates, the AI Runtime Reasons

**Status:** Accepted — platform architecture decision (frozen)
**Date:** 2026-07-09
**Deciders:** Engineering (Lead Implementation Engineer, architecture review)
**Related:**
- `docs/adr/ADR-021-engineering-operating-system.md` — EOS
- `engines/content/lib/run_score_stage.py` — reference EOS Worker (M1/M2)

---

## Context

M1 proved the Engineering OS (EOS) execution spine: external source → durable queue → Worker →
skill → structured JSON → Supabase persistence → observability. The scoring step in M1 was a
deterministic placeholder. M2 replaced it with genuine reasoning by calling the AI Runtime through
its native, OpenAI-compatible interface.

During M2 a critical architectural question surfaced and was resolved from source: **what is the
true isolation boundary in a runtime implementation?** The answer — confirmed in the runtime's own
profile/lifecycle code — is the **Profile**: each profile is a fully independent runtime (own
config, `.env`, skills, gateway, cron, memory, auth). Transport (API Server, Telegram, Slack, …)
is only a pipe *into* a runtime, not an identity or a boundary.

This led to a sharper separation that this ADR records: the EOS must integrate with **AI runtimes
generically**, not with individual models, providers, or transports. Today the runtime
implementation happens to be Hermes; tomorrow it may be OpenCode or another. The boundary must be
drawn so that neither side's internal evolution forces changes on the other.

---

## Decision

### 1. The runtime boundary

```
Engineering OS (EOS)                │   AI Runtime (implementation)
------------------------------------│------------------------------------------
queue orchestration                 │   model execution
retries / backoff                   │   model + provider ROUTING
dead-letter queue                   │   provider AUTHENTICATION
persistence (Supabase)              │   RUNTIME LIFECYCLE / bootstrap
observability                       │   model SELECTION
job contract (payload → JSON)       │   transport (API Server, Telegram, …)
```

The EOS consumes an **OpenAI-compatible `/v1/chat/completions`** contract. It knows only three
runtime-agnostic facts, published by the runtime:

- `AI_RUNTIME_BASE_URL`  — e.g. `http://127.0.0.1:8642/v1`
- `AI_RUNTIME_API_KEY`   — bearer token for the runtime's API server
- `AI_RUNTIME_MODEL`     — the model the runtime publishes as its reasoning model (telemetry only)

The EOS **never** names, selects, authenticates, or bootstraps a model/provider/transport.

### 2. The EOS must never own

- **provider selection**
- **model selection**
- **provider authentication**
- **runtime lifecycle**

Those belong entirely to the AI Runtime implementation. The runtime publishes its capability; the
EOS consumes it.

### 3. Wording discipline (enforced in code + docs)

**Do not say:** "the EOS uses <a specific model>."

**Say instead:** "the EOS uses an AI Runtime. Today that runtime happens to be Hermes."

This distinction is load-bearing: it prevents the EOS from acquiring runtime-specific coupling and
keeps future runtime swaps (OpenCode, etc.) non-events for EOS code.

### 4. Runtime Implementation v1 = Hermes

Hermes is **Runtime Implementation v1**, not the architecture. Its bootstrap is Hermes-specific and
uses only supported lifecycle commands:

- `hermes profile create <name> [--clone]` — complete runtime (config, `.env`, skills)
- `hermes -p <name> auth add <provider>` — per-profile, isolated credentials
- `hermes -p <name> config set model.default <id>` / `model.provider <name>`
- `hermes -p <name> gateway run` — runs the profile's gateway (API Server transport enabled via
  the profile `.env`: `API_SERVER_ENABLED`, `API_SERVER_KEY`, `API_SERVER_PORT`)

The EOS-facing `AI_RUNTIME_*` keys are *published* into the profile `.env` by the runtime
bootstrap. They are the only contract the EOS reads.

### 5. Future runtimes

When OpenCode (or another runtime) is proven with the same engineering discipline, the EOS code is
**unchanged**. The new runtime simply publishes the same `AI_RUNTIME_*` contract. Example internal
evolution that must NOT touch the EOS:

- **Hermes Runtime:** one model today, another tomorrow.
- **OpenCode Runtime:** a different model, or another free model.

Runtime-internal model/provider/transport decisions are the runtime's sovereign concern.

### 6. Abstraction discipline

No common "Runtime interface" abstraction is introduced yet. Per the established discipline,
shared infrastructure is extracted **only after proven reuse** (≥2 runtimes actually implemented).
Until then, each runtime's bootstrap lives in its own lifecycle; the EOS depends only on the
OpenAI-compatible contract above. A common abstraction is deferred until OpenCode (or a second
runtime) is independently proven.

---

## Consequences

- The EOS code (`engines/content/lib/run_score_stage.py`, `engines/_shared/eos_queue.py`) contains
  **zero** model/provider/transport names in its execution path. It references only `AI_RUNTIME_*`.
- Swapping or upgrading the reasoning model is a runtime-bootstrap change, not an EOS change.
- Adding a second engine (GEO, Prospect) reuses the same EOS contract; each engine maps to its own
  runtime profile (Runtime v1) today.
- Adding a second *runtime* (OpenCode) requires no EOS code change — only its native bootstrap
  publishing `AI_RUNTIME_*`.

## Anti-patterns explicitly forbidden

- EOS code branching on `if runtime == "hermes"` / `"opencode"`.
- EOS hardcoding a model id, provider, or API base URL.
- EOS calling a provider SDK directly (it must always go through the AI Runtime).
- Naming a specific model in EOS-facing documentation.

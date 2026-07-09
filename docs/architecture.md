# Architecture

Growth OS is an open-source **AI execution platform**. It owns the machinery that turns a unit of
work (a *job*) into a structured result persisted for observability. It is consumed by products
through **public APIs and events** — never through shared databases, runtimes, or internal
libraries.

## Bounded contexts

| Context | Owns | Does NOT own |
|---|---|---|
| **Growth OS (this repo)** | EOS, workers, queue, DLQ, runs, scheduling, AI Runtime contract, prompts, analytics emissions, future engines | users, orgs, billing, brands, reports, product business logic |
| **Consumer product** (e.g. the first private consumer) | its own domain, users, billing, UI | any orchestration/runtime internals |

## The execution spine

```
job source ──▶ durable queue (jobs + DLQ) ──▶ Worker (claim → reason → persist) ──▶ runs store
                                              │
                                              └─ reason step calls the AI Runtime (contract)
```

1. **Enqueue** — a consumer (or a scheduler) inserts a job into `content_engine_queue`.
2. **Claim** — a headless Worker atomically claims the next pending job (single-Worker lock via
   `locked_at`).
3. **Reason** — the Worker invokes an AI Runtime via the OpenAI-compatible contract
   (`docs/runtime-contract.md`). The EOS never names a model/provider.
4. **Complete / Fail** — on success the structured result is written to `content_engine_runs` and
   the job is marked `done`; on failure it retries up to `max_attempts`, then routes to
   `content_engine_dlq`.
5. **Observe** — `content_engine_runs` is the output + observability store. The consumer reads
   results via the public API or subscribes to events.

## Engine-agnostic by design

The queue contract is generic: `engine` + `stage` + `payload_json`. The Content engine hosts
`score`, `generate`, `review`, and `publish` stages on the same spine, plus the deterministic
**Selection** mechanism (EOM §8, not a stage), with its **Approved Pool** as a logical view over
`content_engine_runs`. GEO, Prospect, Analytics, and others reuse the same spine with their own
engine id and skill.

## Runtime-agnostic by design

The reasoning step speaks only the AI Runtime contract (ADR-022). Today the reference runtime is
Hermes; a future runtime (e.g. OpenCode) is a non-event for EOS code as long as it serves the same
contract.

## Why a separate platform

- **Independent lifecycle:** own versioning, release cadence, roadmap.
- **Clean ownership:** the platform never sees product secrets or customer data.
- **Reuse:** any product integrates identically — the first consumer is just the first of many.
